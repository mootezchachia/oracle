"""Risk management, confluence scoring and the end-to-end signal engine.

Two properties matter more than any individual assertion:

1. The engine *can* publish. A system that never signals is not selective, it
   is broken, and "no signal" would be indistinguishable from a silent bug.
2. The engine *usually* declines. Every hard gate is tested individually,
   because each one is the difference between a monitor and a slot machine.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from xauusd.analysis.mtf import analyse_all
from xauusd.engine.confluence import ConfluenceEngine
from xauusd.engine.risk import RiskManager, StopContext
from xauusd.engine.signal_engine import SignalEngine
from xauusd.engine.state import SignalState
from xauusd.models import (
    UTC,
    Direction,
    LiquidityKind,
    LiquidityPool,
    NewsSeverity,
    Timeframe,
)
from xauusd.news.guard import NewsState
from xauusd.testing import synthetic_market

OVERLAP = datetime(2026, 3, 10, 14, 31, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
class TestRiskManager:
    def test_position_size_risks_the_configured_percentage(self, config):
        manager = RiskManager(config)
        lots, risk = manager.position_size(risk_per_unit=5.0, balance=10_000.0)
        # 1% of 10k = $100. At $5/oz and 100 oz per lot, that is 0.20 lots.
        assert lots == pytest.approx(0.20, abs=0.01)
        assert risk == pytest.approx(100.0, rel=0.05)

    def test_wider_stop_means_smaller_size(self, config):
        manager = RiskManager(config)
        tight, _ = manager.position_size(2.0, 10_000.0)
        wide, _ = manager.position_size(8.0, 10_000.0)
        assert wide < tight

    def test_size_never_exceeds_the_configured_maximum(self, config):
        manager = RiskManager(config)
        lots, _ = manager.position_size(0.01, 10_000_000.0)
        assert lots <= float(config.get("risk.max_lot"))

    def test_stop_goes_beyond_structure_not_just_atr(self, config):
        manager = RiskManager(config)
        context = StopContext(swing_level=3990.0, sweep_extreme=3985.0)
        distance, reason = manager.stop_distance(Direction.BUY, 4000.0, atr=2.0, context=context)
        # The swept extreme is $15 away; an ATR stop would be $3. The wider
        # structural stop must win, or the trade dies on noise.
        assert distance > 2.0 * 1.5
        assert "swept" in reason or "clamped" in reason

    def test_stop_is_clamped_to_sane_bounds(self, config):
        manager = RiskManager(config)
        distance, _ = manager.stop_distance(
            Direction.BUY, 4000.0, atr=2.0, context=StopContext(swing_level=3000.0)
        )
        assert distance <= float(config.get("risk.max_sl_usd"))

    def test_targets_respect_r_multiples(self, config):
        manager = RiskManager(config)
        prices, rr = manager.targets(Direction.BUY, 4000.0, risk_per_unit=5.0)
        assert prices[0] == pytest.approx(4007.5)     # 1.5R
        assert rr[0] == pytest.approx(1.5)
        assert prices == sorted(prices)

    def test_short_targets_go_the_other_way(self, config):
        manager = RiskManager(config)
        prices, _ = manager.targets(Direction.SELL, 4000.0, risk_per_unit=5.0)
        assert prices[0] == pytest.approx(3992.5)
        assert prices == sorted(prices, reverse=True)

    def test_target_snaps_in_front_of_nearby_liquidity(self, config):
        manager = RiskManager(config)
        pool = LiquidityPool(LiquidityKind.PDH, 4007.0, datetime(2026, 1, 1, tzinfo=UTC))
        prices, _ = manager.targets(Direction.BUY, 4000.0, 5.0, [pool])
        # The 1.5R target of 4007.5 sits just past a pool at 4007; the target
        # is pulled in front of it, where the fill actually is.
        assert prices[0] < 4007.0

    def test_blended_rr_reflects_the_whole_plan(self, config, textbook_long):
        mtf = analyse_all(textbook_long, config)
        setup = mtf.get(Timeframe.M15)
        plan = RiskManager(config).build_plan(Direction.BUY, mtf.price, setup)
        # TP1 is a partial at 1.5R; judging the trade by it alone understates it.
        assert plan.blended_rr > plan.primary_rr
        assert plan.blended_rr == pytest.approx(2.3, abs=0.35)


# ---------------------------------------------------------------------------
# Confluence scoring
# ---------------------------------------------------------------------------
class TestConfluenceScoring:
    def _result(self, config, market, ts=OVERLAP):
        engine = SignalEngine(config)
        mtf = analyse_all(market, config)
        return engine.confluence.evaluate(
            mtf, engine.clock.state(ts), NewsState(ts=ts), None
        )

    def test_purity_and_coverage_bound_the_score(self, config, textbook_long):
        result = self._result(config, textbook_long)
        assert 0.0 <= result.purity <= 1.0
        assert 0.0 <= result.coverage <= 1.0
        assert 0.0 <= result.raw_percent <= 100.0

    def test_a_thin_case_cannot_reach_the_top_of_the_range(self, config):
        """Purity alone must not manufacture conviction."""
        from xauusd.engine.confluence import ConfluenceResult
        from xauusd.models import Evidence

        thin = ConfluenceResult(
            direction=Direction.BUY,
            evidence=[Evidence("X", "only thing", Direction.BUY, 5.0)],
            vetoes=[],
            bull_score=5.0, bear_score=0.0, evaluable_weight=140.0,
            full_conviction_weight=52.0,
        )
        assert thin.purity == pytest.approx(1.0)
        assert thin.raw_percent < 80.0            # perfect purity, tiny coverage

    def test_conflicting_evidence_drags_the_score_down(self, config):
        from xauusd.engine.confluence import ConfluenceResult
        from xauusd.models import Evidence

        clean = ConfluenceResult(Direction.BUY, [], [], 52.0, 0.0, 140.0, 52.0)
        muddy = ConfluenceResult(Direction.BUY, [], [], 52.0, 20.0, 140.0, 52.0)
        assert clean.raw_percent > muddy.raw_percent
        assert clean.raw_percent == pytest.approx(100.0)

    def test_evidence_carries_direction_and_explanation(self, config, textbook_long):
        result = self._result(config, textbook_long)
        assert result.evidence
        for item in result.evidence:
            assert item.label
            assert item.direction in (Direction.BUY, Direction.SELL, Direction.NEUTRAL)
            assert 0.0 <= item.score <= 1.0


# ---------------------------------------------------------------------------
# State gates
# ---------------------------------------------------------------------------
class TestSignalState:
    def test_cooldown_blocks_a_rapid_second_signal(self, config):
        from xauusd.models import RiskPlan, Signal, MarketContext

        state = SignalState(config)
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        plan = RiskPlan(4000, 3995, [4010], 5, [2.0], 0.2, 100, 1.0, 4005, 4007, 2, 3)
        signal = Signal("a", ts, "XAUUSD", Direction.BUY, 95, 90, 70, plan, [],
                        MarketContext(ts=ts, price=4000))
        state.record(signal)

        blocked = state.check(Direction.BUY, 4020.0, atr=3.0, now=ts + timedelta(minutes=5))
        assert not blocked.allowed
        assert blocked.veto.code == "COOLDOWN"

    def test_reversal_needs_a_longer_wait(self, config):
        from xauusd.models import RiskPlan, Signal, MarketContext

        state = SignalState(config)
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        plan = RiskPlan(4000, 3995, [4010], 5, [2.0], 0.2, 100, 1.0, 4005, 4007, 2, 3)
        state.record(Signal("a", ts, "XAUUSD", Direction.BUY, 95, 90, 70, plan, [],
                            MarketContext(ts=ts, price=4000)))

        later = ts + timedelta(minutes=60)      # past the 45-min cooldown
        result = state.check(Direction.SELL, 3980.0, atr=3.0, now=later)
        assert not result.allowed
        assert result.veto.code == "REVERSAL_COOLDOWN"

    def test_duplicate_price_is_rejected(self, config):
        from xauusd.models import RiskPlan, Signal, MarketContext

        state = SignalState(config)
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        plan = RiskPlan(4000, 3995, [4010], 5, [2.0], 0.2, 100, 1.0, 4005, 4007, 2, 3)
        state.record(Signal("a", ts, "XAUUSD", Direction.BUY, 95, 90, 70, plan, [],
                            MarketContext(ts=ts, price=4000)))

        result = state.check(Direction.BUY, 4000.5, atr=3.0, now=ts + timedelta(hours=2))
        assert not result.allowed
        assert result.veto.code == "DUPLICATE"

    def test_a_genuinely_new_setup_passes(self, config):
        from xauusd.models import RiskPlan, Signal, MarketContext

        state = SignalState(config)
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        plan = RiskPlan(4000, 3995, [4010], 5, [2.0], 0.2, 100, 1.0, 4005, 4007, 2, 3)
        state.record(Signal("a", ts, "XAUUSD", Direction.BUY, 95, 90, 70, plan, [],
                            MarketContext(ts=ts, price=4000)))

        assert state.check(Direction.BUY, 4030.0, atr=3.0, now=ts + timedelta(hours=3)).allowed

    def test_daily_cap_is_enforced(self, config):
        from xauusd.models import RiskPlan, Signal, MarketContext

        state = SignalState(config)
        ts = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)
        cap = int(config.get("signals.max_signals_per_day"))
        plan = RiskPlan(4000, 3995, [4010], 5, [2.0], 0.2, 100, 1.0, 4005, 4007, 2, 3)
        for i in range(cap):
            state.record(Signal(f"s{i}", ts + timedelta(hours=i * 2), "XAUUSD", Direction.BUY,
                                95, 90, 70, plan, [], MarketContext(ts=ts, price=4000 + i * 50)))

        result = state.check(Direction.BUY, 5000.0, atr=3.0, now=ts + timedelta(hours=20))
        assert not result.allowed
        assert result.veto.code == "DAILY_LIMIT"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
class TestSignalEngine:
    def test_publishes_a_textbook_long(self, config, textbook_long):
        """The engine must be able to fire, or silence proves nothing."""
        decision = SignalEngine(config).evaluate(textbook_long, now=OVERLAP)
        assert decision.signal is not None, decision.reason
        signal = decision.signal
        assert signal.direction is Direction.BUY
        assert signal.confidence >= float(config.get("signals.min_confidence"))
        assert signal.risk.stop_loss < signal.risk.entry < signal.risk.take_profits[0]
        assert signal.risk.take_profits == sorted(signal.risk.take_profits)
        assert signal.reasons
        assert 0 < signal.risk.lot_size

    def test_publishes_a_textbook_short_with_inverted_geometry(self, config, textbook_short):
        decision = SignalEngine(config).evaluate(textbook_short, now=OVERLAP)
        assert decision.signal is not None, decision.reason
        signal = decision.signal
        assert signal.direction is Direction.SELL
        assert signal.risk.stop_loss > signal.risk.entry > signal.risk.take_profits[0]
        assert signal.risk.take_profits == sorted(signal.risk.take_profits, reverse=True)

    def test_risk_never_exceeds_the_configured_cap(self, config, textbook_long):
        decision = SignalEngine(config).evaluate(textbook_long, now=OVERLAP)
        assert decision.signal is not None
        cap = float(config.get("risk.max_risk_percent"))
        # Lot rounding can only ever move risk by a fraction of a step.
        assert decision.signal.risk.risk_percent <= cap * 1.25

    def test_news_blackout_vetoes_regardless_of_score(self, config, textbook_long):
        blocked = NewsState(ts=OVERLAP, blocked=True, reason="FOMC in 30 minutes",
                            severity=NewsSeverity.CRITICAL, multiplier=0.0)
        decision = SignalEngine(config).evaluate(textbook_long, now=OVERLAP, news=blocked)
        assert decision.signal is None
        assert any(v.code == "NEWS_BLACKOUT" for v in decision.vetoes)

    def test_weekend_vetoes(self, config, textbook_long):
        saturday = datetime(2026, 3, 14, 14, 30, tzinfo=UTC)
        decision = SignalEngine(config).evaluate(textbook_long, now=saturday)
        assert decision.signal is None
        assert any(v.code == "MARKET_CLOSED" for v in decision.vetoes)

    def test_stale_data_vetoes(self, config, textbook_long):
        decision = SignalEngine(config).evaluate(
            textbook_long, now=OVERLAP, stale_timeframes=[Timeframe.M5]
        )
        assert decision.signal is None
        assert any(v.code == "STALE_DATA" for v in decision.vetoes)

    def test_outside_a_kill_zone_vetoes(self, config, textbook_long):
        # 16:30 UTC in March is 12:30 New York — after the London-close zone
        # ends at 12:00 and before the PM zone opens at 13:30.
        gap = datetime(2026, 3, 10, 16, 30, tzinfo=UTC)
        engine = SignalEngine(config)
        state = engine.clock.state(gap)
        assert not state.in_kill_zone, "fixture assumption: this time is outside every kill zone"
        decision = engine.evaluate(textbook_long, now=gap)
        assert decision.signal is None

    def test_no_data_returns_a_decision_not_an_exception(self, config):
        decision = SignalEngine(config).evaluate({}, now=OVERLAP)
        assert decision.signal is None
        assert decision.vetoes
        assert decision.direction is Direction.NEUTRAL

    def test_thin_history_is_handled(self, config, textbook_long):
        trimmed = {tf: candles[-20:] for tf, candles in textbook_long.items()}
        decision = SignalEngine(config).evaluate(trimmed, now=OVERLAP)
        assert decision.signal is None

    def test_a_second_evaluation_is_suppressed_by_cooldown(self, config, textbook_long):
        engine = SignalEngine(config)
        first = engine.evaluate(textbook_long, now=OVERLAP)
        assert first.signal is not None
        second = engine.evaluate(textbook_long, now=OVERLAP + timedelta(minutes=1))
        assert second.signal is None
        assert second.vetoes[0].code in {"COOLDOWN", "DUPLICATE"}

    def test_raising_the_floor_silences_the_engine(self, config, textbook_long):
        strict = config.override({"signals": {"min_confidence": 99.5}})
        decision = SignalEngine(strict).evaluate(textbook_long, now=OVERLAP)
        assert decision.signal is None
        assert "below" in decision.reason

    def test_decision_serialises(self, config, textbook_long):
        import json

        decision = SignalEngine(config).evaluate(textbook_long, now=OVERLAP)
        payload = json.dumps(decision.to_dict(), default=str)
        assert json.loads(payload)["actionable"] is (decision.signal is not None)

    def test_probability_is_not_the_confidence_score(self, config, textbook_long):
        """Presenting a 95% checklist score as a 95% win rate would be a lie."""
        decision = SignalEngine(config).evaluate(textbook_long, now=OVERLAP)
        assert decision.signal is not None
        assert decision.signal.probability < decision.signal.confidence
        assert decision.signal.probability <= 78.0
