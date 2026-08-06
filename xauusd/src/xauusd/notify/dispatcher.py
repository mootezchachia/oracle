"""Fan-out to every enabled notification channel, concurrently."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Sequence

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Decision, Signal
from ..sessions.calendar import SessionState
from .channels import Channel, DiscordChannel, TelegramChannel
from .formatter import format_heartbeat, format_signal_text

log = get_logger("notify.dispatcher")


class Dispatcher:
    """Sends alerts to all configured channels and tracks delivery."""

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        self._config = config
        self._hb_cfg = config.section("notify.heartbeat")
        self.channels: list[Channel] = [
            TelegramChannel(config, session),
            DiscordChannel(config, session),
        ]
        self.sent = 0
        self.failed = 0
        self.last_sent: datetime | None = None

    @property
    def active(self) -> list[str]:
        return [c.name for c in self.channels if c.enabled]

    async def close(self) -> None:
        await asyncio.gather(*(c.close() for c in self.channels), return_exceptions=True)

    async def send_signal(self, signal: Signal) -> dict[str, bool]:
        """Deliver a signal everywhere at once. Returns per-channel success."""
        targets = [c for c in self.channels if c.enabled]
        if not targets:
            log.warning(
                "no notification channel is enabled — signal %s only reaches the dashboard and log",
                signal.id,
            )
            log.info("SIGNAL PAYLOAD\n%s", format_signal_text(signal))
            return {}

        results = await asyncio.gather(
            *(c.send_signal(signal) for c in targets), return_exceptions=True
        )
        outcome: dict[str, bool] = {}
        for channel, result in zip(targets, results):
            ok = result is True
            outcome[channel.name] = ok
            if ok:
                self.sent += 1
            else:
                self.failed += 1
                if isinstance(result, BaseException):
                    log.error("%s raised while sending %s: %s", channel.name, signal.id, result)

        if any(outcome.values()):
            self.last_sent = datetime.now(UTC)
        log.info("signal %s delivery: %s", signal.id, outcome or "nowhere")
        return outcome

    async def send_text(self, text: str) -> dict[str, bool]:
        targets = [c for c in self.channels if c.enabled]
        if not targets:
            return {}
        results = await asyncio.gather(*(c.send_text(text) for c in targets), return_exceptions=True)
        return {c.name: (r is True) for c, r in zip(targets, results)}

    async def send_heartbeat(self, decision: Decision, session: SessionState, uptime: float) -> dict[str, bool]:
        if not self._hb_cfg.get("enabled", True):
            return {}
        return await self.send_text(format_heartbeat(decision, session, uptime))

    def health(self) -> dict[str, object]:
        return {
            "active": self.active,
            "sent": self.sent,
            "failed": self.failed,
            "last_sent": self.last_sent.isoformat() if self.last_sent else None,
            "channel_failures": {c.name: c.failures for c in self.channels},
        }
