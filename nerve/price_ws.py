"""ORACLE — Real-Time Price Feed
WebSocket + REST fallback for live price data from multiple exchanges.
Inspired by aulekator's multi-exchange data pipeline (Coinbase + Binance + aggregation).

Supports:
  1. Binance REST API (BTC, ETH, SOL, etc.) — klines for any timeframe
  2. Coinbase REST API (spot prices)
  3. CoinGecko free API (broader market data)
  4. Price aggregation (median across exchanges for manipulation resistance)
  5. Candle building for technical analysis integration
"""

import json
import math
import time
import requests
from datetime import datetime, timezone
from config import *

PRICE_LOG = DATA_DIR / "price_feed.jsonl"
CANDLE_CACHE = DATA_DIR / "candle_cache.json"

# Exchange endpoints (no auth needed)
BINANCE_API = "https://api.binance.com/api/v3"
COINBASE_API = "https://api.coinbase.com/v2"
COINGECKO_API = "https://api.coingecko.com/api/v3"


# ─── Price Fetchers ───────────────────────────────────────────────

def fetch_binance_price(symbol: str = "BTCUSDT") -> dict:
    """Fetch current price from Binance."""
    try:
        r = requests.get(f"{BINANCE_API}/ticker/price", params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        data = r.json()
        return {
            "exchange": "binance",
            "symbol": symbol,
            "price": float(data["price"]),
            "timestamp": now_iso(),
        }
    except Exception as e:
        return {"exchange": "binance", "symbol": symbol, "error": str(e)}


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "15m",
                         limit: int = 100) -> list:
    """Fetch OHLCV candles from Binance.

    Intervals: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d
    """
    try:
        r = requests.get(f"{BINANCE_API}/klines", params={
            "symbol": symbol, "interval": interval, "limit": limit,
        }, timeout=10)
        r.raise_for_status()

        candles = []
        for k in r.json():
            candles.append({
                "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc).isoformat(),
                "trades": int(k[8]),
            })
        return candles
    except Exception as e:
        print(f"  Binance klines error: {e}")
        return []


def fetch_coinbase_price(pair: str = "BTC-USD") -> dict:
    """Fetch current price from Coinbase."""
    try:
        r = requests.get(f"{COINBASE_API}/prices/{pair}/spot", timeout=5)
        r.raise_for_status()
        data = r.json()["data"]
        return {
            "exchange": "coinbase",
            "symbol": pair,
            "price": float(data["amount"]),
            "currency": data["currency"],
            "timestamp": now_iso(),
        }
    except Exception as e:
        return {"exchange": "coinbase", "symbol": pair, "error": str(e)}


def fetch_coingecko_price(coin_id: str = "bitcoin") -> dict:
    """Fetch price from CoinGecko (free, no API key)."""
    try:
        r = requests.get(f"{COINGECKO_API}/simple/price", params={
            "ids": coin_id, "vs_currencies": "usd",
            "include_24hr_vol": "true", "include_24hr_change": "true",
        }, timeout=5)
        r.raise_for_status()
        data = r.json().get(coin_id, {})
        return {
            "exchange": "coingecko",
            "symbol": coin_id,
            "price": data.get("usd", 0),
            "volume_24h": data.get("usd_24h_vol", 0),
            "change_24h_pct": data.get("usd_24h_change", 0),
            "timestamp": now_iso(),
        }
    except Exception as e:
        return {"exchange": "coingecko", "symbol": coin_id, "error": str(e)}


# ─── Price Aggregation ────────────────────────────────────────────

