"""MetaTrader 5 order execution.

Windows-only, requires a running terminal with **Algo Trading enabled**
(the toolbar button, or Tools → Options → Expert Advisors → Allow algorithmic
trading). The MT5 API is synchronous, so every call is dispatched to a thread.

Three practical details that cause most real-world order rejections, all
handled here rather than left to discover in production:

* **Filling mode is broker-specific.** ``ORDER_FILLING_IOC`` works on many
  brokers and is rejected by others. The correct mode is read from the
  symbol's own ``filling_mode`` bitmask.
* **Stops have a minimum distance.** ``trade_stops_level`` is the closest an
  SL or TP may sit to the current price. A stop inside that band is rejected
  outright, so stops are pushed out to the boundary and the change is logged.
* **Volume must land on the broker's step.** 0.137 lots is not a valid order.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Direction, Signal
from .broker import (
    AccountInfo,
    AccountType,
    Broker,
    OrderResult,
    Position,
    SafetyError,
    SymbolSpec,
)

log = get_logger("execution.mt5")

_SYMBOL_CANDIDATES = ("XAUUSD", "XAUUSD.r", "XAUUSD.m", "XAUUSDm", "GOLD", "XAUUSD_", "XAUUSD.a")


class MT5Broker(Broker):
    name = "mt5"

    def __init__(self, config: Config) -> None:
        cfg = config.section("execution")
        data_cfg = config.section("data.mt5")

        self._configured_symbol = str(data_cfg.get("symbol", "XAUUSD"))
        self._login = data_cfg.get("login", None)
        self._password = data_cfg.get("password", None)
        self._server = data_cfg.get("server", None)
        self._terminal_path = data_cfg.get("terminal_path", None)

        self.magic = int(cfg.get("magic", 20260806))
        self.deviation = int(cfg.get("max_slippage_points", 20))
        self.require_demo = bool(cfg.get("require_demo_account", True))
        self.allowlist = {str(s).upper() for s in cfg.get("symbol_allowlist", ["XAUUSD"])}
        self.comment = str(cfg.get("order_comment", "xauusd-sentinel"))[:31]

        self._mt5: Any = None
        self.symbol: str = self._configured_symbol
        self._spec: SymbolSpec | None = None
        self._account: AccountInfo | None = None

    # -- lifecycle -----------------------------------------------------------
    async def connect(self) -> bool:
        return await asyncio.to_thread(self._connect_blocking)

    def _connect_blocking(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError:
            log.error(
                "MetaTrader5 package not installed — execution unavailable. "
                "This package is Windows-only: pip install -r requirements-mt5.txt"
            )
            return False

        kwargs: dict[str, Any] = {}
        if self._terminal_path:
            kwargs["path"] = str(self._terminal_path)
        if self._login:
            kwargs["login"] = int(self._login)
        if self._password:
            kwargs["password"] = str(self._password)
        if self._server:
            kwargs["server"] = str(self._server)

        if not mt5.initialize(**kwargs):
            log.error("mt5.initialize failed: %s", mt5.last_error())
            return False

        self._mt5 = mt5

        # --- SAFETY GATE 1: the account must be a demo account --------------
        account = self._read_account()
        if account is None:
            mt5.shutdown()
            self._mt5 = None
            raise SafetyError("could not read account info — refusing to arm execution")

        self._account = account
        if self.require_demo and not account.is_demo:
            mt5.shutdown()
            self._mt5 = None
            raise SafetyError(
                f"account {account.login} on {account.server} reports as "
                f"{account.account_type.value}, not a demo account. Execution stays "
                f"disarmed. Set execution.require_demo_account=false only if you "
                f"genuinely intend to trade real money with this system."
            )
        if not self.require_demo and not account.is_demo:
            log.critical(
                "=" * 70 + "\n"
                "  LIVE ACCOUNT EXECUTION IS ARMED — account %s on %s\n"
                "  Real money is at risk. The demo-account safety check is OFF.\n"
                + "=" * 70,
                account.login, account.server,
            )

        # --- SAFETY GATE 2: the symbol must be on the allowlist -------------
        resolved = self._resolve_symbol()
        if resolved is None:
            mt5.shutdown()
            self._mt5 = None
            raise SafetyError("no tradeable gold symbol found on this server")

        normalised = resolved.upper().replace(".", "").replace("_", "")
        if not any(allowed.replace(".", "") in normalised for allowed in self.allowlist):
            mt5.shutdown()
            self._mt5 = None
            raise SafetyError(
                f"resolved symbol {resolved!r} is not on the allowlist "
                f"{sorted(self.allowlist)} — refusing to trade it"
            )

        self.symbol = resolved
        self._spec = self._read_spec()

        # --- SAFETY GATE 3: algo trading must actually be permitted ---------
        terminal = mt5.terminal_info()
        if terminal is not None and not getattr(terminal, "trade_allowed", True):
            log.error(
                "Algo Trading is disabled in the terminal. Enable the toolbar "
                "button (or Tools -> Options -> Expert Advisors) — orders will "
                "be rejected until you do."
            )
            return False

        log.info(
            "MT5 execution armed: %s account %s on %s, symbol=%s, balance=%.2f %s, magic=%d",
            account.account_type.value, account.login, account.server,
            self.symbol, account.balance, account.currency, self.magic,
        )
        return True

    async def close(self) -> None:
        if self._mt5 is not None:
            await asyncio.to_thread(self._mt5.shutdown)
            self._mt5 = None

    # -- reads ---------------------------------------------------------------
    def _read_account(self) -> AccountInfo | None:
        info = self._mt5.account_info()
        if info is None:
            return None
        trade_mode = getattr(info, "trade_mode", None)
        mapping = {
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_DEMO", 0): AccountType.DEMO,
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1): AccountType.CONTEST,
            getattr(self._mt5, "ACCOUNT_TRADE_MODE_REAL", 2): AccountType.REAL,
        }
        # An unrecognised trade_mode is treated as REAL. Guessing "probably
        # demo" on an unknown value is exactly the wrong default.
        account_type = mapping.get(trade_mode, AccountType.REAL)
        return AccountInfo(
            login=int(getattr(info, "login", 0)),
            server=str(getattr(info, "server", "")),
            currency=str(getattr(info, "currency", "USD")),
            balance=float(getattr(info, "balance", 0.0)),
            equity=float(getattr(info, "equity", 0.0)),
            margin_free=float(getattr(info, "margin_free", 0.0)),
            leverage=int(getattr(info, "leverage", 0)),
            account_type=account_type,
            name=str(getattr(info, "name", "")),
        )

    def _resolve_symbol(self) -> str | None:
        mt5 = self._mt5
        for candidate in [self._configured_symbol, *_SYMBOL_CANDIDATES]:
            if mt5.symbol_info(candidate) is not None and mt5.symbol_select(candidate, True):
                return candidate
        for symbol in mt5.symbols_get() or []:
            name = symbol.name
            if "XAU" in name.upper() and "USD" in name.upper() and mt5.symbol_select(name, True):
                return name
        return None

    def _read_spec(self) -> SymbolSpec | None:
        info = self._mt5.symbol_info(self.symbol)
        if info is None:
            return None
        return SymbolSpec(
            name=self.symbol,
            digits=int(getattr(info, "digits", 2)),
            point=float(getattr(info, "point", 0.01)),
            volume_min=float(getattr(info, "volume_min", 0.01)),
            volume_max=float(getattr(info, "volume_max", 100.0)),
            volume_step=float(getattr(info, "volume_step", 0.01)),
            stops_level_points=int(getattr(info, "trade_stops_level", 0)),
            contract_size=float(getattr(info, "trade_contract_size", 100.0)),
            tick_value=float(getattr(info, "trade_tick_value", 1.0)),
        )

    async def account(self) -> AccountInfo | None:
        if self._mt5 is None:
            return self._account
        self._account = await asyncio.to_thread(self._read_account)
        return self._account

    async def symbol_spec(self) -> SymbolSpec | None:
        return self._spec

    async def tick(self) -> tuple[float, float] | None:
        if self._mt5 is None:
            return None
        return await asyncio.to_thread(self._tick_blocking)

    def _tick_blocking(self) -> tuple[float, float] | None:
        tick = self._mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return None
        return float(tick.bid), float(tick.ask)

    # -- filling mode --------------------------------------------------------
    def _filling_mode(self) -> int:
        """Pick a filling mode the broker actually accepts.

        ``symbol_info.filling_mode`` is a bitmask of permitted modes. Sending
        an unsupported one is a guaranteed rejection with an opaque retcode.
        """
        mt5 = self._mt5
        info = mt5.symbol_info(self.symbol)
        mask = int(getattr(info, "filling_mode", 0)) if info else 0

        if mask & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            return mt5.ORDER_FILLING_FOK
        if mask & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _clamp_stops(
        self, direction: Direction, price: float, stop_loss: float, take_profit: float
    ) -> tuple[float, float]:
        """Push SL/TP outside the broker's minimum-distance band."""
        spec = self._spec
        if spec is None:
            return stop_loss, take_profit
        minimum = spec.min_stop_distance
        if minimum <= 0:
            return spec.round_price(stop_loss), spec.round_price(take_profit)

        sign = direction.sign
        if abs(price - stop_loss) < minimum:
            adjusted = price - sign * minimum
            log.warning(
                "stop %.2f is inside the broker's %.2f minimum distance — moved to %.2f",
                stop_loss, minimum, adjusted,
            )
            stop_loss = adjusted
        if take_profit and abs(take_profit - price) < minimum:
            take_profit = price + sign * minimum
        return spec.round_price(stop_loss), spec.round_price(take_profit)

    # -- orders --------------------------------------------------------------
    async def open_position(
        self, signal: Signal, volume: float, stop_loss: float, take_profit: float
    ) -> OrderResult:
        if self._mt5 is None:
            return OrderResult(False, error="MT5 not connected")
        return await asyncio.to_thread(
            self._open_blocking, signal, volume, stop_loss, take_profit
        )

    def _open_blocking(
        self, signal: Signal, volume: float, stop_loss: float, take_profit: float
    ) -> OrderResult:
        mt5 = self._mt5
        spec = self._spec

        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return OrderResult(False, error="no tick data")

        is_buy = signal.direction is Direction.BUY
        price = float(tick.ask if is_buy else tick.bid)
        volume = spec.round_volume(volume) if spec else round(volume, 2)
        stop_loss, take_profit = self._clamp_stops(signal.direction, price, stop_loss, take_profit)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"{self.comment} {signal.id}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, error=f"order_send returned None: {mt5.last_error()}")

        retcode = int(result.retcode)
        if retcode != mt5.TRADE_RETCODE_DONE:
            log.error(
                "order rejected: retcode=%s comment=%s request=%s",
                retcode, getattr(result, "comment", ""), request,
            )
            return OrderResult(
                False, retcode=retcode, comment=str(getattr(result, "comment", "")),
                error=f"broker rejected the order (retcode {retcode})",
            )

        log.info(
            "OPENED %s %.2f lots %s @ %.2f  SL %.2f  TP %.2f  ticket=%s",
            signal.direction.value, float(result.volume), self.symbol,
            float(result.price), stop_loss, take_profit, result.order,
        )
        return OrderResult(
            True, ticket=int(result.order), price=float(result.price),
            volume=float(result.volume), retcode=retcode,
            comment=str(getattr(result, "comment", "")),
        )

    async def positions(self) -> list[Position]:
        if self._mt5 is None:
            return []
        return await asyncio.to_thread(self._positions_blocking)

    def _positions_blocking(self) -> list[Position]:
        raw = self._mt5.positions_get(symbol=self.symbol)
        if raw is None:
            return []
        out: list[Position] = []
        for item in raw:
            # Never touch a position this system did not open.
            if int(getattr(item, "magic", 0)) != self.magic:
                continue
            direction = (
                Direction.BUY
                if int(item.type) == self._mt5.POSITION_TYPE_BUY
                else Direction.SELL
            )
            comment = str(getattr(item, "comment", ""))
            signal_id = comment.split()[-1] if " " in comment else ""
            out.append(
                Position(
                    ticket=int(item.ticket),
                    symbol=str(item.symbol),
                    direction=direction,
                    volume=float(item.volume),
                    entry_price=float(item.price_open),
                    stop_loss=float(item.sl),
                    take_profit=float(item.tp),
                    open_time=datetime.fromtimestamp(int(item.time), tz=UTC),
                    signal_id=signal_id,
                    profit=float(item.profit),
                )
            )
        return out

    async def modify_position(
        self, ticket: int, stop_loss: float | None = None, take_profit: float | None = None
    ) -> OrderResult:
        if self._mt5 is None:
            return OrderResult(False, error="MT5 not connected")
        return await asyncio.to_thread(self._modify_blocking, ticket, stop_loss, take_profit)

    def _modify_blocking(
        self, ticket: int, stop_loss: float | None, take_profit: float | None
    ) -> OrderResult:
        mt5 = self._mt5
        current = [p for p in self._positions_blocking() if p.ticket == ticket]
        if not current:
            return OrderResult(False, error=f"position {ticket} not found or not ours")
        position = current[0]

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": ticket,
            "sl": self._spec.round_price(stop_loss) if (self._spec and stop_loss is not None) else (stop_loss if stop_loss is not None else position.stop_loss),
            "tp": self._spec.round_price(take_profit) if (self._spec and take_profit is not None) else (take_profit if take_profit is not None else position.take_profit),
            "magic": self.magic,
        }
        result = mt5.order_send(request)
        if result is None or int(result.retcode) != mt5.TRADE_RETCODE_DONE:
            retcode = int(result.retcode) if result is not None else -1
            return OrderResult(False, ticket=ticket, retcode=retcode, error="modify rejected")
        log.info("modified %s: SL=%s TP=%s", ticket, request["sl"], request["tp"])
        return OrderResult(True, ticket=ticket, retcode=int(result.retcode))

    async def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        if self._mt5 is None:
            return OrderResult(False, error="MT5 not connected")
        return await asyncio.to_thread(self._close_blocking, ticket, volume)

    def _close_blocking(self, ticket: int, volume: float | None) -> OrderResult:
        mt5 = self._mt5
        current = [p for p in self._positions_blocking() if p.ticket == ticket]
        if not current:
            return OrderResult(False, error=f"position {ticket} not found or not ours")
        position = current[0]

        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            return OrderResult(False, error="no tick data")

        closing_buy = position.direction is Direction.SELL
        close_volume = position.volume if volume is None else min(volume, position.volume)
        if self._spec:
            close_volume = self._spec.round_volume(close_volume)
            # A partial that would leave a sub-minimum remainder must close all.
            remainder = round(position.volume - close_volume, 8)
            if 0 < remainder < self._spec.volume_min:
                close_volume = position.volume

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": close_volume,
            "type": mt5.ORDER_TYPE_BUY if closing_buy else mt5.ORDER_TYPE_SELL,
            "position": ticket,
            "price": float(tick.ask if closing_buy else tick.bid),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"{self.comment} close"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        result = mt5.order_send(request)
        if result is None or int(result.retcode) != mt5.TRADE_RETCODE_DONE:
            retcode = int(result.retcode) if result is not None else -1
            log.error("close rejected for %s: retcode=%s", ticket, retcode)
            return OrderResult(False, ticket=ticket, retcode=retcode, error="close rejected")

        log.info("CLOSED %.2f lots of ticket %s @ %.2f", close_volume, ticket, float(result.price))
        return OrderResult(
            True, ticket=ticket, price=float(result.price),
            volume=close_volume, retcode=int(result.retcode),
        )
