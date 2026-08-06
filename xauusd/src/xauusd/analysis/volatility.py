"""Volatility regime detection.

Gold's character changes completely between regimes, and the same setup is a
different trade in each one:

``LOW``         thin tape, stops get picked off by noise — stand aside
``NORMAL``      the regime the confluence model is calibrated for
``EXPANSION``   the regime that pays — displacement legs actually run
``NEWS_SPIKE``  a single violent bar; entering here is gambling on the fill
``EXTREME``     panic tape, spreads blow out, risk models stop being valid

Regimes are derived from an ATR percentile rank rather than absolute levels, so
the classification adapts as Gold's baseline volatility drifts over the years,
with absolute rails as a final sanity check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from ..models import Candle, VolatilityRegime, clamp
from .indicators import atr, percentile_rank


@dataclass(slots=True)
class VolatilityState:
    regime: VolatilityRegime
    atr: float
    atr_percentile: float
    bar_range_atr: float          # current bar range in ATR units
    expansion_ratio: float        # fast ATR / slow ATR
    spike_ts: datetime | None = None
    detail: str = ""

    @property
    def tradeable(self) -> bool:
        return self.regime in (VolatilityRegime.NORMAL, VolatilityRegime.EXPANSION)

    def to_dict(self) -> dict[str, object]:
        return {
            "regime": self.regime.value,
            "atr": round(self.atr, 3),
            "atr_percentile": round(self.atr_percentile, 1),
            "bar_range_atr": round(self.bar_range_atr, 2),
            "expansion_ratio": round(self.expansion_ratio, 2),
            "detail": self.detail,
        }


def classify_volatility(
    candles: Sequence[Candle],
    period: int = 14,
    lookback: int = 100,
    low_percentile: float = 25.0,
    expansion_percentile: float = 75.0,
    spike_atr_ratio: float = 2.6,
    min_atr: float = 0.0,
    max_atr: float = float("inf"),
) -> VolatilityState:
    """Classify the current volatility regime for a single timeframe."""
    if len(candles) < period + 5:
        return VolatilityState(
            VolatilityRegime.NORMAL, 0.0, 50.0, 0.0, 1.0, detail="Insufficient history"
        )

    atr_series = atr(candles, period)
    current = atr_series[-1]
    if current is None or current <= 0:
        return VolatilityState(
            VolatilityRegime.NORMAL, 0.0, 50.0, 0.0, 1.0, detail="ATR unavailable"
        )

    history = [v for v in atr_series[-lookback:] if v is not None]
    pct = percentile_rank(history, current)

    fast = atr(candles[-max(period * 2, 30):], max(period // 2, 3))
    fast_value = next((v for v in reversed(fast) if v is not None), current)
    expansion_ratio = fast_value / current if current else 1.0

    last = candles[-1]
    bar_range_atr = last.range / current if current else 0.0

    # --- Order matters: spike and extreme override the percentile read ------
    if bar_range_atr >= spike_atr_ratio:
        return VolatilityState(
            VolatilityRegime.NEWS_SPIKE,
            current,
            pct,
            bar_range_atr,
            expansion_ratio,
            spike_ts=last.ts,
            detail=f"Bar range {bar_range_atr:.1f}x ATR — spike, no entries",
        )

    if current > max_atr:
        return VolatilityState(
            VolatilityRegime.EXTREME,
            current,
            pct,
            bar_range_atr,
            expansion_ratio,
            detail=f"ATR {current:.2f} above the {max_atr:.2f} ceiling — panic tape",
        )

    if current < min_atr:
        return VolatilityState(
            VolatilityRegime.LOW,
            current,
            pct,
            bar_range_atr,
            expansion_ratio,
            detail=f"ATR {current:.2f} below the {min_atr:.2f} floor — tape too thin",
        )

    if pct >= expansion_percentile:
        regime = VolatilityRegime.EXPANSION
        detail = f"ATR in the {pct:.0f}th percentile — expansion, legs can run"
    elif pct <= low_percentile:
        regime = VolatilityRegime.LOW
        detail = f"ATR in the {pct:.0f}th percentile — compression, noise dominates"
    else:
        regime = VolatilityRegime.NORMAL
        detail = f"ATR in the {pct:.0f}th percentile — normal conditions"

    return VolatilityState(regime, current, pct, bar_range_atr, expansion_ratio, detail=detail)


def recent_spike(
    candles: Sequence[Candle],
    period: int = 14,
    spike_atr_ratio: float = 2.6,
    within_minutes: int = 15,
    now: datetime | None = None,
) -> datetime | None:
    """Timestamp of the most recent volatility spike, if it is still recent.

    Used to enforce a cool-down: after a violent bar, spreads are wide and the
    next few candles are unrepresentative. Waiting is free; a bad fill is not.
    """
    if len(candles) < period + 2:
        return None
    atr_series = atr(candles, period)
    reference = now or candles[-1].ts

    for i in range(len(candles) - 1, max(len(candles) - 30, period), -1):
        value = atr_series[i]
        if not value:
            continue
        if candles[i].range / value >= spike_atr_ratio:
            age = (reference - candles[i].ts).total_seconds() / 60.0
            return candles[i].ts if age <= within_minutes else None
    return None


def volatility_confidence_multiplier(state: VolatilityState) -> float:
    """How much the regime should scale the raw confluence score."""
    return {
        VolatilityRegime.EXPANSION: 1.05,
        VolatilityRegime.NORMAL: 1.00,
        VolatilityRegime.LOW: 0.82,
        VolatilityRegime.NEWS_SPIKE: 0.0,   # hard stop — handled as a veto too
        VolatilityRegime.EXTREME: 0.0,
    }[state.regime]


def atr_settled(
    candles: Sequence[Candle], baseline_atr: float, period: int = 14, ratio: float = 1.6
) -> bool:
    """Has volatility mean-reverted to within ``ratio`` of a pre-news baseline?

    This is the post-release gate: after CPI or NFP, price is not tradeable
    until the initial two-way whipsaw has finished expressing itself.
    """
    if baseline_atr <= 0:
        return True
    series = atr(candles, period)
    current = next((v for v in reversed(series) if v is not None), None)
    if current is None:
        return True
    return current <= baseline_atr * ratio
