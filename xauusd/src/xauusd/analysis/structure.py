"""Market structure: swings, BOS/CHOCH, liquidity, premium/discount.

The swing definition follows the convention used by the widely-adopted
``smart-money-concepts`` Python package and the corresponding TradingView
scripts: a swing high is the highest high of the ``swing_length`` bars either
side of it (and symmetrically for lows). That symmetry means the most recent
``swing_length`` bars cannot yet be confirmed — which is correct, and the
reason a fresh structure break is only validated on candle close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from ..models import (
    Candle,
    DealingRange,
    Direction,
    LiquidityKind,
    LiquidityPool,
    LiquiditySweep,
    StructureBreak,
    StructureEvent,
    SwingPoint,
    SwingType,
)


# ---------------------------------------------------------------------------
# Swings
# ---------------------------------------------------------------------------
def find_swings(candles: Sequence[Candle], swing_length: int = 5) -> list[SwingPoint]:
    """Confirmed fractal swing highs and lows, in chronological order."""
    points: list[SwingPoint] = []
    n = len(candles)
    if n < swing_length * 2 + 1:
        return points

    for i in range(swing_length, n - swing_length):
        window = candles[i - swing_length: i + swing_length + 1]
        pivot = candles[i]

        if pivot.high == max(c.high for c in window):
            # Guard against flat plateaus creating duplicate pivots.
            if not points or not (points[-1].kind is SwingType.HIGH and points[-1].price == pivot.high):
                points.append(SwingPoint(i, pivot.ts, pivot.high, SwingType.HIGH))
        if pivot.low == min(c.low for c in window):
            if not points or not (points[-1].kind is SwingType.LOW and points[-1].price == pivot.low):
                points.append(SwingPoint(i, pivot.ts, pivot.low, SwingType.LOW))
    return points


def alternating_swings(points: Sequence[SwingPoint]) -> list[SwingPoint]:
    """Collapse consecutive same-type swings, keeping the most extreme one.

    Raw fractals often produce HIGH, HIGH, LOW, LOW sequences. Structure logic
    needs a clean zig-zag, so runs are reduced to their extreme.
    """
    cleaned: list[SwingPoint] = []
    for point in points:
        if cleaned and cleaned[-1].kind is point.kind:
            previous = cleaned[-1]
            better = (
                point.price > previous.price
                if point.kind is SwingType.HIGH
                else point.price < previous.price
            )
            if better:
                cleaned[-1] = point
            continue
        cleaned.append(point)
    return cleaned


def last_swing(points: Sequence[SwingPoint], kind: SwingType) -> SwingPoint | None:
    for point in reversed(points):
        if point.kind is kind:
            return point
    return None


# ---------------------------------------------------------------------------
# Trend from swing sequence (HH/HL vs LH/LL)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TrendState:
    direction: Direction
    higher_highs: int
    higher_lows: int
    lower_highs: int
    lower_lows: int
    label: str

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "label": self.label,
            "hh": self.higher_highs,
            "hl": self.higher_lows,
            "lh": self.lower_highs,
            "ll": self.lower_lows,
        }


def classify_trend(points: Sequence[SwingPoint], lookback: int = 6) -> TrendState:
    """Read the swing sequence the way a discretionary trader would."""
    swings = alternating_swings(points)[-lookback * 2:]
    highs = [p for p in swings if p.kind is SwingType.HIGH]
    lows = [p for p in swings if p.kind is SwingType.LOW]

    hh = sum(1 for a, b in zip(highs, highs[1:]) if b.price > a.price)
    lh = sum(1 for a, b in zip(highs, highs[1:]) if b.price < a.price)
    hl = sum(1 for a, b in zip(lows, lows[1:]) if b.price > a.price)
    ll = sum(1 for a, b in zip(lows, lows[1:]) if b.price < a.price)

    bull = hh + hl
    bear = lh + ll
    if bull > bear and bull >= 2:
        direction, label = Direction.BUY, "Higher highs / higher lows"
    elif bear > bull and bear >= 2:
        direction, label = Direction.SELL, "Lower highs / lower lows"
    else:
        direction, label = Direction.NEUTRAL, "Ranging / no clean structure"
    return TrendState(direction, hh, hl, lh, ll, label)


# ---------------------------------------------------------------------------
# BOS / CHOCH
# ---------------------------------------------------------------------------
def detect_structure_breaks(
    candles: Sequence[Candle],
    swing_length: int = 5,
    atr_values: Sequence[float | None] | None = None,
) -> list[StructureBreak]:
    """Walk the series forward and record every confirmed BOS / CHOCH.

    Rules
    -----
    * A break requires a **candle close** beyond the reference swing, not just
      a wick. A wick through a level is a liquidity sweep, not a break.
    * The break is a **BOS** when it extends the prevailing structural
      direction, and a **CHOCH** when it reverses it. The first break after a
      neutral start is treated as a BOS.
    * ``displacement`` records the size of the breaking candle's range in ATR
      units, which is what separates a genuine institutional repricing from
      slow drift.
    """
    swings = alternating_swings(find_swings(candles, swing_length))
    if not swings:
        return []

    breaks: list[StructureBreak] = []
    bias = Direction.NEUTRAL

    # Track the most recent confirmed swing available *before* each bar.
    swing_by_index: dict[int, list[SwingPoint]] = {}
    for point in swings:
        confirm_index = point.index + swing_length  # when the fractal becomes known
        swing_by_index.setdefault(confirm_index, []).append(point)

    active_high: SwingPoint | None = None
    active_low: SwingPoint | None = None
    broken_highs: set[int] = set()
    broken_lows: set[int] = set()

    for i, candle in enumerate(candles):
        for point in swing_by_index.get(i, []):
            if point.kind is SwingType.HIGH:
                active_high = point
            else:
                active_low = point

        displacement = 0.0
        if atr_values is not None and i < len(atr_values) and atr_values[i]:
            displacement = candle.range / float(atr_values[i])  # type: ignore[arg-type]

        if active_high and active_high.index not in broken_highs and candle.close > active_high.price:
            event = StructureEvent.CHOCH if bias is Direction.SELL else StructureEvent.BOS
            breaks.append(
                StructureBreak(
                    event=event,
                    direction=Direction.BUY,
                    index=i,
                    ts=candle.ts,
                    broken_level=active_high.price,
                    close_price=candle.close,
                    displacement=displacement,
                )
            )
            broken_highs.add(active_high.index)
            bias = Direction.BUY

        if active_low and active_low.index not in broken_lows and candle.close < active_low.price:
            event = StructureEvent.CHOCH if bias is Direction.BUY else StructureEvent.BOS
            breaks.append(
                StructureBreak(
                    event=event,
                    direction=Direction.SELL,
                    index=i,
                    ts=candle.ts,
                    broken_level=active_low.price,
                    close_price=candle.close,
                    displacement=displacement,
                )
            )
            broken_lows.add(active_low.index)
            bias = Direction.SELL

    return breaks


def latest_break(breaks: Sequence[StructureBreak]) -> StructureBreak | None:
    return breaks[-1] if breaks else None


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------
def find_equal_levels(
    points: Sequence[SwingPoint],
    tolerance: float,
    kind: SwingType,
    max_gap: int = 60,
) -> list[LiquidityPool]:
    """Equal highs / equal lows — the classic engineered-liquidity pattern."""
    filtered = [p for p in points if p.kind is kind]
    pools: list[LiquidityPool] = []
    for i, first in enumerate(filtered):
        for second in filtered[i + 1:]:
            if second.index - first.index > max_gap:
                break
            if abs(second.price - first.price) <= tolerance:
                pools.append(
                    LiquidityPool(
                        kind=LiquidityKind.EQUAL_HIGHS if kind is SwingType.HIGH else LiquidityKind.EQUAL_LOWS,
                        price=(first.price + second.price) / 2.0,
                        ts=second.ts,
                        internal=False,
                    )
                )
                break
    return pools


def build_liquidity_map(
    candles: Sequence[Candle],
    swing_length: int = 5,
    tolerance: float = 0.5,
) -> list[LiquidityPool]:
    """All resting-liquidity levels the algorithm is aware of."""
    swings = find_swings(candles, swing_length)
    pools: list[LiquidityPool] = []
    pools.extend(find_equal_levels(swings, tolerance, SwingType.HIGH))
    pools.extend(find_equal_levels(swings, tolerance, SwingType.LOW))

    # Unswept swing extremes are liquidity too.
    for point in alternating_swings(swings)[-8:]:
        pools.append(
            LiquidityPool(
                kind=LiquidityKind.SWING_HIGH if point.kind is SwingType.HIGH else LiquidityKind.SWING_LOW,
                price=point.price,
                ts=point.ts,
                internal=True,
            )
        )

    pools.extend(previous_day_levels(candles))
    return pools


def previous_day_levels(candles: Sequence[Candle]) -> list[LiquidityPool]:
    """Previous day's high and low — the most-hunted external liquidity."""
    if not candles:
        return []
    today = candles[-1].ts.date()
    previous: list[Candle] = []
    seen_day = None
    for candle in reversed(candles):
        day = candle.ts.date()
        if day == today:
            continue
        if seen_day is None:
            seen_day = day
        if day != seen_day:
            break
        previous.append(candle)
    if not previous:
        return []
    high = max(c.high for c in previous)
    low = min(c.low for c in previous)
    ts = previous[0].ts
    return [
        LiquidityPool(LiquidityKind.PDH, high, ts, internal=False),
        LiquidityPool(LiquidityKind.PDL, low, ts, internal=False),
    ]


