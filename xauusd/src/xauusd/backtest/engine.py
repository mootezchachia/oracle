"""Historical replay.

The backtester drives the **same** :class:`~xauusd.engine.signal_engine.SignalEngine`
the live monitor uses. There is no parallel "backtest strategy" that can drift
away from live behaviour — if the backtest is wrong, the live engine is wrong
in exactly the same way, which is the only useful property a backtest has.

Lookahead is prevented structurally rather than by discipline:
:class:`~xauusd.data.csv_provider.CSVProvider` holds a cursor and refuses to
return any bar stamped after it, so the analysis code physically cannot see the
future. Higher timeframes are aggregated from the same base series, so an H4
bar only becomes visible once its final H1 constituent has closed.

Trade simulation is intentionally pessimistic:

* Entry is at the close of the signal bar plus a configured spread.
* A bar that touches both the stop and a target is scored as the stop.
* No compounding — everything is measured in R.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping, Sequence

from ..config import Config
from ..data.csv_provider import CSVProvider
from ..logging_setup import get_logger
from ..models import UTC, Candle, Decision, Direction, EconomicEvent, Timeframe
from ..engine.signal_engine import SignalEngine
from ..learning.journal import resolve_outcome
from ..news.calendar_feed import EconomicCalendar
from ..news.guard import NewsGuard, NewsState
from .metrics import BacktestReport, TradeRecord, build_report

log = get_logger("backtest")


class Backtester:
    """Bar-by-bar replay over historical candles."""

    def __init__(
        self,
        config: Config,
        provider: CSVProvider,
        events: Sequence[EconomicEvent] = (),
        correlations: Mapping[str, Sequence[Candle]] | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.engine = SignalEngine(config)
        self.correlations = dict(correlations or {})

        self.calendar = EconomicCalendar(config)
        if events:
            self.calendar.load(events)
        self.guard = NewsGuard(config, self.calendar) if events else None

        self._history = config.section("data.history")
        self._spread = float(config.get("risk.spread_allowance_usd", 0.35))
        self._resolve_after = timedelta(hours=float(config.get("learning.resolve_after_hours", 12)))

    # -- main loop ------------------------------------------------------------
    def run(
        self,
        step: Timeframe = Timeframe.M5,
        start: datetime | None = None,
        end: datetime | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> BacktestReport:
        """Replay the series, evaluating once per closed ``step`` bar."""
        base = self.provider.base_series()
        if not base:
            raise ValueError("no historical candles loaded")

        # Evaluate on the close of each step-timeframe bar — that is exactly
        # when the live engine would run.
        step_bars = self.provider._series(step)  # noqa: SLF001 - internal by design
        checkpoints = [c.ts + timedelta(seconds=step.seconds) for c in step_bars]
        if start:
            checkpoints = [t for t in checkpoints if t >= start]
        if end:
            checkpoints = [t for t in checkpoints if t <= end]

        # Skip the warm-up period. Ideally that means enough H4 bars for EMA200
        # to exist — about 35 days of continuous data. Shorter datasets are
        # still usable, but the H4 EMA stack will be unavailable, so the model
        # runs with one confirmation missing. Say so rather than silently
        # producing a degraded backtest that looks like a real one.
        ideal_warmup = 210 * Timeframe.H4.seconds
        available = (base[-1].ts - base[0].ts).total_seconds()
        warmup = ideal_warmup
        if available < ideal_warmup * 1.15:
            warmup = available * 0.35
            log.warning(
                "dataset spans %.1f days; %.1f are needed for a full H4 warm-up. "
                "Using a %.1f-day warm-up — H4 EMA200 will be unavailable and "
                "scores will run lower than they would live.",
                available / 86400, ideal_warmup / 86400, warmup / 86400,
            )

        floor = base[0].ts + timedelta(seconds=warmup)
        checkpoints = [t for t in checkpoints if t >= floor]

        if not checkpoints:
            raise ValueError(
                f"no evaluation points after the warm-up period "
                f"(dataset spans {available / 86400:.1f} days)"
            )

        log.info(
            "backtest: %d checkpoints from %s to %s",
            len(checkpoints), checkpoints[0], checkpoints[-1],
        )

        trades: list[TradeRecord] = []
        vetoes: dict[str, int] = {}
        evaluations = 0
        original_cursor = self.provider.cursor

        try:
            for index, moment in enumerate(checkpoints):
                self.provider.cursor = moment
                decision = self._evaluate(moment)
                evaluations += 1

                for veto in decision.vetoes:
                    vetoes[veto.code] = vetoes.get(veto.code, 0) + 1

                if decision.signal is not None:
                    trade = self._simulate(decision, base)
                    if trade is not None:
                        trades.append(trade)

                if progress is not None and index % 100 == 0:
                    progress(index, len(checkpoints))
        finally:
            self.provider.cursor = original_cursor

        return build_report(
            trades,
            start=checkpoints[0],
            end=checkpoints[-1],
            evaluations=evaluations,
            veto_breakdown=vetoes,
        )

    # -- internals -------------------------------------------------------------
    def _evaluate(self, moment: datetime) -> Decision:
        candles_by_tf: dict[Timeframe, list[Candle]] = {}
        for timeframe in Timeframe:
            count = int(self._history.get(timeframe.value, 300))
            series = self.provider.window(timeframe, count)
            if series:
                candles_by_tf[timeframe] = series

        news_state: NewsState | None = None
        if self.guard is not None:
            news_state = self.guard.evaluate(
                moment, candles_by_tf.get(Timeframe.M15, []),
                int(self.config.get("indicators.atr_period", 14)),
            )

        correlations = {
            name: [c for c in series if c.ts <= moment]
            for name, series in self.correlations.items()
        }

        return self.engine.evaluate(
            candles_by_tf,
            now=moment,
            news=news_state,
            correlations=correlations or None,
        )

    def _simulate(self, decision: Decision, base: Sequence[Candle]) -> TradeRecord | None:
        """Replay forward from the signal and record the realised outcome."""
        signal = decision.signal
        assert signal is not None
        plan = signal.risk

        # Pay the spread on entry — a signal generated on a close does not fill
        # at that close.
        entry = plan.entry + signal.direction.sign * self._spread
        risk = abs(entry - plan.stop_loss)
        if risk <= 0:
            return None

        future = [c for c in base if c.ts > signal.ts]
        if not future:
            return None

        outcome, exit_ts, mfe, mae, realised = resolve_outcome(
            signal.direction, entry, plan.stop_loss, plan.take_profits, future, self._resolve_after
        )

        return TradeRecord(
            id=signal.id,
            ts=signal.ts,
            direction=signal.direction,
            entry=entry,
            stop=plan.stop_loss,
            targets=list(plan.take_profits),
            confidence=signal.confidence,
            outcome=outcome,
            realised_r=realised,
            mfe_r=mfe,
            mae_r=mae,
            exit_ts=exit_ts,
            session=signal.context.session,
            kill_zone=signal.context.kill_zone,
            volatility=signal.context.volatility_regime.value,
        )


def run_backtest(
    config: Config,
    csv_path: str,
    base_timeframe: Timeframe = Timeframe.M1,
    step: Timeframe = Timeframe.M5,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestReport:
    """Convenience wrapper: load a CSV and replay it."""
    provider = CSVProvider.from_file(csv_path, base_timeframe)
    backtester = Backtester(config, provider)
    return backtester.run(step=step, start=start, end=end)
