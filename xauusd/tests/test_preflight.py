"""Preflight checks.

This command is what someone runs at the moment they connect real money's
neighbour — a demo terminal — to an automated system. If it reports READY when
it should not, every downstream safety gate is being asked to catch a mistake
that should have been caught here.
"""

from __future__ import annotations

import platform
import sys

import pytest

import fake_mt5
from xauusd.preflight import FAIL, PASS, SKIP, WARN, run_preflight


@pytest.fixture
def windows(monkeypatch):
    """Pretend we are on Windows with the fake terminal available."""
    fake_mt5.state.reset()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "11")
    yield fake_mt5
    fake_mt5.state.reset()


def status_of(flight, name: str) -> str:
    return next(c.status for c in flight.checks if c.name == name)


class TestPreflight:
    def test_demo_account_reports_ready(self, config, windows):
        ok, flight = run_preflight(config)
        assert ok is True
        assert status_of(flight, "Platform") is PASS
        assert status_of(flight, "Account") is PASS
        assert status_of(flight, "Gold symbol") is PASS
        assert "READY" in flight.render(colour=False)

    def test_live_account_is_not_ready(self, config, windows):
        """The check that matters most."""
        windows.state.account.trade_mode = windows.ACCOUNT_TRADE_MODE_REAL
        ok, flight = run_preflight(config)
        assert ok is False
        assert status_of(flight, "Account") is FAIL
        assert "NOT READY" in flight.render(colour=False)

    def test_live_account_warns_when_demo_check_is_disabled(self, config, windows):
        windows.state.account.trade_mode = windows.ACCOUNT_TRADE_MODE_REAL
        cfg = config.override({"execution": {"require_demo_account": False}})
        ok, flight = run_preflight(cfg)
        assert status_of(flight, "Account") is WARN
        assert "Real money" in next(c.fix for c in flight.checks if c.name == "Account")

    def test_algo_trading_disabled_blocks(self, config, windows):
        windows.state.terminal.trade_allowed = False
        ok, flight = run_preflight(config)
        assert ok is False
        assert status_of(flight, "Algo trading") is FAIL

    def test_missing_symbol_blocks(self, config, windows):
        windows.state.known_symbols = ["EURUSD"]
        ok, flight = run_preflight(config)
        assert ok is False
        assert status_of(flight, "Gold symbol") is FAIL

    def test_broker_suffix_is_accepted(self, config, windows):
        windows.state.known_symbols = ["XAUUSD.r"]
        ok, flight = run_preflight(config)
        assert ok is True
        assert flight.symbol == "XAUUSD.r"

    def test_wide_broker_stop_level_warns(self, config, windows):
        # 2000 points x $0.01 = $20 minimum stop, above risk.max_sl_usd of $12.
        windows.state.symbol.trade_stops_level = 2000
        ok, flight = run_preflight(config)
        assert status_of(flight, "Gold symbol") is WARN
        assert "minimum stop" in next(c.fix for c in flight.checks if c.name == "Gold symbol")

    def test_terminal_failure_is_reported(self, config, windows):
        monkey = windows.initialize
        windows.initialize = lambda **kw: False
        try:
            ok, flight = run_preflight(config)
        finally:
            windows.initialize = monkey
        assert ok is False
        assert status_of(flight, "Terminal") is FAIL

    def test_terminal_is_always_shut_down(self, config, windows):
        """A preflight that leaves the terminal attached would block the monitor."""
        run_preflight(config)
        assert windows.state.initialized is False

    def test_nothing_is_traded_by_a_preflight(self, config, windows):
        run_preflight(config)
        assert windows.state.sent == []
        assert windows.state.positions == {}


class TestNonWindows:
    def test_linux_fails_platform_and_skips_the_rest(self, config, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "release", lambda: "6.1")
        ok, flight = run_preflight(config)
        assert ok is False
        assert status_of(flight, "Platform") is FAIL
        for name in ("Terminal", "Account", "Gold symbol"):
            assert status_of(flight, name) is SKIP
        # The fix must point at the paper-mode fallback, not just say "no".
        assert "paper" in next(c.fix for c in flight.checks if c.name == "Platform")

    def test_missing_package_is_reported_with_the_install_command(self, config, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setitem(sys.modules, "MetaTrader5", None)
        monkeypatch.delitem(sys.modules, "MetaTrader5")

        import builtins
        real_import = builtins.__import__

        def blocked(name, *a, **kw):
            if name == "MetaTrader5":
                raise ImportError("no module")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", blocked)
        ok, flight = run_preflight(config)
        assert ok is False
        assert status_of(flight, "MetaTrader5 package") is FAIL
        assert "requirements-mt5" in next(
            c.fix for c in flight.checks if c.name == "MetaTrader5 package")