def detect_sweeps(
    candles: Sequence[Candle],
    pools: Sequence[LiquidityPool],
    lookback: int = 12,
    min_penetration: float = 0.0,
) -> list[LiquiditySweep]:
    """A sweep is a wick beyond a pool that closes back inside the range.

    That is the signature of a stop raid: price reaches for resting orders,
    fills institutional size against them, and immediately rejects. Trading
    *with* the raid direction is the retail trap; the edge is the reversal.
    """
    sweeps: list[LiquiditySweep] = []
    if not candles:
        return sweeps
    start = max(0, len(candles) - lookback)

    for i in range(start, len(candles)):
        candle = candles[i]
        for pool in pools:
            if pool.swept:
                continue
            if pool.side is Direction.BUY:
                penetration = candle.high - pool.price
                if penetration > min_penetration and candle.close < pool.price:
                    sweeps.append(
                        LiquiditySweep(
                            pool=pool,
                            direction=Direction.SELL,   # reversal is down
                            index=i,
                            ts=candle.ts,
                            penetration=penetration,
                        )
                    )
            else:
                penetration = pool.price - candle.low
                if penetration > min_penetration and candle.close > pool.price:
                    sweeps.append(
                        LiquiditySweep(
                            pool=pool,
                            direction=Direction.BUY,    # reversal is up
                            index=i,
                            ts=candle.ts,
                            penetration=penetration,
                        )
                    )
    return sweeps


