"""Economic calendar ingestion.

Uses the free, key-less weekly JSON feed published by FairEconomy (the same
data that backs the Forex Factory calendar). Two weeks are fetched so that a
Friday-evening evaluation still knows about Monday's releases.

The feed is the *schedule*; interpreting what each release means for Gold is
:mod:`xauusd.news.classifier`'s job.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, EconomicEvent
from .classifier import classify_event

log = get_logger("news.calendar")

_TIMEOUT = aiohttp.ClientTimeout(total=25)


def _parse_ts(raw: str) -> datetime | None:
    """The feed emits ISO-8601 with a numeric offset, e.g. 2026-08-02T05:15:00-04:00."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except ValueError:
                continue
    log.debug("unparseable event timestamp: %r", raw)
    return None


def parse_feed(payload: Any, currencies: Sequence[str], config: Config) -> list[EconomicEvent]:
    """Convert raw feed JSON into classified :class:`EconomicEvent` objects."""
    events: list[EconomicEvent] = []
    if not isinstance(payload, list):
        log.warning("unexpected calendar payload type: %s", type(payload).__name__)
        return events

    wanted = {c.upper() for c in currencies}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        currency = str(entry.get("country") or entry.get("currency") or "").upper()
        if wanted and currency not in wanted:
            continue
        ts = _parse_ts(str(entry.get("date") or ""))
        if ts is None:
            continue

        title = str(entry.get("title") or "").strip()
        impact = str(entry.get("impact") or "").strip()
        events.append(
            EconomicEvent(
                title=title,
                currency=currency,
                ts=ts,
                impact=impact,
                severity=classify_event(title, impact, currency, config),
                forecast=str(entry.get("forecast") or ""),
                previous=str(entry.get("previous") or ""),
                actual=str(entry.get("actual") or ""),
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


class EconomicCalendar:
    """Async client with an in-memory cache and a stale-data fallback.

    If the feed is unreachable the previously fetched schedule is retained
    rather than assuming an all-clear. A missing calendar must never be
    mistaken for "no events today" — that is exactly how a system ends up long
    into an NFP print.
    """

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        self._config = config
        self._cfg = config.section("news")
        self._session = session
        self._owns_session = session is None
        self._events: list[EconomicEvent] = []
        self._fetched_at: datetime | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def events(self) -> list[EconomicEvent]:
        return list(self._events)

    @property
    def fetched_at(self) -> datetime | None:
        return self._fetched_at

    @property
    def stale(self) -> bool:
        if self._fetched_at is None:
            return True
        age = datetime.now(UTC) - self._fetched_at
        return age > timedelta(minutes=float(self._cfg.get("refresh_minutes", 30)) * 3)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_one(self, url: str) -> list[dict[str, Any]]:
        session = await self._ensure_session()
        async with session.get(url, headers={"User-Agent": "xauusd-sentinel/1.0"}) as response:
            response.raise_for_status()
            # The feed is served as text/plain, so json() must not enforce type.
            return await response.json(content_type=None)

    async def refresh(self, force: bool = False) -> list[EconomicEvent]:
        """Fetch this week and next week, merge, classify and cache."""
        async with self._lock:
            if not force and self._fetched_at is not None:
                age = datetime.now(UTC) - self._fetched_at
                if age < timedelta(minutes=float(self._cfg.get("refresh_minutes", 30))):
                    return self.events

            urls = [
                self._cfg.get("calendar_url", ""),
                self._cfg.get("calendar_url_next_week", ""),
            ]
            currencies = list(self._cfg.get("currencies", ["USD", "ALL"]))
            merged: list[EconomicEvent] = []
            errors: list[str] = []

            results = await asyncio.gather(
                *(self._fetch_one(url) for url in urls if url),
                return_exceptions=True,
            )
            for url, result in zip([u for u in urls if u], results):
                if isinstance(result, BaseException):
                    errors.append(f"{url}: {result}")
                    log.warning("calendar fetch failed for %s: %s", url, result)
                    continue
                merged.extend(parse_feed(result, currencies, self._config))

            if merged:
                seen: set[tuple[str, str]] = set()
                unique: list[EconomicEvent] = []
                for event in sorted(merged, key=lambda e: e.ts):
                    key = (event.title, event.ts.isoformat())
                    if key in seen:
                        continue
                    seen.add(key)
                    unique.append(event)
                self._events = unique
                self._fetched_at = datetime.now(UTC)
                self.last_error = None
                log.info("economic calendar refreshed: %d events", len(unique))
            else:
                self.last_error = "; ".join(errors) or "empty calendar response"
                log.error("calendar refresh produced no events (%s); keeping cache", self.last_error)

            return self.events

    # -- queries -------------------------------------------------------------
    def upcoming(self, now: datetime | None = None, within_minutes: float | None = None) -> list[EconomicEvent]:
        now = now or datetime.now(UTC)
        result = [e for e in self._events if e.ts >= now]
        if within_minutes is not None:
            limit = now + timedelta(minutes=within_minutes)
            result = [e for e in result if e.ts <= limit]
        return result

    def recent(self, now: datetime | None = None, within_minutes: float = 120.0) -> list[EconomicEvent]:
        now = now or datetime.now(UTC)
        floor = now - timedelta(minutes=within_minutes)
        return [e for e in self._events if floor <= e.ts <= now]

    def next_event(self, now: datetime | None = None, min_severity: int = 0) -> EconomicEvent | None:
        for event in self.upcoming(now):
            if event.severity.rank >= min_severity:
                return event
        return None

    def window(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        return [e for e in self._events if start <= e.ts <= end]

    def load(self, events: Iterable[EconomicEvent]) -> None:
        """Inject events directly — used by the backtester and by tests."""
        self._events = sorted(events, key=lambda e: e.ts)
        self._fetched_at = datetime.now(UTC)
