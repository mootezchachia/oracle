"""Core domain types for the XAUUSD Sentinel.

Everything downstream — indicators, structure detection, the confidence engine,
the notifier, the backtester — speaks in terms of the objects defined here.
They are intentionally plain dataclasses with no third-party dependencies so
that the analysis core stays portable and trivially testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------
class Timeframe(str, Enum):
    """The five timeframes the system reasons about."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"

    @property
    def minutes(self) -> int:
        return {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}[self.value]

    @property
    def seconds(self) -> int:
        return self.minutes * 60

    @property
    def rank(self) -> int:
        """Higher rank == higher timeframe. Used to order top-down analysis."""
        return {"M1": 0, "M5": 1, "M15": 2, "H1": 3, "H4": 4}[self.value]

    def __lt__(self, other: "Timeframe") -> bool:  # type: ignore[override]
        return self.rank < other.rank


ALL_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
)


# ---------------------------------------------------------------------------
# Direction / bias
# ---------------------------------------------------------------------------
class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"

    @property
    def sign(self) -> int:
        return {"BUY": 1, "SELL": -1, "NEUTRAL": 0}[self.value]

    @property
    def opposite(self) -> "Direction":
        if self is Direction.BUY:
            return Direction.SELL
        if self is Direction.SELL:
            return Direction.BUY
        return Direction.NEUTRAL

    @classmethod
    def from_sign(cls, value: float) -> "Direction":
        if value > 0:
            return cls.BUY
        if value < 0:
            return cls.SELL
        return cls.NEUTRAL


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    EXPANSION = "EXPANSION"
    NEWS_SPIKE = "NEWS_SPIKE"
    EXTREME = "EXTREME"


class NewsSeverity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}[self.value]


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Candle:
    """A single closed OHLCV bar. ``ts`` is the bar OPEN time in UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0          # tick volume when real volume is unavailable
    real_volume: float = 0.0     # exchange volume, when the broker provides it

    # -- derived geometry ---------------------------------------------------
    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_ratio(self) -> float:
        return self.body / self.range if self.range > 0 else 0.0

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class Series(list):
    """A list of :class:`Candle` with a couple of ergonomic accessors."""

    def closes(self) -> list[float]:
        return [c.close for c in self]

    def highs(self) -> list[float]:
        return [c.high for c in self]

    def lows(self) -> list[float]:
        return [c.low for c in self]

    def volumes(self) -> list[float]:
        return [c.volume for c in self]

    @property
    def last(self) -> Candle | None:
        return self[-1] if self else None


# ---------------------------------------------------------------------------
# Market structure primitives
# ---------------------------------------------------------------------------
class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    index: int
    ts: datetime
    price: float
    kind: SwingType


class StructureEvent(str, Enum):
    BOS = "BOS"        # break of structure — trend continuation
    CHOCH = "CHOCH"    # change of character — first sign of reversal


@dataclass(frozen=True, slots=True)
class StructureBreak:
    """A confirmed BOS or CHOCH."""

    event: StructureEvent
    direction: Direction
    index: int
    ts: datetime
    broken_level: float
    close_price: float
    displacement: float = 0.0     # size of the breaking leg in ATR units

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "direction": self.direction.value,
            "ts": self.ts.isoformat(),
            "level": round(self.broken_level, 3),
            "displacement_atr": round(self.displacement, 2),
        }


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """A three-candle imbalance (inefficiency) left behind by displacement."""

    direction: Direction     # BUY == bullish FVG (support), SELL == bearish
    top: float
    bottom: float
    index: int
    ts: datetime
    filled: bool = False
    fill_ratio: float = 0.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        """The consequent encroachment (CE) — 50% of the gap."""
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "top": round(self.top, 3),
            "bottom": round(self.bottom, 3),
            "ce": round(self.midpoint, 3),
            "ts": self.ts.isoformat(),
            "filled": self.filled,
        }


class BlockKind(str, Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    BREAKER = "BREAKER"
    MITIGATION = "MITIGATION"


@dataclass(frozen=True, slots=True)
class OrderBlock:
    """Last opposing candle before a displacement leg that broke structure."""

    kind: BlockKind
    direction: Direction     # the direction the block is expected to push price
    top: float
    bottom: float
    index: int
    ts: datetime
    displacement: float = 0.0
    mitigated: bool = False
    volume: float = 0.0

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float, tolerance: float = 0.0) -> bool:
        return (self.bottom - tolerance) <= price <= (self.top + tolerance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "direction": self.direction.value,
            "top": round(self.top, 3),
            "bottom": round(self.bottom, 3),
            "ts": self.ts.isoformat(),
            "mitigated": self.mitigated,
        }


class LiquidityKind(str, Enum):
    EQUAL_HIGHS = "EQUAL_HIGHS"
    EQUAL_LOWS = "EQUAL_LOWS"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    PDH = "PREVIOUS_DAY_HIGH"
    PDL = "PREVIOUS_DAY_LOW"


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    """Resting orders: equal highs/lows, session extremes, prior-day extremes."""

    kind: LiquidityKind
    price: float
    ts: datetime
    swept: bool = False
    swept_ts: datetime | None = None
    internal: bool = False   # internal (inside the dealing range) vs external

    @property
    def side(self) -> Direction:
        """Direction price travels to *take* this liquidity."""
        highs = {
            LiquidityKind.EQUAL_HIGHS,
            LiquidityKind.SWING_HIGH,
            LiquidityKind.SESSION_HIGH,
            LiquidityKind.PDH,
        }
        return Direction.BUY if self.kind in highs else Direction.SELL

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "price": round(self.price, 3),
            "swept": self.swept,
            "internal": self.internal,
        }


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A stop raid: wick through a pool that closed back inside the range."""

    pool: LiquidityPool
    direction: Direction     # direction of the *reversal* the sweep implies
    index: int
    ts: datetime
    penetration: float       # how far beyond the pool price traded
    reclaimed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool": self.pool.to_dict(),
            "direction": self.direction.value,
            "ts": self.ts.isoformat(),
            "penetration": round(self.penetration, 3),
        }


