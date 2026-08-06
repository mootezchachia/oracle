"""Backtest performance metrics.

Everything is computed in **R units** (multiples of initial risk) rather than
currency, because R is position-size independent and therefore the only honest
way to compare a strategy across account sizes and over time.

The metrics reported are the ones that actually decide whether a system is
worth running:

* **Win rate** — necessary but nowhere near sufficient on its own.
* **Profit factor** — gross win R divided by gross loss R. Below 1.0 the
  system loses money regardless of how good the win rate looks.
* **Expectancy** — average R per trade. This is the number that compounds.
* **Max drawdown** — the deepest peak-to-trough decline in the R curve, and the
  number that determines whether the system is survivable in practice.
* **Per-session and per-month breakdowns** — a system that only works in the
  London/NY overlap should be told to trade only there.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from ..models import Direction, SignalOutcome


@dataclass(slots=True)
class TradeRecord:
    """One completed simulated trade."""

    id: str
    ts: datetime
    direction: Direction
    entry: float
    stop: float
    targets: list[float]
    confidence: float
    outcome: SignalOutcome
    realised_r: float
    mfe_r: float
    mae_r: float
    exit_ts: datetime | None
    session: str = ""
    kill_zone: str | None = None
    volatility: str = ""

    @property
    def won(self) -> bool:
        return self.realised_r > 0

    @property
    def lost(self) -> bool:
        return self.realised_r < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "direction": self.direction.value,
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "targets": [round(t, 2) for t in self.targets],
            "confidence": round(self.confidence, 1),
            "outcome": self.outcome.value,
            "r": round(self.realised_r, 2),
            "mfe_r": round(self.mfe_r, 2),
            "mae_r": round(self.mae_r, 2),
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None,
            "session": self.session,
            "kill_zone": self.kill_zone,
            "volatility": self.volatility,
        }


@dataclass(slots=True)
class GroupStats:
    label: str
    trades: int
    wins: int
    losses: int
    total_r: float

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided * 100.0 if decided else 0.0

    @property
    def expectancy(self) -> float:
        return self.total_r / self.trades if self.trades else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1),
            "total_r": round(self.total_r, 2),
            "expectancy_r": round(self.expectancy, 3),
        }


@dataclass(slots=True)
class BacktestReport:
    trades: list[TradeRecord] = field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None
    evaluations: int = 0
    veto_breakdown: dict[str, int] = field(default_factory=dict)

    # -- headline numbers -----------------------------------------------------
    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.won]

    @property
    def losses(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.lost]

    @property
    def win_rate(self) -> float:
        decided = len(self.wins) + len(self.losses)
        return len(self.wins) / decided * 100.0 if decided else 0.0

    @property
    def gross_win_r(self) -> float:
        return sum(t.realised_r for t in self.wins)

    @property
    def gross_loss_r(self) -> float:
        return abs(sum(t.realised_r for t in self.losses))

    @property
    def profit_factor(self) -> float:
        if self.gross_loss_r == 0:
            return float("inf") if self.gross_win_r > 0 else 0.0
        return self.gross_win_r / self.gross_loss_r

    @property
    def total_r(self) -> float:
        return sum(t.realised_r for t in self.trades)

    @property
    def expectancy(self) -> float:
        return self.total_r / self.count if self.count else 0.0

    @property
    def average_rr(self) -> float:
        """Average planned reward:risk to TP1 across all taken trades."""
        ratios = [
            abs(t.targets[0] - t.entry) / abs(t.entry - t.stop)
            for t in self.trades
            if t.targets and abs(t.entry - t.stop) > 0
        ]
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def average_win_r(self) -> float:
        return self.gross_win_r / len(self.wins) if self.wins else 0.0

    @property
    def average_loss_r(self) -> float:
        return -self.gross_loss_r / len(self.losses) if self.losses else 0.0

    @property
    def equity_curve(self) -> list[float]:
        curve: list[float] = []
        running = 0.0
        for trade in sorted(self.trades, key=lambda t: t.ts):
            running += trade.realised_r
            curve.append(running)
        return curve

    @property
    def max_drawdown_r(self) -> float:
        peak = 0.0
        worst = 0.0
        for value in self.equity_curve:
            peak = max(peak, value)
            worst = min(worst, value - peak)
        return abs(worst)

    @property
    def max_consecutive_losses(self) -> int:
        run = best = 0
        for trade in sorted(self.trades, key=lambda t: t.ts):
            if trade.lost:
                run += 1
                best = max(best, run)
            elif trade.won:
                run = 0
        return best

    # -- breakdowns ------------------------------------------------------------
    def _group(self, key) -> list[GroupStats]:
        buckets: dict[str, list[TradeRecord]] = defaultdict(list)
        for trade in self.trades:
            buckets[key(trade)].append(trade)
        stats = [
            GroupStats(
                label=label,
                trades=len(group),
                wins=sum(1 for t in group if t.won),
                losses=sum(1 for t in group if t.lost),
                total_r=sum(t.realised_r for t in group),
            )
            for label, group in buckets.items()
        ]
        stats.sort(key=lambda s: s.total_r, reverse=True)
        return stats

    def by_session(self) -> list[GroupStats]:
        return self._group(lambda t: t.session or "UNKNOWN")

    def by_kill_zone(self) -> list[GroupStats]:
        return self._group(lambda t: t.kill_zone or "NONE")

    def by_month(self) -> list[GroupStats]:
        return sorted(self._group(lambda t: t.ts.strftime("%Y-%m")), key=lambda s: s.label)

    def by_direction(self) -> list[GroupStats]:
        return self._group(lambda t: t.direction.value)

    def by_confidence(self, bucket: int = 2) -> list[GroupStats]:
        return sorted(
            self._group(lambda t: f"{int(t.confidence // bucket) * bucket}%"),
            key=lambda s: s.label,
        )

    @property
    def best_session(self) -> GroupStats | None:
        sessions = [s for s in self.by_session() if s.trades >= 3]
        return sessions[0] if sessions else None

    @property
    def worst_session(self) -> GroupStats | None:
        sessions = [s for s in self.by_session() if s.trades >= 3]
        return sessions[-1] if sessions else None

    # -- serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None,
            },
            "evaluations": self.evaluations,
            "trades": self.count,
            "win_rate": round(self.win_rate, 1),
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor != float("inf") else None,
            "total_r": round(self.total_r, 2),
            "expectancy_r": round(self.expectancy, 3),
            "average_rr": round(self.average_rr, 2),
            "average_win_r": round(self.average_win_r, 2),
            "average_loss_r": round(self.average_loss_r, 2),
            "max_drawdown_r": round(self.max_drawdown_r, 2),
            "max_consecutive_losses": self.max_consecutive_losses,
            "by_session": [s.to_dict() for s in self.by_session()],
            "by_kill_zone": [s.to_dict() for s in self.by_kill_zone()],
            "by_month": [s.to_dict() for s in self.by_month()],
            "by_direction": [s.to_dict() for s in self.by_direction()],
            "by_confidence": [s.to_dict() for s in self.by_confidence()],
            "best_session": self.best_session.to_dict() if self.best_session else None,
            "worst_session": self.worst_session.to_dict() if self.worst_session else None,
            "veto_breakdown": dict(sorted(self.veto_breakdown.items(), key=lambda kv: -kv[1])),
            "trade_list": [t.to_dict() for t in self.trades],
        }

    def render(self) -> str:
        """Human-readable console report."""
        if not self.trades:
            lines = [
                "═" * 62,
                " XAUUSD SENTINEL — BACKTEST",
                "═" * 62,
                f" Period       {self.start:%Y-%m-%d} → {self.end:%Y-%m-%d}" if self.start and self.end else "",
                f" Evaluations  {self.evaluations}",
                "",
                " No trades were taken. That is a valid outcome for a system",
                " built to stand aside — check the veto breakdown below.",
                "",
            ]
            for code, count in sorted(self.veto_breakdown.items(), key=lambda kv: -kv[1])[:12]:
                lines.append(f"   {code:<28} {count:>6}")
            lines.append("═" * 62)
            return "\n".join(line for line in lines if line != "")

        pf = "∞" if self.profit_factor == float("inf") else f"{self.profit_factor:.2f}"
        lines = [
            "═" * 62,
            " XAUUSD SENTINEL — BACKTEST",
            "═" * 62,
            f" Period          {self.start:%Y-%m-%d} → {self.end:%Y-%m-%d}" if self.start and self.end else "",
            f" Evaluations     {self.evaluations:,}",
            f" Trades          {self.count}",
            "",
            f" Win rate        {self.win_rate:.1f}%   ({len(self.wins)}W / {len(self.losses)}L)",
            f" Profit factor   {pf}",
            f" Total           {self.total_r:+.2f}R",
            f" Expectancy      {self.expectancy:+.3f}R per trade",
            f" Average RR      {self.average_rr:.2f}",
            f" Avg win / loss  {self.average_win_r:+.2f}R / {self.average_loss_r:+.2f}R",
            f" Max drawdown    {self.max_drawdown_r:.2f}R",
            f" Max losing run  {self.max_consecutive_losses}",
            "",
            "─" * 62,
            " BY SESSION",
        ]
        for stat in self.by_session():
            lines.append(
                f"   {stat.label:<24} {stat.trades:>3} trades  "
                f"{stat.win_rate:>5.1f}%  {stat.total_r:>+7.2f}R"
            )

        lines += ["", "─" * 62, " BY MONTH"]
        for stat in self.by_month():
            lines.append(
                f"   {stat.label:<24} {stat.trades:>3} trades  "
                f"{stat.win_rate:>5.1f}%  {stat.total_r:>+7.2f}R"
            )

        if self.best_session:
            lines += ["", f" Best session    {self.best_session.label} ({self.best_session.total_r:+.2f}R)"]
        if self.worst_session:
            lines.append(f" Worst session   {self.worst_session.label} ({self.worst_session.total_r:+.2f}R)")

        if self.veto_breakdown:
            lines += ["", "─" * 62, " WHY THE ENGINE STOOD ASIDE"]
            for code, count in sorted(self.veto_breakdown.items(), key=lambda kv: -kv[1])[:10]:
                lines.append(f"   {code:<28} {count:>6}")

        lines.append("═" * 62)
        return "\n".join(line for line in lines if line != "")


def build_report(
    trades: Iterable[TradeRecord],
    start: datetime | None = None,
    end: datetime | None = None,
    evaluations: int = 0,
    veto_breakdown: dict[str, int] | None = None,
) -> BacktestReport:
    return BacktestReport(
        trades=sorted(trades, key=lambda t: t.ts),
        start=start,
        end=end,
        evaluations=evaluations,
        veto_breakdown=dict(veto_breakdown or {}),
    )
