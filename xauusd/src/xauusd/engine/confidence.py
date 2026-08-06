"""The confidence engine.

Turns a raw confluence percentage into the number that gates publication, by
applying the contextual multipliers that a checklist alone cannot capture:

    confidence = raw × session × news × correlation × volatility × alignment

Every multiplier is recorded with its own explanation, so a 94% and an 88% can
be compared line by line rather than taken on faith. Multipliers below 1.0
(risk) are applied without cap; the combined *bonus* is capped, because context
should never be able to manufacture conviction the chart did not earn.

``probability`` is deliberately a separate number from ``confidence``.
Confidence measures how much of the checklist agrees. Probability is the
estimated chance TP1 is reached before the stop, and it is calibrated from the
journal's realised hit-rate once enough samples exist — before that it falls
back to a conservative mapping. Presenting a 94% checklist score as a 94% win
rate would be dishonest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..analysis.correlation import CorrelationReport
from ..analysis.mtf import MTFResult
from ..analysis.volatility import volatility_confidence_multiplier
from ..config import Config
from ..models import Direction, Timeframe, clamp
from ..news.guard import NewsState
from ..sessions.calendar import SessionState
from .confluence import ConfluenceResult

# The most a favourable context is allowed to inflate the raw checklist score.
MAX_CONTEXT_BONUS = 1.12


@dataclass(slots=True)
class Modifier:
    name: str
    value: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "value": round(self.value, 3), "reason": self.reason}


@dataclass(slots=True)
class ConfidenceResult:
    direction: Direction
    raw: float
    confidence: float
    probability: float
    modifiers: list[Modifier] = field(default_factory=list)
    threshold: float = 90.0
    calibrated: bool = False

    @property
    def passes(self) -> bool:
        return self.direction is not Direction.NEUTRAL and self.confidence >= self.threshold

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "raw": round(self.raw, 1),
            "confidence": round(self.confidence, 1),
            "probability": round(self.probability, 1),
            "threshold": round(self.threshold, 1),
            "passes": self.passes,
            "calibrated": self.calibrated,
            "modifiers": [m.to_dict() for m in self.modifiers],
        }


class ConfidenceEngine:
    """Applies contextual multipliers and produces the publishable score."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sig_cfg = config.section("signals")
        # Populated by the learning layer: confidence bucket -> realised hit rate.
        self._calibration: dict[int, float] = {}

    def set_calibration(self, table: Mapping[int, float]) -> None:
        """Install a realised-outcome calibration table from the journal."""
        self._calibration = {int(k): float(v) for k, v in table.items()}

    # -- scoring -------------------------------------------------------------
    def score(
        self,
        confluence: ConfluenceResult,
        mtf: MTFResult,
        session: SessionState,
        news: NewsState,
        correlation: CorrelationReport | None,
        session_min_confidence_bonus: float = 0.0,
    ) -> ConfidenceResult:
        threshold = float(self._sig_cfg.get("min_confidence", 90.0)) + session_min_confidence_bonus

        if confluence.direction is Direction.NEUTRAL or confluence.vetoes:
            return ConfidenceResult(
                confluence.direction, confluence.raw_percent, 0.0, 0.0,
                [Modifier("veto", 0.0, confluence.vetoes[0].reason if confluence.vetoes else "No direction")],
                threshold,
            )

        raw = confluence.raw_percent
        modifiers: list[Modifier] = []

        # --- risk multipliers (uncapped) -----------------------------------
        risk_product = 1.0
        bonus_product = 1.0

        def apply(name: str, value: float, reason: str) -> None:
            nonlocal risk_product, bonus_product
            modifiers.append(Modifier(name, value, reason))
            if value < 1.0:
                risk_product *= value
            else:
                bonus_product *= value

        session_mult = session.multiplier
        apply("session", session_mult, session.primary)

        news_mult = news.multiplier
        apply("news", news_mult, news.reason or "No significant events nearby")

        if correlation is not None:
            apply("correlation", correlation.multiplier, correlation.detail)

        setup_tf = Timeframe(self._config.get("mtf.setup_timeframe", "M15"))
        setup = mtf.get(setup_tf)
        if setup is not None:
            vol_mult = volatility_confidence_multiplier(setup.volatility)
            apply("volatility", vol_mult, setup.volatility.detail)

        # Alignment scales between 0.90 (barely aligned) and 1.04 (unanimous).
        alignment_mult = 0.90 + mtf.alignment_score * 0.14
        apply("alignment", alignment_mult, f"Timeframe agreement {mtf.alignment_score:.0%}")

        bonus_product = min(bonus_product, MAX_CONTEXT_BONUS)
        confidence = clamp(raw * risk_product * bonus_product, 0.0, 99.0)

        probability, calibrated = self._probability(confidence)

        return ConfidenceResult(
            direction=confluence.direction,
            raw=raw,
            confidence=confidence,
            probability=probability,
            modifiers=modifiers,
            threshold=threshold,
            calibrated=calibrated,
        )

    # -- probability ---------------------------------------------------------
    def _probability(self, confidence: float) -> tuple[float, bool]:
        """Estimated chance of reaching TP1 before the stop."""
        if self._calibration:
            bucket = int(confidence // 5) * 5
            for candidate in (bucket, bucket - 5, bucket + 5):
                if candidate in self._calibration:
                    return clamp(self._calibration[candidate] * 100.0, 5.0, 95.0), True

        # Uncalibrated fallback. A 90% checklist score is not a 90% win rate;
        # this maps the 88–99 band onto a deliberately conservative 55–78%.
        if confidence <= 0:
            return 0.0, False
        mapped = 55.0 + (confidence - 88.0) * (23.0 / 11.0)
        return clamp(mapped, 30.0, 78.0), False

    def expectancy(self, probability: float, rr: float) -> float:
        """Expected R per trade — the number that actually decides viability."""
        p = clamp(probability / 100.0, 0.0, 1.0)
        return p * rr - (1.0 - p)
