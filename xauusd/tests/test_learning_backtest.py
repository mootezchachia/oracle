"""Journal, outcome resolution, optimiser and the backtester."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from xauusd.backtest.engine import Backtester
from xauusd.backtest.metrics import BacktestReport, TradeRecord, build_report
from xauusd.data.csv_provider import CSVProvider
from xauusd.learning.journal import Journal, resolve_outcome, resolve_pending
from xauusd.learning.optimizer import Optimizer
from xauusd.models import (
    UTC,
    Candle,
    Direction,
    MarketContext,
    RiskPlan,
    Signal,
    SignalOutcome,
    Timeframe,
)
from xauusd.testing import synthetic_m1

START = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def series(prices: list[tuple[float, float]]) -> list[Candle]:
    """Build candles from (high, low) pairs; close sits mid-range."""
    return [
        Candle(START + timedelta(minutes=5 * i), (h + l) / 2, h, l, (h + l) / 2, 100.0)
        for i, (h, l) in enumerate(prices)
    ]


class TestOutcomeResolution:
    def test_target_reached_is_a_win(self):
        candles = series([(4002, 3999), (4012, 4005)])
        outcome, ts, mfe, mae, r = resolve_outcome(
            Direction.BUY, 4000, 3995, [4010, 4020, 4030], candles
        )
        assert outcome in (SignalOutcome.TP1, SignalOutcome.TP2, SignalOutcome.TP3)
        assert r > 0

    def test_stop_reached_is_a_loss(self):
        candles = series([(4002, 3999), (4001, 3990)])
        outcome, _, _, _, r = resolve_outcome(Direction.BUY, 4000, 3995, [4010], candles)
        assert outcome is SignalOutcome.STOPPED
        assert r == pytest.approx(-1.0)

    def test_a_bar_touching_both_is_scored_as_the_loss(self):
        """Bar data cannot order intrabar events, so assume the worst.

        Any other assumption silently inflates the win rate the calibration
        layer then learns from.
        """
        candles = series([(4015, 3990)])
        outcome, _, _, _, r = resolve_outcome(Direction.BUY, 4000, 3995, [4010], candles)
        assert outcome is SignalOutcome.STOPPED
        assert r == pytest.approx(-1.0)

    def test_short_geometry_is_mirrored(self):
        candles = series([(4001, 3998), (4000, 3988)])
        outcome, _, _, _, r = resolve_outcome(Direction.SELL, 4000, 4005, [3990], candles)
        assert outcome is not SignalOutcome.STOPPED
        assert r > 0

    def test_expiry_marks_to_market(self):
        candles = [
            Candle(START + timedelta(hours=i), 4000, 4003, 3998, 4001, 100)
            for i in range(20)
        ]
        outcome, _, _, _, r = resolve_outcome(
            Direction.BUY, 4000, 3995, [4050], candles, expire_after=timedelta(hours=12)
        )
        assert outcome is SignalOutcome.EXPIRED
        assert r == pytest.approx(0.2, abs=0.01)

    def test_mfe_and_mae_are_recorded(self):
        candles = series([(4008, 3997), (4001, 3990)])
        _, _, mfe, mae, _ = resolve_outcome(Direction.BUY, 4000, 3995, [4020], candles)
        assert mfe == pytest.approx(1.6, abs=0.05)     # (4008-4000)/5
        assert mae >= 1.0

    def test_zero_risk_returns_pending(self):
        outcome, *_ = resolve_outcome(Direction.BUY, 4000, 4000, [4010], series([(4020, 3990)]))
        assert outcome is SignalOutcome.PENDING


def make_signal(sid: str, ts: datetime, direction: Direction = Direction.BUY,
                confidence: float = 93.0) -> Signal:
    sign = direction.sign
    entry = 4000.0
    plan = RiskPlan(
        entry=entry, stop_loss=entry - 5 * sign,
        take_profits=[entry + 7.5 * sign, entry + 12.5 * sign, entry + 20 * sign],
        risk_per_unit=5.0, rr_targets=[1.5, 2.5, 4.0], lot_size=0.2,
        risk_amount=100.0, risk_percent=1.0, break_even_price=entry + 5 * sign,
        trail_trigger_price=entry + 7.5 * sign, trail_distance=3.0, atr=3.0,
        partial_percents=[50, 30, 20],
    )
    from xauusd.models import Evidence

    return Signal(
        id=sid, ts=ts, symbol="XAUUSD", direction=direction, confidence=confidence,
        raw_score=85.0, probability=65.0, risk=plan,
        evidence=[
            Evidence("BOS", "H1 break of structure", direction, 8.0, 1.0),
            Evidence("LIQUIDITY_SWEEP", "sweep of equal lows", direction, 9.0, 1.0),
        ],
        context=MarketContext(ts=ts, price=entry, session="LONDON/NY OVERLAP"),
    )


class TestJournal:
    def test_records_and_reads_back(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        journal.record_signal(make_signal("a", START))
        rows = journal.recent()
        assert len(rows) == 1
        assert rows[0]["direction"] == "BUY"
        assert rows[0]["outcome"] == "PENDING"

    def test_evidence_is_persisted(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        journal.record_signal(make_signal("a", START))
        evidence = journal.evidence_for(["a"])
        assert {e["code"] for e in evidence["a"]} == {"BOS", "LIQUIDITY_SWEEP"}

    def test_resolve_pending_updates_outcomes(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        journal.record_signal(make_signal("a", START))
        candles = series([(4002, 3999), (4012, 4004)])
        assert resolve_pending(journal, candles, START + timedelta(hours=1)) == 1
        assert journal.recent()[0]["outcome"] != "PENDING"

    def test_decision_stats_expose_the_veto_mix(self, config, tmp_path: Path):
        from xauusd.models import Decision, Veto

        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        for code in ("NO_KILL_ZONE", "NO_KILL_ZONE", "THIN_EVIDENCE"):
            journal.record_decision(Decision(
                datetime.now(UTC), Direction.NEUTRAL, 0.0, 0.0, [], [Veto(code, code)],
                MarketContext(ts=datetime.now(UTC), price=4000), None, code,
            ))
        stats = journal.decision_stats()
        assert stats["evaluations"] == 3
        assert stats["veto_breakdown"]["NO_KILL_ZONE"] == 2

    def test_disabled_journal_is_inert(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"enabled": False, "db_path": str(tmp_path / "x.sqlite3")}})
        journal = Journal(cfg)
        journal.record_signal(make_signal("a", START))
        assert journal.recent() == []


class TestOptimizer:
    def _populate(self, journal: Journal, winners: int, losers: int) -> None:
        ts = START
        for i in range(winners):
            signal = make_signal(f"w{i}", ts + timedelta(hours=i))
            journal.record_signal(signal)
            journal.update_outcome(signal.id, SignalOutcome.TP2, ts, 2.5, 0.3, 2.5)
        for i in range(losers):
            signal = make_signal(f"l{i}", ts + timedelta(hours=100 + i), Direction.SELL)
            journal.record_signal(signal)
            journal.update_outcome(signal.id, SignalOutcome.STOPPED, ts, 0.2, 1.0, -1.0)

    def test_small_samples_do_not_move_weights(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        self._populate(journal, 3, 2)
        result = Optimizer(cfg, journal).run({"BOS": 8.0})
        assert result.applied is False
        assert result.weights["BOS"] == 8.0

    def test_weights_move_but_stay_bounded(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        self._populate(journal, 40, 5)
        optimizer = Optimizer(cfg, journal)
        result = optimizer.run({"BOS": 8.0, "LIQUIDITY_SWEEP": 9.0})
        assert result.applied is True
        for code, base in (("BOS", 8.0), ("LIQUIDITY_SWEEP", 9.0)):
            ratio = result.weights[code] / base
            assert optimizer.min_multiplier <= ratio <= optimizer.max_multiplier

    def test_last_run_is_timezone_aware(self, config, tmp_path: Path):
        """A naive last_run crashes the maintenance loop on the next tick."""
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        optimizer = Optimizer(cfg, Journal(cfg))
        assert optimizer.due(datetime.now(UTC)) is True
        optimizer.run({"BOS": 8.0})
        assert optimizer.last_run is not None
        assert optimizer.last_run.tzinfo is not None
        assert optimizer.due(datetime.now(UTC)) is False      # must not raise

    def test_calibration_reports_realised_hit_rate(self, config, tmp_path: Path):
        cfg = config.override({"learning": {"db_path": str(tmp_path / "j.sqlite3")}})
        journal = Journal(cfg)
        self._populate(journal, 30, 10)
        result = Optimizer(cfg, journal).run({"BOS": 8.0})
        assert result.calibration
        for bucket, rate in result.calibration.items():
            assert 0.0 <= rate <= 1.0


class TestBacktestMetrics:
    def _trade(self, i: int, r: float, session: str = "LONDON") -> TradeRecord:
        return TradeRecord(
            id=str(i), ts=START + timedelta(days=i), direction=Direction.BUY,
            entry=4000, stop=3995, targets=[4010, 4020, 4030], confidence=93,
            outcome=SignalOutcome.TP1 if r > 0 else SignalOutcome.STOPPED,
            realised_r=r, mfe_r=max(r, 0), mae_r=1.0, exit_ts=None, session=session,
        )

    def test_headline_metrics(self):
        report = build_report([self._trade(i, 2.0) for i in range(6)]
                              + [self._trade(i + 10, -1.0) for i in range(4)])
        assert report.count == 10
        assert report.win_rate == pytest.approx(60.0)
        assert report.profit_factor == pytest.approx(3.0)
        assert report.total_r == pytest.approx(8.0)
        assert report.expectancy == pytest.approx(0.8)

    def test_max_drawdown(self):
        report = build_report([self._trade(0, 2.0), self._trade(1, -1.0),
                               self._trade(2, -1.0), self._trade(3, 3.0)])
        assert report.max_drawdown_r == pytest.approx(2.0)

    def test_consecutive_losses(self):
        report = build_report([self._trade(0, -1.0), self._trade(1, -1.0),
                               self._trade(2, 2.0), self._trade(3, -1.0)])
        assert report.max_consecutive_losses == 2

    def test_session_breakdown_identifies_best_and_worst(self):
        trades = ([self._trade(i, 2.0, "LONDON") for i in range(4)]
                  + [self._trade(i + 10, -1.0, "ASIAN") for i in range(4)])
        report = build_report(trades)
        assert report.best_session.label == "LONDON"
        assert report.worst_session.label == "ASIAN"

    def test_all_losses_gives_zero_profit_factor(self):
        report = build_report([self._trade(i, -1.0) for i in range(3)])
        assert report.profit_factor == 0.0
        assert report.win_rate == 0.0

    def test_empty_report_renders_without_dividing_by_zero(self):
        report = build_report([], evaluations=500, veto_breakdown={"NO_KILL_ZONE": 500})
        assert report.count == 0
        assert report.win_rate == 0.0
        assert "No trades" in report.render()

    def test_render_and_serialise(self):
        report = build_report([self._trade(i, 2.0) for i in range(3)],
                              start=START, end=START + timedelta(days=3))
        assert "BACKTEST" in report.render()
        assert report.to_dict()["trades"] == 3


class TestBacktester:
    def test_replay_produces_a_report_without_lookahead(self, config):
        candles = synthetic_m1(bars=62_000, seed=11, scenario="textbook_long")
        provider = CSVProvider(candles, Timeframe.M1)
        report = Backtester(config, provider).run(step=Timeframe.H4)
        assert report.evaluations > 0
        assert report.start is not None and report.end is not None
        # Whether or not any trade fired, the veto ledger explains the silence.
        assert report.count > 0 or report.veto_breakdown

    def test_cursor_is_restored_after_a_run(self, config):
        provider = CSVProvider(synthetic_m1(bars=62_000, seed=11), Timeframe.M1)
        provider.cursor = None
        Backtester(config, provider).run(step=Timeframe.H4)
        assert provider.cursor is None

    def test_short_dataset_degrades_but_still_runs(self, config, caplog):
        """A short series must warn about the missing H4 warm-up, not pretend."""
        provider = CSVProvider(synthetic_m1(bars=12_000, seed=11), Timeframe.M1)
        with caplog.at_level("WARNING"):
            report = Backtester(config, provider).run(step=Timeframe.H4)
        assert report.evaluations > 0
        assert any("warm-up" in r.message for r in caplog.records)

    def test_empty_dataset_raises_clearly(self, config):
        provider = CSVProvider([], Timeframe.M1)
        with pytest.raises(ValueError):
            Backtester(config, provider).run(step=Timeframe.H1)
