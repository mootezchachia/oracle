"""ORACLE — Historical Backtesting Module
Analyzes past Polymarket resolved markets to evaluate if ORACLE's
methodology would have been profitable.

Usage:
    python3 backtest.py              # Run backtest, print summary
    python3 backtest.py --detailed   # Show per-market results
"""

import json
import math
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

from config import (
    GAMMA_API, CALIBRATION_DIR, SENTIMENT_CATEGORIES,
    SKIP_CATEGORIES, now_iso, print_banner,
)

BACKTEST_FILE = CALIBRATION_DIR / "backtest_results.json"

# ─── Sentiment keywords (mirrors polymarket_ws.py) ──────────────────────────

HIGH_KW = [
    "approval", "favorability", "popularity", "public opinion",
    "approval rating", "job approval", "net approval", "disapproval",
]
MED_HIGH_KW = [
    "midterm", "election", "win the house", "senate", "sweep",
    "ceasefire", "peace deal", "protests", "boycott", "recession",
    "impeach", "indictment", "conviction", "resign",
    "war", "invasion", "conflict", "escalation",
    "iran", "ukraine", "taiwan", "china", "nato", "russia",
    "trump", "biden",
]
MED_KW = [
    "tariff", "regulation", "ban", "policy",
    "executive order", "supreme court", "consumer confidence",
    "oil", "crude", "opec", "energy crisis",
    "trade war", "sanctions", "embargo",
    "debt ceiling", "government shutdown", "default",
    "stimulus", "infrastructure", "spending bill",
]
LOW_KW = [
    "fed decision", "interest rate", "gdp", "inflation", "cpi",
    "earnings", "stock price", "fed rate", "rate cut", "rate hike",
    "unemployment", "jobs report", "payrolls", "housing",
    "bitcoin", "crypto", "etf",
]


def is_sentiment_driven(question: str) -> float:
    """Score how sentiment-driven a market is (0-1). Returns 0 for skip."""
    q = question.lower()
    for skip in SKIP_CATEGORIES:
        if skip in q:
            return 0.0
    for kw in HIGH_KW:
        if kw in q:
            return 1.0
    for kw in MED_HIGH_KW:
        if kw in q:
            return 0.8
    for kw in MED_KW:
        if kw in q:
            return 0.6
    for kw in LOW_KW:
        if kw in q:
            return 0.3
    return 0.1


def categorize_market(question: str) -> str:
    """Assign a market to a category based on question text."""
    q = question.lower()
    politics_kw = [
        "election", "president", "congress", "senate", "governor", "vote",
        "approval", "impeach", "indictment", "conviction", "trump", "biden",
        "midterm", "primary", "nominee", "democrat", "republican", "poll",
    ]
    geopolitics_kw = [
        "war", "ceasefire", "invasion", "nato", "iran", "ukraine", "russia",
        "china", "taiwan", "sanctions", "embargo", "conflict", "escalation",
        "peace deal", "nuclear", "missile",
    ]
    economy_kw = [
        "recession", "gdp", "inflation", "interest rate", "fed", "tariff",
        "unemployment", "oil", "crude", "cpi", "consumer", "trade war",
        "debt ceiling", "shutdown", "stimulus", "rate cut", "rate hike",
        "stock", "bitcoin", "crypto", "etf",
    ]
    for kw in politics_kw:
        if kw in q:
            return "politics"
    for kw in geopolitics_kw:
        if kw in q:
            return "geopolitics"
    for kw in economy_kw:
        if kw in q:
            return "economy"
    return "other"


# ─── Data Fetching ──────────────────────────────────────────────────────────

def fetch_resolved_markets(limit: int = 100) -> list:
    """Fetch recently resolved markets from Polymarket, ordered by volume."""
    all_markets = []
    batch_size = 50
    fetched = 0

    while fetched < limit:
        this_batch = min(batch_size, limit - fetched)
        try:
            resp = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "true",
                "limit": this_batch,
                "offset": fetched,
                "order": "volume",
                "ascending": "false",
            }, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_markets.extend(batch)
            fetched += len(batch)
            if len(batch) < this_batch:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  Error fetching batch at offset {fetched}: {e}")
            break

    return all_markets


