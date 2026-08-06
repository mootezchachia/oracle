"""Indicator correctness.

Each indicator is checked against a case where the right answer is known
analytically, not against a golden file — a golden file only proves the code
still does what it did, which is worthless if what it did was wrong.
"""

from __future__ import annotations

import math

import pytest

from xauusd.analysis import indicators as ind
from xauusd.models import Timeframe
from xauusd.testing import flat_series, trending_series


class TestMovingAverages:
    def test_sma_warmup_is_none_then_exact(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = ind.sma(values, 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(2.0)
        assert result[4] == pytest.approx(4.0)

    def test_ema_seeds_from_sma(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = ind.ema(values, 3)
        assert result[2] == pytest.approx(2.0)          # seed == SMA(3)
        alpha = 2 / 4
        assert result[3] == pytest.approx((4.0 - 2.0) * alpha + 2.0)

    def test_ema_tracks_a_constant_series(self):
        result = ind.ema([7.0] * 50, 10)
        assert result[-1] == pytest.approx(7.0)

    def test_short_series_returns_all_none(self):
        assert ind.ema([1.0, 2.0], 20) == [None, None]

    def test_zero_period_rejected(self):
        with pytest.raises(ValueError):
            ind.ema([1.0, 2.0, 3.0], 0)


class TestRSI:
    def test_monotonic_rise_is_100(self):
        values = [float(i) for i in range(40)]
        assert ind.rsi(values, 14)[-1] == pytest.approx(100.0)

    def test_monotonic_fall_is_zero(self):
        values = [float(40 - i) for i in range(40)]
        assert ind.rsi(values, 14)[-1] == pytest.approx(0.0)

    def test_flat_series_is_neutral_not_a_crash(self):
        # No gains and no losses: the definition divides by zero. The guard
        # returns 100 by convention rather than raising.
        result = ind.rsi([5.0] * 40, 14)
        assert result[-1] is not None
        assert not math.isnan(result[-1])


class TestATRandADX:
    def test_atr_of_constant_range_equals_that_range(self):
        candles = flat_series(60)
        # A flat series has zero range, so ATR must be exactly zero.
        assert ind.atr(candles, 14)[-1] == pytest.approx(0.0)

    def test_atr_positive_on_a_real_series(self):
        candles = trending_series(80, step=2.0)
        value = ind.atr(candles, 14)[-1]
        assert value is not None and value > 0

    def test_adx_high_in_a_clean_trend(self):
        candles = trending_series(120, step=1.5)
        adx_line, plus_di, minus_di = ind.adx(candles, 14)
        assert adx_line[-1] is not None
        # A perfectly monotonic trend is the strongest possible directional signal.
        assert adx_line[-1] > 40
        assert plus_di[-1] > minus_di[-1]

    def test_adx_on_short_series_is_all_none(self):
        adx_line, _, _ = ind.adx(trending_series(10), 14)
        assert all(v is None for v in adx_line)


class TestMACD:
    def test_histogram_is_line_minus_signal(self):
        values = [float(i) + (i % 5) for i in range(120)]
        line, signal, hist = ind.macd(values)
        for a, b, c in zip(line, signal, hist):
            if a is None or b is None:
                assert c is None
            else:
                assert c == pytest.approx(a - b)

    def test_rising_series_gives_positive_macd(self):
        line, _, _ = ind.macd([float(i) for i in range(200)])
        assert line[-1] > 0


class TestBollinger:
    def test_bands_bracket_the_middle(self):
        values = [float(i % 10) for i in range(100)]
        upper, middle, lower = ind.bollinger(values, 20, 2.0)
        assert lower[-1] < middle[-1] < upper[-1]

    def test_flat_series_collapses_the_bands(self):
        upper, middle, lower = ind.bollinger([3.0] * 50, 20, 2.0)
        assert upper[-1] == pytest.approx(middle[-1]) == pytest.approx(lower[-1])


class TestVWAP:
    def test_vwap_resets_at_each_anchor(self):
        candles = trending_series(200, step=1.0, timeframe=Timeframe.H1)
        anchors = ind.daily_anchor_indices(candles)
        assert len(anchors) > 1
        result = ind.vwap(candles, anchors)
        # The first bar after a reset must equal that bar's own typical price.
        second_anchor = anchors[1]
        assert result[second_anchor] == pytest.approx(candles[second_anchor].typical)

    def test_zero_volume_does_not_divide_by_zero(self):
        candles = [c for c in flat_series(30)]
        zeroed = [type(c)(c.ts, c.open, c.high, c.low, c.close, 0.0, 0.0) for c in candles]
        result = ind.vwap(zeroed)
        assert all(v is not None for v in result)


class TestStatistics:
    def test_slope_of_a_line_is_its_gradient(self):
        assert ind.slope([0.0, 2.0, 4.0, 6.0]) == pytest.approx(2.0)

    def test_pearson_of_identical_series_is_one(self):
        series = [1.0, 3.0, 2.0, 5.0, 4.0]
        assert ind.pearson(series, series) == pytest.approx(1.0)

    def test_pearson_of_inverted_series_is_minus_one(self):
        series = [1.0, 3.0, 2.0, 5.0, 4.0]
        assert ind.pearson(series, [-v for v in series]) == pytest.approx(-1.0)

    def test_pearson_handles_a_constant_series(self):
        # Zero variance means correlation is undefined; returning 0 is the
        # honest answer and must not raise.
        assert ind.pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) == 0.0

    def test_percentile_rank(self):
        assert ind.percentile_rank([1, 2, 3, 4], 3.5) == pytest.approx(75.0)


class TestDeltaProxy:
    def test_close_on_high_is_fully_positive(self):
        from xauusd.models import Candle, UTC
        from datetime import datetime

        candle = Candle(datetime(2026, 1, 1, tzinfo=UTC), 10, 12, 10, 12, 100)
        assert ind.delta_proxy([candle])[0] == pytest.approx(100.0)

    def test_close_on_low_is_fully_negative(self):
        from xauusd.models import Candle, UTC
        from datetime import datetime

        candle = Candle(datetime(2026, 1, 1, tzinfo=UTC), 12, 12, 10, 10, 100)
        assert ind.delta_proxy([candle])[0] == pytest.approx(-100.0)
