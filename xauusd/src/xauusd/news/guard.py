"""The news guard — the component that says "not now".

Three distinct jobs, in order of severity:

1. **Blackout.** Inside a hard window around a CRITICAL or HIGH release, no
   signal is emitted at any confidence. Being flat through FOMC is a position.

2. **Post-release settling.** The blackout ending is not the same as the market
   being tradeable. After a print, Gold routinely runs both ways before it
   decides. The guard keeps trading paused until ATR has mean-reverted toward
   its pre-release baseline *and* a minimum time has passed.

3. **Approach penalty.** Outside the blackout but inside the approach horizon,
   confidence is scaled down rather than blocked. Positioning ahead of a print
   is legitimate; sizing it like a normal setup is not.

The guard also watches geopolitical headlines, because Gold's largest moves
often arrive with no calendar entry at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, EconomicEvent, Headline, NewsSeverity, humanize_delta
from ..analysis.volatility import atr_settled
from .calendar_feed import EconomicCalendar

log = get_logger("news.guard")

_SEVERITY_KEYS = {
    NewsSeverity.CRITICAL: "critical",
    NewsSeverity.HIGH: "high",
    NewsSeverity.MEDIUM: "medium",
}


@dataclass(slots=True)
class NewsState:
    """The guard's verdict for one evaluation cycle."""

    ts: datetime
    blocked: bool = False
    reason: str = ""
    severity: NewsSeverity = NewsSeverity.NONE
    multiplier: float = 1.0
    next_event: EconomicEvent | None = None
    minutes_to_next: float | None = None
    active_event: EconomicEvent | None = None
    settling: bool = False
    settle_detail: str = ""
    shock_headlines: list[Headline] = field(default_factory=list)
    calendar_stale: bool = False

    @property
    def next_event_countdown(self) -> str:
        if self.minutes_to_next is None:
            return "—"
        return humanize_delta(timedelta(minutes=self.minutes_to_next))

    def to_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts.isoformat(),
            "blocked": self.blocked,
            "reason": self.reason,
            "severity": self.severity.value,
            "multiplier": round(self.multiplier, 3),
            "next_event": self.next_event.to_dict() if self.next_event else None,
            "minutes_to_next": round(self.minutes_to_next, 1) if self.minutes_to_next is not None else None,
            "countdown": self.next_event_countdown,
            "active_event": self.active_event.to_dict() if self.active_event else None,
            "settling": self.settling,
            "settle_detail": self.settle_detail,
            "shock_headlines": [h.to_dict() for h in self.shock_headlines[:5]],
            "calendar_stale": self.calendar_stale,
        }