@dataclass(frozen=True, slots=True)
class DealingRange:
    """The current swing range used for premium/discount and OTE maths."""

    high: float
    low: float
    high_ts: datetime
    low_ts: datetime

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0

    def position(self, price: float) -> float:
        """0.0 at the range low, 1.0 at the range high.

        Clamped: a range that price has already left is stale, and reporting
        "170% of range" would present that staleness as if it were a reading.
        """
        if self.size <= 0:
            return 0.5
        return clamp((price - self.low) / self.size, 0.0, 1.0)

    def zone(self, price: float) -> str:
        pos = self.position(price)
        if pos > 0.5:
            return "PREMIUM"
        if pos < 0.5:
            return "DISCOUNT"
        return "EQUILIBRIUM"

    def ote(self, direction: Direction) -> tuple[float, float]:
        """Optimal Trade Entry band: the 62%–79% retracement of the last leg."""
        if direction is Direction.BUY:
            # Retracing down into discount from the high.
            return (self.high - 0.79 * self.size, self.high - 0.62 * self.size)
        return (self.low + 0.62 * self.size, self.low + 0.79 * self.size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "high": round(self.high, 3),
            "low": round(self.low, 3),
            "equilibrium": round(self.equilibrium, 3),
        }


# ---------------------------------------------------------------------------
# Price action
# ---------------------------------------------------------------------------
class CandlePattern(str, Enum):
    PIN_BAR_BULL = "PIN_BAR_BULL"
    PIN_BAR_BEAR = "PIN_BAR_BEAR"
    ENGULFING_BULL = "ENGULFING_BULL"
    ENGULFING_BEAR = "ENGULFING_BEAR"
    MORNING_STAR = "MORNING_STAR"
    EVENING_STAR = "EVENING_STAR"
    INSIDE_BAR = "INSIDE_BAR"
    OUTSIDE_BAR = "OUTSIDE_BAR"
    DOJI = "DOJI"
    REJECTION_BULL = "REJECTION_BULL"
    REJECTION_BEAR = "REJECTION_BEAR"
    MOMENTUM_BULL = "MOMENTUM_BULL"
    MOMENTUM_BEAR = "MOMENTUM_BEAR"
    FALSE_BREAKOUT_BULL = "FALSE_BREAKOUT_BULL"
    FALSE_BREAKOUT_BEAR = "FALSE_BREAKOUT_BEAR"


