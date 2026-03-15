"""ORACLE — Multi-Market Scanner
Fetches up to 500 markets from Polymarket, scores and ranks them for
simulation potential, and outputs the top prediction targets.

Usage:
    python3 market_scanner.py           # Run scan, print top 10
    python3 market_scanner.py --top 20  # Show top 20
    python3 market_scanner.py --json    # Output as JSON
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ─── Config (standalone-friendly) ─────────────────────────────────
try:
    from config import (
        DATA_DIR, GAMMA_API, PREDICTIONS_DIR, SKIP_CATEGORIES,
        now_iso, print_banner,
    )
except ImportError:
    # Allow running from oracle/ root: python3 nerve/market_scanner.py
    sys.path.insert(0, str(Path(__file__).parent))
    from config import (
        DATA_DIR, GAMMA_API, PREDICTIONS_DIR, SKIP_CATEGORIES,
        now_iso, print_banner,
    )

RANKINGS_FILE = DATA_DIR / "market_rankings.json"

# ─── Extended keyword lists ───────────────────────────────────────

INCLUDE_KEYWORDS = [
    "approval", "election", "ceasefire", "peace", "recession", "tariff",
    "fed", "iran", "ukraine", "trump", "midterm", "congress", "senate",
    "governor", "nato", "china", "taiwan", "oil", "inflation", "war",
    "nuclear", "sanction", "regime", "immigration", "border", "policy",
    "ban", "court", "impeach", "indictment", "poll",
]

EXCLUDE_KEYWORDS = [
    "nba", "nfl", "nhl", "mlb", "ufc", "mma", "premier league",
    "champions league", "temperature", "weather", "netflix", "spotify",
    "tiktok", "youtube", "esports", "cricket", "airdrop", "token launch",
] + list(SKIP_CATEGORIES)

# De-duplicate
EXCLUDE_KEYWORDS = list(set(EXCLUDE_KEYWORDS))

# Sentiment weights by keyword tier
_TIER_HIGH = {
    "approval": 1.0, "favorability": 1.0, "popularity": 1.0,
    "public opinion": 1.0, "approval rating": 1.0, "job approval": 1.0,
    "net approval": 1.0, "disapproval": 1.0,
    "election": 1.0, "midterm": 1.0, "poll": 0.9,
}

_TIER_MED_HIGH = {
    "ceasefire": 0.85, "peace": 0.85, "war": 0.85, "invasion": 0.85,
    "conflict": 0.85, "escalation": 0.85,
    "iran": 0.8, "ukraine": 0.8, "taiwan": 0.8, "china": 0.8,
    "nato": 0.8, "russia": 0.8, "nuclear": 0.85,
    "trump": 0.8, "biden": 0.8,
    "congress": 0.8, "senate": 0.8, "governor": 0.75,
    "impeach": 0.85, "indictment": 0.85,
    "recession": 0.8, "regime": 0.8,
}

_TIER_MED = {
    "tariff": 0.6, "sanction": 0.65, "trade war": 0.65,
    "policy": 0.6, "ban": 0.6, "court": 0.6, "regulation": 0.6,
    "executive order": 0.6, "supreme court": 0.65,
    "immigration": 0.6, "border": 0.6,
    "oil": 0.55, "crude": 0.55, "opec": 0.55,
    "debt ceiling": 0.6, "government shutdown": 0.6,
    "stimulus": 0.55, "infrastructure": 0.5,
}

_TIER_LOW = {
    "fed": 0.3, "interest rate": 0.3, "rate cut": 0.3, "rate hike": 0.3,
    "inflation": 0.35, "cpi": 0.3, "gdp": 0.3,
    "unemployment": 0.3, "jobs report": 0.3,
}


# ─── Core functions ───────────────────────────────────────────────

def fetch_markets_page(limit: int = 50, offset: int = 0) -> list:
    """Fetch one page of active markets from the Gamma API."""
    params = {
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume",
        "ascending": "false",
    }
    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _should_exclude(question_lower: str) -> bool:
    for kw in EXCLUDE_KEYWORDS:
        if kw in question_lower:
            return True
    return False


def _matches_include(question_lower: str) -> bool:
    for kw in INCLUDE_KEYWORDS:
        if kw in question_lower:
            return True
    return False


def sentiment_weight(question: str) -> float:
    """Score how narrative/sentiment-driven a market question is (0-1)."""
    q = question.lower()

    if _should_exclude(q):
        return 0.0

    if not _matches_include(q):
        return 0.0

    # Check tiers from highest to lowest, return best match
    best = 0.0
    for kw, score in _TIER_HIGH.items():
        if kw in q:
            best = max(best, score)
    if best > 0:
        return best

    for kw, score in _TIER_MED_HIGH.items():
        if kw in q:
            best = max(best, score)
    if best > 0:
        return best

    for kw, score in _TIER_MED.items():
        if kw in q:
            best = max(best, score)
    if best > 0:
        return best

    for kw, score in _TIER_LOW.items():
        if kw in q:
            best = max(best, score)
    if best > 0:
        return best

    # Matched an include keyword but no tier — give baseline
    return 0.2


def sweet_spot_score(yes_price: float) -> float:
    """Score how close the market is to 50/50. Peaks at 50%, drops at extremes.
    yes_price is expected as 0-100 (percentage)."""
    return max(0.0, 1.0 - abs(yes_price - 50.0) / 50.0)


def timing_score(end_date_str: str) -> float:
    """Score based on days to resolution. Peaks at 7-21 days."""
    if not end_date_str:
        return 0.4  # unknown end date — moderate default

    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        days = (end - datetime.now(timezone.utc)).total_seconds() / 86400

        if days < 0:
            return 0.0    # already expired
        elif days < 1:
            return 0.1
        elif days < 3:
            return 0.4
        elif days < 7:
            return 0.8
        elif days <= 14:
            return 1.0    # sweet spot
        elif days <= 21:
            return 1.0    # sweet spot
        elif days <= 30:
            return 0.7
        elif days <= 60:
            return 0.5
        elif days <= 90:
            return 0.3
        else:
            return 0.15
    except Exception:
        return 0.4


def volume_score(volume: float) -> float:
    """Logarithmic volume score, saturates around $10M."""
    return min(1.0, math.log10(max(volume, 1)) / 7.0)


def _parse_yes_price(market: dict) -> float:
    """Extract yes price (0-100) from market data."""
    prices_raw = market.get("outcomePrices", "")
    if isinstance(prices_raw, str) and prices_raw:
        try:
            prices = json.loads(prices_raw)
            if prices:
                return float(prices[0]) * 100.0
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
    elif isinstance(prices_raw, list) and prices_raw:
        try:
            return float(prices_raw[0]) * 100.0
        except (IndexError, TypeError, ValueError):
            pass

    # Fallback: try bestBid / bestAsk
    best_bid = market.get("bestBid")
    if best_bid is not None:
        try:
            return float(best_bid) * 100.0
        except (TypeError, ValueError):
            pass

    return 50.0  # unknown — assume 50/50


def _days_to_resolution(end_date_str: str) -> float | None:
    """Calculate days until market resolves."""
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return round((end - datetime.now(timezone.utc)).total_seconds() / 86400, 1)
    except Exception:
        return None


def _load_existing_predictions() -> set:
    """Load slugs/questions of markets ORACLE has already predicted on."""
    predicted = set()
    active_dir = PREDICTIONS_DIR / "active"
    if active_dir.exists():
        for f in active_dir.iterdir():
            if f.suffix == ".md":
                predicted.add(f.stem.lower())
    archive_dir = PREDICTIONS_DIR / "archive"
    if archive_dir.exists():
        for f in archive_dir.iterdir():
            if f.suffix == ".md":
                predicted.add(f.stem.lower())
    return predicted


def _check_already_predicted(question: str, predicted_set: set) -> bool:
    """Heuristic check if a question overlaps with existing predictions."""
    q_lower = question.lower()
    for pred in predicted_set:
        # Check if key words from the prediction filename appear in the question
        words = pred.replace("_", " ").replace("-", " ").split()
        meaningful = [w for w in words if len(w) > 3 and not w.startswith("oracle")]
        if meaningful and all(w in q_lower for w in meaningful[:3]):
            return True
    return False


def score_market(market: dict, predicted_set: set) -> dict | None:
    """Score a single market for simulation potential. Returns enriched dict or None."""
    question = market.get("question", "")
    vol = float(market.get("volume", 0) or 0)
    end_date = market.get("endDate", "")

    sw = sentiment_weight(question)
    if sw < 0.1:
        return None
    if vol < 500:
        return None

    yes_price = _parse_yes_price(market)
    ss = sweet_spot_score(yes_price)
    ts = timing_score(end_date)
    vs = volume_score(vol)

    edge = sw * ss * ts * vs
    if edge < 0.02:
        return None

    days_left = _days_to_resolution(end_date)
    already_predicted = _check_already_predicted(question, predicted_set)

    # Priority label
    if edge >= 0.25:
        priority = "HIGH"
    elif edge >= 0.12:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    return {
        "question": question,
        "slug": market.get("slug", ""),
        "market_id": market.get("id", ""),
        "yes_price": round(yes_price, 1),
        "no_price": round(100.0 - yes_price, 1),
        "volume": vol,
        "end_date": end_date,
        "days_to_resolution": days_left,
        "sentiment_weight": round(sw, 3),
        "sweet_spot": round(ss, 3),
        "timing_score": round(ts, 3),
        "volume_score": round(vs, 3),
        "edge_score": round(edge, 4),
        "already_predicted": already_predicted,
        "priority": priority,
        "url": f"https://polymarket.com/event/{market.get('slug', '')}",
        "description": (market.get("description") or "")[:500],
    }


# ─── Scanner ──────────────────────────────────────────────────────

def scan_markets(pages: int = 10, per_page: int = 50, quiet: bool = False) -> list:
    """Fetch up to `pages * per_page` markets, score and rank them."""
    predicted_set = _load_existing_predictions()
    all_scored = []

    for page in range(pages):
        if not quiet:
            print(f"  Fetching page {page + 1}/{pages}...", end="", flush=True)
        try:
            markets = fetch_markets_page(limit=per_page, offset=page * per_page)
        except Exception as e:
            if not quiet:
                print(f" error: {e}")
            continue

        page_hits = 0
        for m in markets:
            scored = score_market(m, predicted_set)
            if scored:
                all_scored.append(scored)
                page_hits += 1

        if not quiet:
            print(f" {page_hits} targets")

        # If API returned fewer than per_page, we've exhausted results
        if len(markets) < per_page:
            if not quiet:
                print(f"  (reached end of available markets at page {page + 1})")
            break

    # Sort by edge_score descending
    all_scored.sort(key=lambda x: x["edge_score"], reverse=True)

    # Save rankings
    output = {
        "generated_at": now_iso(),
        "total_scanned": (page + 1) * per_page if all_scored else 0,
        "total_scored": len(all_scored),
        "rankings": all_scored,
    }
    with open(RANKINGS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    return all_scored


def get_rankings(top: int = 10) -> list:
    """Load cached rankings or run a fresh scan. Returns top N markets.
    This is the function the API should call."""
    if RANKINGS_FILE.exists():
        try:
            data = json.loads(RANKINGS_FILE.read_text())
            rankings = data.get("rankings", [])
            if rankings:
                return rankings[:top]
        except (json.JSONDecodeError, KeyError):
            pass

    # No cache — run fresh scan
    results = scan_markets(quiet=True)
    return results[:top]


# ─── Display ──────────────────────────────────────────────────────

def print_rankings(markets: list, top: int = 10):
    """Pretty-print the ranked market targets."""
    targets = markets[:top]
    total = len(markets)

    print(f"  Scored {total} sentiment-driven markets.")
    print(f"  Showing top {min(top, total)}:\n")

    header = (
        f"  {'#':<4} {'Edge':>6}  {'Sw':>4} {'Ss':>4} {'Tm':>4} {'Vl':>4}"
        f"  {'Yes':>5} {'No':>5}  {'Days':>5}  {'Vol':>12}  {'Pri':<6} Question"
    )
    print(header)
    print(f"  {'=' * 110}")

    for i, m in enumerate(targets, 1):
        q = m["question"]
        if len(q) > 45:
            q = q[:42] + "..."

        days_str = f"{m['days_to_resolution']:.0f}" if m["days_to_resolution"] is not None else "?"
        predicted_mark = " *" if m["already_predicted"] else ""

        print(
            f"  {i:<4} {m['edge_score']:>6.4f}  "
            f"{m['sentiment_weight']:>4.2f} {m['sweet_spot']:>4.2f} "
            f"{m['timing_score']:>4.2f} {m['volume_score']:>4.2f}  "
            f"{m['yes_price']:>4.1f}% {m['no_price']:>4.1f}%  "
            f"{days_str:>5}  "
            f"${m['volume']:>11,.0f}  "
            f"{m['priority']:<6} "
            f"{q}{predicted_mark}"
        )

    predicted_count = sum(1 for m in targets if m["already_predicted"])
    if predicted_count:
        print(f"\n  * = ORACLE has existing prediction ({predicted_count} of {min(top, total)})")

    print(f"\n  Rankings saved to: {RANKINGS_FILE}")


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ORACLE Multi-Market Scanner — rank Polymarket targets for simulation"
    )
    parser.add_argument(
        "--top", type=int, default=10,
        help="Number of top targets to display (default: 10)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON instead of table"
    )
    parser.add_argument(
        "--pages", type=int, default=10,
        help="Number of API pages to fetch (50 markets each, default: 10)"
    )
    args = parser.parse_args()

    if not args.json:
        print_banner("Multi-Market Scanner")

    markets = scan_markets(pages=args.pages, quiet=args.json)

    if args.json:
        output = {
            "generated_at": now_iso(),
            "total_scored": len(markets),
            "top": markets[:args.top],
        }
        print(json.dumps(output, indent=2))
    else:
        print()
        print_rankings(markets, top=args.top)
        print()


if __name__ == "__main__":
    main()
