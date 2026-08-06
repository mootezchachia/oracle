"""Shared fixtures.

The whole suite runs offline against deterministic synthetic data — no network,
no MetaTrader terminal, no database server. A failing test is always a real
regression.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xauusd.config import Config, load_config  # noqa: E402
from xauusd.models import UTC, Timeframe  # noqa: E402
from xauusd.testing import synthetic_market  # noqa: E402

CONFIG_PATH = ROOT / "config" / "config.yaml"

# 14:30 UTC on a Tuesday: inside the London/New York overlap and the New York
# AM kill zone, which is the context the engine is calibrated for.
REFERENCE_TS = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config(CONFIG_PATH, apply_env=False)


@pytest.fixture(scope="session")
def market() -> dict[Timeframe, list]:
    """A mid-quality bullish market — the engine should usually decline it."""
    return synthetic_market(seed=7, scenario="bullish_sweep", end_ts=REFERENCE_TS)


@pytest.fixture(scope="session")
def textbook_long() -> dict[Timeframe, list]:
    """A setup clean enough that the engine should publish it."""
    return synthetic_market(seed=5, scenario="textbook_long", end_ts=REFERENCE_TS)


@pytest.fixture(scope="session")
def textbook_short() -> dict[Timeframe, list]:
    return synthetic_market(seed=13, scenario="textbook_short", end_ts=REFERENCE_TS)


@pytest.fixture
def now() -> datetime:
    return REFERENCE_TS + timedelta(minutes=1)
