"""ORACLE — 15-Minute Crypto Market Module
Purpose-built for Polymarket BTC/ETH/SOL Up/Down 15-minute prediction markets.

Inspired by:
  - aulekator's Polymarket-BTC-15-Minute-Trading-Bot (signal pipeline)
  - discountry's flash-crash strategy (probability spike detection)
  - FrondEnt's PolymarketBTC15mAssistant (live TA + prediction display)
  - dev-protocol's arbitrage bot (UP/DOWN token price monitoring)

Pipeline:
  1. Discover active 15m crypto markets on Polymarket
  2. Fetch real-time crypto prices (multi-exchange)
  3. Run technical analysis on 15m candles
  4. Detect flash crashes in market probability
  5. Compute fusion signal
  6. Output trade recommendation
"""

import json
import math
import re
import time
import requests
from datetime import datetime, timezone
from config import *

CRYPTO_LOG = DATA_DIR / "crypto_15m.jsonl"
CRYPTO_MARKETS = DATA_DIR / "crypto_15m_markets.json"

GAMMA_API = "https://gamma-api.polymarket.com"


# ─── Market Discovery ─────────────────────────────────────────────

CRYPTO_15M_KEYWORDS = [
    "bitcoin up or down",
    "btc up or down",
    "ethereum up or down",
    "eth up or down",
    "solana up or down",
    "sol up or down",
    "xrp up or down",
    "crypto up or down",
    "15 minute",
    "15-minute",
    "15min",
]


def discover_15m_markets() -> list:
    """Find active 15-minute crypto prediction markets on Polymarket."""
    found = []

    try:
        # Search for crypto-related short-term markets
        for offset in range(0, 200, 50):
            r = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "false",
                "limit": 50,
                "offset": offset,
                "order": "volume",
                "ascending": "false",
            }, timeout=10)
            r.raise_for_status()
            markets = r.json()

            if not markets:
                break

            for m in markets:
                q = (m.get("question", "") or "").lower()
                desc = (m.get("description", "") or "").lower()
                combined = q + " " + desc

                # Check if it's a 15m crypto market
                is_crypto = any(kw in combined for kw in [
                    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                    "xrp", "crypto",
                ])
                is_15m = any(kw in combined for kw in [
                    "15 minute", "15-minute", "15min", "up or down",
                ])

                if is_crypto and is_15m:
                    # Parse outcomes and prices
                    outcomes = m.get("outcomes", "")
                    if isinstance(outcomes, str):
                        try:
                            outcomes = json.loads(outcomes)
                        except Exception:
                            outcomes = outcomes.split(",")

                    prices = m.get("outcomePrices", "")
                    if isinstance(prices, str):
                        try:
                            prices = json.loads(prices)
                        except Exception:
                            prices = []

                    prices = [float(p) for p in prices] if prices else []

                    # Determine which crypto
                    crypto = "BTC"
                    if "ethereum" in combined or "eth" in combined:
                        crypto = "ETH"
                    elif "solana" in combined or "sol" in combined:
                        crypto = "SOL"
                    elif "xrp" in combined:
                        crypto = "XRP"

                    # Find UP/DOWN prices
                    up_price = None
                    down_price = None
                    for i, outcome in enumerate(outcomes):
                        o = str(outcome).strip().lower()
                        if i < len(prices):
                            if "up" in o or "yes" in o:
                                up_price = prices[i]
                            elif "down" in o or "no" in o:
                                down_price = prices[i]

                    found.append({
                        "market_id": m.get("id", ""),
                        "question": m.get("question", ""),
                        "slug": m.get("slug", ""),
                        "crypto": crypto,
                        "up_price": up_price,
                        "down_price": down_price,
                        "outcomes": [str(o).strip() for o in outcomes],
                        "prices": prices,
                        "volume": float(m.get("volume", 0) or 0),
                        "end_date": m.get("endDate", ""),
                        "url": f"https://polymarket.com/event/{m.get('slug', '')}",
                    })

    except Exception as e:
        print(f"  Market discovery error: {e}")

    # Cache results
    if found:
        with open(CRYPTO_MARKETS, "w") as f:
            json.dump({"markets": found, "updated": now_iso()}, f, indent=2)

    return found


# ─── Flash Crash Detection ────────────────────────────────────────

