"""MetaTrader 5 market data.

This is the provider to use for live trading: the candles come from the same
server your fills happen against, tick volume is genuine, and symbol names
match your broker's.

The ``MetaTrader5`` package is Windows-only and requires a running terminal, so
it is imported lazily inside :meth:`connect`. On any other platform the
provider reports itself unavailable and the chain falls through to Yahoo.

MT5's API is synchronous and blocking, so every call is dispatched to a thread
executor to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, Timeframe
from .base import DataProvider

log = get_logger("data.mt5")

# Broker symbol suffixes vary wildly; these are tried in order.
_SYMBOL_CANDIDATES = ("XAUUSD", "XAUUSD.r", "XAUUSD.m", "XAUUSDm", "GOLD", "XAUUSD_", "XAUUSD.a")


class MT5Provider(DataProvider):
    name = "mt5"

    def __init__(self, config: Config) -> None:
        cfg = config.section("data.mt5")
        self._configured_symbol = str(cfg.get("symbol", "XAUUSD"))
        self._login = cfg.get("login", None)
        self._password = cfg.get("password", None)
        self._server = cfg.get("server", None)
        self._terminal_path = cfg.get("terminal_path", None)
        self._mt5: Any = None
        self.symbol: str = self._configured_symbol
        self._timeframes: dict[Timeframe, int] = {}

    # -- lifecycle -----------------------------------------------------------
    async def connect(self) -> bool:
        return await asyncio.to_thread(self._connect_blocking)

    def _connect_blocking(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError:
            log.info("MetaTrader5 package not installed — skipping MT5 provider")
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
            log.warning("mt5.initialize failed: %s", mt5.last_error())
            return False

        self._mt5 = mt5
        self._timeframes = {
            Timeframe.M1: mt5.TIMEFRAME_M1,
            Timeframe.M5: mt5.TIMEFRAME_M5,
            Timeframe.M15: mt5.TIMEFRAME_M15,
            Timeframe.H1: mt5.TIMEFRAME_H1,
            Timeframe.H4: mt5.TIMEFRAME_H4,
        }

        resolved = self._resolve_symbol()
        if resolved is None:
            log.error("no tradeable gold symbol found on this MT5 server")
            mt5.shutdown()
            self._mt5 = None
            return False

        self.symbol = resolved
        info = mt5.terminal_info()
        log.info(
            "MT5 connected: symbol=%s terminal=%s",
            self.symbol,
            getattr(info, "name", "unknown"),
        )
        return True

    def _resolve_symbol(self) -> str | None:
        """Find the broker's actual gold symbol and make sure it is selected."""
        mt5 = self._mt5
        candidates = [self._configured_symbol, *(_SYMBOL_CANDIDATES)]
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if mt5.symbol_info(candidate) is not None and mt5.symbol_select(candidate, True):
                return candidate

        # Last resort: scan everything the server exposes for a gold cross.
        for symbol in mt5.symbols_get() or []:
            name = symbol.name
            if "XAU" in name.upper() and "USD" in name.upper():
                if mt5.symbol_select(name, True):
                    return name
        return None

    async def close(self) -> None:
        if self._mt5 is not None:
            await asyncio.to_thread(self._mt5.shutdown)
            self._mt5 = None

    @property
    def supports_symbols(self) -> bool:
        return True

    # -- fetching ------------------------------------------------------------
    async def fetch(self, timeframe: Timeframe, count: int) -> list[Candle]:
        return await self.fetch_symbol(self.symbol, timeframe, count)

    async def fetch_symbol(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        if self._mt5 is None:
            return []
        return await asyncio.to_thread(self._copy_rates, symbol, timeframe, count)

    def _copy_rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        mt5 = self._mt5
        native = self._timeframes.get(timeframe)
        if native is None:
            return []
        rates = mt5.copy_rates_from_pos(symbol, native, 0, max(count, 2))
        if rates is None or len(rates) == 0:
            log.debug("copy_rates_from_pos returned nothing for %s %s", symbol, timeframe.value)
            return []

        candles: list[Candle] = []
        for row in rates:
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(int(row["time"]), tz=UTC),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["tick_volume"]),
                    real_volume=float(row["real_volume"]) if "real_volume" in row.dtype.names else 0.0,
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles

    async def latest_price(self) -> float | None:
        if self._mt5 is None:
            return None
        return await asyncio.to_thread(self._tick_price)

    def _tick_price(self) -> float | None:
        tick = self._mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return None
        bid, ask = float(tick.bid), float(tick.ask)
        return (bid + ask) / 2.0 if bid and ask else (bid or ask or None)

    async def spread(self) -> float | None:
        """Live spread in USD — used to sanity-check stop distances."""
        if self._mt5 is None:
            return None
        return await asyncio.to_thread(self._spread_blocking)

    def _spread_blocking(self) -> float | None:
        tick = self._mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return None
        return float(tick.ask) - float(tick.bid)
