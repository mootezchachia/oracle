"""Preflight checks — everything that must be true before execution can arm.

Connecting a strategy to a live terminal fails in a dozen small ways, and each
one produces a different unhelpful error somewhere else. This runs every check
in order, reports exactly which one failed, and tells you the fix.

Nothing here places an order or modifies anything. It is safe to run at any
time, including while the monitor is running.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

_ICON = {PASS: "✓", WARN: "!", FAIL: "✗", SKIP: "–"}
_COLOUR = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}
_RESET = "\033[0m"


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, WARN, SKIP)


class Preflight:
    """Runs the checks and accumulates results."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.checks: list[Check] = []
        self.mt5: Any = None
        self.symbol: str | None = None

    def _add(self, name: str, status: str, detail: str, fix: str = "") -> Check:
        check = Check(name, status, detail, fix)
        self.checks.append(check)
        return check

    # -- individual checks ---------------------------------------------------
    def check_platform(self) -> bool:
        system = platform.system()
        release = platform.release()
        if system == "Windows":
            self._add("Platform", PASS, f"Windows {release}")
            return True
        self._add(
            "Platform", FAIL, f"{system} {release}",
            "MetaTrader 5's Python API is Windows-only — it talks to the terminal "
            "over Windows named pipes, so there is no Linux or macOS build.\n"
            "        Options: run on Windows, a Windows VM (Parallels/UTM/VirtualBox),\n"
            "        a cheap Windows VPS, or stay in paper mode:\n"
            "          XAUUSD_EXECUTION__MODE=paper python -m xauusd run",
        )
        return False

    def check_python(self) -> bool:
        major, minor = sys.version_info[:2]
        version = f"{major}.{minor}.{sys.version_info[2]}"
        if (major, minor) >= (3, 11):
            self._add("Python", PASS, version)
            return True
        self._add("Python", FAIL, version, "This project needs Python 3.11 or newer.")
        return False

    def check_package(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError:
            self._add(
                "MetaTrader5 package", FAIL, "not installed",
                "pip install -r requirements-mt5.txt",
            )
            return False
        self.mt5 = mt5
        version = getattr(mt5, "__version__", "unknown")
        self._add("MetaTrader5 package", PASS, str(version))
        return True

    def check_terminal(self) -> bool:
        mt5 = self.mt5
        cfg = self.config.section("data.mt5")
        kwargs: dict[str, Any] = {}
        if cfg.get("terminal_path", None):
            kwargs["path"] = str(cfg.get("terminal_path"))
        if cfg.get("login", None):
            kwargs["login"] = int(cfg.get("login"))
            kwargs["password"] = str(cfg.get("password") or "")
            kwargs["server"] = str(cfg.get("server") or "")

        if not mt5.initialize(**kwargs):
            self._add(
                "Terminal", FAIL, f"initialize failed: {mt5.last_error()}",
                "Open the MetaTrader 5 terminal and log into an account first.\n"
                "        If it is open, set data.mt5.terminal_path to terminal64.exe.",
            )
            return False

        info = mt5.terminal_info()
        name = getattr(info, "name", "?")
        build = getattr(info, "build", "?")
        self._add("Terminal", PASS, f"{name}, build {build}")

        if info is not None and not getattr(info, "trade_allowed", True):
            self._add(
                "Algo trading", FAIL, "disabled in the terminal",
                "Click the 'Algo Trading' button in the MT5 toolbar so it turns green\n"
                "        (or Tools -> Options -> Expert Advisors -> Allow algorithmic trading).\n"
                "        Orders are rejected until this is on.",
            )
            return False
        self._add("Algo trading", PASS, "enabled")
        return True

    def check_account(self) -> bool:
        mt5 = self.mt5
        info = mt5.account_info()
        if info is None:
            self._add("Account", FAIL, "could not read account", "Log into an account in the terminal.")
            return False

        mode = getattr(info, "trade_mode", None)
        demo_modes = {
            getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0),
            getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1),
        }
        is_demo = mode in demo_modes
        label = "DEMO" if mode == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0) else (
            "CONTEST" if mode == getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1) else "REAL"
        )
        detail = (
            f"{label} #{getattr(info, 'login', '?')} on {getattr(info, 'server', '?')} — "
            f"{getattr(info, 'balance', 0):,.2f} {getattr(info, 'currency', '')}"
        )

        if is_demo:
            self._add("Account", PASS, detail)
            return True

        require_demo = bool(self.config.get("execution.require_demo_account", True))
        if require_demo:
            self._add(
                "Account", FAIL, detail,
                "This is a LIVE account. Execution will refuse to arm, by design.\n"
                "        Log into a DEMO account in the terminal — that is what you want\n"
                "        for a system with no verified track record.",
            )
            return False
        self._add(
            "Account", WARN, detail,
            "LIVE account with require_demo_account disabled. Real money is at risk.",
        )
        return True

    def check_symbol(self) -> bool:
        mt5 = self.mt5
        candidates = [
            str(self.config.get("data.mt5.symbol", "XAUUSD")),
            "XAUUSD", "XAUUSD.r", "XAUUSD.m", "XAUUSDm", "GOLD", "XAUUSD_", "XAUUSD.a",
        ]
        found = None
        for name in candidates:
            if mt5.symbol_info(name) is not None and mt5.symbol_select(name, True):
                found = name
                break
        if found is None:
            for sym in mt5.symbols_get() or []:
                upper = sym.name.upper()
                if "XAU" in upper and "USD" in upper and mt5.symbol_select(sym.name, True):
                    found = sym.name
                    break

        if found is None:
            self._add(
                "Gold symbol", FAIL, "not found on this server",
                "Open Market Watch (Ctrl+M), right-click -> Symbols, and enable your\n"
                "        broker's gold instrument. Then set data.mt5.symbol to its exact name.",
            )
            return False

        self.symbol = found
        info = mt5.symbol_info(found)
        tick = mt5.symbol_info_tick(found)
        spread = (float(tick.ask) - float(tick.bid)) if tick else 0.0
        stops = int(getattr(info, "trade_stops_level", 0)) * float(getattr(info, "point", 0.01))

        allowlist = {str(s).upper() for s in self.config.get("execution.symbol_allowlist", ["XAUUSD"])}
        normalised = found.upper().replace(".", "").replace("_", "")
        if not any(a.replace(".", "") in normalised for a in allowlist):
            self._add(
                "Gold symbol", FAIL, f"{found} is not on the allowlist {sorted(allowlist)}",
                f"Add it: execution.symbol_allowlist: [{found}]",
            )
            return False

        detail = (
            f"{found} — spread ${spread:.2f}, lots {getattr(info, 'volume_min', '?')}"
            f"–{getattr(info, 'volume_max', '?')}, min stop ${stops:.2f}"
        )
        max_sl = float(self.config.get("risk.max_sl_usd", 12.0))
        if stops > max_sl:
            self._add("Gold symbol", WARN, detail,
                      f"Broker's minimum stop (${stops:.2f}) exceeds risk.max_sl_usd (${max_sl:.2f}); "
                      "stops will be widened and risk per trade will run higher than configured.")
        elif spread > 1.0:
            self._add("Gold symbol", WARN, detail,
                      f"Spread of ${spread:.2f} is wide for gold. If this persists outside news, "
                      "it will eat a meaningful share of every trade.")
        else:
            self._add("Gold symbol", PASS, detail)
        return True

    def check_config(self) -> None:
        cfg = self.config
        self._add(
            "Risk settings", PASS,
            f"{cfg.get('risk.max_risk_percent', 1)}% per trade, "
            f"balance {cfg.get('risk.account_balance', 0):,.0f}, "
            f"max {cfg.get('execution.max_daily_trades', 4)} trades/day, "
            f"daily stop {cfg.get('execution.max_daily_loss_percent', 3)}%",
        )
        self._add(
            "Signal gate", PASS,
            f"min confidence {cfg.get('signals.min_confidence', 90)}%, "
            f"min R:R {cfg.get('signals.min_rr', 2)}, "
            f"kill zone required: {cfg.get('signals.require_kill_zone', True)}",
        )

        channels = []
        if cfg.get("notify.telegram.enabled", False) and cfg.get("notify.telegram.bot_token", None):
            channels.append("telegram")
        if cfg.get("notify.discord.enabled", False) and cfg.get("notify.discord.webhook_url", None):
            channels.append("discord")
        if channels:
            self._add("Notifications", PASS, ", ".join(channels))
        else:
            self._add(
                "Notifications", WARN, "none configured",
                "Alerts will only reach the dashboard and log. Set "
                "XAUUSD_NOTIFY__TELEGRAM__BOT_TOKEN and __CHAT_ID to get them on your phone.",
            )

        enabled = bool(cfg.get("execution.enabled", False))
        mode = cfg.get("execution.mode", "paper")
        if enabled:
            self._add("Execution", PASS, f"ENABLED, mode={mode}")
        else:
            self._add(
                "Execution", WARN, f"disabled (mode={mode} when enabled)",
                "Monitoring only — no orders will be placed. Enable it with "
                "XAUUSD_EXECUTION__ENABLED=true once the checks above pass.",
            )

    # -- orchestration --------------------------------------------------------
    def run(self) -> bool:
        self.check_python()
        windows = self.check_platform()
        if not windows:
            self._add("Terminal", SKIP, "requires Windows")
            self._add("Account", SKIP, "requires Windows")
            self._add("Gold symbol", SKIP, "requires Windows")
            self.check_config()
            return False

        if not self.check_package():
            self._add("Terminal", SKIP, "MetaTrader5 package missing")
            self._add("Account", SKIP, "MetaTrader5 package missing")
            self._add("Gold symbol", SKIP, "MetaTrader5 package missing")
            self.check_config()
            return False

        try:
            if self.check_terminal():
                if self.check_account():
                    self.check_symbol()
        finally:
            if self.mt5 is not None:
                self.mt5.shutdown()

        self.check_config()
        return all(c.ok for c in self.checks)

    # -- output ---------------------------------------------------------------
    def render(self, colour: bool = True) -> str:
        width = max(len(c.name) for c in self.checks) + 2
        lines = ["", "═" * 74, " XAUUSD SENTINEL — PREFLIGHT", "═" * 74]
        for c in self.checks:
            tint = _COLOUR[c.status] if colour else ""
            reset = _RESET if colour else ""
            lines.append(f" {tint}[{_ICON[c.status]}]{reset} {c.name:<{width}} {c.detail}")
            if c.fix:
                for line in c.fix.split("\n"):
                    lines.append(f"     {'→' if line is c.fix.split(chr(10))[0] else ' '} {line.strip()}"
                                 if line.strip() else "")
        lines.append("═" * 74)

        failures = [c for c in self.checks if c.status == FAIL]
        if failures:
            lines.append(f" NOT READY — fix {len(failures)} item(s) above, then run this again.")
        else:
            warns = [c for c in self.checks if c.status == WARN]
            lines.append(" READY." + (f"  ({len(warns)} warning(s) — read them, they matter.)" if warns else ""))
            lines.append("")
            lines.append(" Arm demo execution with:")
            lines.append("   set XAUUSD_EXECUTION__ENABLED=true")
            lines.append("   set XAUUSD_EXECUTION__MODE=mt5")
            lines.append("   python -m xauusd run")
            lines.append("")
            lines.append(" Then open http://localhost:8787 and watch the Execution card.")
            lines.append(" Emergency stop:  python -m xauusd flatten     (or: type NUL > data\\HALT)")
        lines.append("═" * 74)
        return "\n".join(lines)


def run_preflight(config: Config) -> tuple[bool, Preflight]:
    flight = Preflight(config)
    ok = flight.run()
    return ok, flight