class FlashCrashDetector:
    """Detects rapid probability drops in UP/DOWN markets.
    Inspired by discountry's bot: buy when probability drops 30%+ in 10 seconds.
    """

    def __init__(self):
        self.price_history = {}  # market_id -> [(timestamp, up_price, down_price)]
        self.alerts = []

    def record_price(self, market_id: str, up_price: float, down_price: float):
        """Record a price snapshot."""
        now = time.time()
        if market_id not in self.price_history:
            self.price_history[market_id] = []

        self.price_history[market_id].append((now, up_price, down_price))

        # Keep only last 60 snapshots
        self.price_history[market_id] = self.price_history[market_id][-60:]

    def check_crash(self, market_id: str, drop_threshold: float = 0.20,
                    window_seconds: float = 30.0) -> dict:
        """Check if a flash crash occurred on this market.

        Args:
            drop_threshold: minimum probability drop (0.20 = 20 cents)
            window_seconds: time window to check

        Returns:
            {"crash": bool, "side": "UP"|"DOWN", "drop": float, ...}
        """
        history = self.price_history.get(market_id, [])
        if len(history) < 2:
            return {"crash": False}

        now = time.time()
        recent = [(t, up, down) for t, up, down in history
                  if now - t <= window_seconds]

        if len(recent) < 2:
            return {"crash": False}

        # Check UP side crash
        first_up = recent[0][1]
        last_up = recent[-1][1]
        if first_up and last_up and first_up > 0:
            up_drop = first_up - last_up
            if up_drop >= drop_threshold:
                self.alerts.append({
                    "market_id": market_id,
                    "side": "UP",
                    "drop": round(up_drop, 4),
                    "from_price": round(first_up, 4),
                    "to_price": round(last_up, 4),
                    "window_seconds": round(now - recent[0][0], 1),
                    "timestamp": now_iso(),
                })
                return {
                    "crash": True,
                    "side": "UP",
                    "buy_side": "UP",  # buy the crashed side
                    "drop": round(up_drop, 4),
                    "current_price": round(last_up, 4),
                }

        # Check DOWN side crash
        first_down = recent[0][2]
        last_down = recent[-1][2]
        if first_down and last_down and first_down > 0:
            down_drop = first_down - last_down
            if down_drop >= drop_threshold:
                self.alerts.append({
                    "market_id": market_id,
                    "side": "DOWN",
                    "drop": round(down_drop, 4),
                    "from_price": round(first_down, 4),
                    "to_price": round(last_down, 4),
                    "window_seconds": round(now - recent[0][0], 1),
                    "timestamp": now_iso(),
                })
                return {
                    "crash": True,
                    "side": "DOWN",
                    "buy_side": "DOWN",
                    "drop": round(down_drop, 4),
                    "current_price": round(last_down, 4),
                }

        return {"crash": False}


# ─── 15m Analysis Pipeline ────────────────────────────────────────

def analyze_15m_market(market: dict) -> dict:
    """Run full analysis pipeline on a single 15m crypto market.

    Combines:
    1. Current market state (UP/DOWN prices, volume)
    2. Real-time crypto price (multi-exchange median)
    3. Technical analysis on 15m candles
    4. Signal fusion
    """
    from price_ws import aggregate_price, get_candles, detect_price_spike

    crypto = market.get("crypto", "BTC")
    result = {
        "market_id": market.get("market_id", ""),
        "question": market.get("question", ""),
        "crypto": crypto,
        "timestamp": now_iso(),
    }

    # 1. Market state
    result["up_price"] = market.get("up_price")
    result["down_price"] = market.get("down_price")
    result["volume"] = market.get("volume", 0)
    result["url"] = market.get("url", "")

    # 2. Real-time crypto price
    price_data = aggregate_price(crypto)
    result["crypto_price"] = price_data.get("price")
    result["price_sources"] = price_data.get("source_count", 0)
    result["price_spread_pct"] = price_data.get("spread_pct", 0)

    # 3. Technical analysis on 15m candles
    candles_raw = get_candles(crypto, "15m", 100)
    ta_signals = []
    ta_snapshot = {}

    if candles_raw and len(candles_raw) >= 30:
        from technical import Candle, compute_signals, ta_snapshot as get_ta_snap
        candles = [Candle.from_dict(c) for c in candles_raw]
        ta_signals = compute_signals(candles, "15m")
        ta_snapshot = get_ta_snap(candles, "15m")
        result["ta"] = ta_snapshot

        # Spike detection
        spike = detect_price_spike(candles_raw)
        result["spike"] = spike
    else:
        result["ta"] = {"error": "Insufficient candle data"}
        result["spike"] = {"spike": False}

    # 4. Signal fusion
    from signal_fusion import Signal, run_fusion

    # Convert TA signals + market implied probability into fusion
    extra_signals = list(ta_signals)

    # Add market probability as a signal
    up_price = market.get("up_price")
    if up_price and up_price > 0:
        # Market thinks UP probability = up_price
        # If up_price > 0.5, market is bullish; < 0.5 bearish
        market_direction = (up_price - 0.5) * 2  # normalize to [-1, 1]
        extra_signals.append(Signal(
            source="market_implied",
            direction=market_direction,
            strength=abs(market_direction),
            timeframe="instant",
            metadata={"up_price": up_price},
        ))

    fusion = run_fusion(extra_signals=extra_signals, market_id=market.get("market_id", ""))
    result["fusion"] = fusion

    # 5. Final recommendation
    result["recommendation"] = fusion.get("recommendation", "HOLD")
    result["confidence"] = fusion.get("confidence", 0)
    result["edge_pct"] = fusion.get("edge_pct", 0)

    # Log
    append_jsonl(CRYPTO_LOG, result)

    return result


