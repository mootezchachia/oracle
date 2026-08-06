"""Command-line entry point.

    python -m xauusd run                 # live monitor + dashboard
    python -m xauusd analyse             # one-shot evaluation, prints the verdict
    python -m xauusd backtest data.csv   # historical replay
    python -m xauusd calendar            # what the news guard currently sees
    python -m xauusd selftest            # end-to-end check on synthetic data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config, load_config
from .logging_setup import setup_logging
from .models import UTC, Timeframe


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace, config: Config) -> int:
    from .runner import run

    try:
        asyncio.run(run(config, with_dashboard=not args.no_dashboard))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_analyse(args: argparse.Namespace, config: Config) -> int:
    """Fetch live data once, evaluate, print the decision, exit."""
    from .runner import Sentinel

    async def _once() -> int:
        sentinel = Sentinel(config)
        if not await sentinel.start():
            return 1
        try:
            decision = await sentinel.cycle()
        finally:
            await sentinel.stop()

        if args.json:
            print(json.dumps(decision.to_dict(), indent=2, default=str))
            return 0

        context = decision.context
        print("═" * 62)
        print(f" XAUUSD SENTINEL — {decision.ts:%Y-%m-%d %H:%M} UTC")
        print("═" * 62)
        print(f" Price        {context.price:.2f}")
        print(f" Session      {context.session}")
        print(f" Volatility   {context.volatility_regime.value}")
        print(f" Bias         {decision.direction.value}")
        print(f" Confidence   {decision.confidence:.1f}%  (raw {decision.raw_score:.1f}%)")
        print(f" Verdict      {decision.reason}")

        if decision.vetoes:
            print("\n VETOES")
            for veto in decision.vetoes:
                print(f"   ✕ {veto.code}: {veto.reason}")

        supporting = [e for e in decision.evidence if e.direction is decision.direction and e.score >= 0.3]
        if supporting:
            print("\n CONFLUENCE")
            for item in sorted(supporting, key=lambda e: -e.contribution):
                print(f"   ✔ {item.label}  ({item.contribution:.1f})")

        against = [e for e in decision.evidence if e.direction is decision.direction.opposite and e.score >= 0.3]
        if against:
            print("\n AGAINST")
            for item in sorted(against, key=lambda e: -e.contribution):
                print(f"   ✕ {item.label}  ({item.contribution:.1f})")

        if decision.signal is not None:
            plan = decision.signal.risk
            print("\n TRADE PLAN")
            print(f"   Entry   {plan.entry:.2f}")
            print(f"   Stop    {plan.stop_loss:.2f}   (${plan.risk_per_unit:.2f})")
            for i, (tp, rr) in enumerate(zip(plan.take_profits, plan.rr_targets), start=1):
                print(f"   TP{i}     {tp:.2f}   ({rr:.2f}R)")
            print(f"   Size    {plan.lot_size:.2f} lots — risking {plan.risk_percent:.2f}%")
        print("═" * 62)
        return 0

    return asyncio.run(_once())


def cmd_backtest(args: argparse.Namespace, config: Config) -> int:
    from .backtest.engine import Backtester
    from .data.csv_provider import CSVProvider

    path = Path(args.csv)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2

    provider = CSVProvider.from_file(path, Timeframe(args.base))
    span = provider.span
    if span:
        print(f"loaded {path.name}: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}")

    backtester = Backtester(config, provider)

    def progress(done: int, total: int) -> None:
        if total:
            print(f"\r  replaying {done * 100 // total:3d}%", end="", flush=True)

    report = backtester.run(
        step=Timeframe(args.step),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        progress=None if args.json else progress,
    )
    if not args.json:
        print("\r" + " " * 24 + "\r", end="")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.render())

    if args.out:
        Path(args.out).write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
        print(f"\nreport written to {args.out}")
    return 0


def cmd_calendar(args: argparse.Namespace, config: Config) -> int:
    from .news.calendar_feed import EconomicCalendar
    from .news.guard import NewsGuard

    async def _show() -> int:
        calendar = EconomicCalendar(config)
        try:
            await calendar.refresh(force=True)
        finally:
            await calendar.close()

        now = datetime.now(UTC)
        guard = NewsGuard(config, calendar)
        state = guard.evaluate(now)

        print("═" * 74)
        print(f" ECONOMIC CALENDAR — {now:%Y-%m-%d %H:%M} UTC")
        print("═" * 74)
        print(f" Trading {'PAUSED' if state.blocked else 'permitted'}  ·  {state.reason or 'no active restriction'}")
        print(f" Confidence multiplier ×{state.multiplier:.2f}")
        print("─" * 74)
        print(f" {'WHEN':<18}{'CCY':<6}{'SEV':<10}{'EVENT':<40}")
        print("─" * 74)
        for event in calendar.upcoming(now, 60 * float(args.hours))[:40]:
            delta = event.ts - now
            when = f"+{int(delta.total_seconds() // 3600)}h{int(delta.total_seconds() % 3600 // 60):02d}m"
            print(f" {when:<18}{event.currency:<6}{event.severity.value:<10}{event.title[:40]:<40}")
        print("═" * 74)
        return 0

    return asyncio.run(_show())


def cmd_flatten(args: argparse.Namespace, config: Config) -> int:
    """Emergency: close every position this system owns, right now."""
    from .execution import ExecutionManager, build_broker

    async def _flatten() -> int:
        cfg = config.override({"execution": {"enabled": True}})
        manager = ExecutionManager(cfg, build_broker(cfg))
        if not await manager.start():
            print(f"could not connect: {manager.disarm_reason}", file=sys.stderr)
            return 1
        try:
            positions = await manager.broker.positions()
            if not positions:
                print("no open positions")
                return 0
            print(f"closing {len(positions)} position(s)...")
            for result in await manager.flatten("manual flatten"):
                status = "closed" if result.ok else f"FAILED — {result.error}"
                print(f"  ticket {result.ticket}: {status}")
            remaining = await manager.broker.positions()
            if remaining:
                print(f"WARNING: {len(remaining)} position(s) still open", file=sys.stderr)
                return 1
            print("all positions closed")
            return 0
        finally:
            await manager.close()

    return asyncio.run(_flatten())


def cmd_positions(args: argparse.Namespace, config: Config) -> int:
    """Show the account and any positions this system owns."""
    from .execution import ExecutionManager, build_broker

    async def _show() -> int:
        cfg = config.override({"execution": {"enabled": True}})
        manager = ExecutionManager(cfg, build_broker(cfg))
        if not await manager.start():
            print(f"could not connect: {manager.disarm_reason}", file=sys.stderr)
            return 1
        try:
            snapshot = await manager.snapshot()
            if args.json:
                print(json.dumps(snapshot, indent=2, default=str))
                return 0

            account = snapshot["account"] or {}
            print("═" * 70)
            print(f" ACCOUNT  {account.get('login', '—')} @ {account.get('server', '—')}")
            print(f" Type     {account.get('type', '—')}"
                  f"{'  ⚠ REAL MONEY' if account.get('is_demo') is False else ''}")
            print(f" Balance  {account.get('balance', 0):.2f} {account.get('currency', '')}"
                  f"   Equity {account.get('equity', 0):.2f}")
            print(f" Broker   {snapshot['broker']}   armed={snapshot['armed']}")
            if snapshot["kill_switch"]["active"]:
                print(f" HALTED   {snapshot['kill_switch']['reason']}")
            print("─" * 70)

            positions = snapshot["positions"]
            if not positions:
                print(" No open positions.")
            for position in positions:
                print(f" #{position['ticket']}  {position['direction']:<5}"
                      f" {position['volume']:.2f} lots  entry {position['entry']:.2f}"
                      f"  SL {position['stop_loss']:.2f}  P/L {position['profit']:+.2f}")
            print("═" * 70)
            return 0
        finally:
            await manager.close()

    return asyncio.run(_show())


def cmd_selftest(args: argparse.Namespace, config: Config) -> int:
    """Run the full engine over synthetic candles — no network required."""
    from .engine.signal_engine import SignalEngine
    from .testing import synthetic_market

    candles = synthetic_market()
    engine = SignalEngine(config)
    decision = engine.evaluate(candles, now=candles[Timeframe.M1][-1].ts + timedelta(minutes=1))

    print("═" * 62)
    print(" SELF TEST — synthetic market")
    print("═" * 62)
    for timeframe, series in sorted(candles.items(), key=lambda kv: kv[0].rank, reverse=True):
        print(f"  {timeframe.value:<4} {len(series):>4} bars  last close {series[-1].close:.2f}")
    print("─" * 62)
    print(f"  Direction   {decision.direction.value}")
    print(f"  Confidence  {decision.confidence:.1f}%  (raw {decision.raw_score:.1f}%)")
    print(f"  Evidence    {len(decision.evidence)} checks")
    print(f"  Verdict     {decision.reason}")
    if decision.vetoes:
        for veto in decision.vetoes:
            print(f"    ✕ {veto.code}: {veto.reason}")
    print("═" * 62)
    print(" engine executed end to end")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusd",
        description="XAUUSD Sentinel — institutional-grade Gold monitoring and signal engine",
    )
    parser.add_argument("-c", "--config", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="start the live monitor")
    run_p.add_argument("--no-dashboard", action="store_true", help="run headless")
    run_p.set_defaults(func=cmd_run)

    an_p = sub.add_parser("analyse", aliases=["analyze"], help="one-shot evaluation")
    an_p.add_argument("--json", action="store_true", help="emit JSON")
    an_p.set_defaults(func=cmd_analyse)

    bt_p = sub.add_parser("backtest", help="replay historical candles")
    bt_p.add_argument("csv", help="OHLCV file (MT5 export or generic CSV)")
    bt_p.add_argument("--base", default="M1", choices=[t.value for t in Timeframe], help="file's timeframe")
    bt_p.add_argument("--step", default="M5", choices=[t.value for t in Timeframe], help="evaluation cadence")
    bt_p.add_argument("--start", help="ISO date to start from")
    bt_p.add_argument("--end", help="ISO date to stop at")
    bt_p.add_argument("--json", action="store_true", help="emit JSON")
    bt_p.add_argument("--out", help="write the JSON report to this path")
    bt_p.set_defaults(func=cmd_backtest)

    cal_p = sub.add_parser("calendar", help="show what the news guard sees")
    cal_p.add_argument("--hours", type=float, default=48, help="lookahead window")
    cal_p.set_defaults(func=cmd_calendar)

    pos_p = sub.add_parser("positions", help="show the trading account and open positions")
    pos_p.add_argument("--json", action="store_true", help="emit JSON")
    pos_p.set_defaults(func=cmd_positions)

    flat_p = sub.add_parser("flatten", help="EMERGENCY: close every position this system owns")
    flat_p.set_defaults(func=cmd_flatten)

    st_p = sub.add_parser("selftest", help="run the engine on synthetic data")
    st_p.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.verbose:
        config = config.override({"logging": {"level": "DEBUG"}})
    setup_logging(config)

    return int(args.func(args, config))


if __name__ == "__main__":
    raise SystemExit(main())