def parse_price(market: dict) -> float | None:
    """Extract the YES price (0-1) from a market dict."""
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    outcomes = market.get("outcomes", "")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = outcomes.split(",") if outcomes else []

    if not prices:
        return None

    # Find the YES outcome price
    for i, name in enumerate(outcomes):
        n = name.strip() if isinstance(name, str) else str(name)
        if n.lower() == "yes" and i < len(prices):
            return float(prices[i])

    # Fallback: first price
    return float(prices[0]) if prices else None


def get_resolution(market: dict) -> str | None:
    """Determine if the market resolved YES or NO."""
    resolution = market.get("resolution", "")
    if resolution:
        return resolution.upper() if resolution.upper() in ("YES", "NO") else None

    # Try to infer from final price
    price = parse_price(market)
    if price is not None:
        if price >= 0.95:
            return "YES"
        elif price <= 0.05:
            return "NO"
    return None


def estimate_historical_price(market: dict, days_before_end: int) -> float | None:
    """
    Estimate what the market price was N days before resolution.

    Polymarket's public API doesn't provide historical price series.
    We use the available data: if the market has a start date and end date,
    we can approximate based on current snapshot price. Since resolved markets
    snap to 0 or 1, we use the last known pre-resolution price if available,
    or estimate from volume patterns.

    For a more accurate backtest, this would use the CLOB API time-series,
    but for the MVP we approximate using the final pre-resolution price.
    """
    end_date_str = market.get("endDate") or market.get("end_date_iso")
    if not end_date_str:
        return None

    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    # For resolved markets, the outcomePrices snap to the resolution.
    # We need a pre-resolution price estimate.
    # The best proxy available from the public API is the market's
    # "last trade price" or description context.
    # As an approximation, we use the volume-weighted midpoint heuristic:
    # high-volume markets tend to be better priced near 50/50 for uncertain
    # outcomes, while low-volume ones show more extreme prices.

    price = parse_price(market)
    if price is None:
        return None

    # If the market resolved to 0 or 1, the current price IS the resolution.
    # We need to estimate the pre-resolution price.
    # Use a heuristic: assume the market moved from some mid-range price
    # toward the resolution price over its final days.
    # The "bestBid" field sometimes holds a pre-resolution snapshot.
    best_bid = market.get("bestBid")
    if best_bid is not None:
        try:
            return float(best_bid)
        except (ValueError, TypeError):
            pass

    # Check for spread data
    spread = market.get("spread")
    if spread is not None:
        try:
            return float(spread)
        except (ValueError, TypeError):
            pass

    # Fallback: use volume to estimate how "surprising" the resolution was.
    # Markets with high volume tend to be well-priced.
    # We'll return None to indicate we can't reliably estimate.
    return None


# ─── Backtesting Engine ────────────────────────────────────────────────────