# ─── Full Scan ────────────────────────────────────────────────────

def scan_15m_markets() -> list:
    """Discover and analyze all active 15m crypto markets."""
    print("  Discovering 15m crypto markets...")
    markets = discover_15m_markets()
    print(f"  Found {len(markets)} active 15m markets")

    results = []
    for m in markets:
        print(f"\n  Analyzing: {m['question'][:60]}...")
        try:
            analysis = analyze_15m_market(m)
            results.append(analysis)
        except Exception as e:
            print(f"    Error: {e}")

    return results


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("15-Minute Crypto Markets")

    # Discover markets
    markets = discover_15m_markets()

    if not markets:
        print("  No active 15m crypto markets found on Polymarket.")
        print("  These markets are created periodically — check back later.")
        print()

        # Show what we would analyze with demo data
        print("  ─── Demo: BTC 15m Analysis (from price feed) ───")
        from price_ws import aggregate_price, get_candles
        price = aggregate_price("BTC")
        if "price" in price:
            print(f"  BTC Price: ${price['price']:,.2f} ({price['source_count']} sources)")
        candles = get_candles("BTC", "15m", 50)
        if candles:
            print(f"  15m Candles: {len(candles)} loaded")
            latest = candles[-1]
            change = ((latest["close"] - latest["open"]) / latest["open"]) * 100
            direction = "UP" if change > 0 else "DOWN"
            print(f"  Latest 15m: {direction} {abs(change):.3f}%")
            print(f"    O: ${latest['open']:,.2f}  H: ${latest['high']:,.2f}  "
                  f"L: ${latest['low']:,.2f}  C: ${latest['close']:,.2f}")
        print()
        return []

    print(f"\n  ═══ ACTIVE 15M CRYPTO MARKETS ═══\n")
    for m in markets:
        up = f"{m['up_price']:.2f}" if m['up_price'] else "?"
        down = f"{m['down_price']:.2f}" if m['down_price'] else "?"
        print(f"  {m['crypto']:<5} UP: {up}  DOWN: {down}  "
              f"Vol: ${m['volume']:,.0f}")
        print(f"        {m['question'][:60]}")
        print(f"        {m['url']}")
        print()

    # Full analysis
    print(f"  ─── Running Full Analysis Pipeline ───\n")
    results = []
    for m in markets[:5]:  # limit to top 5
        try:
            analysis = analyze_15m_market(m)
            results.append(analysis)

            rec = analysis.get("recommendation", "HOLD")
            conf = analysis.get("confidence", 0)
            edge = analysis.get("edge_pct", 0)
            ta = analysis.get("ta", {})

            print(f"  {m['crypto']} → {rec} (conf: {conf:.1%}, edge: {edge:.1f}%)")
            if ta and "bias" in ta:
                print(f"    TA Bias: {ta['bias']}  RSI: {ta.get('rsi_14', '?')}")
            if analysis.get("spike", {}).get("spike"):
                s = analysis["spike"]
                d = "UP" if s["direction"] > 0 else "DOWN"
                print(f"    SPIKE: {d} {s['move_pct']:.2f}%")
            print()
        except Exception as e:
            print(f"  {m['crypto']} → Error: {e}\n")

    return results


if __name__ == "__main__":
    main()
