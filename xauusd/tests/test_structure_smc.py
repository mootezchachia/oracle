"""Market structure and Smart Money Concepts.

These tests use hand-built candle sequences where the correct answer is
unambiguous, because a structure detector that is *almost* right is worse than
none at all — it produces confident, wrong signals.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from xauusd.analysis.smc import (
    displacement_score,
    find_breaker_blocks,
    find_fair_value_gaps,
    find_order_blocks,
    optimal_trade_entry,
    power_of_three,
    unfilled_gaps,
)
from xauusd.analysis.structure import (
    alternating_swings,
    build_dealing_range,
    classify_trend,
    detect_structure_breaks,
    detect_sweeps,
    find_equal_levels,
    find_swings,
)
from xauusd.models import (
    UTC,
    Candle,
    DealingRange,
    Direction,
    LiquidityKind,
    LiquidityPool,
    StructureEvent,
    SwingType,
)

START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


def bars(spec: list[tuple[float, float, float, float]]) -> list[Candle]:
    """Build candles from (open, high, low, close) tuples."""
    return [
        Candle(START + timedelta(minutes=15 * i), o, h, l, c, 100.0)
        for i, (o, h, l, c) in enumerate(spec)
    ]


def ramp(count: int, start: float, step: float, spread: float = 0.5) -> list[tuple[float, float, float, float]]:
    out = []
    price = start
    for _ in range(count):
        close = price + step
        out.append((price, max(price, close) + spread, min(price, close) - spread, close))
        price = close
    return out


class TestSwings:
    def test_isolated_peak_is_a_swing_high(self):
        spec = ramp(6, 100, 1) + [(106, 112, 105, 106)] + ramp(6, 106, -1)
        swings = find_swings(bars(spec), swing_length=3)
        highs = [s for s in swings if s.kind is SwingType.HIGH]
        assert any(s.price == pytest.approx(112) for s in highs)

    def test_last_bars_cannot_be_confirmed(self):
        # A fractal needs `swing_length` bars on BOTH sides. The most recent
        # bars are structurally unconfirmable, and claiming otherwise is
        # lookahead bias.
        candles = bars(ramp(30, 100, 1))
        swings = find_swings(candles, swing_length=5)
        assert all(s.index <= len(candles) - 1 - 5 for s in swings)

    def test_alternating_collapses_runs_to_the_extreme(self):
        candles = bars(ramp(10, 100, 1) + [(110, 118, 109, 110)] + ramp(4, 110, -1)
                       + [(106, 122, 105, 106)] + ramp(8, 106, -1))
        cleaned = alternating_swings(find_swings(candles, 3))
        for a, b in zip(cleaned, cleaned[1:]):
            assert a.kind is not b.kind


class TestTrend:
    def test_rising_series_reads_bullish(self):
        candles = bars(ramp(60, 100, 1.0, spread=1.5))
        trend = classify_trend(find_swings(candles, 3))
        assert trend.direction is not Direction.SELL

    def test_falling_series_reads_bearish(self):
        candles = bars(ramp(60, 200, -1.0, spread=1.5))
        trend = classify_trend(find_swings(candles, 3))
        assert trend.direction is not Direction.BUY


class TestStructureBreaks:
    def test_close_beyond_a_swing_high_is_a_break(self):
        spec = ramp(5, 100, 1) + [(105, 115, 104, 105)] + ramp(5, 105, -1) + ramp(10, 100, 2.5)
        breaks = detect_structure_breaks(bars(spec), swing_length=3)
        bullish = [b for b in breaks if b.direction is Direction.BUY]
        assert bullish, "a close above a confirmed swing high must register"

    def test_a_wick_alone_is_not_a_break(self):
        # Price spikes above the swing high but closes back below it. That is a
        # liquidity sweep, not a break of structure — conflating the two is the
        # single most common false-signal source in retail SMC tooling.
        spec = ramp(5, 100, 1) + [(105, 120, 104, 105)] + ramp(5, 105, -1)
        spec += [(100, 119.5, 99, 101)]      # wick to 119.5, close at 101
        breaks = detect_structure_breaks(bars(spec), swing_length=3)
        assert not [b for b in breaks if b.direction is Direction.BUY and b.close_price > 119]

    def test_reversal_is_labelled_choch_not_bos(self):
        # A CHOCH only exists relative to an established bias, so the sequence
        # must first break UP (setting a bullish bias) and only then break the
        # protected low. A simple V-shape produces one BOS and no CHOCH.
        spec = (ramp(6, 100, 1)                      # 100 -> 106
                + [(106, 112, 105, 106)]             # swing high at 112
                + ramp(5, 106, -1)                   # pull back, swing low ~100
                + ramp(8, 101, 2.5)                  # close above 112 -> BOS up
                + ramp(20, 121, -2.0))               # collapse through the low
        breaks = detect_structure_breaks(bars(spec), swing_length=3)
        assert any(b.event is StructureEvent.BOS and b.direction is Direction.BUY for b in breaks)
        assert any(b.event is StructureEvent.CHOCH and b.direction is Direction.SELL for b in breaks)


class TestLiquidity:
    def test_equal_highs_within_tolerance_are_a_pool(self):
        spec = (ramp(5, 100, 1) + [(105, 110.0, 104, 105)] + ramp(5, 105, -1)
                + ramp(5, 100, 1) + [(105, 110.1, 104, 105)] + ramp(5, 105, -1))
        swings = find_swings(bars(spec), 3)
        pools = find_equal_levels(swings, tolerance=0.5, kind=SwingType.HIGH)
        assert pools, "highs 0.1 apart with a 0.5 tolerance are equal highs"

    def test_wick_through_a_pool_that_closes_back_inside_is_a_sweep(self):
        pool = LiquidityPool(LiquidityKind.EQUAL_HIGHS, 110.0, START)
        candles = bars(ramp(5, 100, 1) + [(105, 113, 104, 106)])
        sweeps = detect_sweeps(candles, [pool], lookback=10)
        assert sweeps
        assert sweeps[0].direction is Direction.SELL      # the reversal is down
        assert sweeps[0].penetration == pytest.approx(3.0)

    def test_a_close_beyond_the_pool_is_not_a_sweep(self):
        pool = LiquidityPool(LiquidityKind.EQUAL_HIGHS, 110.0, START)
        candles = bars(ramp(5, 100, 1) + [(105, 113, 104, 112)])
        assert not detect_sweeps(candles, [pool], lookback=10)


class TestFairValueGaps:
    def test_bullish_gap_detected_and_measured(self):
        spec = [
            (100, 101, 99, 100.5),
            (100.5, 106, 100, 105.5),      # displacement candle
            (105.5, 107, 102, 106.5),      # low 102 > high 101 of two bars back
        ]
        gaps = find_fair_value_gaps(bars(spec))
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.direction is Direction.BUY
        assert gap.bottom == pytest.approx(101.0)
        assert gap.top == pytest.approx(102.0)
        assert gap.midpoint == pytest.approx(101.5)

    def test_bearish_gap_detected(self):
        spec = [
            (106, 107, 105, 105.5),
            (105.5, 106, 100, 100.5),
            (100.5, 104, 99, 99.5),        # high 104 < low 105 of two bars back
        ]
        gaps = find_fair_value_gaps(bars(spec))
        assert len(gaps) == 1
        assert gaps[0].direction is Direction.SELL

    def test_gap_is_marked_filled_when_price_trades_back_through(self):
        spec = [
            (100, 101, 99, 100.5),
            (100.5, 106, 100, 105.5),
            (105.5, 107, 102, 106.5),
            (106.5, 107, 100.5, 101),      # trades back below the gap bottom
        ]
        gaps = find_fair_value_gaps(bars(spec))
        assert gaps[0].filled is True
        assert not unfilled_gaps(gaps)

    def test_no_gap_in_continuous_price(self):
        assert find_fair_value_gaps(bars(ramp(30, 100, 0.1, spread=2.0))) == []


class TestOrderBlocks:
    def test_order_block_is_the_last_opposing_candle_before_the_leg(self):
        spec = ramp(5, 100, 1) + [(105, 112, 104, 105)] + ramp(4, 105, -1)
        spec += [(101, 101.2, 99.0, 99.5)]          # the down-close origin
        spec += ramp(8, 99.5, 3.0)                  # displacement through 112
        candles = bars(spec)
        breaks = detect_structure_breaks(candles, swing_length=3)
        blocks = find_order_blocks(candles, breaks)
        bullish = [b for b in blocks if b.direction is Direction.BUY]
        assert bullish
        assert bullish[0].bottom <= 99.5 <= bullish[0].top or bullish[0].bottom <= 101 <= bullish[0].top

    def test_breaker_forms_when_a_block_fails(self):
        spec = ramp(5, 100, 1) + [(105, 112, 104, 105)] + ramp(4, 105, -1)
        spec += [(101, 101.2, 99.0, 99.5)]
        spec += ramp(8, 99.5, 3.0)
        spec += ramp(20, 123, -3.0)                 # collapse back through it
        candles = bars(spec)
        breaks = detect_structure_breaks(candles, swing_length=3)
        blocks = find_order_blocks(candles, breaks)
        breakers = find_breaker_blocks(candles, blocks)
        assert breakers
        # A failed bullish block flips to bearish.
        assert any(b.direction is Direction.SELL for b in breakers)


class TestDisplacement:
    def test_one_directional_run_scores_high(self):
        candles = bars(ramp(30, 100, 3.0, spread=0.1))
        atr = 3.0
        assert displacement_score(candles, len(candles) - 1, atr) > 1.0

    def test_choppy_drift_scores_low(self):
        spec = []
        price = 100.0
        for i in range(30):
            step = 3.0 if i % 2 == 0 else -2.9
            spec.append((price, price + 3.2, price - 3.2, price + step))
            price += step
        assert displacement_score(bars(spec), 29, 3.0) < 0.5

    def test_missing_atr_returns_zero_rather_than_dividing_by_zero(self):
        assert displacement_score(bars(ramp(10, 100, 1)), 9, None) == 0.0
        assert displacement_score(bars(ramp(10, 100, 1)), 9, 0.0) == 0.0


class TestPremiumDiscountAndOTE:
    def test_position_is_clamped_when_price_leaves_the_range(self):
        dealing = DealingRange(110.0, 100.0, START, START)
        assert dealing.position(150.0) == pytest.approx(1.0)
        assert dealing.position(50.0) == pytest.approx(0.0)

    def test_zones(self):
        dealing = DealingRange(110.0, 100.0, START, START)
        assert dealing.zone(108) == "PREMIUM"
        assert dealing.zone(102) == "DISCOUNT"
        assert dealing.zone(105) == "EQUILIBRIUM"

    def test_ote_band_is_the_62_to_79_retracement(self):
        dealing = DealingRange(110.0, 100.0, START, START)
        zone = optimal_trade_entry(dealing, Direction.BUY)
        assert zone is not None
        assert zone.low == pytest.approx(102.1)      # 110 - 0.79 * 10
        assert zone.high == pytest.approx(103.8)     # 110 - 0.62 * 10
        assert zone.sweet_spot == pytest.approx(102.95)

    def test_dealing_range_always_contains_current_price(self):
        # A range built from lagging fractals must be extended, or price ends
        # up outside its own range and every premium/discount read is garbage.
        candles = bars(ramp(40, 100, 1) + ramp(20, 140, 4))
        dealing = build_dealing_range(candles, swing_length=5)
        assert dealing is not None
        price = candles[-1].close
        assert dealing.low <= price <= dealing.high


class TestPowerOfThree:
    def test_sweep_of_the_asian_low_implies_distribution_higher(self):
        # Take the low at 94, then recover without ever tagging the range high.
        candles = bars([(100, 101, 94, 100)] + ramp(6, 100, 0.3))
        result = power_of_three(candles, (98.0, 104.0), price=102.0)
        assert result.phase == "MANIPULATION"
        assert result.direction is Direction.BUY

    def test_inside_the_range_is_accumulation(self):
        candles = bars(ramp(10, 100, 0.1))
        result = power_of_three(candles, (98.0, 104.0), price=101.0)
        assert result.phase == "ACCUMULATION"

    def test_no_range_yields_unknown(self):
        assert power_of_three(bars(ramp(5, 100, 1)), None, 100).phase == "UNKNOWN"