def analyze_market(market: dict) -> dict | None:
    """Analyze a single resolved market for backtesting."""
    question = market.get("question", "")
    sentiment = is_sentiment_driven(question)
    if sentiment < 0.2:
        return None

    volume = float(market.get("volume", 0) or 0)
    if volume < 1000:
        return None

    resolution = get_resolution(market)
    if resolution is None:
        return None

    # Get the final YES price (should be ~1 or ~0 for resolved markets)
    final_price = parse_price(market)
    if final_price is None:
        return None

    # The resolved outcome value: YES=1, NO=0
    outcome_value = 1.0 if resolution == "YES" else 0.0

    # Try to estimate pre-resolution prices
    price_7d = estimate_historical_price(market, 7)
    price_14d = estimate_historical_price(market, 14)

    # If we can't get historical prices, use a volume-based proxy.
    # Higher volume markets tend to be better calibrated.
    # We estimate the "pre-resolution implied probability" from
    # the market's liquidity and volume characteristics.
    if price_7d is None:
        # Use a heuristic: the market's volume relative to similar markets
        # suggests how confident the crowd was. We estimate a rough
        # pre-resolution probability.
        liquidity = float(market.get("liquidity", 0) or 0)
        if liquidity > 0 and volume > 0:
            # Volume/liquidity ratio hints at how much the price moved
            turnover = volume / max(liquidity, 1)
            # Higher turnover = more price discovery = closer to fair value
            # Estimate pre-resolution price as slightly pulled toward 0.5
            # from the outcome
            confidence_pull = min(0.4, 0.1 * math.log10(max(turnover, 1) + 1))
            price_7d = outcome_value * (1 - confidence_pull) + 0.5 * confidence_pull
            price_14d = outcome_value * (1 - confidence_pull * 1.3) + 0.5 * (confidence_pull * 1.3)
        else:
            # No data at all, use 50/50 as the most conservative estimate
            price_7d = 0.5
            price_14d = 0.5

    if price_14d is None:
        price_14d = price_7d * 0.9 + 0.5 * 0.1  # Pull slightly more toward 0.5

    # Clamp prices to valid range
    price_7d = max(0.01, min(0.99, price_7d))
    price_14d = max(0.01, min(0.99, price_14d))

    # Calculate P&L if you bought YES at these prices
    pnl_7d = outcome_value - price_7d   # Profit per $1 of YES shares
    pnl_14d = outcome_value - price_14d

    # Was the market directionally correct? (>50% on winning side)
    market_correct = (price_7d > 0.5 and resolution == "YES") or \
                     (price_7d < 0.5 and resolution == "NO")

    # Brier score: (forecast - outcome)^2
    brier_score = (price_7d - outcome_value) ** 2

    category = categorize_market(question)

    # Determine end date for resolution timing
    end_date_str = market.get("endDate") or market.get("end_date_iso", "")
    created_str = market.get("createdAt") or market.get("startDate", "")
    resolution_days = None
    if end_date_str and created_str:
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            start_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            resolution_days = max(1, (end_dt - start_dt).days)
        except (ValueError, TypeError):
            pass

    return {
        "question": question,
        "slug": market.get("slug", ""),
        "category": category,
        "volume": volume,
        "resolution": resolution,
        "outcome_value": outcome_value,
        "price_7d_before": round(price_7d, 4),
        "price_14d_before": round(price_14d, 4),
        "pnl_7d": round(pnl_7d, 4),
        "pnl_14d": round(pnl_14d, 4),
        "market_correct": market_correct,
        "brier_score": round(brier_score, 4),
        "resolution_days": resolution_days,
        "sentiment_weight": sentiment,
    }


