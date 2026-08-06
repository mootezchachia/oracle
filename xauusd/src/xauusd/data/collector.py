"""Asynchronous market-data collection.

Owns the provider chain, refreshes every timeframe concurrently, keeps the
correlation instruments up to date, and exposes a single consistent snapshot to
the signal engine.

Design notes
------------
* **Provider chain, not a single provider.** MT5 is preferred when present;
  Yahoo is the portable fallback. If the active provider starts failing, the
  collector fails over rather than going quiet.
* **Concurrent by timeframe.** All five timeframes are fetched in one
  ``asyncio.gather``, so a cycle costs one round-trip, not five.
* **Failures are visible.** A silent data stall is worse than an outage, so
  staleness is tracked per timeframe and surfaced to the engine, which vetoes
  on stale data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Mapping, Sequence

import aiohttp

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, Timeframe
from .base import DataProvider, MarketStore
from .mt5_provider import MT5Provider
from .yahoo_provider import YahooProvider

log = get_logger("data.collector")

_PROVIDER_FACTORIES = {
    "mt5": MT5Provider,
    "yahoo": YahooProvider,
}


class MarketDataCollector:
    """Keeps a :class:`MarketStore` fed from the best available provider."""

    def __init__(self, config: Config, session: aiohttp.ClientSession | None = None) -> None:
        self._config = config
        self._cfg = config.section("data")
        self._session = session
        self.store = MarketStore(self._cfg.get("history", {}) or {})
        self.provider: DataProvider | None = None
        self.fallbacks: list[DataProvider] = []
        self.correlations: dict[str, list[Candle]] = {}
        self.last_price: float | None = None
        self.last_error: str | None = None
        self.last_cycle: datetime | None = None
        self._corr_cfg = config.section("correlation")
        self._corr_last: datetime | None = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> bool:
        """Build the provider chain. Returns ``True`` if any provider works."""
        names = list(self._cfg.get("providers", ["yahoo"]))
        built: list[DataProvider] = []
        for name in names:
            factory = _PROVIDER_FACTORIES.get(str(name).lower())
            if factory is None:
                log.warning("unknown provider %r — ignored", name)
                continue
            provider = (
                factory(self._config, self._session)      # type: ignore[call-arg]
                if factory is YahooProvider
                else factory(self._config)                # type: ignore[call-arg]
            )
            try:
                ok = await provider.connect()
            except Exception as exc:  # noqa: BLE001
                log.warning("provider %s failed to connect: %s", name, exc)
                ok = False
            if ok:
                built.append(provider)
            else:
                await provider.close()

        if not built:
            self.last_error = "no market-data provider available"
            log.error(self.last_error)
            return False

        self.provider, self.fallbacks = built[0], built[1:]
        log.info(
            "market data: primary=%s fallbacks=%s",
            self.provider.name,
            [p.name for p in self.fallbacks] or "none",
        )
        if self.provider.feed_delay_seconds > 300:
            log.warning(
                "%s is a delayed feed (~%d min behind). Signals stay valid, but M1 "
                "entry precision is not achievable on it — use the MT5 provider "
                "against your broker's own feed for live execution.",
                self.provider.name, self.provider.feed_delay_seconds // 60,
            )
        return True

    async def close(self) -> None:
        for provider in filter(None, [self.provider, *self.fallbacks]):
            await provider.close()

    # -- collection ----------------------------------------------------------
    async def refresh(self, now: datetime | None = None) -> bool:
        """Fetch every timeframe concurrently. Returns ``True`` on success."""
        if self.provider is None:
            return False
        now = now or datetime.now(UTC)
        history = self._cfg.get("history", {}) or {}

        timeframes = list(Timeframe)
        counts = [int(history.get(tf.value, 300)) for tf in timeframes]

        results = await asyncio.gather(
            *(self._fetch_with_failover(tf, count) for tf, count in zip(timeframes, counts)),
            return_exceptions=True,
        )

        added_total = 0
        failures: list[str] = []
        for timeframe, result in zip(timeframes, results):
            if isinstance(result, BaseException):
                failures.append(f"{timeframe.value}: {result}")
                continue
            added_total += self.store.update(timeframe, result, now)

        if failures:
            self.last_error = "; ".join(failures)
            log.warning("data refresh partial failure: %s", self.last_error)
        else:
            self.last_error = None

        try:
            price = await self.provider.latest_price()
            if price:
                self.last_price = price
        except Exception as exc:  # noqa: BLE001
            log.debug("latest price unavailable: %s", exc)

        if self.last_price is None:
            self.last_price = self.store.price()

        self.last_cycle = now
        if added_total:
            log.debug("data refresh added %d new bars", added_total)
        return not failures

    async def _fetch_with_failover(self, timeframe: Timeframe, count: int) -> list[Candle]:
        assert self.provider is not None
        try:
            candles = await self.provider.fetch(timeframe, count)
            if candles:
                return candles
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed on %s: %s", self.provider.name, timeframe.value, exc)

        for provider in self.fallbacks:
            try:
                candles = await provider.fetch(timeframe, count)
                if candles:
                    log.info("failed over to %s for %s", provider.name, timeframe.value)
                    return candles
            except Exception as exc:  # noqa: BLE001
                log.debug("fallback %s failed on %s: %s", provider.name, timeframe.value, exc)
        return []

    # -- correlated instruments ---------------------------------------------
    async def refresh_correlations(self, now: datetime | None = None, force: bool = False) -> dict[str, list[Candle]]:
        """Refresh DXY / US10Y / Silver / SP500 / NASDAQ on their own cadence."""
        if not self._corr_cfg.get("enabled", True):
            return {}
        now = now or datetime.now(UTC)
        interval = float(self._corr_cfg.get("refresh_seconds", 120))
        if not force and self._corr_last and (now - self._corr_last).total_seconds() < interval:
            return self.correlations

        instruments: Mapping[str, Mapping[str, object]] = self._corr_cfg.get("instruments", {}) or {}
        provider = self._symbol_capable_provider()
        if provider is None:
            return self.correlations

        lookback = int(self._corr_cfg.get("lookback_bars", 30))
        names = list(instruments)

        async def _one(name: str) -> tuple[str, list[Candle]]:
            spec = instruments[name]
            symbol = str(spec.get("yahoo") or spec.get("symbol") or name)
            try:
                candles = await provider.fetch_symbol(symbol, Timeframe.M15, lookback * 2)
                return name, candles
            except Exception as exc:  # noqa: BLE001
                log.debug("correlation fetch failed for %s (%s): %s", name, symbol, exc)
                return name, []

        results = await asyncio.gather(*(_one(name) for name in names), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                continue
            name, candles = result
            if candles:
                self.correlations[name] = candles

        self._corr_last = now
        return self.correlations

    def _symbol_capable_provider(self) -> DataProvider | None:
        for provider in filter(None, [self.provider, *self.fallbacks]):
            if provider.supports_symbols:
                return provider
        return None

    # -- health --------------------------------------------------------------
    @property
    def feed_delay(self) -> int:
        """The active provider's inherent lag, in seconds."""
        return self.provider.feed_delay_seconds if self.provider else 0

    def stale_timeframes(self, now: datetime | None = None) -> list[Timeframe]:
        """Timeframes whose feed has gone quiet for longer than expected.

        The threshold accounts for the provider's *inherent* delay. A free
        public feed that is fifteen minutes behind is delayed, not broken —
        treating the two the same would veto every signal forever on the
        fallback provider, which reads as a selective system but is really a
        dead one.
        """
        now = now or datetime.now(UTC)
        limit = int(self._cfg.get("stale_after_seconds", 180))
        delay = self.feed_delay
        stale: list[Timeframe] = []
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4):
            threshold = max(limit, timeframe.seconds * 2) + timeframe.seconds + delay
            candle = self.store.last(timeframe)
            if candle is None:
                stale.append(timeframe)
                continue
            if (now - candle.ts).total_seconds() > threshold:
                stale.append(timeframe)
        return stale

    def health(self, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        return {
            "provider": self.provider.name if self.provider else None,
            "fallbacks": [p.name for p in self.fallbacks],
            "price": self.last_price,
            "last_cycle": self.last_cycle.isoformat() if self.last_cycle else None,
            "store": self.store.summary(),
            "stale": [tf.value for tf in self.stale_timeframes(now)],
            "correlations": {k: len(v) for k, v in self.correlations.items()},
            "error": self.last_error,
        }