# ---------------------------------------------------------------------------
# Dealing range / premium-discount
# ---------------------------------------------------------------------------
def build_dealing_range(
    candles: Sequence[Candle], swing_length: int = 5, fallback_bars: int = 60
) -> DealingRange | None:
    """The swing range price is currently working inside.

    Uses the most recent confirmed swing high and swing low, then **extends**
    the range with any price action since the older of the two. Fractals are
    only confirmed ``swing_length`` bars after the fact, so an un-extended
    range goes stale the moment price runs past it — and a stale range makes
    premium/discount and OTE readings meaningless (price shows up at "360% of
    range", which is not a thing). Extending keeps price inside its own range
    by construction.

    Falls back to the extremes of the last ``fallback_bars`` when structure is
    too fresh to have produced two confirmed swings.
    """
    if not candles:
        return None
    swings = alternating_swings(find_swings(candles, swing_length))
    high_point = last_swing(swings, SwingType.HIGH)
    low_point = last_swing(swings, SwingType.LOW)

    if high_point and low_point and high_point.price > low_point.price:
        tail = candles[min(high_point.index, low_point.index):]
        high = max([high_point.price] + [c.high for c in tail])
        low = min([low_point.price] + [c.low for c in tail])
        high_ts = high_point.ts if high == high_point.price else max(tail, key=lambda c: c.high).ts
        low_ts = low_point.ts if low == low_point.price else min(tail, key=lambda c: c.low).ts
        if high > low:
            return DealingRange(high, low, high_ts, low_ts)

    window = candles[-fallback_bars:]
    high = max(window, key=lambda c: c.high)
    low = min(window, key=lambda c: c.low)
    if high.high <= low.low:
        return None
    return DealingRange(high.high, low.low, high.ts, low.ts)


@dataclass(slots=True)
class StructureReport:
    """Everything the structure layer knows about one timeframe."""

    timeframe: str
    trend: TrendState
    swings: list[SwingPoint] = field(default_factory=list)
    breaks: list[StructureBreak] = field(default_factory=list)
    pools: list[LiquidityPool] = field(default_factory=list)
    sweeps: list[LiquiditySweep] = field(default_factory=list)
    dealing_range: DealingRange | None = None

    @property
    def last_break(self) -> StructureBreak | None:
        return self.breaks[-1] if self.breaks else None

    @property
    def last_sweep(self) -> LiquiditySweep | None:
        return self.sweeps[-1] if self.sweeps else None

    def zone(self, price: float) -> str:
        return self.dealing_range.zone(price) if self.dealing_range else "UNKNOWN"

    def to_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "trend": self.trend.to_dict(),
            "last_break": self.last_break.to_dict() if self.last_break else None,
            "last_sweep": self.last_sweep.to_dict() if self.last_sweep else None,
            "dealing_range": self.dealing_range.to_dict() if self.dealing_range else None,
            "pool_count": len(self.pools),
        }


def analyse_structure(
    candles: Sequence[Candle],
    timeframe: str,
    swing_length: int = 5,
    atr_values: Sequence[float | None] | None = None,
    equal_tolerance: float = 0.5,
) -> StructureReport:
    """Full structural read of one timeframe."""
    swings = find_swings(candles, swing_length)
    pools = build_liquidity_map(candles, swing_length, equal_tolerance)
    return StructureReport(
        timeframe=timeframe,
        trend=classify_trend(swings),
        swings=swings,
        breaks=detect_structure_breaks(candles, swing_length, atr_values),
        pools=pools,
        sweeps=detect_sweeps(candles, pools),
        dealing_range=build_dealing_range(candles, swing_length),
    )