@dataclass(frozen=True, slots=True)
class PatternHit:
    pattern: CandlePattern
    direction: Direction
    index: int
    ts: datetime
    strength: float = 1.0    # 0..1, how textbook the formation is

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.value,
            "direction": self.direction.value,
            "strength": round(self.strength, 2),
        }


# ---------------------------------------------------------------------------
# Evidence & signals
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Evidence:
    """One discrete confirmation that feeds the confidence engine.

    ``weight`` is the maximum contribution this factor can make; ``score`` is
    how much of that weight was actually earned (0..1).
    """

    code: str
    label: str
    direction: Direction
    weight: float
    score: float = 1.0
    timeframe: Timeframe | None = None
    detail: str = ""

    @property
    def contribution(self) -> float:
        return self.weight * max(0.0, min(1.0, self.score))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "direction": self.direction.value,
            "weight": round(self.weight, 2),
            "score": round(self.score, 2),
            "contribution": round(self.contribution, 2),
            "timeframe": self.timeframe.value if self.timeframe else None,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Veto:
    """A hard block. Any veto means NO TRADE regardless of the score."""

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(slots=True)
class RiskPlan:
    entry: float
    stop_loss: float
    take_profits: list[float]
    risk_per_unit: float
    rr_targets: list[float]
    lot_size: float
    risk_amount: float
    risk_percent: float
    break_even_price: float
    trail_trigger_price: float
    trail_distance: float
    atr: float
    partial_percents: list[float] = field(default_factory=list)

    @property
    def primary_rr(self) -> float:
        """R multiple of TP1 — the first scale-out, not the whole trade."""
        return self.rr_targets[0] if self.rr_targets else 0.0

    @property
    def blended_rr(self) -> float:
        """Reward:risk of the plan as a whole, weighted by the scale-out plan.

        Judging a scaled exit by TP1 alone understates it badly: banking 50% at
        1.5R and letting the rest run to 4R is a far better trade than the 1.5
        it appears to be. This is the number the minimum-R:R gate uses.
        """
        if not self.rr_targets:
            return 0.0
        shares = self.partial_percents or [100.0 / len(self.rr_targets)] * len(self.rr_targets)
        total = sum(shares[: len(self.rr_targets)])
        if total <= 0:
            return self.primary_rr
        return sum(rr * share for rr, share in zip(self.rr_targets, shares)) / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": round(self.entry, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profits": [round(tp, 2) for tp in self.take_profits],
            "risk_per_unit": round(self.risk_per_unit, 2),
            "rr_targets": [round(r, 2) for r in self.rr_targets],
            "primary_rr": round(self.primary_rr, 2),
            "blended_rr": round(self.blended_rr, 2),
            "partial_percents": self.partial_percents,
            "lot_size": round(self.lot_size, 2),
            "risk_amount": round(self.risk_amount, 2),
            "risk_percent": round(self.risk_percent, 2),
            "break_even_price": round(self.break_even_price, 2),
            "trail_trigger_price": round(self.trail_trigger_price, 2),
            "trail_distance": round(self.trail_distance, 2),
            "atr": round(self.atr, 3),
        }