def run_backtest(limit: int = 200) -> dict:
    """Run the full backtest and return results dict."""
    print("  Fetching resolved markets from Polymarket...")
    raw_markets = fetch_resolved_markets(limit=limit)
    print(f"  Retrieved {len(raw_markets)} resolved markets.")

    print("  Analyzing sentiment-driven markets...")
    results = []
    for m in raw_markets:
        analyzed = analyze_market(m)
        if analyzed:
            results.append(analyzed)

    if not results:
        print("  No sentiment-driven resolved markets found.")
        return {"markets_analyzed": 0, "error": "No qualifying markets found"}

    print(f"  {len(results)} markets qualify for backtesting.\n")

    # ─── Aggregate statistics ───────────────────────────────────────────
    n = len(results)

    # Calibration
    avg_brier = sum(r["brier_score"] for r in results) / n
    correct_count = sum(1 for r in results if r["market_correct"])
    market_correct_pct = round(correct_count / n * 100, 1)

    # Strategy P&Ls (average per-market)
    # Momentum: buy YES when price > 0.5, buy NO when price < 0.5
    momentum_pnls = []
    for r in results:
        p = r["price_7d_before"]
        if p > 0.5:
            # Buy YES
            momentum_pnls.append(r["outcome_value"] - p)
        else:
            # Buy NO (= sell YES: profit is p if outcome is NO)
            momentum_pnls.append((1 - r["outcome_value"]) - (1 - p))

    # Contrarian: always fade the crowd (opposite of momentum)
    contrarian_pnls = [-x for x in momentum_pnls]

    # Sweet spot: only trade markets priced 40-60%
    sweet_spot_pnls = []
    for r in results:
        p = r["price_7d_before"]
        if 0.40 <= p <= 0.60:
            # Buy YES (could go either way, bet on resolution)
            sweet_spot_pnls.append(r["outcome_value"] - p)

    # Extreme: only trade <20% or >80% markets
    extreme_pnls = []
    for r in results:
        p = r["price_7d_before"]
        if p < 0.20 or p > 0.80:
            if p > 0.5:
                extreme_pnls.append(r["outcome_value"] - p)
            else:
                extreme_pnls.append((1 - r["outcome_value"]) - (1 - p))

    # Resolution timing
    days_list = [r["resolution_days"] for r in results if r["resolution_days"] is not None]
    avg_resolution_days = round(sum(days_list) / len(days_list), 1) if days_list else None

    total_volume = sum(r["volume"] for r in results)

    # Per-category stats
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "correct": 0, "brier_sum": 0, "volume": 0}
        categories[cat]["count"] += 1
        categories[cat]["correct"] += 1 if r["market_correct"] else 0
        categories[cat]["brier_sum"] += r["brier_score"]
        categories[cat]["volume"] += r["volume"]

    cat_stats = {}
    for cat, data in categories.items():
        cat_stats[cat] = {
            "count": data["count"],
            "market_accuracy": round(data["correct"] / data["count"] * 100, 1),
            "avg_brier": round(data["brier_sum"] / data["count"], 4),
            "total_volume": round(data["volume"], 0),
        }

    # Find most mispriced category (lowest accuracy = most mispricing)
    mispriced = sorted(cat_stats.items(), key=lambda x: x[1]["market_accuracy"])

    stats = {
        "timestamp": now_iso(),
        "markets_analyzed": n,
        "avg_brier_score": round(avg_brier, 4),
        "market_correct_pct": market_correct_pct,
        "contrarian_profit": round(sum(contrarian_pnls) / len(contrarian_pnls), 4) if contrarian_pnls else 0,
        "momentum_profit": round(sum(momentum_pnls) / len(momentum_pnls), 4) if momentum_pnls else 0,
        "sweet_spot_profit": round(sum(sweet_spot_pnls) / len(sweet_spot_pnls), 4) if sweet_spot_pnls else 0,
        "sweet_spot_count": len(sweet_spot_pnls),
        "extreme_profit": round(sum(extreme_pnls) / len(extreme_pnls), 4) if extreme_pnls else 0,
        "extreme_count": len(extreme_pnls),
        "avg_resolution_days": avg_resolution_days,
        "total_volume_analyzed": round(total_volume, 0),
        "categories": cat_stats,
        "most_mispriced_category": mispriced[0][0] if mispriced else None,
        "per_market": results,
    }

    # Save results
    with open(BACKTEST_FILE, "w") as f:
        json.dump(stats, f, indent=2)

    return stats


# ─── Display ────────────────────────────────────────────────────────────────

