"""Execution: safety gates, order construction and position management.

The safety tests are the ones that matter. Everything else here is a
convenience; the demo-account gate and the symbol allowlist are what make it
defensible to arm this at all, so they are tested from every angle including
the ones a careless refactor would break.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import fake_mt5
from xauusd.execution import PaperBroker, build_broker
from xauusd.execution.broker import AccountType, SafetyError, SymbolSpec
from xauusd.execution.manager import ExecutionManager
from xauusd.models import (
    UTC,
    Candle,
    Direction,
    Evidence,
    MarketContext,
    RiskPlan,
    Signal,
)

TS = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def mt5_module(monkeypatch):
    """Install the fake MetaTrader5 package for the duration of each test."""
    fake_mt5.state.reset()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    yield fake_mt5
    fake_mt5.state.reset()


def make_signal(direction: Direction = Direction.BUY, entry: float = 4000.0) -> Signal:
    sign = direction.sign
    plan = RiskPlan(
        entry=entry,
        stop_loss=entry - 5.0 * sign,
        take_profits=[entry + 7.5 * sign, entry + 12.5 * sign, entry + 20.0 * sign],
        risk_per_unit=5.0, rr_targets=[1.5, 2.5, 4.0], lot_size=0.2,
        risk_amount=100.0, risk_percent=1.0,
        break_even_price=entry + 5.0 * sign, trail_trigger_price=entry + 7.5 * sign,
        trail_distance=3.0, atr=3.0, partial_percents=[50, 30, 20],
    )
    return Signal(
        id="sig001", ts=TS, symbol="XAUUSD", direction=direction, confidence=93.0,
        raw_score=86.0, probability=66.0, risk=plan,
        evidence=[Evidence("BOS", "H1 BOS", direction, 8.0)],
        context=MarketContext(ts=TS, price=entry),
    )


def exec_config(config, **overrides):
    base = {
        "enabled": True,
        "mode": "mt5",
        "require_demo_account": True,
        "symbol_allowlist": ["XAUUSD"],
        "magic": 20260806,
        "max_open_positions": 1,
        "max_daily_trades": 4,
        "max_daily_loss_percent": 3.0,
        "min_free_margin_percent": 30.0,
        "kill_switch_file": None,
    }
    base.update(overrides)
    return config.override({"execution": base})


# ---------------------------------------------------------------------------
# Safety gates — the reason this layer is safe to switch on
# ---------------------------------------------------------------------------
class TestSafetyGates:
    async def test_demo_account_arms(self, config):
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.account.trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_DEMO
        broker = MT5Broker(exec_config(config))
        assert await broker.connect() is True
        account = await broker.account()
        assert account.account_type is AccountType.DEMO
        assert account.is_demo is True

    async def test_live_account_refuses_to_arm(self, config):
        """The single most important test in the suite."""
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.account.trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_REAL
        broker = MT5Broker(exec_config(config))
        with pytest.raises(SafetyError, match="not a demo account"):
            await broker.connect()
        assert fake_mt5.state.initialized is False      # terminal released

    async def test_contest_account_counts_as_demo(self, config):
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.account.trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_CONTEST
        assert await MT5Broker(exec_config(config)).connect() is True

    async def test_unknown_trade_mode_is_treated_as_live(self, config):
        """Guessing 'probably demo' on an unrecognised value is the wrong default."""
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.account.trade_mode = 99
        with pytest.raises(SafetyError, match="not a demo account"):
            await MT5Broker(exec_config(config)).connect()

    async def test_live_account_allowed_only_when_explicitly_unlocked(self, config):
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.account.trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_REAL
        broker = MT5Broker(exec_config(config, require_demo_account=False))
        assert await broker.connect() is True

    async def test_symbol_outside_the_allowlist_refuses(self, config):
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.known_symbols = ["EURUSD"]
        broker = MT5Broker(exec_config(config))
        with pytest.raises(SafetyError):
            await broker.connect()

    async def test_allowlist_matches_broker_suffixes(self, config):
        """`XAUUSD.r` is still XAUUSD; a broker suffix must not defeat the list."""
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.known_symbols = ["XAUUSD.r"]
        broker = MT5Broker(exec_config(config))
        assert await broker.connect() is True
        assert broker.symbol == "XAUUSD.r"

    async def test_algo_trading_disabled_blocks_arming(self, config):
        from xauusd.execution.mt5_broker import MT5Broker

        fake_mt5.state.terminal.trade_allowed = False
        assert await MT5Broker(exec_config(config)).connect() is False


# ---------------------------------------------------------------------------
# Order construction
# ---------------------------------------------------------------------------
class TestOrders:
    async def _broker(self, config, **overrides):
        from xauusd.execution.mt5_broker import MT5Broker

        broker = MT5Broker(exec_config(config, **overrides))
        await broker.connect()
        return broker

    async def test_buy_order_carries_the_right_fields(self, config):
        broker = await self._broker(config)
        signal = make_signal(Direction.BUY, 4000.0)
        result = await broker.open_position(signal, 0.2, 3995.0, 4020.0)

        assert result.ok is True
        request = fake_mt5.state.sent[-1]
        assert request["symbol"] == "XAUUSD"
        assert request["type"] == fake_mt5.ORDER_TYPE_BUY
        assert request["price"] == fake_mt5.state.tick.ask     # buys fill at the ask
        assert request["sl"] == 3995.0
        assert request["magic"] == 20260806
        assert "sig001" in request["comment"]

    async def test_sell_fills_at_the_bid(self, config):
        broker = await self._broker(config)
        await broker.open_position(make_signal(Direction.SELL, 4000.0), 0.2, 4005.0, 3980.0)
        assert fake_mt5.state.sent[-1]["price"] == fake_mt5.state.tick.bid

    async def test_filling_mode_follows_the_symbol_mask(self, config):
        """Sending an unsupported filling mode is a guaranteed rejection."""
        fake_mt5.state.symbol.filling_mode = fake_mt5.SYMBOL_FILLING_FOK
        broker = await self._broker(config)
        result = await broker.open_position(make_signal(), 0.2, 3995.0, 4020.0)
        assert result.ok is True
        assert fake_mt5.state.sent[-1]["type_filling"] == fake_mt5.ORDER_FILLING_FOK

    async def test_stops_are_pushed_outside_the_minimum_distance(self, config):
        # 500 points x 0.01 = $5.00 minimum distance; a $1 stop is illegal.
        fake_mt5.state.symbol.trade_stops_level = 500
        broker = await self._broker(config)
        result = await broker.open_position(make_signal(), 0.2, 3999.0, 4020.0)
        assert result.ok is True
        assert abs(fake_mt5.state.sent[-1]["price"] - fake_mt5.state.sent[-1]["sl"]) >= 5.0

    async def test_volume_is_rounded_to_the_broker_step(self, config):
        fake_mt5.state.symbol.volume_step = 0.1
        fake_mt5.state.symbol.volume_min = 0.1
        broker = await self._broker(config)
        await broker.open_position(make_signal(), 0.137, 3995.0, 4020.0)
        assert fake_mt5.state.sent[-1]["volume"] == pytest.approx(0.1)

    async def test_rejection_is_reported_not_swallowed(self, config):
        broker = await self._broker(config)
        fake_mt5.state.fail_next_order = fake_mt5.TRADE_RETCODE_NO_MONEY
        result = await broker.open_position(make_signal(), 0.2, 3995.0, 4020.0)
        assert result.ok is False
        assert result.retcode == fake_mt5.TRADE_RETCODE_NO_MONEY
        assert result.error

    async def test_only_our_positions_are_visible(self, config):
        """A manual trade on the same account must be invisible to this system."""
        broker = await self._broker(config)
        await broker.open_position(make_signal(), 0.2, 3995.0, 4020.0)

        foreign = fake_mt5._Position(
            ticket=999, symbol="XAUUSD", type=fake_mt5.POSITION_TYPE_BUY, volume=5.0,
            price_open=4000, sl=0, tp=0, time=0, magic=11111, comment="hand placed",
        )
        fake_mt5.state.positions[999] = foreign

        tickets = [p.ticket for p in await broker.positions()]
        assert 999 not in tickets
        assert len(tickets) == 1

    async def test_cannot_close_a_position_we_do_not_own(self, config):
        broker = await self._broker(config)
        fake_mt5.state.positions[999] = fake_mt5._Position(
            ticket=999, symbol="XAUUSD", type=0, volume=5.0, price_open=4000,
            sl=0, tp=0, time=0, magic=11111, comment="hand placed",
        )
        result = await broker.close_position(999)
        assert result.ok is False
        assert 999 in fake_mt5.state.positions      # untouched

    async def test_partial_close_leaves_the_remainder(self, config):
        broker = await self._broker(config)
        opened = await broker.open_position(make_signal(), 0.2, 3995.0, 4020.0)
        assert (await broker.close_position(opened.ticket, 0.1)).ok is True
        remaining = await broker.positions()
        assert remaining[0].volume == pytest.approx(0.1)

    async def test_partial_that_would_orphan_a_sub_minimum_lot_closes_all(self, config):
        broker = await self._broker(config)
        opened = await broker.open_position(make_signal(), 0.02, 3995.0, 4020.0)
        await broker.close_position(opened.ticket, 0.015)
        assert await broker.positions() == []


# ---------------------------------------------------------------------------
# Manager gates
# ---------------------------------------------------------------------------
class TestManagerGates:
    async def _manager(self, config, **overrides) -> ExecutionManager:
        cfg = exec_config(config, mode="paper", **overrides)
        manager = ExecutionManager(cfg, PaperBroker(cfg))
        await manager.start()
        manager.broker.set_price(4000.0)
        return manager

    async def test_disabled_never_trades(self, config):
        cfg = exec_config(config, enabled=False, mode="paper")
        manager = ExecutionManager(cfg, PaperBroker(cfg))
        await manager.start()
        decision = await manager.on_signal(make_signal(), TS)
        assert decision.executed is False
        assert "disabled" in decision.reason

    async def test_happy_path_opens_a_position(self, config):
        manager = await self._manager(config)
        decision = await manager.on_signal(make_signal(), TS)
        assert decision.executed is True, decision.reason
        assert len(await manager.broker.positions()) == 1

    async def test_position_cap_is_enforced(self, config):
        manager = await self._manager(config, max_open_positions=1)
        await manager.on_signal(make_signal(), TS)
        second = await manager.on_signal(make_signal(), TS + timedelta(minutes=30))
        assert second.executed is False
        assert "already holding" in second.reason

    async def test_opposing_position_blocks_entry(self, config):
        manager = await self._manager(config, max_open_positions=5)
        await manager.on_signal(make_signal(Direction.BUY), TS)
        opposing = await manager.on_signal(make_signal(Direction.SELL), TS + timedelta(hours=1))
        assert opposing.executed is False
        assert "opposing" in opposing.reason

    async def test_daily_trade_cap(self, config):
        manager = await self._manager(config, max_daily_trades=2, max_open_positions=10)
        for i in range(2):
            assert (await manager.on_signal(make_signal(), TS + timedelta(minutes=i))).executed
        blocked = await manager.on_signal(make_signal(), TS + timedelta(minutes=5))
        assert blocked.executed is False
        assert "daily trade cap" in blocked.reason

    async def test_daily_loss_limit_disarms(self, config):
        manager = await self._manager(config, max_daily_loss_percent=2.0)
        today = datetime.now(UTC).replace(hour=14, minute=30, second=0, microsecond=0)
        manager.day.day = today.date()
        manager.day.start_equity = 10_000.0
        manager.broker.balance = 9_700.0        # a 3% drawdown
        decision = await manager.on_signal(make_signal(), today)
        assert decision.executed is False
        assert "daily loss limit" in decision.reason

    async def test_clock_moving_backwards_does_not_clear_the_loss_limit(self, config):
        """A backwards clock jump must never re-arm a disarmed day."""
        manager = await self._manager(config, max_daily_loss_percent=2.0)
        today = datetime.now(UTC).replace(hour=14, minute=30, second=0, microsecond=0)
        manager.day.day = today.date()
        manager.day.start_equity = 10_000.0
        manager.broker.balance = 9_700.0

        yesterday = today - timedelta(days=1)
        decision = await manager.on_signal(make_signal(), yesterday)
        assert decision.executed is False
        assert "daily loss limit" in decision.reason

    async def test_kill_switch_file_blocks_entries(self, config, tmp_path: Path):
        halt = tmp_path / "HALT"
        manager = await self._manager(config, kill_switch_file=str(halt))
        assert (await manager.on_signal(make_signal(), TS)).executed is True

        halt.touch()
        blocked = await manager.on_signal(make_signal(), TS + timedelta(hours=2))
        assert blocked.executed is False
        assert "kill switch" in blocked.reason

    async def test_kill_switch_env_blocks_entries(self, config, monkeypatch):
        monkeypatch.setenv("XAUUSD_KILL_SWITCH", "1")
        manager = await self._manager(config)
        decision = await manager.on_signal(make_signal(), TS)
        assert decision.executed is False
        assert "kill switch" in decision.reason

    async def test_flatten_closes_everything(self, config):
        manager = await self._manager(config, max_open_positions=3)
        await manager.on_signal(make_signal(), TS)
        await manager.flatten("test")
        assert await manager.broker.positions() == []
        assert manager.plans == {}

    async def test_day_rolls_over(self, config):
        manager = await self._manager(config, max_daily_trades=1)
        today = datetime.now(UTC).replace(hour=14, minute=30, second=0, microsecond=0)
        manager.day.day = today.date()

        await manager.on_signal(make_signal(), today)
        assert (await manager.on_signal(make_signal(), today)).executed is False

        await manager.flatten("test")
        assert (await manager.on_signal(make_signal(), today + timedelta(days=1))).executed is True


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------
class TestPositionManagement:
    async def _open(self, config, **overrides):
        cfg = exec_config(config, mode="paper", **overrides)
        manager = ExecutionManager(cfg, PaperBroker(cfg))
        await manager.start()
        manager.broker.set_price(4000.0)
        await manager.on_signal(make_signal(Direction.BUY, 4000.0), TS)
        return manager

    async def test_break_even_moves_the_stop_after_one_r(self, config):
        manager = await self._open(config)
        position = (await manager.broker.positions())[0]
        original_stop = position.stop_loss

        manager.broker.set_price(4005.5)       # ~1R above entry
        actions = await manager.manage(TS, 4005.5)

        updated = (await manager.broker.positions())[0]
        assert updated.stop_loss > original_stop
        assert updated.stop_loss >= position.entry_price
        assert any("break-even" in a for a in actions)

    async def test_break_even_does_not_fire_early(self, config):
        manager = await self._open(config)
        original = (await manager.broker.positions())[0].stop_loss
        await manager.manage(TS, 4002.0)       # only 0.4R
        assert (await manager.broker.positions())[0].stop_loss == original

    async def test_partial_close_at_tp1(self, config):
        manager = await self._open(config)
        opened = (await manager.broker.positions())[0]
        assert opened.volume == pytest.approx(0.2)

        manager.broker.set_price(4008.0)       # past TP1 at 4007.5
        actions = await manager.manage(TS, 4008.0)

        remaining = await manager.broker.positions()
        assert remaining[0].volume < 0.2
        assert any("TP1" in a for a in actions)

    async def test_trailing_stop_only_moves_forward(self, config):
        manager = await self._open(config)
        manager.broker.set_price(4012.0)
        await manager.manage(TS, 4012.0)
        after_trail = (await manager.broker.positions())[0].stop_loss

        # Price pulls back — the stop must NOT follow it down.
        manager.broker.set_price(4009.0)
        await manager.manage(TS, 4009.0)
        assert (await manager.broker.positions())[0].stop_loss == pytest.approx(after_trail)

    async def test_plan_is_dropped_when_the_broker_closes_the_position(self, config):
        manager = await self._open(config)
        ticket = (await manager.broker.positions())[0].ticket
        assert ticket in manager.plans

        await manager.broker.close_position(ticket)
        await manager.manage(TS, 4000.0)
        assert ticket not in manager.plans

    async def test_unknown_position_is_left_strictly_alone(self, config):
        """After a restart we own positions with no plan; do not touch them."""
        manager = await self._open(config)
        position = (await manager.broker.positions())[0]
        manager.plans.clear()

        await manager.manage(TS, 4020.0)
        assert (await manager.broker.positions())[0].stop_loss == position.stop_loss

    async def test_paper_broker_resolves_a_stop_hit(self, config):
        manager = await self._open(config)
        candle = Candle(TS, 4000, 4001, 3990, 3992, 100)
        events = await manager.broker.apply_bar(candle)
        assert events
        assert await manager.broker.positions() == []
        assert manager.broker.balance < manager.broker.starting_balance

    async def test_snapshot_is_serialisable(self, config):
        import json

        manager = await self._open(config)
        snapshot = await manager.snapshot()
        json.dumps(snapshot, default=str)
        assert snapshot["armed"] is True
        assert snapshot["account"]["is_demo"] is True
        assert len(snapshot["positions"]) == 1


class TestBrokerFactory:
    def test_paper_is_the_default(self, config):
        assert isinstance(build_broker(config), PaperBroker)

    def test_unknown_mode_raises(self, config):
        with pytest.raises(ValueError):
            build_broker(config.override({"execution": {"mode": "nonsense"}}))

    def test_symbol_spec_rounds_volume(self):
        spec = SymbolSpec("XAUUSD", volume_min=0.01, volume_max=10.0, volume_step=0.01)
        assert spec.round_volume(0.137) == pytest.approx(0.14)
        assert spec.round_volume(0.001) == pytest.approx(0.01)
        assert spec.round_volume(999.0) == pytest.approx(10.0)
