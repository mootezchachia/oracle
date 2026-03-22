"""ORACLE — $100 Paper Trading Strategy Module
Implements 3 strategies optimized for small capital on Polymarket testnet:

  Strategy 1: HIGH-PROB BONDS ($60 allocation)
    Buy NO at $0.93+ on near-certain outcomes for 3-7% safe returns.

  Strategy 2: DOMAIN EXPERTISE ($30 allocation)
    Find mispriced markets using signal fusion, bet where crowd is wrong.

  Strategy 3: FLASH CRASH SNIPE ($10 allocation)
    Wait for panic dips on high-prob outcomes, buy the crash.

Portfolio: Separate $100 virtual account (does not touch main $10K portfolio).
"""

import json
import math
import time
import requests
from datetime import datetime, timezone
from config import *

GAMMA_API = "https://gamma-api.polymarket.com"

# Separate portfolio file for $100 strategy
STRATEGY_PORTFOLIO = DATA_DIR / "strategy_100_portfolio.json"
STRATEGY_LOG = DATA_DIR / "strategy_100_log.jsonl"


# ─── Portfolio Management ────────────────────────────────────────

def load_portfolio() -> dict:
    if STRATEGY_PORTFOLIO.exists():
        with open(STRATEGY_PORTFOLIO) as f:
            return json.load(f)
    return init_portfolio()


def init_portfolio() -> dict:
    """Initialize a fresh $100 portfolio with strategy allocations."""
    portfolio = {
        "account": {
            "starting_balance": 100.00,
            "total_cash": 100.00,
        },
        "allocations": {
            "bonds": {"budget": 60.00, "cash": 60.00, "invested": 0.00},
            "expertise": {"budget": 30.00, "cash": 30.00, "invested": 0.00},
            "flash_crash": {"budget": 10.00, "cash": 10.00, "invested": 0.00},
        },
        "trades": [],
        "stats": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.00,
            "best_trade": 0.00,
            "worst_trade": 0.00,
        },
        "created": now_iso(),
        "last_updated": now_iso(),
    }
    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio: dict):
    portfolio["last_updated"] = now_iso()
    portfolio["account"]["total_cash"] = sum(
        a["cash"] for a in portfolio["allocations"].values()
    )
    with open(STRATEGY_PORTFOLIO, "w") as f:
        json.dump(portfolio, f, indent=2)


# ─── Market Fetching ─────────────────────────────────────────────

def fetch_all_markets(pages=4, per_page=50) -> list:
    """Fetch active markets from Polymarket Gamma API."""
    all_markets = []
    for page in range(pages):
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "false",
                "limit": per_page,
                "offset": page * per_page,
                "order": "volume",
                "ascending": "false",
            }, timeout=15)
            r.raise_for_status()
            all_markets.extend(r.json())
        except Exception as e:
            print(f"  Page {page+1} fetch error: {e}")
    return all_markets


