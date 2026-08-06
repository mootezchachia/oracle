"""Cross-platform market data via Yahoo Finance's public chart endpoint.

MetaTrader 5's Python API only runs on Windows with a live terminal, which
makes it unusable for a containerised monitor. This provider is the portable
default: no key, no account, works anywhere the container has outbound HTTPS.

Two caveats, handled explicitly rather than papered over:

* Yahoo has no true spot-XAU intraday series. ``GC=F`` (COMEX front-month
  gold futures) is the closest liquid proxy and tracks XAUUSD within a few
  dollars of basis; ``XAUUSD=X`` is used for the quoted spot price.
* Yahoo serves no H4 interval, so H4 is aggregated from H1 with UTC-anchored
  buckets.

For live execution, prefer the MT5 provider — your broker's own feed is what
your fills happen against.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Sequence

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, Timeframe
from .base import DataProvider, aggregate

log = get_logger("data.yahoo")

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_TIMEOUT = aiohttp.ClientTimeout(total=25)

# Yahoo interval + the range needed to satisfy typical bar counts.
_INTERVALS: dict[Timeframe, tuple[str, str]] = {
    Timeframe.M1: ("1m", "5d"),
    Timeframe.M5: ("5m", "1mo"),
    Timeframe.M15: ("15m", "1mo"),
    Timeframe.H1: ("1h", "3mo"),
    Timeframe.H4: ("1h", "1y"),      # aggregated locally
}


class YahooProvider(DataProvider):
    name = "yahoo"

    # Yahoo's free intraday endpoints run roughly ten to fifteen minutes behind
    # the live market. That is fine for monitoring and backtesting, and fatal
    # for M1-precision entries — hence the startup warning in the collector.
    feed_delay_seconds = 900

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        cfg = config.section("data.yahoo")
        self.symbol = str(cfg.get("symbol", "GC=F"))
        self.spot_symbol = str(cfg.get("spot_symbol", "XAUUSD=X"))
        self._session = session
        self._owns_session = session is None
        self._lock = asyncio.Semaphore(4)   # be polite to a free endpoint

    @property
    def supports_symbols(self) -> bool:
        return True

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
            self._owns_session = True
        return self._session

    async def connect(self) -> bool:
        try:
            candles = await self.fetch(Timeframe.M5, 5)
        except Exception as exc:  # noqa: BLE001 - provider probe must not raise
            log.warning("Yahoo provider unavailable: %s", exc)
            return False
        if not candles:
            log.warning("Yahoo provider returned no data for %s", self.symbol)
            return False
        log.info("Yahoo provider connected (%s, spot %s)", self.symbol, self.spot_symbol)
        return True

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # -- fetching ------------------------------------------------------------
    async def _request(self, symbol: str, interval: str, range_: str) -> dict[str, Any]:
        session = await self._ensure_session()
        params = {"interval": interval, "range": range_, "includePrePost": "false"}
        url = f"{_BASE}{symbol}"
        async with self._lock:
            async with session.get(
                url, params=params, headers={"User-Agent": "Mozilla/5.0 (compatible; xauusd-sentinel/1.0)"}
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

    @staticmethod
    def _to_candles(payload: dict[str, Any]) -> list[Candle]:
        try:
            result = payload["chart"]["result"][0]
            stamps: Sequence[int] = result["timestamp"]
            quote = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError):
            return []

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        candles: list[Candle] = []
        for i, stamp in enumerate(stamps):
            try:
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            except IndexError:
                continue
            if None in (o, h, l, c):
                continue  # Yahoo emits nulls for illiquid minutes
            volume = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(int(stamp), tz=UTC),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(volume),
                    real_volume=float(volume),
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles

    async def fetch(self, timeframe: Timeframe, count: int) -> list[Candle]:
        return await self.fetch_symbol(self.symbol, timeframe, count)

    async def fetch_symbol(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        interval, range_ = _INTERVALS[timeframe]
        payload = await self._request(symbol, interval, range_)
        candles = self._to_candles(payload)
        if timeframe is Timeframe.H4:
            candles = aggregate(candles, Timeframe.H1, Timeframe.H4)
        return candles[-count:] if count else candles

    async def latest_price(self) -> float | None:
        """Prefer the spot cross for the headline price; fall back to futures."""
        for symbol in (self.spot_symbol, self.symbol):
            try:
                payload = await self._request(symbol, "1m", "1d")
                meta = payload["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                if price:
                    return float(price)
            except Exception as exc:  # noqa: BLE001
                log.debug("price lookup failed for %s: %s", symbol, exc)
        return None
