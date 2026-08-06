"""Broker abstraction and the safety contract every executor must satisfy.

The rules encoded here are not configuration niceties — they are the reason
this layer is safe to switch on:

1. **Demo accounts only, by default.** A broker refuses to arm itself unless
   the connected account reports as a demo account. Trading a live account
   requires deliberately flipping ``require_demo_account`` to ``false``, and
   the system says so loudly every time it starts.
2. **One symbol.** The allowlist is checked against the *resolved broker
   symbol*, not the configured string, so a broker that maps ``GOLD`` to
   something else cannot slip past it.
3. **Every order is tagged.** A magic number identifies this system's
   positions. It never modifies or closes a position it did not open — your
   manual trades on the same account are untouchable.
4. **Nothing is assumed to have worked.** Every order return code is checked
   and surfaced; a rejected order is an error, never a silent no-op.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..models import UTC, Direction, Signal


class AccountType(str, Enum):
    DEMO = "DEMO"
    CONTEST = "CONTEST"
    REAL = "REAL"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class AccountInfo:
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin_free: float
    leverage: int
    account_type: AccountType
    name: str = ""

    @property
    def is_demo(self) -> bool:
        return self.account_type in (AccountType.DEMO, AccountType.CONTEST)

    def to_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "server": self.server,
            "currency": self.currency,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "margin_free": round(self.margin_free, 2),
            "leverage": self.leverage,
            "type": self.account_type.value,
            "is_demo": self.is_demo,
        }


@dataclass(slots=True)
class Position:
    """An open position this system owns."""

    ticket: int
    symbol: str
    direction: Direction
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    open_time: datetime
    signal_id: str = ""
    initial_volume: float = 0.0
    initial_risk: float = 0.0        # per-unit distance from entry to the original stop
    profit: float = 0.0
    partials_taken: int = 0
    break_even_done: bool = False
    trailing: bool = False

    def r_multiple(self, price: float) -> float:
        """Current open profit in R, using the ORIGINAL risk as the unit."""
        if self.initial_risk <= 0:
            return 0.0
        return (price - self.entry_price) * self.direction.sign / self.initial_risk

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "volume": round(self.volume, 2),
            "entry": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "open_time": self.open_time.isoformat(),
            "signal_id": self.signal_id,
            "profit": round(self.profit, 2),
            "partials_taken": self.partials_taken,
            "break_even_done": self.break_even_done,
            "trailing": self.trailing,
        }


@dataclass(slots=True)
class OrderResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    volume: float = 0.0
    retcode: int = 0
    comment: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ticket": self.ticket,
            "price": round(self.price, 2),
            "volume": round(self.volume, 2),
            "retcode": self.retcode,
            "comment": self.comment,
            "error": self.error,
        }


@dataclass(slots=True)
class SymbolSpec:
    """Broker-side constraints. Ignoring these is how orders get rejected."""

    name: str
    digits: int = 2
    point: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: int = 0      # minimum SL/TP distance, in points
    contract_size: float = 100.0
    tick_value: float = 1.0

    @property
    def min_stop_distance(self) -> float:
        """Minimum distance from price to SL/TP, in quote currency."""
        return self.stops_level_points * self.point

    def round_volume(self, volume: float) -> float:
        if self.volume_step <= 0:
            return max(self.volume_min, min(self.volume_max, volume))
        steps = round(volume / self.volume_step)
        rounded = steps * self.volume_step
        # Guard against binary float dust producing 0.30000000000000004 lots.
        rounded = round(rounded, 8)
        return max(self.volume_min, min(self.volume_max, rounded))

    def round_price(self, price: float) -> float:
        return round(price, self.digits)


class Broker(abc.ABC):
    """Everything the execution manager needs from a trading venue."""

    name: str = "broker"

    @abc.abstractmethod
    async def connect(self) -> bool: ...

    @abc.abstractmethod
    async def account(self) -> AccountInfo | None: ...

    @abc.abstractmethod
    async def symbol_spec(self) -> SymbolSpec | None: ...

    @abc.abstractmethod
    async def tick(self) -> tuple[float, float] | None:
        """Current ``(bid, ask)``."""

    @abc.abstractmethod
    async def open_position(
        self, signal: Signal, volume: float, stop_loss: float, take_profit: float
    ) -> OrderResult: ...

    @abc.abstractmethod
    async def positions(self) -> list[Position]:
        """Only positions opened by this system (matched on the magic number)."""

    @abc.abstractmethod
    async def modify_position(
        self, ticket: int, stop_loss: float | None = None, take_profit: float | None = None
    ) -> OrderResult: ...

    @abc.abstractmethod
    async def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        """Close fully, or partially when ``volume`` is given."""

    async def sync_price(self, price: float) -> None:
        """Tell the broker the current mid price.

        A no-op for real venues, which have their own feed. The paper broker
        needs it, and it must happen *before* a signal is acted on — otherwise
        the first order of a session is placed against no price at all.
        """
        return None

    async def close(self) -> None:
        return None


class SafetyError(RuntimeError):
    """Raised when a hard safety precondition is not met.

    These are never caught and retried. If the account is not a demo account,
    or the symbol is not the one you allowlisted, the correct behaviour is to
    stay disarmed and say why.
    """