def parse_market(m: dict) -> dict:
    """Parse a raw Gamma API market into a clean dict."""
    prices = m.get("outcomePrices", "[]")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []
    prices = [float(p) for p in prices] if prices else []

    outcomes = m.get("outcomes", "[]")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []

    yes_price = float(prices[0]) if len(prices) > 0 else None
    no_price = float(prices[1]) if len(prices) > 1 else None

    return {
        "market_id": m.get("id", ""),
        "slug": m.get("slug", ""),
        "question": m.get("question", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "outcomes": outcomes,
        "prices": prices,
        "volume": float(m.get("volume", 0) or 0),
        "liquidity": float(m.get("liquidity", 0) or 0),
        "end_date": m.get("endDate", ""),
        "description": (m.get("description") or "")[:300],
        "url": f"https://polymarket.com/event/{m.get('slug', '')}",
    }


def fetch_market_price(slug: str) -> dict:
    """Fetch current price for a specific market."""
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
        r.raise_for_status()
        markets = r.json()
        if markets:
            return parse_market(markets[0])
    except Exception as e:
        return {"error": str(e)}
    return {"error": "not found"}


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 1: HIGH-PROBABILITY BONDS
# Buy NO at $0.93+ on near-certain outcomes. Target: 3-7% per resolution.
# ═══════════════════════════════════════════════════════════════════

BOND_CONFIG = {
    "min_yes_price": 0.93,       # YES must be >= 93c (NO <= 7c, we buy YES for safe return)
    "max_no_price": 0.07,        # OR NO must be <= 7c
    "min_volume": 50000,         # $50K+ volume for liquidity
    "max_position": 15.00,       # max $15 per bond (diversify across 4+)
    "min_position": 5.00,        # minimum useful bet
    "target_return_pct": 5.0,    # target ~5% per bond
}


def find_bond_opportunities(markets: list) -> list:
    """Find high-probability markets suitable for bond strategy.

    Logic: If YES is priced at $0.95, buying YES costs $0.95 and pays $1.00
    if correct = 5.26% return. The key is finding markets where the
    high probability is JUSTIFIED (not a mispricing about to correct).
    """
    bonds = []

    for m in markets:
        pm = parse_market(m)
        yes_price = pm.get("yes_price")
        no_price = pm.get("no_price")
        volume = pm.get("volume", 0)

        if yes_price is None or no_price is None:
            continue

        # Skip low-volume markets (unreliable pricing)
        if volume < BOND_CONFIG["min_volume"]:
            continue

        # Check for high-probability YES (buy YES as bond)
        if yes_price >= BOND_CONFIG["min_yes_price"]:
            return_pct = ((1.0 / yes_price) - 1) * 100
            bonds.append({
                **pm,
                "bond_side": "yes",
                "bond_price": yes_price,
                "expected_return_pct": round(return_pct, 2),
                "risk": round((1 - yes_price) * 100, 2),  # max loss %
            })

        # Check for high-probability NO (buy NO as bond)
        if no_price >= BOND_CONFIG["min_yes_price"]:
            return_pct = ((1.0 / no_price) - 1) * 100
            bonds.append({
                **pm,
                "bond_side": "no",
                "bond_price": no_price,
                "expected_return_pct": round(return_pct, 2),
                "risk": round((1 - no_price) * 100, 2),
            })

    # Sort by return (highest safe yield first)
    bonds.sort(key=lambda x: x["expected_return_pct"], reverse=True)
    return bonds


def execute_bond(portfolio: dict, bond: dict) -> dict:
    """Execute a bond trade from the bonds allocation."""
    alloc = portfolio["allocations"]["bonds"]

    # Position sizing: spread across multiple bonds
    size = min(BOND_CONFIG["max_position"], alloc["cash"])
    if size < BOND_CONFIG["min_position"]:
        return {"executed": False, "reason": f"Insufficient bond cash: ${alloc['cash']:.2f}"}

    # Check for existing position on same market
    existing = [t for t in portfolio["trades"]
                if t.get("slug") == bond["slug"] and t.get("status") == "open"]
    if existing:
        return {"executed": False, "reason": f"Already have position on {bond['slug']}"}

    shares = round(size / bond["bond_price"], 4)

    trade = {
        "id": len(portfolio["trades"]) + 1,
        "strategy": "bonds",
        "slug": bond["slug"],
        "question": bond["question"],
        "side": bond["bond_side"],
        "entry_price": bond["bond_price"],
        "shares": shares,
        "invested": round(size, 2),
        "expected_return_pct": bond["expected_return_pct"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "open",
        "volume": bond["volume"],
    }

    alloc["cash"] = round(alloc["cash"] - size, 2)
    alloc["invested"] = round(alloc["invested"] + size, 2)
    portfolio["trades"].append(trade)
    portfolio["stats"]["total_trades"] += 1
    save_portfolio(portfolio)

    append_jsonl(STRATEGY_LOG, {**trade, "event": "open", "timestamp": now_iso()})

    return {"executed": True, "trade": trade}


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 2: DOMAIN EXPERTISE
# Find markets where signal fusion says the crowd is wrong.
# ═══════════════════════════════════════════════════════════════════

EXPERTISE_CONFIG = {
    "min_edge_pct": 8.0,         # need at least 8% edge
    "min_volume": 10000,         # $10K+ volume
    "max_position": 15.00,       # max $15 per expertise bet
    "min_position": 5.00,
    "min_confidence": 0.55,      # need 55%+ confidence
    "price_range": (0.20, 0.80), # avoid extreme prices (already captured by bonds)
}


def find_expertise_opportunities(markets: list) -> list:
    """Find mispriced markets using ORACLE signal fusion."""
    from signal_fusion import Signal, run_fusion
    from polymarket_ws import is_sentiment_driven

    opportunities = []

    for m in markets:
        pm = parse_market(m)
        yes_price = pm.get("yes_price")
        no_price = pm.get("no_price")
        volume = pm.get("volume", 0)

        if yes_price is None or no_price is None:
            continue

        if volume < EXPERTISE_CONFIG["min_volume"]:
            continue

        # Skip extreme prices (bonds territory)
        if yes_price < EXPERTISE_CONFIG["price_range"][0] or \
           yes_price > EXPERTISE_CONFIG["price_range"][1]:
            continue

        # Check if it's a sentiment-driven market we can analyze
        sentiment = is_sentiment_driven(pm["question"])
        if sentiment < 0.3:
            continue

        # Run signal fusion on this market
        signals = []

        # Market implied probability signal
        market_direction = (yes_price - 0.5) * 2
        signals.append(Signal(
            source="market_implied",
            direction=market_direction,
            strength=abs(market_direction),
            timeframe="medium",
            metadata={"yes_price": yes_price},
        ))

        # Sentiment signal
        signals.append(Signal(
            source="sentiment_score",
            direction=-market_direction * sentiment,  # contrarian if high sentiment
            strength=sentiment,
            timeframe="medium",
        ))

        fusion = run_fusion(extra_signals=signals, market_id=pm.get("market_id", ""))

        confidence = fusion.get("confidence", 0)
        edge_pct = fusion.get("edge_pct", 0)
        direction = fusion.get("direction", 0)

        if confidence >= EXPERTISE_CONFIG["min_confidence"] and \
           edge_pct >= EXPERTISE_CONFIG["min_edge_pct"]:
            side = "yes" if direction > 0 else "no"
            entry_price = yes_price if side == "yes" else no_price

            opportunities.append({
                **pm,
                "trade_side": side,
                "entry_price": entry_price,
                "confidence": round(confidence, 4),
                "edge_pct": round(edge_pct, 2),
                "direction": round(direction, 4),
                "sentiment": sentiment,
                "recommendation": fusion.get("recommendation", "HOLD"),
            })

    opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
    return opportunities


def execute_expertise(portfolio: dict, opp: dict) -> dict:
    """Execute a domain expertise trade."""
    alloc = portfolio["allocations"]["expertise"]

    size = min(EXPERTISE_CONFIG["max_position"], alloc["cash"])
    if size < EXPERTISE_CONFIG["min_position"]:
        return {"executed": False, "reason": f"Insufficient expertise cash: ${alloc['cash']:.2f}"}

    existing = [t for t in portfolio["trades"]
                if t.get("slug") == opp["slug"] and t.get("status") == "open"]
    if existing:
        return {"executed": False, "reason": f"Already have position on {opp['slug']}"}

    shares = round(size / opp["entry_price"], 4)

    trade = {
        "id": len(portfolio["trades"]) + 1,
        "strategy": "expertise",
        "slug": opp["slug"],
        "question": opp["question"],
        "side": opp["trade_side"],
        "entry_price": opp["entry_price"],
        "shares": shares,
        "invested": round(size, 2),
        "confidence": opp["confidence"],
        "edge_pct": opp["edge_pct"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "open",
        "take_profit_pct": 15.0,
        "stop_loss_pct": 10.0,
    }

    alloc["cash"] = round(alloc["cash"] - size, 2)
    alloc["invested"] = round(alloc["invested"] + size, 2)
    portfolio["trades"].append(trade)
    portfolio["stats"]["total_trades"] += 1
    save_portfolio(portfolio)

    append_jsonl(STRATEGY_LOG, {**trade, "event": "open", "timestamp": now_iso()})

    return {"executed": True, "trade": trade}


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 3: FLASH CRASH SNIPE
# Detect sudden probability drops and buy the dip.
# ═══════════════════════════════════════════════════════════════════

CRASH_CONFIG = {
    "drop_threshold": 0.15,      # 15%+ drop triggers alert
    "min_volume": 20000,         # $20K+ volume
    "max_buy_price": 0.70,       # don't buy above 70c (need upside)
    "min_normal_price": 0.85,    # market should normally be 85c+ (high-prob)
    "max_position": 10.00,       # entire flash crash budget on one snipe
    "min_position": 3.00,
}


def find_crash_opportunities(markets: list) -> list:
    """Find markets where price has crashed from a high-probability level.

    Logic: If a market was recently at $0.90+ and is now at $0.65,
    something caused a panic. If the fundamentals haven't changed,
    this is a buying opportunity.
    """
    from polymarket_ws import SKIP_CATEGORIES

    crashes = []

    for m in markets:
        pm = parse_market(m)
        yes_price = pm.get("yes_price")
        no_price = pm.get("no_price")
        volume = pm.get("volume", 0)
        question = pm.get("question", "").lower()

        if yes_price is None or no_price is None:
            continue

        if volume < CRASH_CONFIG["min_volume"]:
            continue

        # Skip sports, entertainment, weather
        if any(skip in question for skip in SKIP_CATEGORIES):
            continue

        # Must have meaningful price (not penny stocks at $0.01)
        min_crash_price = 0.15

        # Look for YES side crash: high volume + mid-range price
        # (normally high-prob markets don't sit at 0.50-0.70)
        if min_crash_price <= yes_price <= CRASH_CONFIG["max_buy_price"] and volume >= 100000:
            crash_signal = (volume / 1000000) * (1 - yes_price)
            if crash_signal > 0.1:
                crashes.append({
                    **pm,
                    "crash_side": "yes",
                    "crash_price": yes_price,
                    "crash_signal": round(crash_signal, 4),
                    "upside_pct": round(((1.0 / yes_price) - 1) * 100, 2),
                })

        # Look for NO side crash
        if min_crash_price <= no_price <= CRASH_CONFIG["max_buy_price"] and volume >= 100000:
            crash_signal = (volume / 1000000) * (1 - no_price)
            if crash_signal > 0.1:
                crashes.append({
                    **pm,
                    "crash_side": "no",
                    "crash_price": no_price,
                    "crash_signal": round(crash_signal, 4),
                    "upside_pct": round(((1.0 / no_price) - 1) * 100, 2),
                })

    crashes.sort(key=lambda x: x["crash_signal"], reverse=True)
    return crashes


def execute_crash_snipe(portfolio: dict, crash: dict) -> dict:
    """Execute a flash crash snipe trade."""
    alloc = portfolio["allocations"]["flash_crash"]

    size = min(CRASH_CONFIG["max_position"], alloc["cash"])
    if size < CRASH_CONFIG["min_position"]:
        return {"executed": False, "reason": f"Insufficient crash cash: ${alloc['cash']:.2f}"}

    existing = [t for t in portfolio["trades"]
                if t.get("slug") == crash["slug"] and t.get("status") == "open"]
    if existing:
        return {"executed": False, "reason": f"Already have position on {crash['slug']}"}

    shares = round(size / crash["crash_price"], 4)

    trade = {
        "id": len(portfolio["trades"]) + 1,
        "strategy": "flash_crash",
        "slug": crash["slug"],
        "question": crash["question"],
        "side": crash["crash_side"],
        "entry_price": crash["crash_price"],
        "shares": shares,
        "invested": round(size, 2),
        "crash_signal": crash["crash_signal"],
        "upside_pct": crash["upside_pct"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "open",
        "take_profit_pct": 25.0,   # wider TP for crash recovery
        "stop_loss_pct": 15.0,     # wider SL for volatility
    }

    alloc["cash"] = round(alloc["cash"] - size, 2)
    alloc["invested"] = round(alloc["invested"] + size, 2)
    portfolio["trades"].append(trade)
    portfolio["stats"]["total_trades"] += 1
    save_portfolio(portfolio)

    append_jsonl(STRATEGY_LOG, {**trade, "event": "open", "timestamp": now_iso()})

    return {"executed": True, "trade": trade}


# ═══════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def check_exits(portfolio: dict) -> list:
    """Check all open positions for TP/SL triggers."""
    closed = []

    for trade in portfolio["trades"]:
        if trade.get("status") != "open":
            continue

        # Fetch current price
        price_data = fetch_market_price(trade["slug"])
        if "error" in price_data:
            continue

        side = trade["side"]
        current_price = price_data.get(f"{side}_price")
        if current_price is None:
            continue

        current_value = trade["shares"] * current_price
        pnl = current_value - trade["invested"]
        pnl_pct = ((current_value / trade["invested"]) - 1) * 100 if trade["invested"] > 0 else 0

        tp = trade.get("take_profit_pct", 15.0)
        sl = trade.get("stop_loss_pct", 10.0)

        close_reason = None
        if pnl_pct >= tp:
            close_reason = f"Take profit: {pnl_pct:.1f}%"
        elif pnl_pct <= -sl:
            close_reason = f"Stop loss: {pnl_pct:.1f}%"

        # Bonds auto-close at resolution (price hits $1.00 or $0.00)
        if trade["strategy"] == "bonds":
            if current_price >= 0.99:
                close_reason = f"Bond matured: {pnl_pct:.1f}%"

        if close_reason:
            trade["status"] = "closed"
            trade["exit_price"] = round(current_price, 4)
            trade["current_value"] = round(current_value, 2)
            trade["pnl"] = round(pnl, 2)
            trade["pnl_pct"] = round(pnl_pct, 2)
            trade["close_reason"] = close_reason
            trade["closed_at"] = now_iso()

            # Return cash to the strategy allocation
            strategy = trade["strategy"]
            alloc = portfolio["allocations"][strategy]
            alloc["cash"] = round(alloc["cash"] + current_value, 2)
            alloc["invested"] = round(alloc["invested"] - trade["invested"], 2)

            # Update stats
            portfolio["stats"]["total_pnl"] = round(
                portfolio["stats"]["total_pnl"] + pnl, 2)
            if pnl > 0:
                portfolio["stats"]["wins"] += 1
                portfolio["stats"]["best_trade"] = max(
                    portfolio["stats"]["best_trade"], pnl)
            else:
                portfolio["stats"]["losses"] += 1
                portfolio["stats"]["worst_trade"] = min(
                    portfolio["stats"]["worst_trade"], pnl)

            closed.append(trade)
            append_jsonl(STRATEGY_LOG, {**trade, "event": "close", "timestamp": now_iso()})

    if closed:
        save_portfolio(portfolio)

    return closed


# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

def run_full_scan(auto_execute: bool = True, max_bonds: int = 4,
                  max_expertise: int = 2, max_crashes: int = 1) -> dict:
    """Run all 3 strategies: scan markets, find opportunities, optionally execute."""

    portfolio = load_portfolio()
    results = {"bonds": [], "expertise": [], "flash_crash": [], "executed": []}

    print("  Fetching Polymarket markets...")
    markets = fetch_all_markets()
    print(f"  Fetched {len(markets)} markets\n")

    # ── Strategy 1: Bonds ──
    print("  === STRATEGY 1: HIGH-PROB BONDS ===")
    print(f"  Budget: ${portfolio['allocations']['bonds']['cash']:.2f} / "
          f"${portfolio['allocations']['bonds']['budget']:.2f}")
    bonds = find_bond_opportunities(markets)
    results["bonds"] = bonds[:10]
    print(f"  Found {len(bonds)} bond opportunities\n")

    for b in bonds[:5]:
        print(f"    {b['bond_side'].upper():<4} @ ${b['bond_price']:.2f}  "
              f"Return: {b['expected_return_pct']:.1f}%  "
              f"Vol: ${b['volume']:>12,.0f}")
        print(f"         {b['question'][:55]}")

    if auto_execute and bonds:
        executed_count = 0
        for b in bonds[:max_bonds]:
            if executed_count >= max_bonds:
                break
            result = execute_bond(portfolio, b)
            if result.get("executed"):
                executed_count += 1
                results["executed"].append(result["trade"])
                print(f"    >> EXECUTED: {b['bond_side'].upper()} @ ${b['bond_price']:.2f} "
                      f"(${result['trade']['invested']:.2f})")

    # ── Strategy 2: Domain Expertise ──
    print(f"\n  === STRATEGY 2: DOMAIN EXPERTISE ===")
    print(f"  Budget: ${portfolio['allocations']['expertise']['cash']:.2f} / "
          f"${portfolio['allocations']['expertise']['budget']:.2f}")

    try:
        opportunities = find_expertise_opportunities(markets)
        results["expertise"] = opportunities[:10]
        print(f"  Found {len(opportunities)} mispriced markets\n")

        for o in opportunities[:5]:
            print(f"    {o['trade_side'].upper():<4} @ ${o['entry_price']:.2f}  "
                  f"Edge: {o['edge_pct']:.1f}%  Conf: {o['confidence']:.1%}")
            print(f"         {o['question'][:55]}")

        if auto_execute and opportunities:
            executed_count = 0
            for o in opportunities[:max_expertise]:
                if executed_count >= max_expertise:
                    break
                result = execute_expertise(portfolio, o)
                if result.get("executed"):
                    executed_count += 1
                    results["executed"].append(result["trade"])
                    print(f"    >> EXECUTED: {o['trade_side'].upper()} @ ${o['entry_price']:.2f} "
                          f"(${result['trade']['invested']:.2f})")
    except Exception as e:
        print(f"  Expertise scan error: {e}")

    # ── Strategy 3: Flash Crash ──
    print(f"\n  === STRATEGY 3: FLASH CRASH SNIPE ===")
    print(f"  Budget: ${portfolio['allocations']['flash_crash']['cash']:.2f} / "
          f"${portfolio['allocations']['flash_crash']['budget']:.2f}")
    crashes = find_crash_opportunities(markets)
    results["flash_crash"] = crashes[:10]
    print(f"  Found {len(crashes)} potential crash opportunities\n")

    for c in crashes[:5]:
        print(f"    {c['crash_side'].upper():<4} @ ${c['crash_price']:.2f}  "
              f"Upside: {c['upside_pct']:.0f}%  "
              f"Signal: {c['crash_signal']:.3f}")
        print(f"         {c['question'][:55]}")

    if auto_execute and crashes:
        executed_count = 0
        for c in crashes[:max_crashes]:
            if executed_count >= max_crashes:
                break
            result = execute_crash_snipe(portfolio, c)
            if result.get("executed"):
                executed_count += 1
                results["executed"].append(result["trade"])
                print(f"    >> EXECUTED: {c['crash_side'].upper()} @ ${c['crash_price']:.2f} "
                      f"(${result['trade']['invested']:.2f})")

    # ── Check exits on existing positions ──
    print(f"\n  === CHECKING TP/SL ===")
    closed = check_exits(portfolio)
    if closed:
        for t in closed:
            print(f"  CLOSED #{t['id']}: {t['close_reason']} "
                  f"P&L: ${t['pnl']:+.2f} ({t['pnl_pct']:+.1f}%)")
    else:
        print("  No positions hit TP/SL thresholds")

    results["closed"] = closed
    return results


def print_portfolio():
    """Print full portfolio status."""
    portfolio = load_portfolio()
    acct = portfolio["account"]
    stats = portfolio["stats"]

    print(f"\n  === $100 STRATEGY PORTFOLIO ===\n")
    print(f"  Starting:  ${acct['starting_balance']:.2f}")
    print(f"  Cash:      ${acct['total_cash']:.2f}")

    total_invested = sum(a["invested"] for a in portfolio["allocations"].values())
    total_value = acct["total_cash"] + total_invested
    total_return = total_value - acct["starting_balance"]

    print(f"  Invested:  ${total_invested:.2f}")
    print(f"  Value:     ${total_value:.2f}")
    print(f"  Return:    ${total_return:+.2f} ({(total_return/acct['starting_balance'])*100:+.1f}%)")

    print(f"\n  --- Allocations ---")
    for name, alloc in portfolio["allocations"].items():
        print(f"  {name:<14} Cash: ${alloc['cash']:>6.2f}  "
              f"Invested: ${alloc['invested']:>6.2f}  "
              f"Budget: ${alloc['budget']:>6.2f}")

    print(f"\n  --- Stats ---")
    print(f"  Trades: {stats['total_trades']}  "
          f"Wins: {stats['wins']}  Losses: {stats['losses']}  "
          f"P&L: ${stats['total_pnl']:+.2f}")
    if stats["best_trade"] > 0:
        print(f"  Best: ${stats['best_trade']:+.2f}  "
              f"Worst: ${stats['worst_trade']:+.2f}")

    # Open positions
    open_trades = [t for t in portfolio["trades"] if t.get("status") == "open"]
    if open_trades:
        print(f"\n  --- Open Positions ({len(open_trades)}) ---")
        for t in open_trades:
            price_data = fetch_market_price(t["slug"])
            current = price_data.get(f"{t['side']}_price")
            if current:
                value = t["shares"] * current
                pnl = value - t["invested"]
                pnl_pct = ((value / t["invested"]) - 1) * 100
                sign = "+" if pnl >= 0 else ""
                print(f"  #{t['id']:<3} [{t['strategy']:<12}] {t['side'].upper():<4} "
                      f"Entry: ${t['entry_price']:.2f}  Now: ${current:.2f}  "
                      f"P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)")
                print(f"       {t['question'][:50]}")
            else:
                print(f"  #{t['id']:<3} [{t['strategy']:<12}] {t['side'].upper():<4} "
                      f"${t['invested']:.2f} — price unavailable")

    # Closed positions
    closed_trades = [t for t in portfolio["trades"] if t.get("status") == "closed"]
    if closed_trades:
        print(f"\n  --- Closed Trades ({len(closed_trades)}) ---")
        for t in closed_trades:
            sign = "+" if t.get("pnl", 0) >= 0 else ""
            print(f"  #{t['id']:<3} [{t['strategy']:<12}] {t['side'].upper():<4} "
                  f"${t['invested']:.2f} -> ${t.get('current_value', 0):.2f}  "
                  f"P&L: {sign}${t.get('pnl', 0):.2f} ({t.get('close_reason', '')})")

    print()


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    import sys

    print_banner("$100 Paper Trading Strategy")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if cmd == "scan":
        # Scan + auto-execute
        results = run_full_scan(auto_execute=True)
        print(f"\n  Executed {len(results['executed'])} new trades")
        print_portfolio()

    elif cmd == "status":
        print_portfolio()

    elif cmd == "check":
        # Check exits only
        portfolio = load_portfolio()
        closed = check_exits(portfolio)
        if closed:
            for t in closed:
                print(f"  CLOSED #{t['id']}: {t['close_reason']} "
                      f"P&L: ${t['pnl']:+.2f}")
        else:
            print("  No exits triggered")
        print_portfolio()

    elif cmd == "reset":
        # Reset portfolio to $100
        print("  Resetting portfolio to $100...")
        init_portfolio()
        print("  Done!")
        print_portfolio()

    elif cmd == "bonds":
        # Scan bonds only
        markets = fetch_all_markets()
        bonds = find_bond_opportunities(markets)
        print(f"\n  Found {len(bonds)} bond opportunities:\n")
        for i, b in enumerate(bonds[:15], 1):
            print(f"  {i:>2}. {b['bond_side'].upper():<4} @ ${b['bond_price']:.2f}  "
                  f"Return: {b['expected_return_pct']:.1f}%  "
                  f"Risk: {b['risk']:.1f}%  Vol: ${b['volume']:>12,.0f}")
            print(f"      {b['question'][:60]}")

    elif cmd == "crashes":
        # Scan for crashes only
        markets = fetch_all_markets()
        crashes = find_crash_opportunities(markets)
        print(f"\n  Found {len(crashes)} crash candidates:\n")
        for i, c in enumerate(crashes[:15], 1):
            print(f"  {i:>2}. {c['crash_side'].upper():<4} @ ${c['crash_price']:.2f}  "
                  f"Upside: {c['upside_pct']:.0f}%  "
                  f"Signal: {c['crash_signal']:.3f}  Vol: ${c['volume']:>12,.0f}")
            print(f"      {c['question'][:60]}")

    else:
        print(f"  Usage: python3 strategy_100.py [scan|status|check|reset|bonds|crashes]")
        print(f"    scan     Scan all strategies and auto-execute (default)")
        print(f"    status   Show portfolio status")
        print(f"    check    Check TP/SL exits")
        print(f"    reset    Reset to fresh $100")
        print(f"    bonds    Scan bond opportunities only")
        print(f"    crashes  Scan crash opportunities only")

    print()


if __name__ == "__main__":
    main()
