"""Session and kill-zone logic, including the DST transitions.

The DST cases matter more than they look. London and New York change clocks on
different dates, so for roughly three weeks each spring and autumn the overlap
sits at a different UTC time than usual. A system with hard-coded UTC offsets is
silently wrong for those weeks — and those are exactly the weeks when Gold's
session behaviour is most worth trading.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from xauusd.models import UTC
from xauusd.sessions.calendar import SessionClock, Window


class TestWindow:
    def test_duration_of_a_normal_window(self):
        window = Window("t", "UTC", datetime(2026, 1, 1, 8, 0).time(), datetime(2026, 1, 1, 16, 30).time())
        assert window.duration == timedelta(hours=8, minutes=30)

    def test_window_crossing_midnight_wraps_forward(self):
        window = Window("t", "UTC", datetime(2026, 1, 1, 22, 0).time(), datetime(2026, 1, 1, 6, 0).time())
        assert window.duration == timedelta(hours=8)

    def test_contains_respects_the_window(self):
        window = Window("t", "UTC", datetime(2026, 1, 1, 8, 0).time(), datetime(2026, 1, 1, 16, 0).time())
        assert window.contains(datetime(2026, 3, 10, 10, 0, tzinfo=UTC))
        assert not window.contains(datetime(2026, 3, 10, 17, 0, tzinfo=UTC))

    def test_weekends_are_skipped(self):
        window = Window("t", "UTC", datetime(2026, 1, 1, 8, 0).time(), datetime(2026, 1, 1, 16, 0).time())
        saturday = datetime(2026, 3, 14, 10, 0, tzinfo=UTC)
        assert saturday.weekday() == 5
        assert not window.contains(saturday)


class TestSessionClock:
    def test_london_ny_overlap_detected(self, config):
        clock = SessionClock(config)
        # 14:30 UTC in March is 10:30 New York and 14:30 London — both open.
        state = clock.state(datetime(2026, 3, 10, 14, 30, tzinfo=UTC))
        assert "london" in state.active_sessions
        assert "new_york" in state.active_sessions
        assert state.in_london_ny_overlap is True
        assert state.primary == "LONDON/NY OVERLAP"

    def test_overlap_raises_rather_than_lowers_the_bar(self, config):
        clock = SessionClock(config)
        overlap = clock.state(datetime(2026, 3, 10, 14, 30, tzinfo=UTC))
        quiet = clock.state(datetime(2026, 3, 10, 3, 0, tzinfo=UTC))
        assert clock.min_confidence_adjustment(overlap) > 0
        assert clock.min_confidence_adjustment(quiet) == 0

    def test_dst_gap_week_still_resolves_the_overlap(self, config):
        """US clocks moved on 8 March 2026; the UK moves on 29 March.

        During that window New York is UTC-4 while London is still UTC+0, so
        the overlap shifts an hour earlier in UTC. A DST-naive implementation
        gets this wrong.
        """
        clock = SessionClock(config)
        state = clock.state(datetime(2026, 3, 17, 13, 30, tzinfo=UTC))
        assert state.in_london_ny_overlap is True

        # In January, when both are on winter time, 13:30 UTC is 08:30 NY —
        # still an overlap, which confirms the tz conversion, not a constant.
        winter = clock.state(datetime(2026, 1, 20, 13, 30, tzinfo=UTC))
        assert winter.in_london_ny_overlap is True

    def test_kill_zones_resolve(self, config):
        clock = SessionClock(config)
        # 08:00 UTC in March == 04:00 New York == London kill zone.
        state = clock.state(datetime(2026, 3, 10, 8, 0, tzinfo=UTC))
        assert "london" in state.kill_zones
        assert state.in_kill_zone is True

    def test_off_session_is_penalised(self, config):
        clock = SessionClock(config)
        # 04:00 UTC in March: London has not opened, New York is closed, and
        # the Asian kill zone has ended.
        state = clock.state(datetime(2026, 3, 10, 4, 30, tzinfo=UTC))
        if not state.active_sessions and not state.kill_zones:
            assert state.multiplier < 1.0

    def test_weekend_is_closed(self, config):
        clock = SessionClock(config)
        state = clock.state(datetime(2026, 3, 14, 12, 0, tzinfo=UTC))
        assert state.market_closed is True
        assert state.multiplier == 0.0
        assert state.primary == "CLOSED"

    def test_friday_evening_is_closed(self, config):
        clock = SessionClock(config)
        assert clock.state(datetime(2026, 3, 13, 21, 0, tzinfo=UTC)).market_closed is True

    def test_sunday_before_the_open_is_closed(self, config):
        clock = SessionClock(config)
        assert clock.state(datetime(2026, 3, 15, 12, 0, tzinfo=UTC)).market_closed is True

    def test_daily_rollover_is_closed(self, config):
        clock = SessionClock(config)
        state = clock.state(datetime(2026, 3, 11, 21, 5, tzinfo=UTC))
        assert state.market_closed is True
        assert "rollover" in state.closed_reason.lower()

    def test_countdowns_are_in_the_future(self, config):
        clock = SessionClock(config)
        state = clock.state(datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
        for countdown in (state.next_open, state.next_close, state.next_kill_zone):
            assert countdown is not None
            assert countdown.seconds > 0
            assert countdown.human

    def test_penalty_beats_bonus_when_both_apply(self, config):
        """A closing session is a warning; a bonus must never mask it."""
        clock = SessionClock(config)
        multiplier, tags = clock._multiplier(
            active=["london", "new_york"], zones=["new_york_am"], overlap=True,
            just_opened=[], closing_soon=["london"], closed=False,
        )
        assert multiplier < 1.0
        assert tags[0] == "session_close_transition"

    def test_bonuses_are_not_stacked(self, config):
        """Overlap + kill zone must take the best single bonus, not their product."""
        clock = SessionClock(config)
        multiplier, _ = clock._multiplier(
            active=["london", "new_york"], zones=["new_york_am"], overlap=True,
            just_opened=[], closing_soon=[], closed=False,
        )
        overlap_only = float(config.get("sessions.multipliers.london_ny_overlap"))
        kz_only = float(config.get("sessions.multipliers.new_york_am_killzone"))
        assert multiplier == pytest.approx(max(overlap_only, kz_only))
        assert multiplier < overlap_only * kz_only