@dataclass(slots=True)
class MarketContext:
    """Everything the engine knew at the moment it made a decision.

    Persisted with each signal so that the learning layer can correlate
    outcomes with the conditions that produced them.
    """

    ts: datetime
    price: float
    session: str = "OFF"
    active_sessions: list[str] = field(default_factory=list)
    kill_zone: str | None = None
    in_overlap: bool = False
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL
    atr: dict[str, float] = field(default_factory=dict)
    trend: dict[str, str] = field(default_factory=dict)
    news_severity: NewsSeverity = NewsSeverity.NONE
    next_event: dict[str, Any] | None = None
    correlation: dict[str, Any] = field(default_factory=dict)
    dealing_range: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "price": round(self.price, 3),
            "session": self.session,
            "active_sessions": self.active_sessions,
            "kill_zone": self.kill_zone,
            "in_overlap": self.in_overlap,
            "volatility_regime": self.volatility_regime.value,
            "atr": {k: round(v, 3) for k, v in self.atr.items()},
            "trend": self.trend,
            "news_severity": self.news_severity.value,
            "next_event": self.next_event,
            "correlation": self.correlation,
            "dealing_range": self.dealing_range,
        }


class SignalOutcome(str, Enum):
    PENDING = "PENDING"
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"
    STOPPED = "STOPPED"
    BREAK_EVEN = "BREAK_EVEN"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class Signal:
    """A published, high-conviction trade idea."""

    id: str
    ts: datetime
    symbol: str
    direction: Direction
    confidence: float
    raw_score: float
    probability: float
    risk: RiskPlan
    evidence: list[Evidence]
    context: MarketContext
    notes: list[str] = field(default_factory=list)
    outcome: SignalOutcome = SignalOutcome.PENDING
    resolved_ts: datetime | None = None
    max_favourable_r: float = 0.0
    max_adverse_r: float = 0.0

    @property
    def reasons(self) -> list[str]:
        return [e.label for e in self.evidence if e.direction is self.direction]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 1),
            "raw_score": round(self.raw_score, 1),
            "probability": round(self.probability, 1),
            "risk": self.risk.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "reasons": self.reasons,
            "context": self.context.to_dict(),
            "notes": self.notes,
            "outcome": self.outcome.value,
        }


@dataclass(slots=True)
class Decision:
    """The engine's verdict for one evaluation cycle — signal or NO TRADE."""

    ts: datetime
    direction: Direction
    confidence: float
    raw_score: float
    evidence: list[Evidence]
    vetoes: list[Veto]
    context: MarketContext
    signal: Signal | None = None
    reason: str = ""

    @property
    def actionable(self) -> bool:
        return self.signal is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "direction": self.direction.value,
            "confidence": round(self.confidence, 1),
            "raw_score": round(self.raw_score, 1),
            "evidence": [e.to_dict() for e in self.evidence],
            "vetoes": [v.to_dict() for v in self.vetoes],
            "context": self.context.to_dict(),
            "signal": self.signal.to_dict() if self.signal else None,
            "reason": self.reason,
            "actionable": self.actionable,
        }


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EconomicEvent:
    title: str
    currency: str
    ts: datetime
    impact: str
    severity: NewsSeverity
    forecast: str = ""
    previous: str = ""
    actual: str = ""

    def minutes_until(self, now: datetime) -> float:
        return (self.ts - now).total_seconds() / 60.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "currency": self.currency,
            "ts": self.ts.isoformat(),
            "impact": self.impact,
            "severity": self.severity.value,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class Headline:
    title: str
    source: str
    ts: datetime
    url: str = ""
    shock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "ts": self.ts.isoformat(),
            "url": self.url,
            "shock": self.shock,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(UTC)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or math.isnan(denominator):
        return default
    return numerator / denominator


def humanize_delta(delta: timedelta) -> str:
    """'2h 14m' style formatting used across the dashboard and alerts."""
    total = int(delta.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{sign}{days}d {hours}h"
    if hours:
        return f"{sign}{hours}h {minutes}m"
    if minutes:
        return f"{sign}{minutes}m {seconds}s"
    return f"{sign}{seconds}s"
