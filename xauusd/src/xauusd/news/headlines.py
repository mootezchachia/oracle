"""Geopolitical and Gold-specific headline monitoring.

Gold's biggest single-day moves frequently have no calendar entry — an
escalation, a surprise central-bank statement, a sanctions announcement. This
module polls a handful of public RSS feeds and flags headlines carrying shock
vocabulary, which the news guard converts into a confidence penalty.

RSS is parsed with the standard library so the system carries no extra
dependency for what is, structurally, a very simple XML document.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Sequence
from xml.etree import ElementTree

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Headline

log = get_logger("news.headlines")

_TIMEOUT = aiohttp.ClientTimeout(total=20)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def _parse_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(raw).astimezone(UTC)
        except ValueError:
            return datetime.now(UTC)


def parse_rss(xml_text: str, source: str, shock_keywords: Sequence[str]) -> list[Headline]:
    """Parse an RSS/Atom document into :class:`Headline` objects."""
    headlines: list[Headline] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        log.debug("RSS parse failure for %s: %s", source, exc)
        return headlines

    items = root.iter("item")
    for item in items:
        title = _clean(item.findtext("title"))
        if not title:
            continue
        headlines.append(
            Headline(
                title=title,
                source=source,
                ts=_parse_date(item.findtext("pubDate")),
                url=_clean(item.findtext("link")),
                shock=_is_shock(title, shock_keywords),
            )
        )

    if not headlines:  # Atom fallback
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{ns}entry"):
            title = _clean(entry.findtext(f"{ns}title"))
            if not title:
                continue
            link_el = entry.find(f"{ns}link")
            headlines.append(
                Headline(
                    title=title,
                    source=source,
                    ts=_parse_date(entry.findtext(f"{ns}updated")),
                    url=link_el.get("href", "") if link_el is not None else "",
                    shock=_is_shock(title, shock_keywords),
                )
            )
    return headlines


def _is_shock(title: str, keywords: Sequence[str]) -> bool:
    lowered = title.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


class HeadlineMonitor:
    """Polls the configured feeds concurrently and keeps a recent-headline cache."""

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        self._cfg = config.section("news.headlines")
        self._session = session
        self._owns_session = session is None
        self._headlines: list[Headline] = []
        self._fetched_at: datetime | None = None

    @property
    def headlines(self) -> list[Headline]:
        return list(self._headlines)

    @property
    def shocks(self) -> list[Headline]:
        return [h for h in self._headlines if h.shock]

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _fetch(self, url: str, keywords: Sequence[str]) -> list[Headline]:
        session = await self._ensure_session()
        async with session.get(url, headers={"User-Agent": "xauusd-sentinel/1.0"}) as response:
            response.raise_for_status()
            text = await response.text()
        source = url.split("/")[2] if "//" in url else url
        return parse_rss(text, source, keywords)

    async def refresh(self, force: bool = False) -> list[Headline]:
        if not self._cfg.get("enabled", True):
            return []
        if not force and self._fetched_at is not None:
            age = datetime.now(UTC) - self._fetched_at
            if age < timedelta(minutes=float(self._cfg.get("refresh_minutes", 15))):
                return self.headlines

        feeds = list(self._cfg.get("feeds", []))
        keywords = list(self._cfg.get("shock_keywords", []))
        if not feeds:
            return []

        results = await asyncio.gather(
            *(self._fetch(url, keywords) for url in feeds), return_exceptions=True
        )
        collected: list[Headline] = []
        for url, result in zip(feeds, results):
            if isinstance(result, BaseException):
                log.debug("headline feed failed %s: %s", url, result)
                continue
            collected.extend(result)

        if collected:
            cutoff = datetime.now(UTC) - timedelta(hours=12)
            deduped: dict[str, Headline] = {}
            for headline in sorted(collected, key=lambda h: h.ts, reverse=True):
                if headline.ts < cutoff:
                    continue
                deduped.setdefault(headline.title.lower(), headline)
            self._headlines = list(deduped.values())[:120]
            self._fetched_at = datetime.now(UTC)
            shocks = sum(1 for h in self._headlines if h.shock)
            log.info("headlines refreshed: %d items, %d flagged", len(self._headlines), shocks)
        return self.headlines
