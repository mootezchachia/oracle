"""Economic calendar parsing, event classification and the news guard.

The guard's job is to say "not now". Every one of these tests is really asking
the same question: would this system have been flat through the print?
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from xauusd.models import UTC, EconomicEvent, Headline, NewsSeverity
from xauusd.news.calendar_feed import EconomicCalendar, parse_feed
from xauusd.news.classifier import classify_event, is_gold_sensitive
from xauusd.news.guard import NewsGuard
from xauusd.news.headlines import parse_rss
from xauusd.testing import trending_series

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

FEED = [
    {"title": "FOMC Statement", "country": "USD", "date": "2026-03-10T14:00:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-03-11T08:30:00-04:00",
     "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-03-12T08:30:00-04:00",
     "impact": "Medium", "forecast": "220K", "previous": "218K"},
    {"title": "Bank Holiday", "country": "AUD", "date": "2026-03-09T17:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "Building Consents m/m", "country": "NZD", "date": "2026-03-10T18:45:00-04:00",
     "impact": "Low", "forecast": "", "previous": "-4.0%"},
]


class TestClassifier:
    def test_fomc_is_critical(self, config):
        assert classify_event("FOMC Statement", "High", "USD", config) is NewsSeverity.CRITICAL

    def test_nfp_is_critical(self, config):
        assert classify_event("Non-Farm Employment Change", "High", "USD", config) is NewsSeverity.CRITICAL

    def test_non_usd_critical_is_demoted(self, config):
        """A euro-area rate decision moves Gold, but not the way a Fed one does."""
        assert classify_event("Interest Rate Decision", "High", "USD", config) is NewsSeverity.CRITICAL
        assert classify_event("Interest Rate Decision", "High", "EUR", config) is NewsSeverity.HIGH

    def test_claims_are_high(self, config):
        assert classify_event("Unemployment Claims", "Medium", "USD", config) is NewsSeverity.HIGH

    def test_holidays_carry_no_severity(self, config):
        assert classify_event("Bank Holiday", "Holiday", "AUD", config) is NewsSeverity.NONE

    def test_empty_title_is_safe(self, config):
        assert classify_event("", "High", "USD", config) is NewsSeverity.NONE

    def test_gold_sensitivity(self):
        assert is_gold_sensitive("US CPI comes in hot")
        assert not is_gold_sensitive("New Zealand building consents")


class TestFeedParsing:
    def test_parses_and_classifies(self, config):
        events = parse_feed(FEED, ["USD", "ALL"], config)
        titles = [e.title for e in events]
        assert "FOMC Statement" in titles
        assert "Building Consents m/m" not in titles      # NZD filtered out
        fomc = next(e for e in events if e.title == "FOMC Statement")
        assert fomc.severity is NewsSeverity.CRITICAL
        assert fomc.ts.tzinfo is not None
        assert fomc.ts == datetime(2026, 3, 10, 18, 0, tzinfo=UTC)   # -04:00 → UTC

    def test_events_are_sorted(self, config):
        events = parse_feed(FEED, ["USD", "ALL", "AUD", "NZD"], config)
        assert events == sorted(events, key=lambda e: e.ts)

    def test_garbage_payload_returns_empty_not_an_exception(self, config):
        assert parse_feed({"unexpected": "shape"}, ["USD"], config) == []
        assert parse_feed([{"no": "date"}], ["USD"], config) == []

    def test_unparseable_timestamp_is_skipped(self, config):
        bad = [{"title": "X", "country": "USD", "date": "not-a-date", "impact": "High"}]
        assert parse_feed(bad, ["USD"], config) == []


class TestNewsGuard:
    def _guard(self, config, events) -> NewsGuard:
        calendar = EconomicCalendar(config)
        calendar.load(events)
        return NewsGuard(config, calendar)

    def test_blocks_inside_the_critical_window(self, config):
        event = EconomicEvent("FOMC Statement", "USD", NOW + timedelta(minutes=30),
                              "High", NewsSeverity.CRITICAL)
        state = self._guard(config, [event]).evaluate(NOW)
        assert state.blocked is True
        assert state.multiplier == 0.0
        assert "FOMC" in state.reason

    def test_blocks_immediately_after_the_release(self, config):
        event = EconomicEvent("FOMC Statement", "USD", NOW - timedelta(minutes=20),
                              "High", NewsSeverity.CRITICAL)
        assert self._guard(config, [event]).evaluate(NOW).blocked is True

    def test_post_release_cooldown_extends_past_the_blackout(self, config):
        """Blackout ending is not the same as the tape being tradeable."""
        event = EconomicEvent("Core CPI m/m", "USD", NOW - timedelta(minutes=50),
                              "High", NewsSeverity.CRITICAL)
        state = self._guard(config, [event]).evaluate(NOW)
        assert state.blocked is True
        assert state.settling is True

    def test_approach_penalty_without_a_block(self, config):
        event = EconomicEvent("FOMC Statement", "USD", NOW + timedelta(hours=3),
                              "High", NewsSeverity.CRITICAL)
        state = self._guard(config, [event]).evaluate(NOW)
        assert state.blocked is False
        assert 0 < state.multiplier < 1.0
        assert state.severity is NewsSeverity.CRITICAL

    def test_distant_event_leaves_the_score_untouched(self, config):
        event = EconomicEvent("FOMC Statement", "USD", NOW + timedelta(days=3),
                              "High", NewsSeverity.CRITICAL)
        state = self._guard(config, [event]).evaluate(NOW)
        assert state.blocked is False
        assert state.multiplier == pytest.approx(1.0)

    def test_next_event_and_countdown_are_reported(self, config):
        event = EconomicEvent("Core CPI m/m", "USD", NOW + timedelta(hours=8),
                              "High", NewsSeverity.CRITICAL)
        state = self._guard(config, [event]).evaluate(NOW)
        assert state.next_event is not None
        assert state.minutes_to_next == pytest.approx(480, rel=1e-3)
        assert state.next_event_countdown

    def test_missing_calendar_blocks_rather_than_permits(self, config):
        """An unknown schedule is a risk, never an all-clear."""
        calendar = EconomicCalendar(config)          # never refreshed → stale
        state = NewsGuard(config, calendar).evaluate(NOW)
        assert state.blocked is True
        assert "blind" in state.reason.lower()

    def test_shock_headline_reduces_confidence(self, config):
        event = EconomicEvent("Building Consents", "NZD", NOW + timedelta(days=2),
                              "Low", NewsSeverity.LOW)
        guard = self._guard(config, [event])
        guard.set_headlines([
            Headline("Escalation reported near key shipping lane", "test", NOW - timedelta(minutes=10), shock=True)
        ])
        state = guard.evaluate(NOW)
        assert state.multiplier < 1.0
        assert state.shock_headlines

    def test_stale_headline_is_ignored(self, config):
        event = EconomicEvent("Building Consents", "NZD", NOW + timedelta(days=2),
                              "Low", NewsSeverity.LOW)
        guard = self._guard(config, [event])
        guard.set_headlines([
            Headline("Old escalation story", "test", NOW - timedelta(hours=6), shock=True)
        ])
        assert guard.evaluate(NOW).multiplier == pytest.approx(1.0)

    def test_prune_drops_old_baselines(self, config):
        guard = self._guard(config, [])
        guard._baselines[f"{(NOW - timedelta(days=5)).isoformat()}|Old"] = 1.0
        guard._baselines[f"{NOW.isoformat()}|New"] = 1.0
        guard.prune(NOW, keep_hours=48)
        assert len(guard._baselines) == 1


class TestHeadlineParsing:
    RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Gold rallies as war risk premium returns</title>
            <link>http://example.com/a</link>
            <pubDate>Tue, 10 Mar 2026 09:00:00 GMT</pubDate></item>
      <item><title>Quarterly earnings roundup</title>
            <link>http://example.com/b</link>
            <pubDate>Tue, 10 Mar 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""

    def test_parses_items_and_flags_shocks(self):
        headlines = parse_rss(self.RSS, "example.com", ["war", "invasion"])
        assert len(headlines) == 2
        assert headlines[0].shock is True
        assert headlines[1].shock is False
        assert headlines[0].ts.tzinfo is not None

    def test_malformed_xml_returns_empty(self):
        assert parse_rss("<not xml", "src", ["war"]) == []
