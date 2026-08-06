"""Smart Money Concepts / ICT primitives.

Implements the points of interest an ICT-style desk actually trades from:

* **Fair Value Gaps** — three-candle imbalances left by displacement.
* **Order Blocks** — the last opposing candle before a structure-breaking leg.
* **Breaker Blocks** — a failed order block, now flipped to the other side.
* **Mitigation Blocks** — a block price returns to in order to offload risk.
* **Displacement** — the energetic leg that validates all of the above.
* **Optimal Trade Entry** — the 62–79% retracement band of the impulse leg.
* **Power of Three** — accumulation → manipulation → distribution, read per day.

FVG and swing definitions match the conventions used by the widely-adopted
``smart-money-concepts`` package so behaviour is comparable to the TradingView
scripts most desks already have on their charts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from ..models import (
    BlockKind,
    Candle,
    DealingRange,
    Direction,
    FairValueGap,
    OrderBlock,
    StructureBreak,
)
from .structure import SwingType, alternating_swings, find_swings


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------
def find_fair_value_gaps(
    candles: Sequence[Candle],
    min_size: float = 0.0,
    max_age: int = 200,
) -> list[FairValueGap]:
    """Detect three-candle imbalances and mark those price has since filled.

    Bullish FVG: ``low[i] > high[i-2]`` — the market moved up so fast that
    candle *i* never traded into candle *i-2*'s range. Bearish is the mirror.
    """
    gaps: list[FairValueGap] = []
    n = len(candles)
    start = max(2, n - max_age)

    for i in range(start, n):
        prev2, current = candles[i - 2], candles[i]

        if current.low > prev2.high:
            size = current.low - prev2.high
            if size >= min_size:
                gaps.append(
                    FairValueGap(Direction.BUY, current.low, prev2.high, i, current.ts)
                )
        elif current.high < prev2.low:
            size = prev2.low - current.high
            if size >= min_size:
                gaps.append(
                    FairValueGap(Direction.SELL, prev2.low, current.high, i, current.ts)
                )

    return _mark_filled_gaps(candles, gaps)


def _mark_filled_gaps(candles: Sequence[Candle], gaps: Sequence[FairValueGap]) -> list[FairValueGap]:
    """Recompute the fill state of each gap from subsequent price action."""
    updated: list[FairValueGap] = []
    for gap in gaps:
        deepest = 0.0
        filled = False
        for candle in candles[gap.index + 1:]:
            if gap.direction is Direction.BUY:
                if candle.low <= gap.bottom:
                    filled, deepest = True, 1.0
                    break
                if candle.low < gap.top and gap.size > 0:
                    deepest = max(deepest, (gap.top - candle.low) / gap.size)
            else:
                if candle.high >= gap.top:
                    filled, deepest = True, 1.0
                    break
                if candle.high > gap.bottom and gap.size > 0:
                    deepest = max(deepest, (candle.high - gap.bottom) / gap.size)
        updated.append(
            FairValueGap(
                direction=gap.direction,
                top=gap.top,
                bottom=gap.bottom,
                index=gap.index,
                ts=gap.ts,
                filled=filled,
                fill_ratio=deepest,
            )
        )
    return updated


def unfilled_gaps(gaps: Sequence[FairValueGap], direction: Direction | None = None) -> list[FairValueGap]:
    result = [g for g in gaps if not g.filled]
    if direction is not None:
        result = [g for g in result if g.direction is direction]
    return result


def nearest_gap(
    gaps: Sequence[FairValueGap], price: float, direction: Direction
) -> FairValueGap | None:
    """The closest unfilled gap on the correct side of price.

    A bullish FVG is only useful as support when price sits at or above it.
    """
    candidates = [
        g
        for g in unfilled_gaps(gaps, direction)
        if (g.bottom <= price if direction is Direction.BUY else g.top >= price)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda g: abs(price - g.midpoint))


# ---------------------------------------------------------------------------
# Displacement
# ---------------------------------------------------------------------------
def displacement_score(
    candles: Sequence[Candle], index: int, atr_value: float | None, lookback: int = 3
) -> float:
    """How impulsive the leg ending at ``index`` is, in ATR units.

    Institutional repricing shows up as a run of large-bodied candles in one
    direction. Drift does not. This is the gate that keeps the engine out of
    slow, choppy tape.
    """
    if not atr_value or atr_value <= 0 or index < lookback:
        return 0.0
    window = candles[index - lookback + 1: index + 1]
    if not window:
        return 0.0
    net = window[-1].close - window[0].open
    bodies = sum(c.body for c in window)
    if bodies <= 0:
        return 0.0
    efficiency = abs(net) / bodies          # 1.0 == perfectly one-directional
    return (abs(net) / atr_value) * efficiency


def is_displacement(
    candles: Sequence[Candle], index: int, atr_value: float | None, threshold: float = 1.2
) -> bool:
    return displacement_score(candles, index, atr_value) >= threshold


# ---------------------------------------------------------------------------
# Order blocks / breakers / mitigation blocks
# ---------------------------------------------------------------------------
def find_order_blocks(
    candles: Sequence[Candle],
    breaks: Sequence[StructureBreak],
    atr_values: Sequence[float | None] | None = None,
    search_back: int = 20,
) -> list[OrderBlock]:
    """Locate the origin candle of each structure-breaking leg.

    For a bullish break, the order block is the **last down-close candle**
    before the impulse that broke the swing high — that is where the buy-side
    orders were absorbed. Mirror for bearish.
    """
    blocks: list[OrderBlock] = []
    for brk in breaks:
        lo = max(0, brk.index - search_back)
        origin: int | None = None
        for i in range(brk.index, lo - 1, -1):
            candle = candles[i]
            if brk.direction is Direction.BUY and candle.bearish:
                origin = i
                break
            if brk.direction is Direction.SELL and candle.bullish:
                origin = i
                break
        if origin is None:
            continue

        candle = candles[origin]
        atr_value = None
        if atr_values is not None and origin < len(atr_values):
            atr_value = atr_values[origin]
        blocks.append(
            OrderBlock(
                kind=BlockKind.ORDER_BLOCK,
                direction=brk.direction,
                top=candle.high,
                bottom=candle.low,
                index=origin,
                ts=candle.ts,
                displacement=displacement_score(candles, brk.index, atr_value),
                volume=candle.volume,
            )
        )
    return _mark_mitigated(candles, blocks)


def _mark_mitigated(candles: Sequence[Candle], blocks: Sequence[OrderBlock]) -> list[OrderBlock]:
    """A block is mitigated once price trades back into it after formation."""
    out: list[OrderBlock] = []
    for block in blocks:
        mitigated = any(
            candle.low <= block.top and candle.high >= block.bottom
            for candle in candles[block.index + 2:]
        )
        out.append(
            OrderBlock(
                kind=block.kind,
                direction=block.direction,
                top=block.top,
                bottom=block.bottom,
                index=block.index,
                ts=block.ts,
                displacement=block.displacement,
                mitigated=mitigated,
                volume=block.volume,
            )
        )
    return out


def find_breaker_blocks(
    candles: Sequence[Candle], blocks: Sequence[OrderBlock]
) -> list[OrderBlock]:
    """Order blocks that failed and flipped polarity.

    A bullish order block that price closes decisively *below* stops being
    support; on the retest from underneath it becomes resistance. That flipped
    zone is a breaker block, and it is one of the highest-quality short entries
    in the ICT toolkit (and vice versa).
    """
    breakers: list[OrderBlock] = []
    for block in blocks:
        after = candles[block.index + 1:]
        if not after:
            continue
        if block.direction is Direction.BUY:
            failed = any(c.close < block.bottom for c in after)
            flipped = Direction.SELL
        else:
            failed = any(c.close > block.top for c in after)
            flipped = Direction.BUY
        if failed:
            breakers.append(
                OrderBlock(
                    kind=BlockKind.BREAKER,
                    direction=flipped,
                    top=block.top,
                    bottom=block.bottom,
                    index=block.index,
                    ts=block.ts,
                    displacement=block.displacement,
                    volume=block.volume,
                )
            )
    return breakers


def find_mitigation_blocks(
    candles: Sequence[Candle], blocks: Sequence[OrderBlock]
) -> list[OrderBlock]:
    """Blocks price has returned to but not invalidated.

    Unlike a breaker (which failed), a mitigation block held: price came back,
    the desk offloaded risk at better prices, and the original direction
    resumed. These retain their original polarity.
    """
    result: list[OrderBlock] = []
    for block in blocks:
        after = candles[block.index + 2:]
        if not after:
            continue
        touched = any(c.low <= block.top and c.high >= block.bottom for c in after)
        if not touched:
            continue
        if block.direction is Direction.BUY:
            invalidated = any(c.close < block.bottom for c in after)
        else:
            invalidated = any(c.close > block.top for c in after)
        if not invalidated:
            result.append(
                OrderBlock(
                    kind=BlockKind.MITIGATION,
                    direction=block.direction,
                    top=block.top,
                    bottom=block.bottom,
                    index=block.index,
                    ts=block.ts,
                    displacement=block.displacement,
                    mitigated=True,
                    volume=block.volume,
                )
            )
    return result


def active_blocks(
    blocks: Sequence[OrderBlock], price: float, direction: Direction, tolerance: float
) -> list[OrderBlock]:
    """Blocks that price is currently interacting with, in the wanted direction."""
    return [b for b in blocks if b.direction is direction and b.contains(price, tolerance)]


def nearest_block(
    blocks: Sequence[OrderBlock], price: float, direction: Direction
) -> OrderBlock | None:
    candidates = [b for b in blocks if b.direction is direction]
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs(price - b.midpoint))


# ---------------------------------------------------------------------------
# Optimal Trade Entry
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class OTEZone:
    direction: Direction
    low: float
    high: float
    sweet_spot: float     # the 70.5% level ICT singles out

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def to_dict(self) -> dict[str, float | str]:
        return {
            "direction": self.direction.value,
            "low": round(self.low, 3),
            "high": round(self.high, 3),
            "sweet_spot": round(self.sweet_spot, 3),
        }


def optimal_trade_entry(dealing_range: DealingRange | None, direction: Direction) -> OTEZone | None:
    """The 62–79% retracement band of the current dealing range."""
    if dealing_range is None or dealing_range.size <= 0 or direction is Direction.NEUTRAL:
        return None
    low, high = dealing_range.ote(direction)
    size = dealing_range.size
    if direction is Direction.BUY:
        sweet = dealing_range.high - 0.705 * size
    else:
        sweet = dealing_range.low + 0.705 * size
    return OTEZone(direction, min(low, high), max(low, high), sweet)


# ---------------------------------------------------------------------------
# Power of Three (accumulation → manipulation → distribution)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PowerOfThree:
    phase: str                 # ACCUMULATION | MANIPULATION | DISTRIBUTION | UNKNOWN
    direction: Direction       # expected distribution direction
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"phase": self.phase, "direction": self.direction.value, "detail": self.detail}


def power_of_three(
    candles: Sequence[Candle], asian_range: tuple[float, float] | None, price: float
) -> PowerOfThree:
    """Read the daily PO3 model against the Asian accumulation range.

    The model: price consolidates in Asia (accumulation), the London open
    sweeps one side of that range to trap breakout traders (manipulation), then
    the real move runs the other way (distribution).
    """
    if not asian_range or not candles:
        return PowerOfThree("UNKNOWN", Direction.NEUTRAL, "No Asian range established yet")

    low, high = min(asian_range), max(asian_range)
    if high <= low:
        return PowerOfThree("UNKNOWN", Direction.NEUTRAL, "Degenerate Asian range")

    recent = candles[-40:]
    swept_high = any(c.high > high for c in recent)
    swept_low = any(c.low < low for c in recent)

    if swept_high and not swept_low and price < high:
        return PowerOfThree(
            "MANIPULATION", Direction.SELL, "Asian high swept and rejected — distribution lower"
        )
    if swept_low and not swept_high and price > low:
        return PowerOfThree(
            "MANIPULATION", Direction.BUY, "Asian low swept and reclaimed — distribution higher"
        )
    if swept_high and swept_low:
        return PowerOfThree("DISTRIBUTION", Direction.NEUTRAL, "Both sides taken — range expansion, no clean bias")
    if low <= price <= high:
        return PowerOfThree("ACCUMULATION", Direction.NEUTRAL, "Price still inside the Asian range")
    return PowerOfThree(
        "DISTRIBUTION",
        Direction.BUY if price > high else Direction.SELL,
        "Trading outside the Asian range without a sweep",
    )


def asian_range(candles: Sequence[Candle], start_hour_utc: int = 23, end_hour_utc: int = 6) -> tuple[float, float] | None:
    """High/low of the most recent Asian accumulation window (UTC hours)."""
    if not candles:
        return None
    latest = candles[-1].ts
    window: list[Candle] = []
    for candle in reversed(candles):
        if (latest - candle.ts) > timedelta(hours=24):
            break
        hour = candle.ts.hour
        inside = hour >= start_hour_utc or hour < end_hour_utc
        if inside:
            window.append(candle)
    if not window:
        return None
    return (min(c.low for c in window), max(c.high for c in window))


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SMCReport:
    timeframe: str
    gaps: list[FairValueGap]
    order_blocks: list[OrderBlock]
    breakers: list[OrderBlock]
    mitigations: list[OrderBlock]
    ote: OTEZone | None
    displacement: float
    po3: PowerOfThree | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "unfilled_gaps": [g.to_dict() for g in unfilled_gaps(self.gaps)][-4:],
            "order_blocks": [b.to_dict() for b in self.order_blocks[-4:]],
            "breakers": [b.to_dict() for b in self.breakers[-2:]],
            "mitigations": [b.to_dict() for b in self.mitigations[-2:]],
            "ote": self.ote.to_dict() if self.ote else None,
            "displacement": round(self.displacement, 2),
            "po3": self.po3.to_dict() if self.po3 else None,
        }


def analyse_smc(
    candles: Sequence[Candle],
    timeframe: str,
    breaks: Sequence[StructureBreak],
    dealing_range: DealingRange | None,
    atr_values: Sequence[float | None] | None = None,
    bias: Direction = Direction.NEUTRAL,
    min_gap_size: float = 0.0,
) -> SMCReport:
    """Run the full SMC toolkit over one timeframe."""
    gaps = find_fair_value_gaps(candles, min_size=min_gap_size)
    blocks = find_order_blocks(candles, breaks, atr_values)
    last_atr = atr_values[-1] if atr_values else None
    return SMCReport(
        timeframe=timeframe,
        gaps=gaps,
        order_blocks=blocks,
        breakers=find_breaker_blocks(candles, blocks),
        mitigations=find_mitigation_blocks(candles, blocks),
        ote=optimal_trade_entry(dealing_range, bias),
        displacement=displacement_score(candles, len(candles) - 1, last_atr),
    )
