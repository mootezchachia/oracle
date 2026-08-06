"""Notification channels: Telegram and Discord.

Both are thin async HTTP clients with retry-with-backoff. A failed alert is
logged loudly and never silently swallowed — an alert you did not receive is
indistinguishable from a setup that never happened, which is the worst failure
mode this system has.
"""

from __future__ import annotations

import abc
import asyncio
from typing import Any

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import Signal
from .formatter import (
    format_signal_discord,
    format_signal_html,
    format_signal_text,
)

log = get_logger("notify")

_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_ATTEMPTS = 3


class Channel(abc.ABC):
    name: str = "channel"

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None
        self.enabled = False
        self.failures = 0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, url: str, payload: dict[str, Any]) -> bool:
        """POST with exponential backoff. Returns success."""
        session = await self._ensure_session()
        delay = 1.0
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with session.post(url, json=payload) as response:
                    if response.status < 300:
                        self.failures = 0
                        return True
                    body = (await response.text())[:300]
                    log.warning("%s HTTP %s: %s", self.name, response.status, body)
                    # 4xx other than rate limiting will not fix themselves.
                    if 400 <= response.status < 500 and response.status != 429:
                        self.failures += 1
                        return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("%s attempt %d/%d failed: %s", self.name, attempt, _MAX_ATTEMPTS, exc)

            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= 2

        self.failures += 1
        log.error("%s delivery failed after %d attempts", self.name, _MAX_ATTEMPTS)
        return False

    @abc.abstractmethod
    async def send_signal(self, signal: Signal) -> bool: ...

    @abc.abstractmethod
    async def send_text(self, text: str) -> bool: ...


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        super().__init__(session)
        cfg = config.section("notify.telegram")
        self.token = cfg.get("bot_token", None)
        self.chat_id = cfg.get("chat_id", None)
        self.enabled = bool(cfg.get("enabled", False)) and bool(self.token) and bool(self.chat_id)
        if cfg.get("enabled", False) and not self.enabled:
            log.warning("Telegram enabled but bot_token/chat_id missing — channel disabled")

    @property
    def _url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send_signal(self, signal: Signal) -> bool:
        if not self.enabled:
            return False
        return await self._post(
            self._url,
            {
                "chat_id": self.chat_id,
                "text": format_signal_html(signal),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    async def send_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        return await self._post(
            self._url,
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
        )


class DiscordChannel(Channel):
    name = "discord"

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        super().__init__(session)
        cfg = config.section("notify.discord")
        self.webhook_url = cfg.get("webhook_url", None)
        self.enabled = bool(cfg.get("enabled", False)) and bool(self.webhook_url)
        if cfg.get("enabled", False) and not self.enabled:
            log.warning("Discord enabled but webhook_url missing — channel disabled")

    async def send_signal(self, signal: Signal) -> bool:
        if not self.enabled:
            return False
        return await self._post(str(self.webhook_url), format_signal_discord(signal))

    async def send_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        return await self._post(str(self.webhook_url), {"content": f"```\n{text[:1900]}\n```"})
