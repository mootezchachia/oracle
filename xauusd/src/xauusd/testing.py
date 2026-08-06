"""Deterministic synthetic market generation.

Used by ``python -m xauusd selftest`` and by the unit tests. The generator is
seeded, so the same call always produces the same market — a test that fails
must be a real regression, never a coin flip.

The default scenario is a clean bullish leg: an accumulation range, a sweep of
the range low (engineering sell-side liquidity), then a displacement leg up
that breaks structure and leaves a fair value gap behind. That is precisely the
pattern the engine is built to recognise, which makes it the right shape to
assert against.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .data.base import aggregate
from .models import UTC, Candle, Timeframe


def _bar(ts: datetime, open_: float, close: float, wick_up: float, wick_down: float, volume: float) -> Candle:
    high = max(open_, close) + max(wick_up, 0.0)
    low = min(open_, close) - max(wick_down, 0.0)
    return Candle(ts=ts, open=open_, high=high, low=low, close=close, volume=volume, real_volume=volume)


# How many bars of each timeframe the live system keeps in memory. The
# synthetic market is trimmed to the same shape so tests see what production
# sees — no more, no less.
DEFAULT_LIMITS: dict[Timeframe, int] = {
    Timeframe.M1: 720,
    Timeframe.M5: 720,
    Timeframe.M15: 500,
    Timeframe.H1: 400,
    Timeframe.H4: 300,
}

@dataclass(frozen=True)
class Scenario:
    """Shape of the setup at the end of a synthetic series.

    Bar counts are M1 bars; drifts and sigmas are dollars per minute. The
    profile controls how *clean* the setup is, which is what determines whether
    the engine should find it publishable.
    """

    sign: int                    # +1 long setup, -1 short setup
    accumulation: int            # range bars (the Asian session)
    sweep: int                   # stop-raid bars through the range extreme
    displacement: int            # impulsive leg that breaks structure
    distribution: int            # continuation bars
    background_drift: float      # HTF trend, dollars/minute
    noise: float                 # bar-to-bar sigma
    sweep_drift: float
    displacement_drift: float

    @property
    def setup_bars(self) -> int:
        return self.accumulation + self.sweep + self.displacement + self.distribution


SCENARIOS: dict[str, Scenario] = {
    # A realistic, messy setup — the engine should usually decline these.
    "bullish_sweep": Scenario(1, 600, 120, 300, 180, 0.004, 0.30, -0.06, 0.10),
    "bearish_sweep": Scenario(-1, 600, 120, 300, 180, 0.004, 0.30, -0.06, 0.10),
    # A textbook A+ setup: clean HTF trend, tight range, decisive sweep, hard
    # displacement. This is the shape the engine is built to publish, and the
    # test suite asserts it actually does.
    "textbook_long": Scenario(1, 600, 90, 260, 60, 0.006, 0.22, -0.10, 0.16),
    "textbook_short": Scenario(-1, 600, 90, 260, 60, 0.006, 0.22, -0.10, 0.16),
}


def synthetic_m1(
    bars: int = 62_000,
    start_price: float = 4250.0,
    end_ts: datetime | None = None,
    seed: int = 7,
    scenario: str = "bullish_sweep",
) -> list[Candle]:
    """Generate a deterministic M1 series ending at ``end_ts``.

    The series has two regions. Everything before the last ~20 hours is a
    gently trending random walk that gives the higher timeframes real
    structure. The final region is the setup itself: accumulation, a
    liquidity sweep, displacement, then distribution.
    """
    profile = SCENARIOS.get(scenario)
    if profile is None:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {sorted(SCENARIOS)}")

    rng = random.Random(seed)
    end_ts = end_ts or datetime(2026, 3, 10, 14, 30, tzinfo=UTC)
    start_ts = end_ts - timedelta(minutes=bars)

    sign = profile.sign
    setup_start = max(0, bars - profile.setup_bars)
    phase_1 = profile.accumulation
    phase_2 = phase_1 + profile.sweep
    phase_3 = phase_2 + profile.displacement

    candles: list[Candle] = []
    price = start_price
    range_anchor = price

    for i in range(bars):
        ts = start_ts + timedelta(minutes=i)
        offset = i - setup_start

        if offset < 0:
            # Background: a slow trend in the scenario's direction. The drift
            # per minute is tiny; over weeks it produces a credible HTF trend.
            step = rng.gauss(profile.background_drift * sign, profile.noise)
            volume = rng.uniform(80, 200)
            range_anchor = price
        elif offset < phase_1:
            # Accumulation: mean-reverting around the level where it started.
            step = rng.gauss((range_anchor - price) * 0.05, profile.noise)
            volume = rng.uniform(50, 130)
        elif offset < phase_2:
            # Manipulation: drive through the range extreme to take stops.
            step = rng.gauss(profile.sweep_drift * sign, profile.noise * 0.9)
            volume = rng.uniform(150, 320)
        elif offset < phase_3:
            # Displacement: the real move — one-directional and impulsive.
            step = rng.gauss(profile.displacement_drift * sign, profile.noise * 0.75)
            volume = rng.uniform(260, 620)
        else:
            # Distribution: continuation with pullbacks.
            step = rng.gauss(-0.03 * sign, profile.noise * 0.75)
            volume = rng.uniform(150, 340)

        open_ = price
        close = price + step
        wick = abs(rng.gauss(0, profile.noise * 0.7))
        candles.append(_bar(ts, open_, close, wick, wick, volume))
        price = close

    return candles


def synthetic_market(
    bars: int = 62_000,
    end_ts: datetime | None = None,
    seed: int = 7,
    scenario: str = "bullish_sweep",
    limits: dict[Timeframe, int] | None = None,
) -> dict[Timeframe, list[Candle]]:
    """A full multi-timeframe snapshot built from one M1 series."""
    m1 = synthetic_m1(bars=bars, end_ts=end_ts, seed=seed, scenario=scenario)
    caps = {**DEFAULT_LIMITS, **(limits or {})}

    series = {
        Timeframe.M1: m1,
        Timeframe.M5: aggregate(m1, Timeframe.M1, Timeframe.M5),
        Timeframe.M15: aggregate(m1, Timeframe.M1, Timeframe.M15),
        Timeframe.H1: aggregate(m1, Timeframe.M1, Timeframe.H1),
        Timeframe.H4: aggregate(m1, Timeframe.M1, Timeframe.H4),
    }
    return {tf: candles[-caps[tf]:] for tf, candles in series.items()}


def trending_series(
    bars: int = 200,
    start: float = 4000.0,
    step: float = 1.0,
    start_ts: datetime | None = None,
    timeframe: Timeframe = Timeframe.M15,
    noise: float = 0.0,
    seed: int = 3,
) -> list[Candle]:
    """A clean, monotonic trend — used to assert indicator behaviour exactly."""
    rng = random.Random(seed)
    start_ts = start_ts or datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    price = start
    for i in range(bars):
        ts = start_ts + timedelta(seconds=timeframe.seconds * i)
        jitter = rng.gauss(0, noise) if noise else 0.0
        close = price + step + jitter
        candles.append(_bar(ts, price, close, 0.2, 0.2, 100.0))
        price = close
    return candles


def flat_series(
    bars: int = 200,
    price: float = 4000.0,
    start_ts: datetime | None = None,
    timeframe: Timeframe = Timeframe.M15,
) -> list[Candle]:
    """A perfectly flat series — the degenerate case every indicator must survive."""
    start_ts = start_ts or datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            ts=start_ts + timedelta(seconds=timeframe.seconds * i),
            open=price, high=price, low=price, close=price, volume=100.0,
        )
        for i in range(bars)
    ]
