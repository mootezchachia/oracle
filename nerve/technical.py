"""ORACLE — Technical Analysis Module
Inspired by PolymarketBTC15mAssistant (Heiken Ashi, RSI, MACD, VWAP)
and Freqtrade/FreqAI (multi-timeframe feature engineering).

Computes TA indicators from OHLCV candle data at any timeframe.
All indicators output normalized signals compatible with SignalFusionEngine.
"""

import json
import math
from collections import deque
from datetime import datetime, timezone
from config import *

TA_LOG = DATA_DIR / "technical_signals.jsonl"


# ─── OHLCV Candle ────────────────────────────────────────────────

class Candle:
    __slots__ = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, timestamp: str, o: float, h: float, l: float, c: float, v: float = 0):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            timestamp=d.get("timestamp", d.get("t", "")),
            o=float(d.get("open", d.get("o", 0))),
            h=float(d.get("high", d.get("h", 0))),
            l=float(d.get("low", d.get("l", 0))),
            c=float(d.get("close", d.get("c", 0))),
            v=float(d.get("volume", d.get("v", 0))),
        )

    def to_dict(self):
        return {
            "timestamp": self.timestamp, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume,
        }


# ─── Indicator Functions ──────────────────────────────────────────

def sma(values: list, period: int) -> list:
    """Simple Moving Average."""
    result = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        result.append(sum(window) / period)
    return result


def ema(values: list, period: int) -> list:
    """Exponential Moving Average."""
    if len(values) < period:
        return [None] * len(values)

    k = 2 / (period + 1)
    result = [None] * (period - 1)
    result.append(sum(values[:period]) / period)  # seed with SMA

    for i in range(period, len(values)):
        prev = result[-1]
        result.append(values[i] * k + prev * (1 - k))

    return result


