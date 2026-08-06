"""Signal state: cooldowns, deduplication and daily limits.

Quality over quantity is enforced here, not just in the scoring. Without this
layer a genuinely good setup would re-fire on every cycle for as long as the
conditions held, which turns one idea into thirty alerts and destroys the
signal-to-noise ratio the whole system exists to protect.

Four independent brakes:

* **Daily cap** — a hard ceiling on signals per UTC day.
* **Cooldown** — a quiet period after any signal.
* **Reversal cooldown** — a longer quiet period before flipping direction,
  because an immediate flip is usually the system reacting to noise.
* **Price dedupe** — a new signal must be a meaningful ATR distance from the
  last one in the same direction, otherwise it is the same idea restated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Direction, Signal, Veto

log = get_logger("engine.state")


@dataclass(slots=True)
class EmittedSignal:
    ts: datetime
    direction: Direction
    price: float
    signal_id: str


@dataclass(slots=True)
class GateResult:
    allowed: bool
    veto: Veto | None = None

    def __bool__(self) -> bool:
        return self.allowed


class SignalState:
    """Tracks what has already been published and enforces the brakes."""

    def __init__(self, config: Config) -> None:
        self._cfg = config.section("signals")
        self.history: list[EmittedSignal] = []
        self.last_heartbeat: datetime | None = None

    # -- recording ------------------------------------------------------------
    def record(self, signal: Signal) -> None:
        self.history.append(
            EmittedSignal(signal.ts, signal.direction, signal.risk.entry, signal.id)
        )
        # Keep the window bounded; nothing older than a week informs the gates.
        cutoff = signal.ts - timedelta(days=7)
        self.history = [s for s in self.history if s.ts >= cutoff]

    def signals_today(self, now: datetime | None = None) -> list[EmittedSignal]:
        now = now or datetime.now(UTC)
        today: date = now.date()
        return [s for s in self.history if s.ts.date() == today]

    @property
    def last(self) -> EmittedSignal | None:
        return self.history[-1] if self.history else None

    def last_in_direction(self, direction: Direction) -> EmittedSignal | None:
        for entry in reversed(self.history):
            if entry.direction is direction:
                return entry
        return None

    # -- gates ----------------------------------------------------------------
    def check(
        self, direction: Direction, price: float, atr: float, now: datetime | None = None
    ) -> GateResult:
        now = now or datetime.now(UTC)

        max_per_day = int(self._cfg.get("max_signals_per_day", 4))
        emitted_today = self.signals_today(now)
        if len(emitted_today) >= max_per_day:
            return GateResult(
                False,
                Veto("DAILY_LIMIT", f"{len(emitted_today)}/{max_per_day} signals already sent today"),
            )

        last = self.last
        if last is not None:
            elapsed = (now - last.ts).total_seconds() / 60.0

            cooldown = float(self._cfg.get("cooldown_minutes", 45))
            if elapsed < cooldown:
                return GateResult(
                    False,
                    Veto("COOLDOWN", f"{cooldown - elapsed:.0f} min of cooldown remaining"),
                )

            if direction is last.direction.opposite:
                reversal = float(self._cfg.get("opposite_cooldown_minutes", 90))
                if elapsed < reversal:
                    return GateResult(
                        False,
                        Veto(
                            "REVERSAL_COOLDOWN",
                            f"Only {elapsed:.0f} min since the last {last.direction.value} — "
                            f"{reversal:.0f} min required before flipping",
                        ),
                    )

        same_direction = self.last_in_direction(direction)
        if same_direction is not None and atr > 0:
            min_distance = float(self._cfg.get("dedupe_price_atr", 0.75)) * atr
            distance = abs(price - same_direction.price)
            age_hours = (now - same_direction.ts).total_seconds() / 3600.0
            if distance < min_distance and age_hours < 8:
                return GateResult(
                    False,
                    Veto(
                        "DUPLICATE",
                        f"Only ${distance:.2f} from the last {direction.value} at "
                        f"{same_direction.price:.2f} — same idea",
                    ),
                )

        return GateResult(True)

    # -- heartbeat -------------------------------------------------------------
    def heartbeat_due(self, every_minutes: float, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.last_heartbeat is None:
            self.last_heartbeat = now
            return False
        if (now - self.last_heartbeat).total_seconds() / 60.0 >= every_minutes:
            self.last_heartbeat = now
            return True
        return False

    def summary(self, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        last = self.last
        return {
            "today": len(self.signals_today(now)),
            "max_per_day": int(self._cfg.get("max_signals_per_day", 4)),
            "last_signal": {
                "ts": last.ts.isoformat(),
                "direction": last.direction.value,
                "price": round(last.price, 2),
                "id": last.signal_id,
            }
            if last
            else None,
            "total_tracked": len(self.history),
        }