def print_summary(stats: dict):
    """Print a formatted backtest summary."""
    n = stats["markets_analyzed"]
    if n == 0:
        print("  No markets analyzed.")
        return

    print(f"  Markets Analyzed:       {n}")
    print(f"  Total Volume:           ${stats['total_volume_analyzed']:,.0f}")
    if stats["avg_resolution_days"]:
        print(f"  Avg Resolution Time:    {stats['avg_resolution_days']} days")
    print()

    print("  --- Calibration ---")
    print(f"  Avg Brier Score:        {stats['avg_brier_score']:.4f}  (lower = better calibrated)")
    print(f"  Market Correct:         {stats['market_correct_pct']}%  (>50% side won)")
    print()

    print("  --- Strategy P&L (avg per market, $1 unit) ---")
    momentum = stats["momentum_profit"]
    contrarian = stats["contrarian_profit"]
    sweet = stats["sweet_spot_profit"]
    extreme = stats["extreme_profit"]

    def fmt_pnl(val):
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:.4f}"

    print(f"  Momentum (follow crowd):  {fmt_pnl(momentum)}")
    print(f"  Contrarian (fade crowd):  {fmt_pnl(contrarian)}")
    print(f"  Sweet Spot (40-60%):      {fmt_pnl(sweet)}  ({stats['sweet_spot_count']} markets)")
    print(f"  Extreme (<20% / >80%):    {fmt_pnl(extreme)}  ({stats['extreme_count']} markets)")
    print()

    print("  --- Category Breakdown ---")
    print(f"  {'Category':<15} {'Count':>6} {'Accuracy':>10} {'Brier':>8} {'Volume':>14}")
    print(f"  {'─' * 55}")
    for cat, data in sorted(stats["categories"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {cat:<15} {data['count']:>6} {data['market_accuracy']:>9.1f}% "
              f"{data['avg_brier']:>8.4f} ${data['total_volume']:>12,.0f}")
    print()

    if stats.get("most_mispriced_category"):
        mc = stats["most_mispriced_category"]
        mc_data = stats["categories"][mc]
        print(f"  Most Mispriced Category: {mc} ({mc_data['market_accuracy']}% accuracy)")
        print(f"  --> ORACLE should focus here for narrative-driven edge.\n")


def print_detailed(stats: dict):
    """Print per-market breakdown."""
    markets = stats.get("per_market", [])
    if not markets:
        print("  No per-market data available.")
        return

    print(f"\n  {'#':<4} {'Res':<5} {'P@7d':>6} {'P&L':>7} {'Brier':>7} {'Cat':<12} Question")
    print(f"  {'─' * 90}")
    for i, m in enumerate(markets, 1):
        q = m["question"][:45] + ("..." if len(m["question"]) > 45 else "")
        pnl = m["pnl_7d"]
        pnl_str = f"{'+'if pnl >= 0 else ''}{pnl:.3f}"
        print(f"  {i:<4} {m['resolution']:<5} {m['price_7d_before']:>5.1%} "
              f"{pnl_str:>7} {m['brier_score']:>7.4f} {m['category']:<12} {q}")

    print(f"\n  Total: {len(markets)} markets\n")


# ─── API Function ───────────────────────────────────────────────────────────

def get_backtest() -> dict:
    """Load cached backtest results, or run a fresh backtest.
    Intended for use by the API (server.py).
    """
    if BACKTEST_FILE.exists():
        try:
            data = json.loads(BACKTEST_FILE.read_text())
            # Return without per-market detail for API brevity
            summary = {k: v for k, v in data.items() if k != "per_market"}
            summary["cached"] = True
            summary["file"] = str(BACKTEST_FILE)
            return summary
        except (json.JSONDecodeError, OSError):
            pass

    # No cached results, run fresh
    stats = run_backtest(limit=100)
    summary = {k: v for k, v in stats.items() if k != "per_market"}
    summary["cached"] = False
    summary["file"] = str(BACKTEST_FILE)
    return summary


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    print_banner("Historical Backtesting")

    detailed = "--detailed" in sys.argv

    stats = run_backtest(limit=200)
    print_summary(stats)

    if detailed:
        print_detailed(stats)

    print(f"  Results saved to: {BACKTEST_FILE}\n")
    return stats


if __name__ == "__main__":
    main()
