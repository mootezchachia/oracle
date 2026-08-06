"""Cross-asset correlation desk.

Gold does not trade in isolation. The reliable relationships are:

* **DXY** — inverse. Gold is dollar-denominated; a stronger dollar mechanically
  prices gold lower.
* **US10Y** — inverse. Gold pays no yield, so rising real yields raise the
  opportunity cost of holding it.
* **Silver** — direct, but noisier and higher beta. Confirming, not leading.
* **SP500 / NASDAQ** — no stable sign. Watched for risk-on/risk-off context
  only, never used to veto.

The desk answers one question: *does the rest of the macro complex agree with
the direction the chart is suggesting?* When DXY and yields both disagree, the
setup is fighting the macro tape and confidence is cut hard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..models import Candle, Direction, clamp
from .indicators import pearson, slope


@dataclass(slots=True)
class InstrumentDrift:
    name: str
    expected: str            # inverse | direct | none
    weight: float
    drift_percent: float     # % change over the lookback window
    correlation: float       # realised Pearson correlation vs gold
    agrees: bool | None      # None when `expected` is "none"
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "expected": self.expected,
            "drift_percent": round(self.drift_percent, 3),
            "correlation": round(self.correlation, 2),
            "agrees": self.agrees,
            "detail": self.detail,
        }


@dataclass(slots=True)
class CorrelationReport:
    direction: Direction
    multiplier: float
    agreement_score: float          # -1 (all against) .. +1 (all for)
    instruments: list[InstrumentDrift] = field(default_factory=list)
    detail: str = ""

    @property
    def conflicts(self) -> list[str]:
        return [i.name for i in self.instruments if i.agrees is False]

    @property
    def confirmations(self) -> list[str]:
        return [i.name for i in self.instruments if i.agrees is True]

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "multiplier": round(self.multiplier, 3),
            "agreement_score": round(self.agreement_score, 2),
            "confirmations": self.confirmations,
            "conflicts": self.conflicts,
            "instruments": [i.to_dict() for i in self.instruments],
            "detail": self.detail,
        }


def _percent_drift(closes: Sequence[float], lookback: int) -> float:
    window = list(closes[-lookback:])
    if len(window) < 2 or window[0] == 0:
        return 0.0
    return (window[-1] - window[0]) / abs(window[0]) * 100.0


def analyse_correlations(
    gold: Sequence[Candle],
    peers: Mapping[str, Sequence[Candle]],
    spec: Mapping[str, Mapping[str, object]],
    direction: Direction,
    lookback: int = 30,
    agreement_bonus: float = 1.05,
    conflict_penalty: float = 0.88,
    strong_conflict_penalty: float = 0.75,
) -> CorrelationReport:
    """Score the macro complex against a proposed gold direction."""
    if direction is Direction.NEUTRAL or len(gold) < 3:
        return CorrelationReport(direction, 1.0, 0.0, detail="No direction to test")

    gold_closes = [c.close for c in gold]
    instruments: list[InstrumentDrift] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for name, config in spec.items():
        candles = peers.get(name) or []
        expected = str(config.get("expected", "none"))
        weight = float(config.get("weight", 1.0) or 1.0)
        if len(candles) < 3:
            instruments.append(
                InstrumentDrift(name, expected, weight, 0.0, 0.0, None, "No data")
            )
            continue

        closes = [c.close for c in candles]
        drift = _percent_drift(closes, lookback)
        correlation = pearson(gold_closes[-lookback:], closes[-lookback:])

        agrees: bool | None = None
        detail = "context only"
        if expected in ("inverse", "direct"):
            # The direction this peer should be moving if gold's read is right.
            wanted = -direction.sign if expected == "inverse" else direction.sign
            observed = 1 if drift > 0 else (-1 if drift < 0 else 0)
            if observed == 0 or abs(drift) < 0.02:
                agrees = None
                detail = "flat — no information"
            else:
                agrees = observed == wanted
                detail = (
                    f"{name} {'+' if drift > 0 else ''}{drift:.2f}% "
                    f"({'confirms' if agrees else 'conflicts with'} {direction.value})"
                )
                weighted_sum += weight * (1.0 if agrees else -1.0)
                weight_total += weight

        instruments.append(InstrumentDrift(name, expected, weight, drift, correlation, agrees, detail))

    agreement = weighted_sum / weight_total if weight_total else 0.0

    # DXY and US10Y are the two that actually matter for a veto-grade signal.
    primary_conflicts = [
        i.name for i in instruments if i.agrees is False and i.name in {"DXY", "US10Y"}
    ]

    if len(primary_conflicts) >= 2:
        multiplier = strong_conflict_penalty
        detail = "DXY and US10Y both disagree — fighting the macro tape"
    elif agreement <= -0.34:
        multiplier = conflict_penalty
        detail = f"Correlation desk disagrees ({', '.join(i.name for i in instruments if i.agrees is False)})"
    elif agreement >= 0.5:
        multiplier = agreement_bonus
        detail = "Macro complex confirms"
    else:
        multiplier = 1.0
        detail = "Correlation neutral"

    return CorrelationReport(direction, multiplier, clamp(agreement, -1.0, 1.0), instruments, detail)
