"""The live monitor.

Owns the async supervision loop and wires every layer together:

    collector → news guard → signal engine → dispatcher / journal / dashboard

Three independent tasks run concurrently:

* **market loop** — refreshes data and evaluates on a fixed cadence
* **news loop** — refreshes the economic calendar and headline feeds
* **maintenance loop** — resolves pending signals and re-runs the optimiser

Failure policy: a single cycle raising is logged and the loop continues with a
backoff. A monitor that dies silently at 03:00 is worse than one that logs an
error and keeps watching, and the dashboard surfaces the last error either way.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal as os_signal
from datetime import datetime
from typing import Any

import aiohttp

from .analysis.mtf import analyse_all
from .config import Config
from .data.collector import MarketDataCollector
from .engine.confluence import BASE_WEIGHTS
from .engine.signal_engine import SignalEngine
from .learning.journal import Journal, resolve_pending
from .learning.optimizer import Optimizer
from .logging_setup import get_logger
from .models import UTC, Decision, Timeframe
from .news.calendar_feed import EconomicCalendar
from .news.guard import NewsGuard, NewsState
from .news.headlines import HeadlineMonitor
from .notify.dispatcher import Dispatcher
from .sessions.calendar import SessionClock

log = get_logger("runner")


class Sentinel:
    """The long-running XAUUSD monitor."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.started_at = datetime.now(UTC)
        self._session: aiohttp.ClientSession | None = None

        self.collector = MarketDataCollector(config)
        self.calendar = EconomicCalendar(config)
        self.headlines = HeadlineMonitor(config)
        self.guard = NewsGuard(config, self.calendar)
        self.clock = SessionClock(config)
        self.engine = SignalEngine(config)
        self.dispatcher = Dispatcher(config)
        self.journal = Journal(config)
        self.optimizer = Optimizer(config, self.journal)

        self.last_decision: Decision | None = None
        self.last_news: NewsState | None = None
        self.last_error: str | None = None
        self.cycles = 0
        self.signals_emitted = 0
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> bool:
        self._session = aiohttp.ClientSession()
        ok = await self.collector.start()
        if not ok:
            log.error("cannot start: no market data provider is available")
            return False

        await asyncio.gather(
            self.calendar.refresh(force=True),
            self.headlines.refresh(force=True),
            return_exceptions=True,
        )
        self.guard.set_headlines(self.headlines.headlines)

        self._apply_learning()

        log.info(
            "XAUUSD Sentinel online — provider=%s channels=%s min_confidence=%s%%",
            self.collector.provider.name if self.collector.provider else "none",
            self.dispatcher.active or ["none"],
            self.config.get("signals.min_confidence", 90),
        )
        return True

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

        await asyncio.gather(
            self.collector.close(),
            self.calendar.close(),
            self.headlines.close(),
            self.dispatcher.close(),
            return_exceptions=True,
        )
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("XAUUSD Sentinel stopped after %d cycles, %d signals", self.cycles, self.signals_emitted)

    async def run_forever(self) -> None:
        """Run every loop until cancelled or interrupted."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._market_loop(), name="market"),
            asyncio.create_task(self._news_loop(), name="news"),
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
        ]
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._tasks)

    # -- loops -----------------------------------------------------------------
    async def _market_loop(self) -> None:
        interval = float(self.config.get("data.poll_seconds", 20))
        backoff = interval
        while self._running:
            try:
                await self.cycle()
                backoff = interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("market cycle failed")
                backoff = min(backoff * 2, 300.0)
            await asyncio.sleep(backoff)

    async def _news_loop(self) -> None:
        while self._running:
            try:
                await asyncio.gather(
                    self.calendar.refresh(), self.headlines.refresh(), return_exceptions=True
                )
                self.guard.set_headlines(self.headlines.headlines)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("news refresh failed")
            await asyncio.sleep(60.0)

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                now = datetime.now(UTC)
                self.guard.prune(now)

                candles = self.collector.store.get(Timeframe.M5)
                if candles:
                    resolved = resolve_pending(self.journal, candles, now)
                    if resolved:
                        log.info("resolved %d pending signals", resolved)

                if self.optimizer.due(now):
                    self._apply_learning()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("maintenance cycle failed")
            await asyncio.sleep(900.0)

    # -- one evaluation --------------------------------------------------------
    async def cycle(self) -> Decision:
        """Refresh data and produce one decision."""
        now = datetime.now(UTC)
        await self.collector.refresh(now)
        await self.collector.refresh_correlations(now)

        candles_by_tf = self.collector.store.all()
        session_state = self.clock.state(now)

        news_state = self.guard.evaluate(
            now,
            self.collector.store.get(Timeframe.M15),
            int(self.config.get("indicators.atr_period", 14)),
        )
        self.last_news = news_state

        decision = self.engine.evaluate(
            candles_by_tf,
            now=now,
            news=news_state,
            correlations=self.collector.correlations or None,
            stale_timeframes=self.collector.stale_timeframes(now),
            session=session_state,
        )
        self.last_decision = decision
        self.cycles += 1
        self.journal.record_decision(decision)

        if decision.signal is not None:
            self.signals_emitted += 1
            self.journal.record_signal(decision.signal)
            await self.dispatcher.send_signal(decision.signal)

        every = float(self.config.get("notify.heartbeat.every_minutes", 240))
        if self.engine.state.heartbeat_due(every, now):
            uptime = (now - self.started_at).total_seconds()
            await self.dispatcher.send_heartbeat(decision, session_state, uptime)

        return decision

    # -- learning ---------------------------------------------------------------
    def _apply_learning(self) -> None:
        try:
            result = self.optimizer.run(BASE_WEIGHTS)
        except Exception:  # noqa: BLE001
            log.exception("optimiser failed — keeping current weights")
            return
        if result.applied:
            self.engine.apply_weights(result.weights)
            if result.calibration:
                self.engine.apply_calibration(result.calibration)
        else:
            log.info("optimiser: %s", result.detail)

    # -- introspection -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything the dashboard needs, in one JSON-serialisable object."""
        now = datetime.now(UTC)
        session_state = self.clock.state(now)
        store = self.collector.store

        mtf = None
        candles = store.all()
        if candles:
            try:
                mtf = analyse_all(candles, self.config).to_dict()
            except Exception as exc:  # noqa: BLE001 - dashboard must never 500
                log.debug("snapshot analysis failed: %s", exc)

        return {
            "ts": now.isoformat(),
            "symbol": self.config.get("symbol", "XAUUSD"),
            "price": self.collector.last_price or store.price(),
            "uptime_seconds": int((now - self.started_at).total_seconds()),
            "cycles": self.cycles,
            "signals_emitted": self.signals_emitted,
            "session": session_state.to_dict(),
            "news": self.last_news.to_dict() if self.last_news else None,
            "decision": self.last_decision.to_dict() if self.last_decision else None,
            "analysis": mtf,
            "data": self.collector.health(now),
            "notify": self.dispatcher.health(),
            "state": self.engine.state.summary(now),
            "journal": self.journal.decision_stats(),
            "recent_signals": self.journal.recent(10),
            "upcoming_events": [e.to_dict() for e in self.calendar.upcoming(now, 60 * 24)[:8]],
            "headlines": [h.to_dict() for h in self.headlines.headlines[:10]],
            "error": self.last_error or self.collector.last_error,
            "config": {
                "min_confidence": self.config.get("signals.min_confidence", 90),
                "min_rr": self.config.get("signals.min_rr", 2.0),
                "max_risk_percent": self.config.get("risk.max_risk_percent", 1.0),
                "require_kill_zone": self.config.get("signals.require_kill_zone", True),
            },
        }


async def run(config: Config, with_dashboard: bool = True) -> None:
    """Start the sentinel (and optionally the dashboard) until interrupted."""
    sentinel = Sentinel(config)
    if not await sentinel.start():
        raise SystemExit(1)

    dashboard_runner = None
    if with_dashboard and config.get("dashboard.enabled", True):
        from .dashboard.app import start_dashboard

        dashboard_runner = await start_dashboard(sentinel, config)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, stop_event.set)

    main_task = asyncio.create_task(sentinel.run_forever())
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        main_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await main_task
        if dashboard_runner is not None:
            await dashboard_runner.cleanup()
        await sentinel.stop()
