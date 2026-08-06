"""Execution manager — decides whether a signal becomes an order, then runs it.

A signal clearing the confidence floor is necessary but not sufficient to
trade. This layer adds the account-level protections that a per-signal score
cannot express:

* **Daily loss limit.** Once the account is down by the configured percentage,
  execution disarms for the rest of the UTC day. Every strategy has losing
  days; the ones that end careers are the days you kept trading.
* **Position cap.** By default one position at a time. Two correlated XAUUSD
  positions is one position at double the intended risk.
* **Daily trade cap.** Independent of the signal engine's own cap.
* **Kill switch.** A file on disk or an env var stops all new entries
  immediately, without restarting or editing config, and optionally flattens.
* **Equity sanity check.** If free margin cannot cover the order, it is
  declined rather than sent to be rejected.

Once a position is live it is managed to the plan the signal published:
break-even at the configured R, partial closes at TP1/TP2, and an ATR trail
after that. The broker always holds a hard stop, so a crashed monitor leaves a
protected position rather than a naked one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Direction, Signal
from .broker import AccountInfo, Broker, OrderResult, Position

log = get_logger("execution.manager")


@dataclass(slots=True)
class ExecutionDecision:
    """Why an order was or was not placed."""

    executed: bool
    reason: str
    result: OrderResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "reason": self.reason,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass(slots=True)
class DayState:
    day: date
    trades: int = 0
    realised: float = 0.0
    start_equity: float = 0.0


class ExecutionManager:
    def __init__(self, config: Config, broker: Broker) -> None:
        cfg = config.section("execution")
        self._config = config
        self._cfg = cfg
        self.broker = broker

        self.enabled = bool(cfg.get("enabled", False))
        self.max_open = int(cfg.get("max_open_positions", 1))
        self.max_daily_trades = int(cfg.get("max_daily_trades", 4))
        self.max_daily_loss_percent = float(cfg.get("max_daily_loss_percent", 3.0))
        self.min_free_margin_percent = float(cfg.get("min_free_margin_percent", 30.0))
        self.kill_switch_file = cfg.get("kill_switch_file", None)

        manage = cfg.section("manage")
        self.break_even_at_r = float(manage.get("break_even_at_r", 1.0))
        self.break_even_offset_r = float(manage.get("break_even_offset_r", 0.1))
        self.trail_after_r = float(manage.get("trail_after_r", 1.5))
        self.trail_atr_multiple = float(manage.get("trail_atr_multiple", 1.2))
        self.partial_closes = bool(manage.get("partial_closes", True))
        self.partial_percents = [float(p) for p in config.get("risk.partial_percents", [50, 30, 20])]

        self.armed = False
        self.account: AccountInfo | None = None
        self.day = DayState(day=datetime.now(UTC).date())
        self.plans: dict[int, dict[str, Any]] = {}      # ticket -> the signal's plan
        self.last_decision: ExecutionDecision | None = None
        self.disarm_reason: str = ""

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> bool:
        if not self.enabled:
            log.info("execution is disabled — signals will be published but not traded")
            return False
        try:
            ok = await self.broker.connect()
        except Exception as exc:  # SafetyError and anything the broker raises
            self.disarm_reason = str(exc)
            log.critical("EXECUTION DISARMED: %s", exc)
            return False
        if not ok:
            self.disarm_reason = "broker failed to connect"
            log.error("execution disarmed: %s", self.disarm_reason)
            return False

        self.account = await self.broker.account()
        if self.account is not None:
            self.day = DayState(day=datetime.now(UTC).date(), start_equity=self.account.equity)
        self.armed = True
        return True

    async def close(self) -> None:
        await self.broker.close()

    async def sync_price(self, price: float | None) -> None:
        """Keep the broker's view of price current before any order decision."""
        if price and self.armed:
            await self.broker.sync_price(price)

    # -- gates ----------------------------------------------------------------
    def kill_switch_active(self) -> tuple[bool, str]:
        if os.environ.get("XAUUSD_KILL_SWITCH", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True, "kill switch set via XAUUSD_KILL_SWITCH"
        if self.kill_switch_file and Path(str(self.kill_switch_file)).exists():
            return True, f"kill switch file present at {self.kill_switch_file}"
        return False, ""

    def _roll_day(self, now: datetime, equity: float) -> None:
        """Reset the daily counters — but only ever *forward*.

        Rolling on any date mismatch would let a clock that jumps backwards
        (NTP correction, a VM resuming from a snapshot, a bad timezone) wipe a
        daily loss limit that was doing its job. Time going backwards is never
        a reason to re-arm.
        """
        if now.date() > self.day.day:
            log.info(
                "new trading day — resetting daily counters (previous: %d trades, %+.2f realised)",
                self.day.trades, self.day.realised,
            )
            self.day = DayState(day=now.date(), start_equity=equity)
        elif now.date() < self.day.day:
            log.warning(
                "clock moved backwards (%s < %s) — keeping today's counters in force",
                now.date(), self.day.day,
            )

    async def _gate(self, signal: Signal, now: datetime) -> str | None:
        """Return a refusal reason, or ``None`` when the order may proceed."""
        if not self.armed:
            return self.disarm_reason or "execution not armed"

        killed, reason = self.kill_switch_active()
        if killed:
            return reason

        account = await self.broker.account()
        if account is None:
            return "cannot read account state"
        self.account = account
        self._roll_day(now, account.equity)

        if self.day.start_equity <= 0:
            self.day.start_equity = account.equity

        drawdown = (self.day.start_equity - account.equity) / self.day.start_equity * 100.0
        if drawdown >= self.max_daily_loss_percent:
            return (
                f"daily loss limit reached ({drawdown:.2f}% of "
                f"{self.day.start_equity:.2f}) — disarmed until tomorrow"
            )

        if self.day.trades >= self.max_daily_trades:
            return f"daily trade cap reached ({self.day.trades}/{self.max_daily_trades})"

        open_positions = await self.broker.positions()
        if len(open_positions) >= self.max_open:
            return f"already holding {len(open_positions)}/{self.max_open} positions"

        # Never stack an opposing position against an existing one.
        for position in open_positions:
            if position.direction is signal.direction.opposite:
                return f"opposing position {position.ticket} is still open"

        if account.equity > 0:
            free_ratio = account.margin_free / account.equity * 100.0
            if free_ratio < self.min_free_margin_percent:
                return f"free margin {free_ratio:.0f}% below the {self.min_free_margin_percent:.0f}% floor"

        return None

    # -- entry ----------------------------------------------------------------
    async def on_signal(self, signal: Signal, now: datetime | None = None) -> ExecutionDecision:
        now = now or datetime.now(UTC)

        if not self.enabled:
            decision = ExecutionDecision(False, "execution disabled")
            self.last_decision = decision
            return decision

        refusal = await self._gate(signal, now)
        if refusal is not None:
            log.warning("not trading signal %s: %s", signal.id, refusal)
            decision = ExecutionDecision(False, refusal)
            self.last_decision = decision
            return decision

        plan = signal.risk
        # The broker holds the hard stop and the final target, so a crashed
        # monitor leaves a protected position rather than a naked one.
        final_target = plan.take_profits[-1] if plan.take_profits else 0.0

        result = await self.broker.open_position(
            signal, plan.lot_size, plan.stop_loss, final_target
        )
        if not result.ok:
            decision = ExecutionDecision(False, result.error or "order rejected", result)
            self.last_decision = decision
            return decision

        self.day.trades += 1
        self.plans[result.ticket] = {
            "signal_id": signal.id,
            "direction": signal.direction,
            "entry": result.price,
            "stop_loss": plan.stop_loss,
            "take_profits": list(plan.take_profits),
            "initial_risk": abs(result.price - plan.stop_loss),
            "initial_volume": result.volume,
            "partials_taken": 0,
            "break_even_done": False,
            "atr": plan.atr,
        }
        decision = ExecutionDecision(
            True, f"opened {result.volume:.2f} lots at {result.price:.2f}", result
        )
        self.last_decision = decision
        return decision

    # -- management -----------------------------------------------------------
    async def manage(self, now: datetime | None = None, price: float | None = None) -> list[str]:
        """Run break-even, partial closes and trailing on every open position."""
        if not self.armed:
            return []
        now = now or datetime.now(UTC)
        actions: list[str] = []

        positions = await self.broker.positions()
        live_tickets = {p.ticket for p in positions}
        for ticket in list(self.plans):
            if ticket not in live_tickets:
                log.info("position %s closed at the broker — dropping its plan", ticket)
                del self.plans[ticket]

        if price is None:
            tick = await self.broker.tick()
            if tick is None:
                return actions
            price = (tick[0] + tick[1]) / 2.0

        for position in positions:
            plan = self.plans.get(position.ticket)
            if plan is None:
                # A position we own but have no plan for (monitor restarted).
                # Leave it strictly alone — its broker stop is still in force.
                continue

            risk = float(plan["initial_risk"])
            if risk <= 0:
                continue
            direction: Direction = plan["direction"]
            entry = float(plan["entry"])
            r_now = (price - entry) * direction.sign / risk

            actions.extend(await self._partials(position, plan, r_now, price))
            actions.extend(await self._break_even(position, plan, r_now, entry, risk, direction))
            actions.extend(await self._trail(position, plan, r_now, price, direction))

        return actions

    async def _partials(
        self, position: Position, plan: dict[str, Any], r_now: float, price: float
    ) -> list[str]:
        if not self.partial_closes:
            return []
        targets: Sequence[float] = plan["take_profits"]
        taken = int(plan["partials_taken"])
        actions: list[str] = []

        # Only the first N-1 targets are scaled out; the runner rides the last.
        for index in range(taken, max(len(targets) - 1, 0)):
            target = targets[index]
            reached = price >= target if plan["direction"] is Direction.BUY else price <= target
            if not reached:
                break

            share = self.partial_percents[index] if index < len(self.partial_percents) else 33.0
            volume = float(plan["initial_volume"]) * share / 100.0
            result = await self.broker.close_position(position.ticket, volume)
            if result.ok:
                plan["partials_taken"] = index + 1
                actions.append(
                    f"TP{index + 1} hit at {target:.2f} — closed {share:.0f}% of ticket {position.ticket}"
                )
                log.info(actions[-1])
            else:
                log.error("partial close failed on %s: %s", position.ticket, result.error)
                break
        return actions

    async def _break_even(
        self,
        position: Position,
        plan: dict[str, Any],
        r_now: float,
        entry: float,
        risk: float,
        direction: Direction,
    ) -> list[str]:
        if plan["break_even_done"] or r_now < self.break_even_at_r:
            return []
        # Offset slightly beyond entry so a break-even exit still covers costs.
        target = entry + direction.sign * risk * self.break_even_offset_r
        improves = (
            target > position.stop_loss if direction is Direction.BUY else target < position.stop_loss
        )
        if not improves:
            plan["break_even_done"] = True
            return []

        result = await self.broker.modify_position(position.ticket, stop_loss=target)
        if result.ok:
            plan["break_even_done"] = True
            message = f"moved ticket {position.ticket} to break-even at {target:.2f} (+{r_now:.2f}R)"
            log.info(message)
            return [message]
        log.error("break-even move failed on %s: %s", position.ticket, result.error)
        return []

    async def _trail(
        self,
        position: Position,
        plan: dict[str, Any],
        r_now: float,
        price: float,
        direction: Direction,
    ) -> list[str]:
        if r_now < self.trail_after_r:
            return []
        atr = float(plan.get("atr") or 0.0)
        if atr <= 0:
            return []

        target = price - direction.sign * atr * self.trail_atr_multiple
        # A trailing stop only ever moves in the profitable direction.
        improves = (
            target > position.stop_loss if direction is Direction.BUY else target < position.stop_loss
        )
        if not improves:
            return []

        result = await self.broker.modify_position(position.ticket, stop_loss=target)
        if result.ok:
            message = f"trailed ticket {position.ticket} stop to {target:.2f} ({r_now:.2f}R open)"
            log.info(message)
            return [message]
        return []

    # -- manual controls -------------------------------------------------------
    async def flatten(self, reason: str = "manual") -> list[OrderResult]:
        """Close everything this system owns. Used by the kill switch."""
        results: list[OrderResult] = []
        for position in await self.broker.positions():
            log.warning("flattening ticket %s (%s)", position.ticket, reason)
            results.append(await self.broker.close_position(position.ticket))
        self.plans.clear()
        return results

    # -- reporting -------------------------------------------------------------
    async def snapshot(self) -> dict[str, Any]:
        killed, kill_reason = self.kill_switch_active()
        positions = await self.broker.positions() if self.armed else []
        return {
            "enabled": self.enabled,
            "armed": self.armed,
            "broker": self.broker.name,
            "disarm_reason": self.disarm_reason,
            "kill_switch": {"active": killed, "reason": kill_reason},
            "account": self.account.to_dict() if self.account else None,
            "positions": [p.to_dict() for p in positions],
            "day": {
                "date": self.day.day.isoformat(),
                "trades": self.day.trades,
                "max_trades": self.max_daily_trades,
                "start_equity": round(self.day.start_equity, 2),
                "max_loss_percent": self.max_daily_loss_percent,
            },
            "last_decision": self.last_decision.to_dict() if self.last_decision else None,
        }
