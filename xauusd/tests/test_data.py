"""Market-data plumbing: aggregation, closed-bar handling, store and CSV."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from xauusd.data.base import MarketStore, aggregate, drop_unclosed, is_closed, merge
from xauusd.data.csv_provider import CSVProvider, load_csv
from xauusd.models import UTC, Candle, Timeframe
from xauusd.testing import trending_series


def m1(count: int, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
    out = []
    price = 4000.0
    for i in range(count):
        close = price + (1 if i % 3 else -1)
        out.append(Candle(start + timedelta(minutes=i), price, max(price, close) + 0.3,
                          min(price, close) - 0.3, close, 100.0))
        price = close
    return out


class TestAggregation:
    def test_m1_to_m5_ohlc_is_correct(self):
        candles = m1(10)
        result = aggregate(candles, Timeframe.M1, Timeframe.M5)
        assert len(result) == 2
        first = result[0]
        assert first.open == candles[0].open
        assert first.close == candles[4].close
        assert first.high == max(c.high for c in candles[:5])
        assert first.low == min(c.low for c in candles[:5])
        assert first.volume == pytest.approx(sum(c.volume for c in candles[:5]))

    def test_buckets_are_anchored_to_the_epoch(self):
        """Bucket boundaries must not depend on where the series happens to start."""
        offset_start = datetime(2026, 3, 10, 0, 3, tzinfo=UTC)
        result = aggregate(m1(20, offset_start), Timeframe.M1, Timeframe.M5)
        for candle in result:
            assert candle.ts.minute % 5 == 0

    def test_h1_to_h4(self):
        h1 = trending_series(24, timeframe=Timeframe.H1,
                             start_ts=datetime(2026, 3, 10, 0, 0, tzinfo=UTC))
        result = aggregate(h1, Timeframe.H1, Timeframe.H4)
        assert len(result) == 6
        assert all(c.ts.hour % 4 == 0 for c in result)

    def test_same_timeframe_is_a_passthrough(self):
        candles = m1(5)
        assert aggregate(candles, Timeframe.M1, Timeframe.M1) == candles

    def test_non_multiple_is_rejected(self):
        with pytest.raises(ValueError):
            aggregate(m1(10), Timeframe.M15, Timeframe.H1 if False else Timeframe.M5)


class TestClosedBars:
    def test_bar_is_closed_only_after_its_period(self):
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        candle = Candle(ts, 1, 1, 1, 1)
        assert not is_closed(candle, Timeframe.M5, ts + timedelta(minutes=3))
        assert is_closed(candle, Timeframe.M5, ts + timedelta(minutes=5))

    def test_forming_bar_is_dropped(self):
        """Acting on an unclosed candle is the classic retail false-signal source."""
        candles = m1(5)
        now = candles[-1].ts + timedelta(seconds=30)
        kept = drop_unclosed(candles, Timeframe.M1, now)
        assert len(kept) == 4
        assert kept[-1].ts == candles[-2].ts

    def test_merge_prefers_the_newer_bar(self):
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        old = [Candle(ts, 1, 1, 1, 1)]
        new = [Candle(ts, 2, 2, 2, 2)]
        assert merge(old, new)[0].close == 2


class TestMarketStore:
    def test_update_adds_only_closed_bars(self):
        store = MarketStore({"M1": 100})
        candles = m1(10)
        now = candles[-1].ts + timedelta(seconds=10)
        added = store.update(Timeframe.M1, candles, now)
        assert added == 9

    def test_ring_buffer_respects_capacity(self):
        store = MarketStore({"M1": 5})
        candles = m1(20)
        store.update(Timeframe.M1, candles, candles[-1].ts + timedelta(minutes=1))
        assert len(store.get(Timeframe.M1)) == 5

    def test_reinserting_the_same_bar_does_not_duplicate(self):
        store = MarketStore({"M1": 100})
        candles = m1(10)
        now = candles[-1].ts + timedelta(minutes=1)
        store.update(Timeframe.M1, candles, now)
        before = len(store.get(Timeframe.M1))
        store.update(Timeframe.M1, candles, now)
        assert len(store.get(Timeframe.M1)) == before

    def test_last_bar_can_be_revised_in_place(self):
        store = MarketStore({"M1": 100})
        candles = m1(10)
        now = candles[-1].ts + timedelta(minutes=1)
        store.update(Timeframe.M1, candles, now)
        revised = Candle(candles[-1].ts, 1, 2, 0, 1.5, 999)
        store.update(Timeframe.M1, [revised], now)
        assert store.last(Timeframe.M1).volume == 999
        assert len(store.get(Timeframe.M1)) == 10      # revised, not appended

    def test_price_falls_back_through_timeframes(self):
        store = MarketStore()
        assert store.price() is None
        candles = trending_series(5, timeframe=Timeframe.H1)
        store.update(Timeframe.H1, candles, candles[-1].ts + timedelta(hours=2))
        assert store.price() == pytest.approx(store.last(Timeframe.H1).close)

    def test_ready_requires_the_gating_timeframes(self):
        store = MarketStore()
        assert store.ready(minimum=10) is False


class TestCSVProvider:
    def test_loads_a_generic_csv(self, tmp_path: Path):
        path = tmp_path / "gold.csv"
        path.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2026-03-10 00:00:00,4000,4002,3999,4001,120\n"
            "2026-03-10 00:01:00,4001,4004,4000,4003,140\n",
            encoding="utf-8",
        )
        candles = load_csv(path)
        assert len(candles) == 2
        assert candles[0].close == 4001
        assert candles[0].ts.tzinfo is not None

    def test_loads_an_mt5_style_export(self, tmp_path: Path):
        path = tmp_path / "mt5.csv"
        path.write_text(
            "<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>\n"
            "2026.03.10,00:00:00,4000,4002,3999,4001,120\n"
            "2026.03.10,00:01:00,4001,4004,4000,4003,140\n",
            encoding="utf-8",
        )
        candles = load_csv(path)
        assert len(candles) == 2
        assert candles[1].volume == 140

    def test_malformed_rows_are_skipped_not_fatal(self, tmp_path: Path):
        path = tmp_path / "messy.csv"
        path.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2026-03-10 00:00:00,4000,4002,3999,4001,120\n"
            "garbage,,,,,\n"
            "2026-03-10 00:02:00,4001,4004,4000,4003,140\n",
            encoding="utf-8",
        )
        assert len(load_csv(path)) == 2

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_csv(tmp_path / "nope.csv")

    def test_cursor_prevents_lookahead(self):
        candles = m1(100)
        provider = CSVProvider(candles, Timeframe.M1)
        provider.cursor = candles[50].ts
        window = provider.window(Timeframe.M1, 100)
        assert len(window) == 51
        assert all(c.ts <= candles[50].ts for c in window)

    def test_higher_timeframes_are_aggregated_from_the_base(self):
        provider = CSVProvider(m1(300), Timeframe.M1)
        m15 = provider.window(Timeframe.M15, 100)
        assert m15
        assert all(c.ts.minute % 15 == 0 for c in m15)
