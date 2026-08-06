"""Candlestick and price-action pattern recognition.

Patterns are scored 0..1 rather than returned as booleans: a pin bar with a
4:1 wick-to-body ratio closing on its extreme is not the same trade as a
marginal one, and the confidence engine should be able to tell them apart.

All thresholds are expressed relative to ATR or to the candle's own geometry so
that they hold whether Gold is ranging at $2 a bar or expanding at $12.
"""

from __future__ import annotations

from typing import Sequence

from ..models import Candle, CandlePattern, Direction, PatternHit, clamp


def _atr_at(atr_values: Sequence[float | None] | None, index: int) -> float | None:
    if atr_values is None or index >= len(atr_values):
        return None
    return atr_values[index]


# ---------------------------------------------------------------------------
# Single-candle patterns
# ---------------------------------------------------------------------------
def detect_pin_bar(candle: Candle, min_wick_ratio: float = 2.0) -> PatternHit | None:
    """Long wick, small body, close pushed away from the rejected extreme."""
    if candle.range <= 0 or candle.body <= 0:
        return None
    if candle.body_ratio > 0.4:
        return None

    if candle.lower_wick >= min_wick_ratio * candle.body and candle.lower_wick > candle.upper_wick * 2:
        strength = clamp(candle.lower_wick / candle.range, 0.0, 1.0)
        return PatternHit(CandlePattern.PIN_BAR_BULL, Direction.BUY, 0, candle.ts, strength)

    if candle.upper_wick >= min_wick_ratio * candle.body and candle.upper_wick > candle.lower_wick * 2:
        strength = clamp(candle.upper_wick / candle.range, 0.0, 1.0)
        return PatternHit(CandlePattern.PIN_BAR_BEAR, Direction.SELL, 0, candle.ts, strength)
    return None


def detect_doji(candle: Candle, max_body_ratio: float = 0.08) -> PatternHit | None:
    """Indecision. Never a signal on its own — used to *reduce* conviction."""
    if candle.range <= 0:
        return None
    if candle.body_ratio <= max_body_ratio:
        return PatternHit(CandlePattern.DOJI, Direction.NEUTRAL, 0, candle.ts, 1.0 - candle.body_ratio)
    return None


def detect_momentum(candle: Candle, atr_value: float | None, min_atr: float = 1.1) -> PatternHit | None:
    """A wide-range, full-bodied candle — displacement in a single bar."""
    if not atr_value or atr_value <= 0 or candle.range <= 0:
        return None
    if candle.range < min_atr * atr_value or candle.body_ratio < 0.65:
        return None
    strength = clamp((candle.range / atr_value - min_atr) / 1.5 + candle.body_ratio * 0.5, 0.0, 1.0)
    if candle.bullish:
        return PatternHit(CandlePattern.MOMENTUM_BULL, Direction.BUY, 0, candle.ts, strength)
    return PatternHit(CandlePattern.MOMENTUM_BEAR, Direction.SELL, 0, candle.ts, strength)


def detect_rejection(candle: Candle, atr_value: float | None) -> PatternHit | None:
    """A wide bar whose wick gave back most of the move — strong rejection."""
    if not atr_value or atr_value <= 0 or candle.range < atr_value:
        return None
    if candle.lower_wick / candle.range >= 0.55:
        return PatternHit(
            CandlePattern.REJECTION_BULL, Direction.BUY, 0, candle.ts, clamp(candle.lower_wick / candle.range, 0, 1)
        )
    if candle.upper_wick / candle.range >= 0.55:
        return PatternHit(
            CandlePattern.REJECTION_BEAR, Direction.SELL, 0, candle.ts, clamp(candle.upper_wick / candle.range, 0, 1)
        )
    return None


# ---------------------------------------------------------------------------
# Two-candle patterns
# ---------------------------------------------------------------------------
def detect_engulfing(previous: Candle, current: Candle) -> PatternHit | None:
    if previous.body <= 0 or current.body <= 0:
        return None
    ratio = current.body / previous.body

    if current.bullish and previous.bearish and current.close > previous.open and current.open <= previous.close:
        return PatternHit(CandlePattern.ENGULFING_BULL, Direction.BUY, 0, current.ts, clamp(ratio / 2.0, 0.3, 1.0))
    if current.bearish and previous.bullish and current.close < previous.open and current.open >= previous.close:
        return PatternHit(CandlePattern.ENGULFING_BEAR, Direction.SELL, 0, current.ts, clamp(ratio / 2.0, 0.3, 1.0))
    return None


def detect_inside_outside(previous: Candle, current: Candle) -> PatternHit | None:
    if current.high <= previous.high and current.low >= previous.low:
        compression = 1.0 - (current.range / previous.range if previous.range else 1.0)
        return PatternHit(CandlePattern.INSIDE_BAR, Direction.NEUTRAL, 0, current.ts, clamp(compression, 0.0, 1.0))
    if current.high > previous.high and current.low < previous.low:
        expansion = (current.range / previous.range) if previous.range else 1.0
        direction = Direction.BUY if current.bullish else Direction.SELL
        return PatternHit(CandlePattern.OUTSIDE_BAR, direction, 0, current.ts, clamp(expansion / 2.5, 0.3, 1.0))
    return None


