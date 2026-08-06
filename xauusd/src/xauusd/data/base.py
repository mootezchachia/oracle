"""Market-data provider contract and the in-memory candle store."""

from __future__ import annotations

import abc
from collections import deque
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from ..logging_setup import get_logger
from ..models import UTC, Candle, Timeframe

log = get_logger("data")


class DataProvider(abc.ABC):
    """Async source of XAUUSD candles.

    Implementations must be safe to call concurrently for different timeframes
    and must return candles in ascending time order with the **most recent
    candle last**. Whether the final candle is still forming is implementation
    defined; :class:`MarketStore` drops unclosed bars.
    """

    name: str = "base"

    #: Typical lag between real time and the newest bar this feed serves.
    #: Broker feeds are live (0); free public feeds are delayed by design.
    #: The collector adds this to its staleness thresholds so that a delayed
    #: feed reads as "delayed", not as "broken".
    feed_delay_seconds: int = 0

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Initialise the provider. Returns ``False`` if unusable on this host."""

    @abc.abstractmethod
    async def fetch(self, timeframe: Timeframe, count: int) -> list[Candle]:
        """Return up to ``count`` candles for ``timeframe``."""

    async def latest_price(self) -> float | None:
        """Most recent traded/mid price. Defaults to the last M1 close."""
        candles = await self.fetch(Timeframe.M1, 2)
        return candles[-1].close if candles else None

    async def fetch_symbol(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        """Fetch a *correlated* instrument. Optional; default is unsupported."""
        raise NotImplementedError(f"{self.name} cannot fetch arbitrary symbols")

    @property
    def supports_symbols(self) -> bool:
        return False

    async def close(self) -> None:
        """Release any resources."""
        return None


# ---------------------------------------------------------------------------
# Candle utilities
# ---------------------------------------------------------------------------
def is_aligned(candle: Candle, timeframe: Timeframe) -> bool:
    """Does this bar start on a real timeframe boundary?

    Some feeds append a *live* bar stamped with the current quote time rather
    than the bucket start — Yahoo does this on its intraday endpoints. Such a
    bar is a partial snapshot masquerading as a closed candle, and letting one
    into the series corrupts every indicator that reads it.
    """
    return int(candle.ts.timestamp()) % timeframe.seconds == 0


def is_closed(candle: Candle, timeframe: Timeframe, now: datetime | None = None) -> bool:
    """A bar is closed once its full period has elapsed."""
    now = now or datetime.now(UTC)
    return candle.ts + timedelta(seconds=timeframe.seconds) <= now


def drop_unclosed(
    candles: Sequence[Candle], timeframe: Timeframe, now: datetime | None = None
) -> list[Candle]:
    """Strip a still-forming final bar.

    Acting on an unclosed candle is the single most common source of false
    signals in retail systems: a bar that looks like a bullish engulfing at
    minute three can close as a bearish pin at minute five. Everything the
    engine reasons about is a closed bar.

    Misaligned bars are dropped too — see :func:`is_aligned`.
    """
    return [c for c in candles if is_aligned(c, timeframe) and is_closed(c, timeframe, now)]


def aggregate(candles: Sequence[Candle], source: Timeframe, target: Timeframe) -> list[Candle]:
    """Resample candles up to a higher timeframe.

    Needed because most public feeds do not serve H4 directly; it is built from
    H1. Buckets are anchored to the UTC epoch so boundaries are stable and
    reproducible between runs and between live and backtest.
    """
    if target.seconds % source.seconds != 0:
        raise ValueError(f"{target.value} is not a whole multiple of {source.value}")
    if target.seconds == source.seconds:
        return list(candles)

    bucket_seconds = target.seconds
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        key = int(candle.ts.timestamp()) // bucket_seconds
        buckets.setdefault(key, []).append(candle)

    out: list[Candle] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda c: c.ts)
        out.append(
            Candle(
                ts=datetime.fromtimestamp(key * bucket_seconds, tz=UTC),
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
                real_volume=sum(c.real_volume for c in group),
            )
        )
    return out


def merge(existing: Sequence[Candle], incoming: Sequence[Candle]) -> list[Candle]:
    """Merge two candle series, newer data winning on timestamp collisions."""
    by_ts: dict[datetime, Candle] = {c.ts: c for c in existing}
    for candle in incoming:
        by_ts[candle.ts] = candle
    return [by_ts[ts] for ts in sorted(by_ts)]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class MarketStore:
    """Ring buffers of closed candles, one per timeframe."""

    def __init__(self, capacities: Mapping[str, int] | None = None) -> None:
        defaults = {"M1": 720, "M5": 720, "M15": 500, "H1": 400, "H4": 300}
        merged = {**defaults, **{k: int(v) for k, v in (capacities or {}).items()}}
        self._buffers: dict[Timeframe, deque[Candle]] = {
            tf: deque(maxlen=merged.get(tf.value, 500)) for tf in Timeframe
        }
        self._updated: dict[Timeframe, datetime] = {}

    def update(self, timeframe: Timeframe, candles: Iterable[Candle], now: datetime | None = None) -> int:
        """Insert closed candles; returns how many new bars were added."""
        buffer = self._buffers[timeframe]
        known = {c.ts for c in buffer}
        added = 0
        for candle in sorted(drop_unclosed(list(candles), timeframe, now), key=lambda c: c.ts):
            if candle.ts in known:
                # Replace in place — a provider may revise the last closed bar.
                for i, existing in enumerate(buffer):
                    if existing.ts == candle.ts:
                        buffer[i] = candle
                        break
                continue
            if buffer and candle.ts < buffer[-1].ts:
                continue  # out-of-order historical fill; ignore
            buffer.append(candle)
            known.add(candle.ts)
            added += 1
        if added:
            self._updated[timeframe] = now or datetime.now(UTC)
        return added

    def get(self, timeframe: Timeframe) -> list[Candle]:
        return list(self._buffers[timeframe])

    def all(self) -> dict[Timeframe, list[Candle]]:
        return {tf: list(buf) for tf, buf in self._buffers.items() if buf}

    def last(self, timeframe: Timeframe) -> Candle | None:
        buffer = self._buffers[timeframe]
        return buffer[-1] if buffer else None

    def price(self) -> float | None:
        """Best available current price: the lowest timeframe with data wins."""
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4):
            candle = self.last(timeframe)
            if candle is not None:
                return candle.close
        return None

    def updated_at(self, timeframe: Timeframe) -> datetime | None:
        return self._updated.get(timeframe)

    def is_stale(self, timeframe: Timeframe, seconds: int, now: datetime | None = None) -> bool:
        stamp = self._updated.get(timeframe)
        if stamp is None:
            return True
        return (now or datetime.now(UTC)) - stamp > timedelta(seconds=seconds)

    def ready(self, minimum: int = 60) -> bool:
        """Enough history on the gating timeframes to analyse anything."""
        return all(len(self._buffers[tf]) >= minimum for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4))

    def summary(self) -> dict[str, object]:
        return {
            tf.value: {
                "bars": len(buf),
                "last": buf[-1].ts.isoformat() if buf else None,
                "close": round(buf[-1].close, 3) if buf else None,
            }
            for tf, buf in self._buffers.items()
        }
