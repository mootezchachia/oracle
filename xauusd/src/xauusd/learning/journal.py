"""Signal journal — the memory the system learns from.

Every decision is logged, not just the ones that became signals, because the
record of what was *rejected* and why is what tells you whether the filters are
too tight or too loose. Every emitted signal is stored with its full evidence
set and market context, then resolved against subsequent price action.

Resolution walks forward bar by bar and asks, in order: did the stop trade
before the first target? That ordering matters. A bar whose range spans both
the stop and TP1 is ambiguous at bar resolution, and the honest assumption for
a monitoring system is the pessimistic one — the stop was hit first. Anything
else quietly inflates the win rate you then calibrate against.

SQLite is used deliberately: one file, no server, survives container restarts
when the data directory is mounted, and queryable with any tool.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, Candle, Decision, Direction, Signal, SignalOutcome

log = get_logger("learning.journal")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    confidence      REAL NOT NULL,
    raw_score       REAL NOT NULL,
    probability     REAL NOT NULL,
    entry           REAL NOT NULL,
    stop_loss       REAL NOT NULL,
    tp1             REAL,
    tp2             REAL,
    tp3             REAL,
    rr1             REAL,
    lots            REAL,
    risk_amount     REAL,
    session         TEXT,
    kill_zone       TEXT,
    in_overlap      INTEGER,
    volatility      TEXT,
    news_severity   TEXT,
    outcome         TEXT NOT NULL DEFAULT 'PENDING',
    resolved_ts     TEXT,
    mfe_r           REAL DEFAULT 0,
    mae_r           REAL DEFAULT 0,
    realised_r      REAL,
    payload         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_evidence (
    signal_id       TEXT NOT NULL,
    code            TEXT NOT NULL,
    label           TEXT,
    direction       TEXT NOT NULL,
    weight          REAL NOT NULL,
    score           REAL NOT NULL,
    contribution    REAL NOT NULL,
    timeframe       TEXT,
    PRIMARY KEY (signal_id, code),
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decisions (
    ts              TEXT NOT NULL,
    direction       TEXT NOT NULL,
    confidence      REAL NOT NULL,
    raw_score       REAL NOT NULL,
    actionable      INTEGER NOT NULL,
    veto            TEXT,
    reason          TEXT,
    session         TEXT,
    price           REAL
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_evidence_code ON signal_evidence(code);
"""

WIN_OUTCOMES = {SignalOutcome.TP1, SignalOutcome.TP2, SignalOutcome.TP3}
LOSS_OUTCOMES = {SignalOutcome.STOPPED}


class Journal:
    """Thread-safe SQLite-backed signal log."""

    def __init__(self, config: Config) -> None:
        cfg = config.section("learning")
        self.enabled = bool(cfg.get("enabled", True))
        self.path = Path(str(cfg.get("db_path", "data/journal.sqlite3")))
        self.resolve_after = timedelta(hours=float(cfg.get("resolve_after_hours", 12)))
        self._lock = threading.Lock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- writing --------------------------------------------------------------
    def record_decision(self, decision: Decision) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO decisions (ts, direction, confidence, raw_score, actionable, veto, reason, session, price)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    decision.ts.isoformat(),
                    decision.direction.value,
                    decision.confidence,
                    decision.raw_score,
                    int(decision.actionable),
                    decision.vetoes[0].code if decision.vetoes else None,
                    decision.reason,
                    decision.context.session,
                    decision.context.price,
                ),
            )
            conn.commit()

    def record_signal(self, signal: Signal) -> None:
        if not self.enabled:
            return
        plan = signal.risk
        tps = list(plan.take_profits) + [None, None, None]
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO signals (id, ts, symbol, direction, confidence, raw_score, probability,"
                " entry, stop_loss, tp1, tp2, tp3, rr1, lots, risk_amount, session, kill_zone, in_overlap,"
                " volatility, news_severity, outcome, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    signal.id,
                    signal.ts.isoformat(),
                    signal.symbol,
                    signal.direction.value,
                    signal.confidence,
                    signal.raw_score,
                    signal.probability,
                    plan.entry,
                    plan.stop_loss,
                    tps[0], tps[1], tps[2],
                    plan.primary_rr,
                    plan.lot_size,
                    plan.risk_amount,
                    signal.context.session,
                    signal.context.kill_zone,
                    int(signal.context.in_overlap),
                    signal.context.volatility_regime.value,
                    signal.context.news_severity.value,
                    signal.outcome.value,
                    json.dumps(signal.to_dict(), default=str),
                ),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO signal_evidence"
                " (signal_id, code, label, direction, weight, score, contribution, timeframe)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        signal.id, e.code, e.label, e.direction.value,
                        e.weight, e.score, e.contribution,
                        e.timeframe.value if e.timeframe else None,
                    )
                    for e in signal.evidence
                ],
            )
            conn.commit()
        log.info("journalled signal %s (%s @ %.2f)", signal.id, signal.direction.value, plan.entry)

    def update_outcome(
        self,
        signal_id: str,
        outcome: SignalOutcome,
        resolved_ts: datetime,
        mfe_r: float,
        mae_r: float,
        realised_r: float,
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "UPDATE signals SET outcome=?, resolved_ts=?, mfe_r=?, mae_r=?, realised_r=? WHERE id=?",
                (outcome.value, resolved_ts.isoformat(), mfe_r, mae_r, realised_r, signal_id),
            )
            conn.commit()
        log.info("signal %s resolved: %s (%.2fR)", signal_id, outcome.value, realised_r)

    # -- reading --------------------------------------------------------------
    def pending(self, now: datetime | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM signals WHERE outcome='PENDING' ORDER BY ts").fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def resolved(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE outcome != 'PENDING' ORDER BY ts"
            ).fetchall()
        return [dict(r) for r in rows]

    def evidence_for(self, signal_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        if not self.enabled or not signal_ids:
            return {}
        placeholders = ",".join("?" * len(signal_ids))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM signal_evidence WHERE signal_id IN ({placeholders})", tuple(signal_ids)
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["signal_id"], []).append(dict(row))
        return grouped

    def decision_stats(self, days: int = 7) -> dict[str, Any]:
        """Why the engine said no — the most useful diagnostic there is."""
        if not self.enabled:
            return {}
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with closing(self._connect()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM decisions WHERE ts >= ?", (cutoff,)).fetchone()[0]
            actionable = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE ts >= ? AND actionable=1", (cutoff,)
            ).fetchone()[0]
            vetoes = conn.execute(
                "SELECT veto, COUNT(*) AS n FROM decisions WHERE ts >= ? AND veto IS NOT NULL"
                " GROUP BY veto ORDER BY n DESC",
                (cutoff,),
            ).fetchall()
        return {
            "window_days": days,
            "evaluations": total,
            "signals": actionable,
            "veto_breakdown": {r["veto"]: r["n"] for r in vetoes},
        }