def rsi(closes: list, period: int = 14) -> list:
    """Relative Strength Index (0-100)."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    result = [None] * period
    gains = []
    losses = []

    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))

    return result


def macd(closes: list, fast: int = 12, slow: int = 26, signal_period: int = 9) -> dict:
    """MACD with signal line and histogram."""
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    macd_line = []
    for f, s in zip(fast_ema, slow_ema):
        if f is not None and s is not None:
            macd_line.append(f - s)
        else:
            macd_line.append(None)

    # Signal line = EMA of MACD line
    valid_macd = [m for m in macd_line if m is not None]
    signal_line_vals = ema(valid_macd, signal_period) if len(valid_macd) >= signal_period else []

    # Reconstruct with Nones
    signal_line = [None] * (len(macd_line) - len(signal_line_vals)) + signal_line_vals

    histogram = []
    for m, s in zip(macd_line, signal_line):
        if m is not None and s is not None:
            histogram.append(m - s)
        else:
            histogram.append(None)

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(closes: list, period: int = 20, std_dev: float = 2.0) -> dict:
    """Bollinger Bands: middle (SMA), upper, lower."""
    middle = sma(closes, period)
    upper = []
    lower = []

    for i, m in enumerate(middle):
        if m is None:
            upper.append(None)
            lower.append(None)
        else:
            window = closes[max(0, i - period + 1):i + 1]
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            sd = math.sqrt(variance)
            upper.append(m + std_dev * sd)
            lower.append(m - std_dev * sd)

    return {"upper": upper, "middle": middle, "lower": lower}


def vwap(candles: list) -> list:
    """Volume-Weighted Average Price (cumulative within session)."""
    result = []
    cum_vol = 0.0
    cum_tp_vol = 0.0

    for c in candles:
        typical_price = (c.high + c.low + c.close) / 3
        cum_vol += c.volume
        cum_tp_vol += typical_price * c.volume
        if cum_vol > 0:
            result.append(cum_tp_vol / cum_vol)
        else:
            result.append(typical_price)

    return result


def heiken_ashi(candles: list) -> list:
    """Heiken Ashi candles — smoothed price action."""
    if not candles:
        return []

    ha = []
    for i, c in enumerate(candles):
        if i == 0:
            ha_close = (c.open + c.high + c.low + c.close) / 4
            ha_open = (c.open + c.close) / 2
        else:
            ha_close = (c.open + c.high + c.low + c.close) / 4
            ha_open = (ha[-1].open + ha[-1].close) / 2

        ha_high = max(c.high, ha_open, ha_close)
        ha_low = min(c.low, ha_open, ha_close)

        ha.append(Candle(
            timestamp=c.timestamp,
            o=round(ha_open, 6),
            h=round(ha_high, 6),
            l=round(ha_low, 6),
            c=round(ha_close, 6),
            v=c.volume,
        ))

    return ha


# ─── Signal Generation (for Signal Fusion) ────────────────────────

def compute_signals(candles: list, timeframe: str = "15m") -> list:
    """Compute all TA indicators and return fusion-compatible signals.

    Args:
        candles: list of Candle objects (oldest first)
        timeframe: label like "1m", "5m", "15m", "1h", "4h"

    Returns:
        list of (source_name, direction, strength) tuples
    """
    from signal_fusion import Signal

    if len(candles) < 30:
        return []

    closes = [c.close for c in candles]
    signals = []
    tf_map = {"1m": "instant", "5m": "instant", "15m": "short", "1h": "medium", "4h": "long"}
    sig_timeframe = tf_map.get(timeframe, "short")

    # RSI
    rsi_vals = rsi(closes, 14)
    latest_rsi = rsi_vals[-1]
    if latest_rsi is not None:
        # RSI < 30 = oversold (bullish signal), > 70 = overbought (bearish)
        if latest_rsi < 30:
            direction = (30 - latest_rsi) / 30  # 0 to 1 (more oversold = more bullish)
            strength = min(1.0, (30 - latest_rsi) / 20)
        elif latest_rsi > 70:
            direction = -(latest_rsi - 70) / 30  # -1 to 0
            strength = min(1.0, (latest_rsi - 70) / 20)
        else:
            direction = (latest_rsi - 50) / 50  # neutral zone, weak signal
            strength = 0.2
        signals.append(Signal(
            source=f"rsi_{timeframe}",
            direction=direction,
            strength=strength,
            timeframe=sig_timeframe,
            metadata={"rsi": round(latest_rsi, 2)},
        ))

    # MACD
    macd_data = macd(closes)
    hist = macd_data["histogram"]
    valid_hist = [h for h in hist if h is not None]
    if len(valid_hist) >= 2:
        latest_hist = valid_hist[-1]
        prev_hist = valid_hist[-2]
        # Histogram direction and momentum
        direction = 1.0 if latest_hist > 0 else -1.0
        # Strength based on histogram change (momentum)
        momentum = latest_hist - prev_hist
        strength = min(1.0, abs(momentum) / (abs(latest_hist) + 0.0001) * 0.5 + 0.3)
        signals.append(Signal(
            source=f"macd_{timeframe}",
            direction=direction * min(1.0, abs(latest_hist) * 100),
            strength=strength,
            timeframe=sig_timeframe,
            metadata={"histogram": round(latest_hist, 6), "momentum": round(momentum, 6)},
        ))

    # Bollinger Bands
    bb = bollinger_bands(closes)
    if bb["upper"][-1] is not None and bb["lower"][-1] is not None:
        upper = bb["upper"][-1]
        lower = bb["lower"][-1]
        middle = bb["middle"][-1]
        band_width = upper - lower

        if band_width > 0:
            # Position within bands: -1 (at lower) to +1 (at upper)
            position = (closes[-1] - middle) / (band_width / 2)
            # Near bands = stronger signal (mean reversion)
            if abs(position) > 0.8:
                direction = -position  # mean reversion: at upper -> sell, at lower -> buy
                strength = min(1.0, abs(position) - 0.5)
            else:
                direction = position * 0.3  # weak trend-following in middle
                strength = 0.2

            signals.append(Signal(
                source=f"bollinger_{timeframe}",
                direction=max(-1.0, min(1.0, direction)),
                strength=strength,
                timeframe=sig_timeframe,
                metadata={
                    "position": round(position, 3),
                    "band_width_pct": round(band_width / middle * 100, 3) if middle else 0,
                },
            ))

    # VWAP (if volume data available)
    if any(c.volume > 0 for c in candles):
        vwap_vals = vwap(candles)
        if vwap_vals:
            latest_vwap = vwap_vals[-1]
            deviation = (closes[-1] - latest_vwap) / max(latest_vwap, 0.0001)
            # Above VWAP = bullish, below = bearish
            signals.append(Signal(
                source=f"vwap_{timeframe}",
                direction=max(-1.0, min(1.0, deviation * 20)),
                strength=min(1.0, abs(deviation) * 10),
                timeframe=sig_timeframe,
                metadata={"vwap": round(latest_vwap, 6), "deviation_pct": round(deviation * 100, 3)},
            ))

    # Heiken Ashi trend
    ha_candles = heiken_ashi(candles)
    if len(ha_candles) >= 3:
        # Count consecutive green/red HA candles
        trend = 0
        for hc in reversed(ha_candles[-5:]):
            if hc.close > hc.open:
                trend += 1
            elif hc.close < hc.open:
                trend -= 1

        direction = max(-1.0, min(1.0, trend / 5))
        strength = min(1.0, abs(trend) / 3)
        signals.append(Signal(
            source=f"heiken_ashi_{timeframe}",
            direction=direction,
            strength=strength,
            timeframe=sig_timeframe,
            metadata={"trend_count": trend},
        ))

    # Log signals
    for sig in signals:
        append_jsonl(TA_LOG, sig.to_dict())

    return signals


# ─── Convenience: Snapshot Report ─────────────────────────────────

def ta_snapshot(candles: list, timeframe: str = "15m") -> dict:
    """Generate a human-readable TA snapshot from candles."""
    if len(candles) < 30:
        return {"error": "Need at least 30 candles"}

    closes = [c.close for c in candles]

    rsi_vals = rsi(closes, 14)
    macd_data = macd(closes)
    bb = bollinger_bands(closes)

    latest_rsi = rsi_vals[-1] if rsi_vals[-1] is not None else 0
    latest_macd = [h for h in macd_data["histogram"] if h is not None]
    latest_hist = latest_macd[-1] if latest_macd else 0

    # Overall bias
    bullish_count = 0
    bearish_count = 0

    if latest_rsi < 40: bullish_count += 1
    elif latest_rsi > 60: bearish_count += 1
    if latest_hist > 0: bullish_count += 1
    else: bearish_count += 1
    if closes[-1] > closes[-5]: bullish_count += 1
    else: bearish_count += 1

    if bullish_count > bearish_count:
        bias = "BULLISH"
    elif bearish_count > bullish_count:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "timeframe": timeframe,
        "price": closes[-1],
        "rsi_14": round(latest_rsi, 2) if latest_rsi else None,
        "macd_histogram": round(latest_hist, 6) if latest_hist else None,
        "bb_upper": round(bb["upper"][-1], 6) if bb["upper"][-1] else None,
        "bb_lower": round(bb["lower"][-1], 6) if bb["lower"][-1] else None,
        "sma_20": round(bb["middle"][-1], 6) if bb["middle"][-1] else None,
        "bias": bias,
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
    }


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("Technical Analysis Module")

    # Demo with synthetic candles
    import random
    random.seed(42)
    price = 0.55
    demo_candles = []
    for i in range(100):
        change = random.gauss(0, 0.005)
        o = price
        c = price + change
        h = max(o, c) + abs(random.gauss(0, 0.002))
        l = min(o, c) - abs(random.gauss(0, 0.002))
        v = random.uniform(10000, 100000)
        demo_candles.append(Candle(
            timestamp=f"2026-03-21T{i:02d}:00:00Z",
            o=round(o, 6), h=round(h, 6), l=round(l, 6), c=round(c, 6), v=round(v, 2),
        ))
        price = c

    # Snapshot
    snap = ta_snapshot(demo_candles, "15m")
    print(f"  ═══ TA SNAPSHOT (15m demo) ═══")
    print(f"  Price:     {snap['price']:.6f}")
    print(f"  RSI(14):   {snap['rsi_14']}")
    print(f"  MACD Hist: {snap['macd_histogram']}")
    print(f"  BB Upper:  {snap['bb_upper']}")
    print(f"  BB Lower:  {snap['bb_lower']}")
    print(f"  SMA(20):   {snap['sma_20']}")
    print(f"  Bias:      {snap['bias']} ({snap['bullish_signals']}B / {snap['bearish_signals']}R)")

    # Signals for fusion
    signals = compute_signals(demo_candles, "15m")
    print(f"\n  Generated {len(signals)} fusion signals:")
    for sig in signals:
        arrow = "↑" if sig.direction > 0 else "↓" if sig.direction < 0 else "→"
        print(f"    {arrow} {sig.source:<20} dir:{sig.direction:+.3f}  str:{sig.strength:.3f}")

    print()


if __name__ == "__main__":
    main()