class NewsGuard:
    """Stateful across cycles so it can remember pre-release volatility."""

    def __init__(self, config: Config, calendar: EconomicCalendar) -> None:
        self._config = config
        self._cfg = config.section("news")
        self.calendar = calendar
        # event key -> ATR observed before the release, used as the settle target
        self._baselines: dict[str, float] = {}
        self._headlines: list[Headline] = []

    # -- headline feed -------------------------------------------------------
    def set_headlines(self, headlines: Sequence[Headline]) -> None:
        self._headlines = list(headlines)

    def _shock_headlines(self, now: datetime) -> list[Headline]:
        cfg = self._cfg.section("headlines")
        if not cfg.get("enabled", True):
            return []
        window = timedelta(minutes=float(cfg.get("shock_window_minutes", 90)))
        return [h for h in self._headlines if h.shock and (now - h.ts) <= window]

    # -- blackout windows ----------------------------------------------------
    def _blackout_minutes(self, severity: NewsSeverity) -> tuple[float, float]:
        key = _SEVERITY_KEYS.get(severity)
        if key is None:
            return 0.0, 0.0
        entry = self._cfg.get(f"blackout.{key}", {}) or {}
        return float(entry.get("before", 0)), float(entry.get("after", 0))

    def _approach(self, severity: NewsSeverity) -> tuple[float, float]:
        """Returns ``(horizon_minutes, penalty_multiplier)``."""
        key = _SEVERITY_KEYS.get(severity)
        if key is None:
            return 0.0, 1.0
        horizon = float(self._cfg.get(f"approach_horizon_minutes.{key}", 0))
        penalty = float(self._cfg.get(f"approach_penalty.{key}", 1.0))
        return horizon, penalty

    @staticmethod
    def _key(event: EconomicEvent) -> str:
        return f"{event.ts.isoformat()}|{event.title}"

    # -- main evaluation -----------------------------------------------------
    def evaluate(
        self,
        now: datetime | None = None,
        candles: Sequence[Candle] | None = None,
        atr_period: int = 14,
    ) -> NewsState:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        state = NewsState(ts=now)

        if not self._cfg.get("enabled", True):
            return state

        state.calendar_stale = self.calendar.stale
        if state.calendar_stale:
            # Unknown schedule is a risk, not a green light.
            state.multiplier *= 0.9
            state.reason = "Economic calendar is stale — reduced conviction"

        events = self.calendar.events
        if not events and state.calendar_stale:
            state.blocked = True
            state.reason = "No economic calendar available — refusing to trade blind"
            return state

        upcoming = [e for e in events if e.ts >= now]
        state.next_event = next(
            (e for e in upcoming if e.severity.rank >= NewsSeverity.MEDIUM.rank), None
        ) or (upcoming[0] if upcoming else None)
        if state.next_event is not None:
            state.minutes_to_next = state.next_event.minutes_until(now)

        # --- 1. capture pre-release ATR baselines ---------------------------
        if candles:
            self._capture_baselines(now, events, candles, atr_period)

        # --- 2. hard blackout ------------------------------------------------
        for event in events:
            before, after = self._blackout_minutes(event.severity)
            if before == 0 and after == 0:
                continue
            start = event.ts - timedelta(minutes=before)
            end = event.ts + timedelta(minutes=after)
            if start <= now <= end:
                state.blocked = True
                state.active_event = event
                state.severity = event.severity
                state.multiplier = 0.0
                position = "ahead of" if now < event.ts else "after"
                state.reason = (
                    f"{event.severity.value} event {position} release: "
                    f"{event.title} ({event.currency}) at {event.ts:%H:%M} UTC"
                )
                return state

        # --- 3. post-release settling ---------------------------------------
        settle_state = self._check_settling(now, events, candles, atr_period)
        if settle_state is not None:
            return settle_state

        # --- 4. approach penalty ---------------------------------------------
        worst = NewsSeverity.NONE
        penalty = 1.0
        for event in upcoming:
            horizon, event_penalty = self._approach(event.severity)
            if horizon <= 0:
                continue
            if event.minutes_until(now) <= horizon:
                if event.severity.rank > worst.rank:
                    worst = event.severity
                penalty = min(penalty, event_penalty)

        if worst is not NewsSeverity.NONE:
            state.severity = worst
            state.multiplier *= penalty
            countdown = humanize_delta(timedelta(minutes=state.minutes_to_next or 0))
            state.reason = f"{worst.value} event in {countdown} — conviction reduced"

        # --- 5. geopolitical shock -------------------------------------------
        shocks = self._shock_headlines(now)
        if shocks:
            state.shock_headlines = shocks
            shock_penalty = float(self._cfg.get("headlines.shock_penalty", 0.9))
            state.multiplier *= shock_penalty
            if state.severity is NewsSeverity.NONE:
                state.severity = NewsSeverity.MEDIUM
            headline = shocks[0].title[:80]
            state.reason = (state.reason + " | " if state.reason else "") + f"Headline risk: {headline}"

        return state

    # -- internals -----------------------------------------------------------
    def _capture_baselines(
        self, now: datetime, events: Sequence[EconomicEvent], candles: Sequence[Candle], atr_period: int
    ) -> None:
        """Record ATR shortly before each significant release."""
        from ..analysis.indicators import atr as atr_fn, last_valid

        current = last_valid(atr_fn(candles, atr_period))
        if current is None or current <= 0:
            return

        for event in events:
            if event.severity.rank < NewsSeverity.HIGH.rank:
                continue
            key = self._key(event)
            if key in self._baselines:
                continue
            minutes_until = event.minutes_until(now)
            # Capture in the 20-minute run-up, before the pre-print positioning
            # squeeze distorts the reading.
            if 0 < minutes_until <= 20:
                self._baselines[key] = current
                log.debug("captured pre-news ATR baseline %.3f for %s", current, event.title)

    def _check_settling(
        self,
        now: datetime,
        events: Sequence[EconomicEvent],
        candles: Sequence[Candle] | None,
        atr_period: int,
    ) -> NewsState | None:
        """Block until volatility mean-reverts after a significant print."""
        min_minutes = float(self._cfg.get("settle_min_minutes", 20))
        ratio = float(self._cfg.get("settle_atr_ratio", 1.6))

        for event in events:
            if event.severity.rank < NewsSeverity.HIGH.rank:
                continue
            _, after = self._blackout_minutes(event.severity)
            elapsed = (now - event.ts).total_seconds() / 60.0
            if not (after < elapsed <= after + min_minutes + 60):
                continue

            state = NewsState(ts=now, active_event=event, severity=event.severity)

            if elapsed < after + min_minutes:
                state.blocked = True
                state.settling = True
                remaining = after + min_minutes - elapsed
                state.multiplier = 0.0
                state.settle_detail = f"{remaining:.0f} min of post-release cool-down remaining"
                state.reason = f"Volatility settling after {event.title} — {state.settle_detail}"
                return state

            baseline = self._baselines.get(self._key(event))
            if baseline and candles and not atr_settled(candles, baseline, atr_period, ratio):
                state.blocked = True
                state.settling = True
                state.multiplier = 0.0
                state.settle_detail = f"ATR still above {ratio:.1f}x the pre-release baseline ({baseline:.2f})"
                state.reason = f"Post-{event.title} volatility has not normalised — {state.settle_detail}"
                return state
        return None

    def prune(self, now: datetime | None = None, keep_hours: int = 48) -> None:
        """Drop ATR baselines for events that are long gone."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=keep_hours)
        for key in list(self._baselines):
            stamp = key.split("|", 1)[0]
            try:
                if datetime.fromisoformat(stamp) < cutoff:
                    del self._baselines[key]
            except ValueError:
                del self._baselines[key]