# ---------------------------------------------------------------------------
# Three-candle patterns
# ---------------------------------------------------------------------------
def detect_star(first: Candle, middle: Candle, last: Candle) -> PatternHit | None:
    """Morning / evening star — exhaustion, pause, reversal."""
    if first.range <= 0 or last.range <= 0:
        return None
    small_middle = middle.body <= first.body * 0.5 and middle.body <= last.body * 0.5
    if not small_middle:
        return None

    if first.bearish and last.bullish and last.close > (first.open + first.close) / 2:
        strength = clamp(last.body / max(first.body, 1e-9), 0.3, 1.0)
        return PatternHit(CandlePattern.MORNING_STAR, Direction.BUY, 0, last.ts, strength)
    if first.bullish and last.bearish and last.close < (first.open + first.close) / 2:
        strength = clamp(last.body / max(first.body, 1e-9), 0.3, 1.0)
        return PatternHit(CandlePattern.EVENING_STAR, Direction.SELL, 0, last.ts, strength)
    return None


# ---------------------------------------------------------------------------
# False breakout / liquidity grab (multi-bar)
# ---------------------------------------------------------------------------
def detect_false_breakout(
    candles: Sequence[Candle], lookback: int = 20, confirm_bars: int = 2
) -> PatternHit | None:
    """Price breaks a recent extreme, then closes back inside within a bar or two.

    This is the mechanical definition of a liquidity grab: breakout traders are
    filled at the extreme, then immediately trapped when price reclaims.
    """
    if len(candles) < lookback + confirm_bars + 1:
        return None

    recent = candles[-(confirm_bars + 1):]
    reference = candles[-(lookback + confirm_bars + 1): -(confirm_bars + 1)]
    if not reference:
        return None

    ref_high = max(c.high for c in reference)
    ref_low = min(c.low for c in reference)
    current = candles[-1]

    broke_high = any(c.high > ref_high for c in recent)
    if broke_high and current.close < ref_high:
        overshoot = max(c.high for c in recent) - ref_high
        strength = clamp(overshoot / max(current.range, 1e-9), 0.3, 1.0)
        return PatternHit(CandlePattern.FALSE_BREAKOUT_BEAR, Direction.SELL, len(candles) - 1, current.ts, strength)

    broke_low = any(c.low < ref_low for c in recent)
    if broke_low and current.close > ref_low:
        overshoot = ref_low - min(c.low for c in recent)
        strength = clamp(overshoot / max(current.range, 1e-9), 0.3, 1.0)
        return PatternHit(CandlePattern.FALSE_BREAKOUT_BULL, Direction.BUY, len(candles) - 1, current.ts, strength)
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def scan_patterns(
    candles: Sequence[Candle],
    atr_values: Sequence[float | None] | None = None,
    bars: int = 3,
) -> list[PatternHit]:
    """Scan the last ``bars`` closed candles for every supported pattern."""
    hits: list[PatternHit] = []
    n = len(candles)
    if n < 4:
        return hits

    for offset in range(min(bars, n - 3)):
        i = n - 1 - offset
        candle = candles[i]
        atr_value = _atr_at(atr_values, i)

        for hit in (
            detect_pin_bar(candle),
            detect_doji(candle),
            detect_momentum(candle, atr_value),
            detect_rejection(candle, atr_value),
            detect_engulfing(candles[i - 1], candle),
            detect_inside_outside(candles[i - 1], candle),
            detect_star(candles[i - 2], candles[i - 1], candle),
        ):
            if hit is not None:
                # Fade the score of older bars — recency matters for entries.
                decay = 1.0 - 0.25 * offset
                hits.append(
                    PatternHit(hit.pattern, hit.direction, i, hit.ts, clamp(hit.strength * decay, 0.0, 1.0))
                )

    false_break = detect_false_breakout(candles)
    if false_break is not None:
        hits.append(false_break)
    return hits


def net_pattern_bias(hits: Sequence[PatternHit]) -> tuple[Direction, float]:
    """Aggregate pattern hits into a single direction and 0..1 strength."""
    bull = sum(h.strength for h in hits if h.direction is Direction.BUY)
    bear = sum(h.strength for h in hits if h.direction is Direction.SELL)
    total = bull + bear
    if total <= 0:
        return Direction.NEUTRAL, 0.0
    if bull > bear:
        return Direction.BUY, clamp((bull - bear) / total, 0.0, 1.0)
    if bear > bull:
        return Direction.SELL, clamp((bear - bull) / total, 0.0, 1.0)
    return Direction.NEUTRAL, 0.0
