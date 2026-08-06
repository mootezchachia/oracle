"""Paper broker — a simulated venue with no money and no terminal.

Two jobs:

* **Dry run anywhere.** You can watch the full execution path behave on Linux
  or macOS, days before you point it at a real demo terminal. Every order,
  break-even move, trail and partial close happens for real in software.
* **Test the manager deterministically.** No mocking of MT5 internals needed.

It is deliberately pessimistic in the same ways the backtester is: fills pay
the spread, and a bar that spans both the stop and a target is resolved as the
stop, because bar data cannot order intrabar events.

It is *not* a market simulator. Slippage beyond the fixed spread, requotes,
partial fills, swap and commission are not modelled. Numbers from paper mode
will be slightly better than a demo account, and a demo account will be
slightly better than live.
"""

from __future__ import annotations

import itertools
from datetime import datetime
from typing import Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, Direction, Signal
from .broker import AccountInfo, AccountType, Broker, OrderResult, Position, SymbolSpec

log = get_logger("execution.paper")


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, config: Config) -> None:
        risk_cfg = config.section("risk")
        exec_cfg = config.section("execution")

        self.balance = float(risk_cfg.get("account_balance", 10_000.0))
        self.starting_balance = self.balance
        self.contract_size = float(risk_cfg.get("contract_size", 100))
        self.spread = float(risk_cfg.get("spread_allowance_usd", 0.35))
        self.symbol = str(config.get("symbol", "XAUUSD"))
        self.magic = int(exec_cfg.get("magic", 20260806))

        self._spec = SymbolSpec(
            name=self.symbol,
            volume_min=float(risk_cfg.get("min_lot", 0.01)),
            volume_max=float(risk_cfg.get("max_lot", 10.0)),
            volume_step=float(risk_cfg.get("lot_step", 0.01)),
            contract_size=self.contract_size,
        )
        self._positions: dict[int, Position] = {}
        self._tickets = itertools.count(1)
        self._price: float = 0.0
        self.closed_pnl: float = 0.0
        self.trade_log: list[dict[str, object]] = []

    # -- price feed -----------------------------------------------------------
    def set_price(self, price: float) -> None:
        """Feed the current mid price; the runner calls this every cycle."""
        self._price = price
        self._mark_to_market()

    async def sync_price(self, price: float) -> None:
        self.set_price(price)

    def _mark_to_market(self) -> None:
        for position in self._positions.values():
            move = (self._price - position.entry_price) * position.direction.sign
            position.profit = move * position.volume * self.contract_size

    @property
    def equity(self) -> float:
        return self.balance + sum(p.profit for p in self._positions.values())

    # -- Broker interface -----------------------------------------------------
    async def connect(self) -> bool:
        log.info(
            "paper broker armed — simulated balance %.2f, no real orders will be placed",
            self.balance,
        )
        return True

    async def account(self) -> AccountInfo | None:
        return AccountInfo(
            login=0, server="paper", currency="USD",
            balance=self.balance, equity=self.equity,
            margin_free=self.equity, leverage=100,
            account_type=AccountType.DEMO, name="Paper trading",
        )

    async def symbol_spec(self) -> SymbolSpec | None:
        return self._spec

    async def tick(self) -> tuple[float, float] | None:
        if not self._price:
            return None
        half = self.spread / 2.0
        return self._price - half, self._price + half

    async def open_position(
        self, signal: Signal, volume: float, stop_loss: float, take_profit: float
    ) -> OrderResult:
        if not self._price:
            return OrderResult(False, error="no price available")

        volume = self._spec.round_volume(volume)
        # Pay the spread on entry, as a real fill would.
        fill = self._price + signal.direction.sign * (self.spread / 2.0)
        ticket = next(self._tickets)

        self._positions[ticket] = Position(
            ticket=ticket,
            symbol=self.symbol,
            direction=signal.direction,
            volume=volume,
            entry_price=fill,
            stop_loss=stop_loss,
            take_profit=take_profit,
            open_time=signal.ts,
            signal_id=signal.id,
            initial_volume=volume,
            initial_risk=abs(fill - stop_loss),
        )
        log.info(
            "[PAPER] OPENED %s %.2f lots @ %.2f  SL %.2f  TP %.2f  ticket=%d",
            signal.direction.value, volume, fill, stop_loss, take_profit, ticket,
        )
        return OrderResult(True, ticket=ticket, price=fill, volume=volume)

    async def positions(self) -> list[Position]:
        return list(self._positions.values())

    async def modify_position(
        self, ticket: int, stop_loss: float | None = None, take_profit: float | None = None
    ) -> OrderResult:
        position = self._positions.get(ticket)
        if position is None:
            return OrderResult(False, error=f"position {ticket} not found")
        if stop_loss is not None:
            position.stop_loss = stop_loss
        if take_profit is not None:
            position.take_profit = take_profit
        return OrderResult(True, ticket=ticket)

    async def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        position = self._positions.get(ticket)
        if position is None:
            return OrderResult(False, error=f"position {ticket} not found")

        close_volume = position.volume if volume is None else min(volume, position.volume)
        close_volume = self._spec.round_volume(close_volume)
        remainder = round(position.volume - close_volume, 8)
        if 0 < remainder < self._spec.volume_min:
            close_volume = position.volume
            remainder = 0.0

        fill = self._price - position.direction.sign * (self.spread / 2.0)
        realised = (fill - position.entry_price) * position.direction.sign * close_volume * self.contract_size
        self.balance += realised
        self.closed_pnl += realised

        self.trade_log.append({
            "ticket": ticket,
            "signal_id": position.signal_id,
            "direction": position.direction.value,
            "volume": close_volume,
            "entry": round(position.entry_price, 2),
            "exit": round(fill, 2),
            "pnl": round(realised, 2),
            "r": round(position.r_multiple(fill), 2),
        })
        log.info(
            "[PAPER] CLOSED %.2f lots of %d @ %.2f  P/L %+.2f (%.2fR)  balance %.2f",
            close_volume, ticket, fill, realised, position.r_multiple(fill), self.balance,
        )

        if remainder <= 0:
            del self._positions[ticket]
        else:
            position.volume = remainder
            position.partials_taken += 1
        self._mark_to_market()
        return OrderResult(True, ticket=ticket, price=fill, volume=close_volume)

    # -- simulation -----------------------------------------------------------
    async def apply_bar(self, candle: Candle) -> list[str]:
        """Resolve stops and take-profits against a completed bar.

        A real broker fills stops intrabar; the paper broker needs a bar to
        check against. Stop is checked first: when a bar spans both levels the
        order is unknowable, and assuming the loss is the only honest default.
        """
        events: list[str] = []
        for ticket, position in list(self._positions.items()):
            sign = position.direction.sign

            stop_hit = candle.low <= position.stop_loss if sign > 0 else candle.high >= position.stop_loss
            if stop_hit:
                self._price = position.stop_loss
                await self.close_position(ticket)
                events.append(f"stop hit on {ticket}")
                continue

            if position.take_profit:
                tp_hit = (
                    candle.high >= position.take_profit if sign > 0
                    else candle.low <= position.take_profit
                )
                if tp_hit:
                    self._price = position.take_profit
                    await self.close_position(ticket)
                    events.append(f"take profit hit on {ticket}")
        return events

    def summary(self) -> dict[str, object]:
        return {
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "starting_balance": round(self.starting_balance, 2),
            "closed_pnl": round(self.closed_pnl, 2),
            "return_percent": round(
                (self.balance - self.starting_balance) / self.starting_balance * 100, 2
            ) if self.starting_balance else 0.0,
            "open_positions": len(self._positions),
            "closed_trades": len(self.trade_log),
        }
