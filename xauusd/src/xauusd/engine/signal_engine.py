"""The signal engine — where every layer meets.

One public method, :meth:`SignalEngine.evaluate`, takes a market snapshot and
returns a :class:`~xauusd.models.Decision`. The decision is *always* returned,
whether or not it carries a signal, because "no trade, and here is exactly why"
is the output this system produces most of the time and the dashboard needs to
show it.

The engine is deliberately synchronous and side-effect free. All I/O happens in
the runner; all state lives in :class:`~xauusd.engine.state.SignalState`. That
is what lets the backtester drive the identical code path bar by bar.

Order of operations
-------------------
1. Multi-timeframe analysis (H4 → H1 → M15 → M5 → M1)
2. Session context
3. Correlation desk
4. Confluence checklist → evidence and hard vetoes
5. Confidence engine → contextual multipliers
6. Threshold and state gates (cooldown, dedupe, daily cap)
7. Risk plan → entry, stop, targets, size
8. Final R:R gate

A veto at any stage short-circuits the rest — there is no partial credit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Mapping, Sequence

from ..analysis.correlation import CorrelationReport, analyse_correlations
from ..analysis.mtf import MTFResult, analyse_all
from ..config import Config
from ..logging_setup import get_logger
from ..models import (
    UTC,
    Candle,
    Decision,
    Direction,
    MarketContext,
    NewsSeverity,
    Signal,
    Timeframe,
    Veto,
    VolatilityRegime,
)
from ..news.guard import NewsState
from ..sessions.calendar import SessionClock, SessionState
from .confidence import ConfidenceEngine
from .confluence import ConfluenceEngine
from .risk import RiskManager
from .state import SignalState

log = get_logger("engine.signal")


class SignalEngine:
    def __init__(self, config: Config, weights: Mapping[str, float] | None = None) -> None:
        self.config = config
        self.symbol = str(config.get("symbol", "XAUUSD"))
        self.clock = SessionClock(config)
        self.confluence = ConfluenceEngine(config, weights)
        self.confidence = ConfidenceEngine(config)
        self.risk = RiskManager(config)
        self.state = SignalState(config)
        self._corr_cfg = config.section("correlation")
        self._sig_cfg = config.section("signals")

    # -- main entry point ----------------------------------------------------
    def evaluate(
        self,
        candles_by_tf: Mapping[Timeframe, Sequence[Candle]],
        now: datetime | None = None,
        news: NewsState | None = None,
        correlations: Mapping[str, Sequence[Candle]] | None = None,
        stale_timeframes: Sequence[Timeframe] = (),
        balance: float | None = None,
        session: SessionState | None = None,
    ) -> Decision:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        session_state = session or self.clock.state(now)
        news_state = news or NewsState(ts=now)

        mtf = analyse_all(candles_by_tf, self.config)
        price = mtf.price

        context = self._context(now, price, mtf, session_state, news_state)

        if not mtf.analyses:
            return Decision(
                now, Direction.NEUTRAL, 0.0, 0.0, [], [Veto("NO_DATA", "Insufficient candle history")],
                context, None, "Warming up — not enough history to analyse",
            )

        correlation = self._correlations(mtf, correlations, mtf.htf_bias)
        if correlation is not None:
            context.correlation = correlation.to_dict()

        confluence = self.confluence.evaluate(mtf, session_state, news_state, correlation, stale_timeframes)

        scored = self.confidence.score(
            confluence, mtf, session_state, news_state, correlation,
            self.clock.min_confidence_adjustment(session_state),
        )

        vetoes = list(confluence.vetoes)
        if vetoes:
            return Decision(
                now, confluence.direction, 0.0, confluence.raw_percent,
                confluence.evidence, vetoes, context, None,
                f"No trade — {vetoes[0].reason}",
            )

        if not scored.passes:
            return Decision(
                now, scored.direction, scored.confidence, scored.raw,
                confluence.evidence, [], context, None,
                f"No trade — confidence {scored.confidence:.0f}% below the {scored.threshold:.0f}% floor",
            )

        setup_tf = Timeframe(self.config.get("mtf.setup_timeframe", "M15"))
        setup = mtf.get(setup_tf)
        if setup is None:
            return Decision(
                now, scored.direction, scored.confidence, scored.raw, confluence.evidence,
                [Veto("NO_SETUP_TF", f"{setup_tf.value} analysis unavailable")], context, None,
                "No trade — setup timeframe unavailable",
            )

        gate = self.state.check(scored.direction, price, setup.atr, now)
        if not gate.allowed:
            assert gate.veto is not None
            return Decision(
                now, scored.direction, scored.confidence, scored.raw, confluence.evidence,
                [gate.veto], context, None, f"Suppressed — {gate.veto.reason}",
            )

        plan = self.risk.build_plan(scored.direction, price, setup, balance=balance)

        # Gate on the blended R:R of the whole scale-out plan, not on TP1
        # alone — TP1 is a partial, and judging the trade by it alone would
        # reject perfectly good setups for banking profit early.
        min_rr = float(self._sig_cfg.get("min_rr", 2.0))
        if plan.blended_rr < min_rr:
            veto = Veto(
                "POOR_RR",
                f"Blended R:R only {plan.blended_rr:.2f} (TP1 {plan.primary_rr:.2f}R), {min_rr:.1f} required",
            )
            return Decision(
                now, scored.direction, scored.confidence, scored.raw, confluence.evidence,
                [veto], context, None, f"No trade — {veto.reason}",
            )

        signal = Signal(
            id=uuid.uuid4().hex[:12],
            ts=now,
            symbol=self.symbol,
            direction=scored.direction,
            confidence=scored.confidence,
            raw_score=scored.raw,
            probability=scored.probability,
            risk=plan,
            evidence=[e for e in confluence.evidence if e.direction is scored.direction and e.score >= 0.3],
            context=context,
            notes=self.risk.management_notes(plan, scored.direction)
            + [f"Expectancy {self.confidence.expectancy(scored.probability, plan.blended_rr):+.2f}R per trade"]
            + [m.reason for m in scored.modifiers if m.value < 1.0],
        )

        self.state.record(signal)
        log.info(
            "SIGNAL %s %s @ %.2f conf=%.0f%% rr=%.2f (%d confirmations)",
            signal.direction.value, self.symbol, plan.entry, signal.confidence,
            plan.primary_rr, len(signal.evidence),
        )

        return Decision(
            now, scored.direction, scored.confidence, scored.raw,
            confluence.evidence, [], context, signal,
            f"{scored.direction.value} signal at {scored.confidence:.0f}% confidence",
        )

    # -- helpers -------------------------------------------------------------
    def _context(
        self,
        now: datetime,
        price: float,
        mtf: MTFResult,
        session: SessionState,
        news: NewsState,
    ) -> MarketContext:
        setup_tf = Timeframe(self.config.get("mtf.setup_timeframe", "M15"))
        setup = mtf.get(setup_tf)
        return MarketContext(
            ts=now,
            price=price,
            session=session.primary,
            active_sessions=session.active_sessions,
            kill_zone=session.kill_zones[0] if session.kill_zones else None,
            in_overlap=session.in_london_ny_overlap,
            volatility_regime=setup.volatility.regime if setup else VolatilityRegime.NORMAL,
            atr={tf.value: a.atr for tf, a in mtf.analyses.items()},
            trend={tf.value: a.bias.value for tf, a in mtf.analyses.items()},
            news_severity=news.severity,
            next_event=news.next_event.to_dict() if news.next_event else None,
            dealing_range=setup.structure.dealing_range.to_dict()
            if setup and setup.structure.dealing_range
            else None,
        )

    def _correlations(
        self,
        mtf: MTFResult,
        correlations: Mapping[str, Sequence[Candle]] | None,
        direction: Direction,
    ) -> CorrelationReport | None:
        if not correlations or not self._corr_cfg.get("enabled", True):
            return None
        setup_tf = Timeframe(self.config.get("mtf.setup_timeframe", "M15"))
        setup = mtf.get(setup_tf)
        if setup is None:
            return None
        return analyse_correlations(
            setup.candles,
            correlations,
            self._corr_cfg.get("instruments", {}) or {},
            direction,
            lookback=int(self._corr_cfg.get("lookback_bars", 30)),
            agreement_bonus=float(self._corr_cfg.get("agreement_bonus", 1.05)),
            conflict_penalty=float(self._corr_cfg.get("conflict_penalty", 0.88)),
            strong_conflict_penalty=float(self._corr_cfg.get("strong_conflict_penalty", 0.75)),
        )

    # -- learning hooks -------------------------------------------------------
    def apply_weights(self, weights: Mapping[str, float]) -> None:
        """Install optimised evidence weights from the learning layer."""
        self.confluence.weights.update({k: float(v) for k, v in weights.items()})
        log.info("applied %d optimised evidence weights", len(weights))

    def apply_calibration(self, table: Mapping[int, float]) -> None:
        self.confidence.set_calibration(table)
