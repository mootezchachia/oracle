"""TradingView integration — both directions.

**Outbound.** Every signal is available as a compact JSON payload
(:func:`~xauusd.notify.formatter.format_tradingview_alert`) suited to
TradingView-compatible webhook consumers and broker bridges.

**Inbound.** A TradingView alert can POST to ``/tv/webhook`` to inject external
context — a custom indicator firing, a manual chart bias, a higher-timeframe
alert you maintain by hand. Injected context is recorded and surfaced to the
dashboard; it is treated as *one more input*, never as an instruction to trade,
because a webhook body is untrusted input arriving from the public internet.

A shared secret is required whenever one is configured, and payloads expire so
a stale alert cannot influence a decision hours later.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Direction

log = get_logger("integrations.tradingview")

MAX_AGE = timedelta(minutes=30)


@dataclass(slots=True)
class ExternalSignal:
    """A bias injected from outside the engine."""

    source: str
    direction: Direction
    note: str
    ts: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def fresh(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) - self.ts <= MAX_AGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "direction": self.direction.value,
            "note": self.note,
            "ts": self.ts.isoformat(),
            "fresh": self.fresh(),
        }


class TradingViewBridge:
    """Holds the most recent externally injected context."""

    def __init__(self, config: Config) -> None:
        cfg = config.section("tradingview")
        self.enabled = bool(cfg.get("webhook_enabled", True))
        self.path = str(cfg.get("webhook_path", "/tv/webhook"))
        self._secret = cfg.get("webhook_secret", None)
        self.latest: ExternalSignal | None = None
        self.received = 0
        self.rejected = 0

    def authorised(self, supplied: str | None) -> bool:
        """Constant-time secret comparison; open when no secret is configured."""
        if not self._secret:
            return True
        if not supplied:
            return False
        return hmac.compare_digest(str(self._secret), str(supplied))

    def ingest(self, payload: dict[str, Any], now: datetime | None = None) -> ExternalSignal | None:
        """Normalise and store an inbound alert."""
        now = now or datetime.now(UTC)
        raw = str(payload.get("action") or payload.get("direction") or payload.get("side") or "").upper()

        direction = Direction.NEUTRAL
        if raw in {"BUY", "LONG", "BULLISH"}:
            direction = Direction.BUY
        elif raw in {"SELL", "SHORT", "BEARISH"}:
            direction = Direction.SELL

        if direction is Direction.NEUTRAL and not payload.get("note"):
            self.rejected += 1
            log.warning("rejected TradingView payload with no usable direction: %r", raw)
            return None

        external = ExternalSignal(
            source=str(payload.get("source") or "tradingview"),
            direction=direction,
            note=str(payload.get("note") or payload.get("message") or "")[:280],
            ts=now,
            payload={k: v for k, v in payload.items() if k != "secret"},
        )
        self.latest = external
        self.received += 1
        log.info("TradingView context received: %s (%s)", direction.value, external.note[:60])
        return external

    def context(self, now: datetime | None = None) -> dict[str, Any] | None:
        if self.latest is None or not self.latest.fresh(now):
            return None
        return self.latest.to_dict()

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "path": self.path,
            "secured": bool(self._secret),
            "received": self.received,
            "rejected": self.rejected,
            "latest": self.context(),
        }


PINE_ALERT_TEMPLATE = """// XAUUSD Sentinel — TradingView alert message template.
// Paste into the alert's "Message" box and point the webhook URL at
//   http://<host>:8787/tv/webhook
{
  "secret": "YOUR_WEBHOOK_SECRET",
  "source": "tradingview",
  "action": "{{strategy.order.action}}",
  "ticker": "{{ticker}}",
  "price": {{close}},
  "note": "{{strategy.order.comment}}",
  "time": "{{timenow}}"
}
"""
