"""Trading sessions and ICT kill zones — fully DST aware.

Session boundaries are defined in **exchange-local time** and converted through
the IANA tz database, which is the only correct way to do this: London and New
York change clocks on different dates, so any system that hard-codes UTC
offsets is wrong for several weeks a year — including, reliably, the weeks
around the March and October transitions when Gold's session behaviour matters
most.

The module answers four questions the engine asks on every cycle:

1. Which sessions are open right now?
2. Are we inside an ICT kill zone, and which one?
3. Are London and New York overlapping (Gold's highest-quality window)?
4. How long until the next session open and the next session close?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from ..config import Config
from ..models import UTC, humanize_delta

_WEEKEND = {5, 6}   # Saturday, Sunday (Python weekday numbering)


def _parse_hhmm(value: str) -> time:
    hours, _, minutes = str(value).partition(":")
    return time(int(hours), int(minutes or 0))


@dataclass(frozen=True, slots=True)
class Window:
    """A recurring daily window expressed in one timezone."""

    name: str
    tz: str
    open_time: time
    close_time: time

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def duration(self) -> timedelta:
        start = timedelta(hours=self.open_time.hour, minutes=self.open_time.minute)
        end = timedelta(hours=self.close_time.hour, minutes=self.close_time.minute)
        span = end - start
        if span <= timedelta(0):        # window crosses local midnight
            span += timedelta(days=1)
        return span

    def occurrence(self, local_day: date) -> tuple[datetime, datetime]:
        """The UTC (start, end) of this window for a given *local* calendar day."""
        zone = self.zone
        start_local = datetime.combine(local_day, self.open_time, tzinfo=zone)
        start_utc = start_local.astimezone(UTC)
        return start_utc, start_utc + self.duration

    def _candidate_days(self, now: datetime) -> list[date]:
        local_today = now.astimezone(self.zone).date()
        return [local_today - timedelta(days=1), local_today, local_today + timedelta(days=1)]

    def contains(self, now: datetime) -> bool:
        for day in self._candidate_days(now):
            if day.weekday() in _WEEKEND:
                continue
            start, end = self.occurrence(day)
            if start <= now < end:
                return True
        return False

    def next_open(self, now: datetime) -> datetime | None:
        """Next start strictly after ``now``, skipping weekends."""
        local_today = now.astimezone(self.zone).date()
        for offset in range(0, 9):
            day = local_today + timedelta(days=offset)
            if day.weekday() in _WEEKEND:
                continue
            start, _ = self.occurrence(day)
            if start > now:
                return start
        return None

    def next_close(self, now: datetime) -> datetime | None:
        for day in self._candidate_days(now) + [
            now.astimezone(self.zone).date() + timedelta(days=n) for n in range(2, 9)
        ]:
            if day.weekday() in _WEEKEND:
                continue
            _, end = self.occurrence(day)
            if end > now:
                return end
        return None

    def minutes_since_open(self, now: datetime) -> float | None:
        for day in self._candidate_days(now):
            if day.weekday() in _WEEKEND:
                continue
            start, end = self.occurrence(day)
            if start <= now < end:
                return (now - start).total_seconds() / 60.0
        return None

    def minutes_to_close(self, now: datetime) -> float | None:
        for day in self._candidate_days(now):
            if day.weekday() in _WEEKEND:
                continue
            start, end = self.occurrence(day)
            if start <= now < end:
                return (end - now).total_seconds() / 60.0
        return None


@dataclass(slots=True)
class Countdown:
    name: str
    ts: datetime
    seconds: float

    @property
    def human(self) -> str:
        return humanize_delta(timedelta(seconds=self.seconds))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ts": self.ts.isoformat(), "seconds": int(self.seconds), "human": self.human}


@dataclass(slots=True)
class SessionState:
    """The complete session picture at one instant."""

    ts: datetime
    active_sessions: list[str] = field(default_factory=list)
    kill_zones: list[str] = field(default_factory=list)
    in_london_ny_overlap: bool = False
    opening_soon: list[str] = field(default_factory=list)
    just_opened: list[str] = field(default_factory=list)
    closing_soon: list[str] = field(default_factory=list)
    multiplier: float = 1.0
    tags: list[str] = field(default_factory=list)
    market_closed: bool = False
    closed_reason: str = ""
    next_open: Countdown | None = None
    next_close: Countdown | None = None
    next_kill_zone: Countdown | None = None

    @property
    def primary(self) -> str:
        """A single human-readable label for the current session context."""
        if self.market_closed:
            return "CLOSED"
        if self.in_london_ny_overlap:
            return "LONDON/NY OVERLAP"
        if self.kill_zones:
            return f"{self.kill_zones[0].replace('_', ' ').upper()} KILL ZONE"
        if self.active_sessions:
            return "/".join(s.replace("_", " ").upper() for s in self.active_sessions)
        return "OFF SESSION"

    @property
    def in_kill_zone(self) -> bool:
        return bool(self.kill_zones)

    def to_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts.isoformat(),
            "primary": self.primary,
            "active_sessions": self.active_sessions,
            "kill_zones": self.kill_zones,
            "in_kill_zone": self.in_kill_zone,
            "in_london_ny_overlap": self.in_london_ny_overlap,
            "opening_soon": self.opening_soon,
            "just_opened": self.just_opened,
            "closing_soon": self.closing_soon,
            "multiplier": round(self.multiplier, 3),
            "tags": self.tags,
            "market_closed": self.market_closed,
            "closed_reason": self.closed_reason,
            "next_open": self.next_open.to_dict() if self.next_open else None,
            "next_close": self.next_close.to_dict() if self.next_close else None,
            "next_kill_zone": self.next_kill_zone.to_dict() if self.next_kill_zone else None,
        }


class SessionClock:
    """Evaluates session context for any instant. Stateless and testable."""

    def __init__(self, config: Config) -> None:
        cfg = config.section("sessions")
        self._cfg = cfg
        self.sessions = self._build(cfg.get("windows", {}))
        self.kill_zones = self._build(cfg.get("kill_zones", {}))
        self.open_window = int(cfg.get("open_window_minutes", 45))
        self.close_window = int(cfg.get("close_window_minutes", 30))
        self.multipliers: Mapping[str, float] = cfg.get("multipliers", {}) or {}
        self.overlap_bonus = float(cfg.get("overlap_min_confidence_bonus", 0.0))

        blackout = cfg.section("blackout")
        self.friday_close = _parse_hhmm(blackout.get("friday_close_utc", "20:00"))
        self.sunday_open = _parse_hhmm(blackout.get("sunday_open_utc", "22:00"))
        self.rollover = _parse_hhmm(blackout.get("daily_rollover_utc", "21:00"))
        self.rollover_minutes = int(blackout.get("daily_rollover_minutes", 15))

    @staticmethod
    def _build(spec: Mapping[str, Mapping[str, str]]) -> dict[str, Window]:
        windows: dict[str, Window] = {}
        for name, entry in (spec or {}).items():
            windows[name] = Window(
                name=name,
                tz=str(entry.get("tz", "UTC")),
                open_time=_parse_hhmm(str(entry.get("open", "00:00"))),
                close_time=_parse_hhmm(str(entry.get("close", "00:00"))),
            )
        return windows

    # -- market open/closed --------------------------------------------------
    def market_closed(self, now: datetime) -> tuple[bool, str]:
        """Weekend closure and the daily rollover illiquidity window."""
        now_utc = now.astimezone(UTC)
        weekday = now_utc.weekday()
        clock = now_utc.time()

        if weekday == 5:
            return True, "Weekend — market closed"
        if weekday == 4 and clock >= self.friday_close:
            return True, "Friday close — weekend break"
        if weekday == 6 and clock < self.sunday_open:
            return True, "Awaiting Sunday open"

        rollover_start = datetime.combine(now_utc.date(), self.rollover, tzinfo=UTC)
        if rollover_start <= now_utc < rollover_start + timedelta(minutes=self.rollover_minutes):
            return True, "Daily rollover — spreads widen, no entries"
        return False, ""

    # -- main evaluation -----------------------------------------------------
    def state(self, now: datetime | None = None) -> SessionState:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        closed, reason = self.market_closed(now)

        active = [name for name, window in self.sessions.items() if window.contains(now)]
        zones = [name for name, window in self.kill_zones.items() if window.contains(now)]
        overlap = "london" in active and "new_york" in active

        opening_soon: list[str] = []
        just_opened: list[str] = []
        closing_soon: list[str] = []

        for name, window in self.sessions.items():
            since = window.minutes_since_open(now)
            if since is not None and since <= self.open_window:
                just_opened.append(name)
            to_close = window.minutes_to_close(now)
            if to_close is not None and to_close <= self.close_window:
                closing_soon.append(name)
            nxt = window.next_open(now)
            if nxt is not None and 0 < (nxt - now).total_seconds() / 60.0 <= self.open_window:
                opening_soon.append(name)

        multiplier, tags = self._multiplier(active, zones, overlap, just_opened, closing_soon, closed)

        return SessionState(
            ts=now,
            active_sessions=sorted(active),
            kill_zones=sorted(zones),
            in_london_ny_overlap=overlap,
            opening_soon=sorted(opening_soon),
            just_opened=sorted(just_opened),
            closing_soon=sorted(closing_soon),
            multiplier=multiplier,
            tags=tags,
            market_closed=closed,
            closed_reason=reason,
            next_open=self._next(self.sessions, now, "open"),
            next_close=self._next(self.sessions, now, "close"),
            next_kill_zone=self._next(self.kill_zones, now, "open"),
        )

    def _multiplier(
        self,
        active: Iterable[str],
        zones: list[str],
        overlap: bool,
        just_opened: list[str],
        closing_soon: list[str],
        closed: bool,
    ) -> tuple[float, list[str]]:
        """Take the most favourable applicable multiplier, not the product.

        Stacking bonuses multiplicatively would let a routine setup inside an
        overlapping kill zone inflate past the confidence floor on session
        context alone. The best single contextual bonus is enough.
        """
        if closed:
            return 0.0, ["market_closed"]

        applicable: list[tuple[str, float]] = []
        if overlap:
            applicable.append(("london_ny_overlap", float(self.multipliers.get("london_ny_overlap", 1.0))))
        for zone in zones:
            key = f"{zone}_killzone"
            if key in self.multipliers:
                applicable.append((key, float(self.multipliers[key])))
        if just_opened:
            applicable.append(
                ("session_open_transition", float(self.multipliers.get("session_open_transition", 1.0)))
            )
        if closing_soon:
            applicable.append(
                ("session_close_transition", float(self.multipliers.get("session_close_transition", 1.0)))
            )

        active_list = list(active)
        if not applicable:
            if not active_list:
                return float(self.multipliers.get("off_session", 0.8)), ["off_session"]
            return 1.0, ["session_open"]

        # A closing session is a genuine warning, so a penalty below 1.0 always
        # wins over a bonus — never let a bonus mask deteriorating conditions.
        penalties = [item for item in applicable if item[1] < 1.0]
        if penalties:
            name, value = min(penalties, key=lambda item: item[1])
            return value, [name] + [n for n, _ in applicable if n != name]

        name, value = max(applicable, key=lambda item: item[1])
        return value, [name] + [n for n, _ in applicable if n != name]

    @staticmethod
    def _next(windows: Mapping[str, Window], now: datetime, kind: str) -> Countdown | None:
        best: Countdown | None = None
        for name, window in windows.items():
            ts = window.next_open(now) if kind == "open" else window.next_close(now)
            if ts is None:
                continue
            seconds = (ts - now).total_seconds()
            if seconds <= 0:
                continue
            if best is None or seconds < best.seconds:
                best = Countdown(name, ts, seconds)
        return best

    # -- helpers used by the engine -----------------------------------------
    def min_confidence_adjustment(self, state: SessionState) -> float:
        """Extra confidence demanded during overlaps.

        More participants means more liquidity *and* more deliberate traps, so
        the bar goes up during the London/NY overlap, not down.
        """
        return self.overlap_bonus if state.in_london_ny_overlap else 0.0