# ---------------------------------------------------------------------------
# Outcome resolution
# ---------------------------------------------------------------------------
def resolve_outcome(
    direction: Direction,
    entry: float,
    stop: float,
    targets: Sequence[float],
    candles: Sequence[Candle],
    expire_after: timedelta | None = None,
) -> tuple[SignalOutcome, datetime | None, float, float, float]:
    """Replay price after a signal.

    Returns ``(outcome, resolved_ts, mfe_r, mae_r, realised_r)`` where the R
    figures are expressed in units of initial risk.
    """
    risk = abs(entry - stop)
    if risk <= 0 or not candles:
        return SignalOutcome.PENDING, None, 0.0, 0.0, 0.0

    sign = direction.sign
    mfe = 0.0
    mae = 0.0
    best_target = -1
    start = candles[0].ts

    for candle in candles:
        if expire_after is not None and candle.ts - start > expire_after:
            break

        favourable = (candle.high - entry) * sign if sign > 0 else (entry - candle.low) * -sign
        adverse = (entry - candle.low) * sign if sign > 0 else (candle.high - entry) * -sign
        mfe = max(mfe, favourable / risk)
        mae = max(mae, adverse / risk)

        stop_hit = candle.low <= stop if sign > 0 else candle.high >= stop

        hit_index = -1
        for i, target in enumerate(targets):
            reached = candle.high >= target if sign > 0 else candle.low <= target
            if reached:
                hit_index = i

        # Pessimistic tie-break: within a single bar we cannot know the order,
        # so a bar that touches both is scored as a loss (or as the partial
        # already banked, if an earlier bar had reached a target).
        if stop_hit:
            if best_target < 0:
                return SignalOutcome.STOPPED, candle.ts, mfe, mae, -1.0
            # Stop after a target was already reached — treated as banked at
            # that target with the remainder exiting at break-even.
            outcome = [SignalOutcome.TP1, SignalOutcome.TP2, SignalOutcome.TP3][min(best_target, 2)]
            realised = _r_for_target(entry, targets[best_target], risk, sign)
            return outcome, candle.ts, mfe, mae, realised

        if hit_index >= 0:
            best_target = max(best_target, hit_index)
            if best_target >= len(targets) - 1:
                realised = _r_for_target(entry, targets[best_target], risk, sign)
                return SignalOutcome.TP3, candle.ts, mfe, mae, realised

    if best_target >= 0:
        outcome = [SignalOutcome.TP1, SignalOutcome.TP2, SignalOutcome.TP3][min(best_target, 2)]
        realised = _r_for_target(entry, targets[best_target], risk, sign)
        return outcome, candles[-1].ts, mfe, mae, realised

    if expire_after is not None and candles[-1].ts - start >= expire_after:
        # Expired flat: mark to market at the last close.
        realised = (candles[-1].close - entry) * sign / risk
        return SignalOutcome.EXPIRED, candles[-1].ts, mfe, mae, realised

    return SignalOutcome.PENDING, None, mfe, mae, 0.0


def _r_for_target(entry: float, target: float, risk: float, sign: int) -> float:
    return (target - entry) * sign / risk if risk else 0.0


def resolve_pending(journal: Journal, candles: Sequence[Candle], now: datetime | None = None) -> int:
    """Resolve every pending signal against a candle series. Returns the count."""
    if not journal.enabled:
        return 0
    now = now or datetime.now(UTC)
    resolved = 0

    for row in journal.pending(now):
        signal_ts = datetime.fromisoformat(row["ts"])
        after = [c for c in candles if c.ts > signal_ts]
        if not after:
            continue
        targets = [row[key] for key in ("tp1", "tp2", "tp3") if row[key] is not None]
        outcome, ts, mfe, mae, realised = resolve_outcome(
            Direction(row["direction"]),
            float(row["entry"]),
            float(row["stop_loss"]),
            targets,
            after,
            journal.resolve_after,
        )
        if outcome is SignalOutcome.PENDING:
            continue
        journal.update_outcome(row["id"], outcome, ts or now, mfe, mae, realised)
        resolved += 1
    return resolved
