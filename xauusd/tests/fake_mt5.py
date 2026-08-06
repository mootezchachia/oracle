"""A fake MetaTrader5 module, faithful enough to test the broker against.

The real package is Windows-only and needs a live terminal, so the execution
path could otherwise only be tested by trading. This stands in for it: the same
constants, the same return-object shapes, and the same failure modes that
actually bite in production — rejected filling modes, minimum stop distances,
and volume steps.

It is installed into ``sys.modules`` as ``MetaTrader5`` by the fixtures in
``test_execution.py``, so ``MT5Broker`` imports it without knowing.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- constants, mirroring the real package ---------------------------------
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 2
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_NO_MONEY = 10019

ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2


@dataclass
class _Account:
    login: int = 5001234
    server: str = "MetaQuotes-Demo"
    currency: str = "USD"
    balance: float = 10_000.0
    equity: float = 10_000.0
    margin_free: float = 9_500.0
    leverage: int = 100
    trade_mode: int = ACCOUNT_TRADE_MODE_DEMO
    name: str = "Test Account"


@dataclass
class _SymbolInfo:
    name: str = "XAUUSD"
    digits: int = 2
    point: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 50.0
    volume_step: float = 0.01
    trade_stops_level: int = 0
    trade_contract_size: float = 100.0
    trade_tick_value: float = 1.0
    filling_mode: int = SYMBOL_FILLING_IOC
    visible: bool = True


@dataclass
class _Tick:
    bid: float = 4000.0
    ask: float = 4000.35


@dataclass
class _Terminal:
    name: str = "FakeTerminal"
    trade_allowed: bool = True


@dataclass
class _Position:
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    time: int
    magic: int
    comment: str
    profit: float = 0.0


@dataclass
class _Result:
    retcode: int
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    comment: str = ""


# --- mutable module state ---------------------------------------------------
class _State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.initialized = False
        self.account = _Account()
        self.symbol = _SymbolInfo()
        self.tick = _Tick()
        self.terminal = _Terminal()
        self.positions: dict[int, _Position] = {}
        self.tickets = itertools.count(1000)
        self.sent: list[dict[str, Any]] = []
        self.known_symbols = ["XAUUSD", "EURUSD"]
        self.fail_next_order: int | None = None
        self.last_error_value = (0, "no error")


state = _State()


# --- API --------------------------------------------------------------------
def initialize(**kwargs: Any) -> bool:
    state.initialized = True
    return True


def shutdown() -> None:
    state.initialized = False


def last_error() -> tuple[int, str]:
    return state.last_error_value


def account_info() -> _Account | None:
    return state.account if state.initialized else None


def terminal_info() -> _Terminal | None:
    return state.terminal if state.initialized else None


def symbol_info(name: str) -> _SymbolInfo | None:
    if name in state.known_symbols:
        info = _SymbolInfo(**{**state.symbol.__dict__, "name": name})
        return info
    return None


def symbol_select(name: str, enable: bool = True) -> bool:
    return name in state.known_symbols


def symbols_get() -> list[_SymbolInfo]:
    return [_SymbolInfo(name=n) for n in state.known_symbols]


def symbol_info_tick(name: str) -> _Tick | None:
    return state.tick if state.initialized else None


def positions_get(symbol: str | None = None) -> list[_Position]:
    values = list(state.positions.values())
    if symbol:
        values = [p for p in values if p.symbol == symbol]
    return values


def order_send(request: dict[str, Any]) -> _Result:
    state.sent.append(dict(request))

    if state.fail_next_order is not None:
        code = state.fail_next_order
        state.fail_next_order = None
        return _Result(retcode=code, comment="rejected by test")

    action = request.get("action")

    if action == TRADE_ACTION_SLTP:
        ticket = int(request.get("position", 0))
        position = state.positions.get(ticket)
        if position is None:
            return _Result(retcode=TRADE_RETCODE_INVALID_STOPS)
        position.sl = float(request.get("sl", position.sl))
        position.tp = float(request.get("tp", position.tp))
        return _Result(retcode=TRADE_RETCODE_DONE, order=ticket)

    if action != TRADE_ACTION_DEAL:
        return _Result(retcode=TRADE_RETCODE_INVALID_STOPS)

    volume = float(request["volume"])
    price = float(request["price"])
    ticket_ref = request.get("position")

    # Closing (or partially closing) an existing position.
    if ticket_ref:
        ticket = int(ticket_ref)
        position = state.positions.get(ticket)
        if position is None:
            return _Result(retcode=TRADE_RETCODE_INVALID_STOPS)
        remaining = round(position.volume - volume, 8)
        if remaining <= 0:
            del state.positions[ticket]
        else:
            position.volume = remaining
        return _Result(retcode=TRADE_RETCODE_DONE, order=ticket, volume=volume, price=price)

    # Opening. Enforce the same rules a real server does.
    minimum = state.symbol.trade_stops_level * state.symbol.point
    sl = float(request.get("sl", 0.0))
    if minimum > 0 and sl and abs(price - sl) < minimum - 1e-9:
        return _Result(retcode=TRADE_RETCODE_INVALID_STOPS, comment="stops too close")

    filling = request.get("type_filling")
    permitted = {ORDER_FILLING_FOK} if state.symbol.filling_mode & SYMBOL_FILLING_FOK else set()
    if state.symbol.filling_mode & SYMBOL_FILLING_IOC:
        permitted.add(ORDER_FILLING_IOC)
    permitted.add(ORDER_FILLING_RETURN)
    if filling not in permitted:
        return _Result(retcode=TRADE_RETCODE_INVALID_FILL, comment="unsupported filling mode")

    ticket = next(state.tickets)
    state.positions[ticket] = _Position(
        ticket=ticket,
        symbol=str(request["symbol"]),
        type=POSITION_TYPE_BUY if request["type"] == ORDER_TYPE_BUY else POSITION_TYPE_SELL,
        volume=volume,
        price_open=price,
        sl=sl,
        tp=float(request.get("tp", 0.0)),
        time=int(datetime.now(timezone.utc).timestamp()),
        magic=int(request.get("magic", 0)),
        comment=str(request.get("comment", "")),
    )
    return _Result(retcode=TRADE_RETCODE_DONE, order=ticket, volume=volume, price=price)
