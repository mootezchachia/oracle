"""Adaptive parameter optimisation from realised outcomes.

Two independent products, both derived from the journal:

**Evidence weights.** For each evidence code, compare the win rate of signals
that carried it against the baseline win rate of all resolved signals. Codes
that consistently preceded winners get scaled up; codes that preceded losers
get scaled down. The multiplier is bounded on both sides and only applied once
a minimum sample size exists.

**Confidence calibration.** Bucket resolved signals by confidence and measure
the realised hit rate in each bucket. This is what turns the checklist score
into an honest probability — and it is usually humbling, which is the point.

Two guardrails are deliberate:

* **Bounded adjustment.** Weights can move at most ±35% by default. Without a
  bound, a handful of unlucky trades can flip the model's character entirely.
* **Shrinkage toward the baseline.** Small samples are pulled toward "no
  adjustment" so that a 6-sample 100% win rate does not double a weight. This
  is a simple empirical-Bayes shrink, not a claim of statistical significance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..config import Config
from ..logging_setup import get_logger
from ..models import UTC, clamp
from .journal import LOSS_OUTCOMES, WIN_OUTCOMES, Journal

log = get_logger("learning.optimizer")

# Sample size at which an observed rate is trusted at (roughly) half weight.
_SHRINK_PRIOR = 20.0


@dataclass(slots=True)
class CodeStat:
    code: str
    samples: int
    wins: int
    losses: int
    win_rate: float
    avg_r: float
    multiplier: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "samples": self.samples,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate * 100, 1),
            "avg_r": round(self.avg_r, 2),
            "multiplier": round(self.multiplier, 3),
        }


@dataclass(slots=True)
class OptimizationResult:
    baseline_win_rate: float
    samples: int
    weights: dict[str, float] = field(default_factory=dict)
    multipliers: dict[str, float] = field(default_factory=dict)
    calibration: dict[int, float] = field(default_factory=dict)
    stats: list[CodeStat] = field(default_factory=list)
    applied: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_win_rate": round(self.baseline_win_rate * 100, 1),
            "samples": self.samples,
            "applied": self.applied,
            "detail": self.detail,
            "multipliers": {k: round(v, 3) for k, v in self.multipliers.items()},
            "calibration": {str(k): round(v * 100, 1) for k, v in self.calibration.items()},
            "stats": [s.to_dict() for s in self.stats],
        }


class Optimizer:
    def __init__(self, config: Config, journal: Journal) -> None:
        cfg = config.section("learning.optimize")
        self.enabled = bool(cfg.get("enabled", True))
        self.min_samples = int(cfg.get("min_samples", 25))
        self.max_multiplier = float(cfg.get("max_weight_multiplier", 1.35))
        self.min_multiplier = float(cfg.get("min_weight_multiplier", 0.65))
        self.rerun_hours = float(cfg.get("rerun_every_hours", 24))
        self.journal = journal
        self.last_run: datetime | None = None

    def due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.last_run is None:
            return True
        return (now - self.last_run).total_seconds() / 3600.0 >= self.rerun_hours

    # -- core ----------------------------------------------------------------
    def run(self, base_weights: Mapping[str, float], now: datetime | None = None) -> OptimizationResult:
        rows = self.journal.resolved()
        # Always timezone-aware: `due()` compares this against an aware
        # clock, and mixing the two raises rather than misbehaving quietly.
        self.last_run = now or datetime.now(UTC)

        if len(rows) < self.min_samples:
            return OptimizationResult(
                baseline_win_rate=_win_rate(rows),
                samples=len(rows),
                weights=dict(base_weights),
                detail=f"{len(rows)}/{self.min_samples} resolved signals — not enough to optimise",
            )

        baseline = _win_rate(rows)
        evidence = self.journal.evidence_for([r["id"] for r in rows])
        outcome_by_id = {r["id"]: r for r in rows}

        buckets: dict[str, list[dict[str, Any]]] = {}
        for signal_id, items in evidence.items():
            row = outcome_by_id.get(signal_id)
            if row is None:
                continue
            for item in items:
                # Only credit evidence that actually supported the trade.
                if item["direction"] != row["direction"]:
                    continue
                buckets.setdefault(item["code"], []).append(row)

        stats: list[CodeStat] = []
        multipliers: dict[str, float] = {}

        for code, matched in sorted(buckets.items()):
            wins = sum(1 for r in matched if r["outcome"] in {o.value for o in WIN_OUTCOMES})
            losses = sum(1 for r in matched if r["outcome"] in {o.value for o in LOSS_OUTCOMES})
            decided = wins + losses
            if decided == 0:
                continue

            observed = wins / decided
            # Shrink toward the baseline in proportion to sample size.
            confidence_weight = decided / (decided + _SHRINK_PRIOR)
            adjusted = baseline + (observed - baseline) * confidence_weight

            edge = adjusted - baseline
            multiplier = clamp(1.0 + edge * 1.5, self.min_multiplier, self.max_multiplier)

            avg_r = sum(float(r["realised_r"] or 0.0) for r in matched) / len(matched)
            stats.append(CodeStat(code, len(matched), wins, losses, observed, avg_r, multiplier))
            multipliers[code] = multiplier

        weights = {
            code: float(base) * multipliers.get(code, 1.0) for code, base in base_weights.items()
        }
        calibration = self.calibrate(rows)

        stats.sort(key=lambda s: s.multiplier, reverse=True)
        best = ", ".join(f"{s.code} x{s.multiplier:.2f}" for s in stats[:3])
        worst = ", ".join(f"{s.code} x{s.multiplier:.2f}" for s in stats[-3:])

        log.info(
            "optimiser: %d resolved signals, baseline win rate %.0f%%. Up: %s | Down: %s",
            len(rows), baseline * 100, best or "—", worst or "—",
        )

        return OptimizationResult(
            baseline_win_rate=baseline,
            samples=len(rows),
            weights=weights,
            multipliers=multipliers,
            calibration=calibration,
            stats=stats,
            applied=True,
            detail=f"Optimised from {len(rows)} resolved signals",
        )

    def calibrate(self, rows: Sequence[Mapping[str, Any]], bucket_size: int = 5) -> dict[int, float]:
        """Realised hit rate per confidence bucket."""
        buckets: dict[int, list[int]] = {}
        win_values = {o.value for o in WIN_OUTCOMES}
        loss_values = {o.value for o in LOSS_OUTCOMES}

        for row in rows:
            outcome = row["outcome"]
            if outcome not in win_values and outcome not in loss_values:
                continue
            bucket = int(float(row["confidence"]) // bucket_size) * bucket_size
            buckets.setdefault(bucket, []).append(1 if outcome in win_values else 0)

        # A bucket needs a handful of samples before it is worth believing.
        return {
            bucket: sum(values) / len(values)
            for bucket, values in buckets.items()
            if len(values) >= 5
        }


def _win_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    win_values = {o.value for o in WIN_OUTCOMES}
    loss_values = {o.value for o in LOSS_OUTCOMES}
    decided = [r for r in rows if r["outcome"] in win_values or r["outcome"] in loss_values]
    if not decided:
        return 0.5
    return sum(1 for r in decided if r["outcome"] in win_values) / len(decided)