def aggregate_price(symbol: str = "BTC") -> dict:
    """Fetch from multiple exchanges and return median price.
    Inspired by Kalshi-CryptoBot's 3-exchange median aggregation.
    """
    prices = []

    # Map symbol to exchange-specific formats
    binance_sym = f"{symbol}USDT"
    coinbase_sym = f"{symbol}-USD"
    coingecko_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple"}
    coingecko_id = coingecko_map.get(symbol, symbol.lower())

    sources = []

    # Fetch from all exchanges
    b = fetch_binance_price(binance_sym)
    if "price" in b:
        prices.append(b["price"])
        sources.append(b)

    c = fetch_coinbase_price(coinbase_sym)
    if "price" in c:
        prices.append(c["price"])
        sources.append(c)

    g = fetch_coingecko_price(coingecko_id)
    if "price" in g and g["price"] > 0:
        prices.append(g["price"])
        sources.append(g)

    if not prices:
        return {"symbol": symbol, "error": "All exchanges failed", "timestamp": now_iso()}

    # Median price (manipulation resistant)
    prices.sort()
    if len(prices) % 2 == 0:
        median = (prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / 2
    else:
        median = prices[len(prices) // 2]

    # Spread between exchanges
    spread = max(prices) - min(prices)
    spread_pct = (spread / median * 100) if median > 0 else 0

    result = {
        "symbol": symbol,
        "price": round(median, 2),
        "source_count": len(prices),
        "spread": round(spread, 2),
        "spread_pct": round(spread_pct, 4),
        "prices": {s.get("exchange", "?"): s.get("price", 0) for s in sources},
        "timestamp": now_iso(),
    }

    # Log
    append_jsonl(PRICE_LOG, result)

    return result


# ─── Candle Cache (for TA module) ─────────────────────────────────

def get_candles(symbol: str = "BTC", interval: str = "15m", limit: int = 100) -> list:
    """Get OHLCV candles, using Binance as primary source.
    Results cached to disk for TA module consumption.
    """
    binance_sym = f"{symbol}USDT"
    candles = fetch_binance_klines(binance_sym, interval, limit)

    if candles:
        # Cache for offline use
        cache_key = f"{symbol}_{interval}"
        cache = {}
        if CANDLE_CACHE.exists():
            try:
                with open(CANDLE_CACHE) as f:
                    cache = json.load(f)
            except Exception:
                cache = {}

        cache[cache_key] = {
            "candles": candles,
            "updated": now_iso(),
            "count": len(candles),
        }

        with open(CANDLE_CACHE, "w") as f:
            json.dump(cache, f, indent=2)

    return candles


def get_cached_candles(symbol: str = "BTC", interval: str = "15m") -> list:
    """Read candles from cache without network call."""
    if not CANDLE_CACHE.exists():
        return []
    try:
        with open(CANDLE_CACHE) as f:
            cache = json.load(f)
        key = f"{symbol}_{interval}"
        return cache.get(key, {}).get("candles", [])
    except Exception:
        return []


# ─── Spike Detection (inspired by aulekator) ──────────────────────

def detect_price_spike(candles: list, threshold_pct: float = 1.5) -> dict:
    """Detect rapid price movements in recent candles.

    A spike occurs when price moves > threshold_pct in a single candle
    relative to the average candle range.
    """
    if len(candles) < 10:
        return {"spike": False}

    # Average candle range over last 20
    recent = candles[-20:]
    ranges = [abs(c["high"] - c["low"]) for c in recent]
    avg_range = sum(ranges) / len(ranges)

    # Check last 3 candles for spikes
    for i, c in enumerate(candles[-3:]):
        candle_range = abs(c["high"] - c["low"])
        candle_move = abs(c["close"] - c["open"])
        mid = (c["high"] + c["low"]) / 2

        if mid > 0:
            move_pct = (candle_move / mid) * 100
            range_ratio = candle_range / max(avg_range, 0.001)

            if move_pct > threshold_pct or range_ratio > 3.0:
                direction = 1.0 if c["close"] > c["open"] else -1.0
                return {
                    "spike": True,
                    "direction": direction,
                    "move_pct": round(move_pct, 3),
                    "range_ratio": round(range_ratio, 2),
                    "candle": c,
                    "candles_ago": 3 - i,
                }

    return {"spike": False}


# ─── Cross-Exchange Divergence (inspired by aulekator) ────────────

def detect_divergence(symbol: str = "BTC", threshold_pct: float = 0.1) -> dict:
    """Detect price divergence between exchanges.

    When exchanges disagree on price by > threshold, it signals
    information asymmetry or manipulation — a trading opportunity.
    """
    agg = aggregate_price(symbol)

    if "error" in agg:
        return {"divergence": False, "error": agg["error"]}

    spread_pct = agg.get("spread_pct", 0)
    if spread_pct > threshold_pct:
        prices = agg.get("prices", {})
        # Find which exchange is the outlier
        median = agg["price"]
        max_dev = 0
        outlier = ""
        for exchange, price in prices.items():
            dev = abs(price - median) / median * 100
            if dev > max_dev:
                max_dev = dev
                outlier = exchange

        return {
            "divergence": True,
            "symbol": symbol,
            "spread_pct": spread_pct,
            "outlier_exchange": outlier,
            "median_price": agg["price"],
            "prices": prices,
            "timestamp": now_iso(),
        }

    return {"divergence": False, "symbol": symbol, "spread_pct": spread_pct}


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("Real-Time Price Feed")

    symbols = ["BTC", "ETH", "SOL"]

    for sym in symbols:
        print(f"  ─── {sym} ───")
        agg = aggregate_price(sym)

        if "error" in agg:
            print(f"    Error: {agg['error']}\n")
            continue

        print(f"    Median Price: ${agg['price']:,.2f}")
        print(f"    Sources:      {agg['source_count']} exchanges")
        print(f"    Spread:       ${agg['spread']:.2f} ({agg['spread_pct']:.4f}%)")
        for ex, p in agg.get("prices", {}).items():
            print(f"      {ex:<12} ${p:,.2f}")
        print()

    # Fetch 15m candles for BTC
    print(f"  ─── BTC 15m Candles ───")
    candles = get_candles("BTC", "15m", 50)
    if candles:
        print(f"    Fetched {len(candles)} candles")
        latest = candles[-1]
        print(f"    Latest: O:{latest['open']:.2f} H:{latest['high']:.2f} "
              f"L:{latest['low']:.2f} C:{latest['close']:.2f} V:{latest['volume']:.0f}")

        # Spike detection
        spike = detect_price_spike(candles)
        if spike["spike"]:
            d = "UP" if spike["direction"] > 0 else "DOWN"
            print(f"    SPIKE DETECTED: {d} {spike['move_pct']:.2f}% "
                  f"({spike['candles_ago']} candles ago)")
        else:
            print(f"    No spike detected")
    else:
        print(f"    Failed to fetch candles")

    # Divergence check
    print(f"\n  ─── Cross-Exchange Divergence ───")
    div = detect_divergence("BTC", 0.05)
    if div.get("divergence"):
        print(f"    DIVERGENCE: {div['symbol']} spread {div['spread_pct']:.3f}%")
        print(f"    Outlier: {div['outlier_exchange']}")
    else:
        print(f"    No significant divergence (spread: {div.get('spread_pct', 0):.3f}%)")

    print()


if __name__ == "__main__":
    main()
