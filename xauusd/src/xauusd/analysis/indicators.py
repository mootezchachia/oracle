"""Technical indicators, implemented in pure Python.

Every function returns a list the same length as its input, padded with
``None`` for the warm-up period, so results can be indexed alongside the candle
series without offset bookkeeping.

Wilder's smoothing is used for RSI / ATR / ADX (matching MetaTrader and
TradingView defaults) rather than a plain SMA, because that is what the rest of
the market is looking at and therefore where the reactions happen.
"""

from __future__ import annotations

import math
from typing import Sequence

from ..models import Candle

Number = float | None


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
def sma(values: Sequence[float], period: int) -> list[Number]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Number] = [None] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> list[Number]:
    """Exponential MA seeded with an SMA, as MT5 and TradingView do."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Number] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) * alpha + prev
        out[i] = prev
    return out


def wilder_smooth(values: Sequence[float], period: int) -> list[Number]:
    """Wilder's RMA — an EMA with alpha = 1/period."""
    out: list[Number] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) / period + prev
        out[i] = prev
    return out


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------
def rsi(values: Sequence[float], period: int = 14) -> list[Number]:
    out: list[Number] = [None] * len(values)
    if len(values) <= period:
        return out
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[1: period + 1]) / period
    avg_loss = sum(losses[1: period + 1]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Number], list[Number], list[Number]]:
    """Returns ``(macd_line, signal_line, histogram)``."""
    fast_line = ema(values, fast)
    slow_line = ema(values, slow)
    macd_line: list[Number] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_line, slow_line)
    ]

    dense = [v for v in macd_line if v is not None]
    signal_dense = ema(dense, signal)
    signal_line: list[Number] = [None] * len(values)
    offset = len(values) - len(dense)
    for i, value in enumerate(signal_dense):
        signal_line[offset + i] = value

    hist: list[Number] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


# ---------------------------------------------------------------------------
# Volatility / trend strength
# ---------------------------------------------------------------------------
def true_range(candles: Sequence[Candle]) -> list[float]:
    out: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            out.append(candle.high - candle.low)
            continue
        prev_close = candles[i - 1].close
        out.append(
            max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        )
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> list[Number]:
    return wilder_smooth(true_range(candles), period)


def adx(candles: Sequence[Candle], period: int = 14) -> tuple[list[Number], list[Number], list[Number]]:
    """Returns ``(adx, plus_di, minus_di)`` using Wilder's original method."""
    n = len(candles)
    empty: list[Number] = [None] * n
    if n < period * 2 + 1:
        return empty, list(empty), list(empty)

    tr = true_range(candles)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    def _rma(seq: Sequence[float]) -> list[Number]:
        res: list[Number] = [None] * n
        prev = sum(seq[1: period + 1])
        res[period] = prev
        for i in range(period + 1, n):
            prev = prev - prev / period + seq[i]
            res[i] = prev
        return res

    tr_s, plus_s, minus_s = _rma(tr), _rma(plus_dm), _rma(minus_dm)

    plus_di: list[Number] = [None] * n
    minus_di: list[Number] = [None] * n
    dx: list[Number] = [None] * n
    for i in range(period, n):
        t, p, m = tr_s[i], plus_s[i], minus_s[i]
        if not t:
            continue
        plus_di[i] = 100.0 * (p or 0.0) / t
        minus_di[i] = 100.0 * (m or 0.0) / t
        total = (plus_di[i] or 0.0) + (minus_di[i] or 0.0)
        dx[i] = 0.0 if total == 0 else 100.0 * abs((plus_di[i] or 0) - (minus_di[i] or 0)) / total

    adx_line: list[Number] = [None] * n
    start = period * 2
    if start < n:
        seed_values = [v for v in dx[period:start] if v is not None]
        if seed_values:
            prev = sum(seed_values) / len(seed_values)
            adx_line[start - 1] = prev
            for i in range(start, n):
                if dx[i] is None:
                    continue
                prev = (prev * (period - 1) + dx[i]) / period
                adx_line[i] = prev
    return adx_line, plus_di, minus_di


def stdev(values: Sequence[float], period: int) -> list[Number]:
    out: list[Number] = [None] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(variance)
    return out


def bollinger(
    values: Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[list[Number], list[Number], list[Number]]:
    """Returns ``(upper, middle, lower)``."""
    middle = sma(values, period)
    sd = stdev(values, period)
    upper: list[Number] = []
    lower: list[Number] = []
    for m, s in zip(middle, sd):
        if m is None or s is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(m + deviations * s)
            lower.append(m - deviations * s)
    return upper, middle, lower


def bollinger_bandwidth(
    upper: Sequence[Number], middle: Sequence[Number], lower: Sequence[Number]
) -> list[Number]:
    out: list[Number] = []
    for u, m, l in zip(upper, middle, lower):
        if u is None or m is None or l is None or m == 0:
            out.append(None)
        else:
            out.append((u - l) / m * 100.0)
    return out


# ---------------------------------------------------------------------------
# Volume-weighted
# ---------------------------------------------------------------------------
def vwap(candles: Sequence[Candle], anchor_indices: Sequence[int] | None = None) -> list[Number]:
    """Anchored VWAP.

    ``anchor_indices`` marks bars where the accumulation resets — normally the
    first bar of each trading day or of each session. Without anchors this is a
    cumulative VWAP over the whole series.
    """
    anchors = set(anchor_indices or [0])
    out: list[Number] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for i, candle in enumerate(candles):
        if i in anchors:
            cum_pv = 0.0
            cum_vol = 0.0
        volume = candle.volume if candle.volume > 0 else 1.0
        cum_pv += candle.typical * volume
        cum_vol += volume
        out.append(cum_pv / cum_vol if cum_vol else None)
    return out


def daily_anchor_indices(candles: Sequence[Candle]) -> list[int]:
    """Indices where the UTC date changes — the natural VWAP reset points."""
    anchors: list[int] = []
    last_day = None
    for i, candle in enumerate(candles):
        day = candle.ts.date()
        if day != last_day:
            anchors.append(i)
            last_day = day
    return anchors


def volume_ratio(candles: Sequence[Candle], period: int = 20) -> list[Number]:
    """Current volume divided by its moving average — an institutional-interest proxy."""
    volumes = [c.volume for c in candles]
    averages = sma(volumes, period)
    out: list[Number] = []
    for volume, average in zip(volumes, averages):
        out.append(None if not average else volume / average)
    return out


def delta_proxy(candles: Sequence[Candle]) -> list[float]:
    """Signed-volume proxy for order-flow delta.

    Real delta needs bid/ask tick data that retail feeds rarely expose, so each
    bar's volume is signed by where it closed within its own range. It is a
    proxy, labelled as such, never presented as true delta.
    """
    out: list[float] = []
    for candle in candles:
        if candle.range <= 0:
            out.append(0.0)
            continue
        position = (candle.close - candle.low) / candle.range   # 0..1
        out.append(candle.volume * (2.0 * position - 1.0))
    return out


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def percentile_rank(values: Sequence[float], value: float) -> float:
    """Percentage of ``values`` below ``value`` (0..100)."""
    clean = [v for v in values if v is not None]
    if not clean:
        return 50.0
    below = sum(1 for v in clean if v < value)
    return 100.0 * below / len(clean)


def slope(values: Sequence[float]) -> float:
    """Least-squares slope per bar. Used for trend-drift comparisons."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    """Pearson correlation over the overlapping tail of two series."""
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    xs, ys = list(a[-n:]), list(b[-n:])
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def last_valid(values: Sequence[Number]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None
