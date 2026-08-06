"""Confluence scoring — the checklist a discretionary desk runs before risking size.

Every check produces a piece of :class:`~xauusd.models.Evidence` carrying a
direction, a maximum weight and an earned score (0..1). The net direction is
whichever side accumulates more weighted contribution.

The score itself is **purity × coverage**:

* **Purity** — of the evidence that had something to say, what share supports
  the trade. This is what makes the model quiet: one strong conflicting read
  costs far more than three checks that were simply silent.
* **Coverage** — how much supporting weight was actually accumulated, relative
  to :data:`FULL_CONVICTION_WEIGHT`. Without it, a setup with two confirmations
  and nothing against it would score 100%, which is exactly the kind of thin
  signal this system exists to suppress.

A flawless but thin case tops out near 72%. Only a case that is both clean and
complete reaches the 90s, which is where the publication floor sits.

The weights below are the model's priors, ordered the way an institutional
checklist is: structure and liquidity dominate, oscillators are tie-breakers.
:mod:`xauusd.learning.optimizer` adjusts them from realised outcomes within
bounded multipliers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from ..analysis.correlation import CorrelationReport
from ..analysis.mtf import MTFResult, TimeframeAnalysis
from ..analysis.smc import unfilled_gaps
from ..config import Config
from ..models import (
    BlockKind,
    CandlePattern,
    Direction,
    Evidence,
    StructureEvent,
    Timeframe,
    VolatilityRegime,
    Veto,
    clamp,
)
from ..news.guard import NewsState
from ..sessions.calendar import SessionState

# --- Base weights ----------------------------------------------------------
BASE_WEIGHTS: dict[str, float] = {
    # Market structure and liquidity — the backbone of the model
    "HTF_TREND": 10.0,
    "MTF_ALIGNMENT": 9.0,
    "LIQUIDITY_SWEEP": 9.0,
    "BOS": 8.0,
    "ORDER_BLOCK": 8.0,
    "DISPLACEMENT": 7.0,
    "FVG": 7.0,
    "CHOCH": 6.0,
    "PREMIUM_DISCOUNT": 6.0,
    "OTE": 6.0,
    "BREAKER": 5.0,
    "PO3": 5.0,
    "MITIGATION": 4.0,
    "LIQUIDITY_TARGET": 4.0,
    # Context
    "KILL_ZONE": 6.0,
    "CORRELATION": 6.0,
    "VOLATILITY": 4.0,
    "SESSION": 3.0,
    # Indicators — confirmation, never initiation
    "EMA_STACK": 5.0,
    "VOLUME": 4.0,
    "RSI": 4.0,
    "MACD": 3.0,
    "ADX": 3.0,
    "VWAP": 3.0,
    "BOLLINGER": 2.0,
    # Price action
    "PATTERN": 5.0,
    "FALSE_BREAKOUT": 5.0,
}


# Supporting contribution at which the model considers a case fully made.
# Reaching it requires structure, liquidity, a point of interest, session
# context and indicator agreement all firing together — roughly a third of the
# total available weight, which is what a genuine A+ setup actually looks like.
FULL_CONVICTION_WEIGHT = 52.0


@dataclass(slots=True)
class ConfluenceResult:
    direction: Direction
    evidence: list[Evidence]
    vetoes: list[Veto]
    bull_score: float
    bear_score: float
    evaluable_weight: float
    full_conviction_weight: float = FULL_CONVICTION_WEIGHT

    @property
    def net(self) -> float:
        return self.bull_score - self.bear_score

    @property
    def support(self) -> float:
        """Total contribution pointing the same way as the net direction."""
        return self.bull_score if self.direction is Direction.BUY else self.bear_score

    @property
    def against(self) -> float:
        return self.bear_score if self.direction is Direction.BUY else self.bull_score

    @property
    def purity(self) -> float:
        """Share of the evidence that *spoke* which supports the trade (0..1).

        This is the part that matters most: one strong conflicting signal
        should cost more than three checks that simply had nothing to say.
        """
        total = self.support + self.against
        return self.support / total if total > 0 else 0.0

    @property
    def coverage(self) -> float:
        """How much of a full case was actually made (0..1).

        Purity alone would score a setup with two confirmations and nothing
        against it at 100%. Coverage is what stops that: conviction has to be
        *earned* with weight, not just with an absence of disagreement.
        """
        if self.full_conviction_weight <= 0:
            return 1.0
        return clamp(self.support / self.full_conviction_weight, 0.0, 1.0)

    @property
    def raw_percent(self) -> float:
        """Purity scaled by coverage — the checklist score before context.

        A flawless but thin case tops out around 72%; a flawless and complete
        one reaches 100%. Anything with meaningful disagreement is pulled down
        proportionally.
        """
        if self.direction is Direction.NEUTRAL:
            return 0.0
        return clamp(self.purity * (0.72 + 0.28 * self.coverage) * 100.0, 0.0, 100.0)

    @property
    def supporting(self) -> list[Evidence]:
        return [e for e in self.evidence if e.direction is self.direction]

    @property
    def opposing(self) -> list[Evidence]:
        return [e for e in self.evidence if e.direction is self.direction.opposite]

    def to_dict(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "raw_percent": round(self.raw_percent, 1),
            "purity": round(self.purity, 3),
            "coverage": round(self.coverage, 3),
            "support": round(self.support, 2),
            "against": round(self.against, 2),
            "bull": round(self.bull_score, 2),
            "bear": round(self.bear_score, 2),
            "evaluable_weight": round(self.evaluable_weight, 2),
            "evidence": [e.to_dict() for e in self.evidence],
            "vetoes": [v.to_dict() for v in self.vetoes],
        }


class ConfluenceEngine:
    """Runs every check and returns the assembled evidence set."""

    def __init__(self, config: Config, weights: Mapping[str, float] | None = None) -> None:
        self._config = config
        self._ind_cfg = config.section("indicators")
        self._sig_cfg = config.section("signals")
        self.weights: dict[str, float] = {**BASE_WEIGHTS, **(weights or {})}
        self.full_conviction_weight = float(
            self._sig_cfg.get("full_conviction_weight", FULL_CONVICTION_WEIGHT)
        )

    def weight(self, code: str) -> float:
        return self.weights.get(code, BASE_WEIGHTS.get(code, 1.0))

    # -- entry point ---------------------------------------------------------
    def evaluate(
        self,
        mtf: MTFResult,
        session: SessionState,
        news: NewsState,
        correlation: CorrelationReport | None,
        stale_timeframes: Sequence[Timeframe] = (),
    ) -> ConfluenceResult:
        evidence: list[Evidence] = []
        vetoes: list[Veto] = []
        evaluable = 0.0

        def add(item: Evidence | None, weight_used: float | None = None) -> None:
            nonlocal evaluable
            if item is None:
                return
            evidence.append(item)
            evaluable += weight_used if weight_used is not None else item.weight

        def count_only(code: str) -> None:
            """A check ran and found nothing — it still counts in the denominator."""
            nonlocal evaluable
            evaluable += self.weight(code)

        # --- hard vetoes that make the rest moot ---------------------------
        if session.market_closed:
            vetoes.append(Veto("MARKET_CLOSED", session.closed_reason or "Market closed"))
        if news.blocked:
            vetoes.append(Veto("NEWS_BLACKOUT", news.reason or "News blackout"))
        if stale_timeframes:
            vetoes.append(
                Veto("STALE_DATA", f"No fresh data on {', '.join(t.value for t in stale_timeframes)}")
            )
        if not mtf.aligned:
            vetoes.append(Veto("MTF_MISALIGNED", mtf.detail or "Timeframes disagree"))

        bias = mtf.htf_bias
        if bias is Direction.NEUTRAL:
            vetoes.append(Veto("NO_BIAS", "No directional bias from H4/H1"))
            return self._result(Direction.NEUTRAL, evidence, vetoes, 0.0, 0.0, 0.0)

        setup_tf = Timeframe(self._config.get("mtf.setup_timeframe", "M15"))
        setup = mtf.get(setup_tf)
        h4 = mtf.get(Timeframe.H4)
        h1 = mtf.get(Timeframe.H1)
        m5 = mtf.get(Timeframe.M5)
        m1 = mtf.get(Timeframe.M1)

        if setup is None:
            vetoes.append(Veto("NO_SETUP_TF", f"{setup_tf.value} analysis unavailable"))
            return self._result(Direction.NEUTRAL, evidence, vetoes, 0.0, 0.0, 0.0)

        price = mtf.price
        atr = setup.atr or 0.0

        # --- 1. higher-timeframe trend --------------------------------------
        if h4 is not None:
            add(
                Evidence(
                    "HTF_TREND",
                    f"H4 {h4.bias.value.lower()} — {self._bias_reason(h4)}",
                    h4.bias,
                    self.weight("HTF_TREND"),
                    score=clamp(0.55 + h4.bias_strength * 0.45, 0.0, 1.0),
                    timeframe=Timeframe.H4,
                    detail=h4.structure.trend.label,
                )
            )
        else:
            count_only("HTF_TREND")

        add(
            Evidence(
                "MTF_ALIGNMENT",
                f"Top-down chain aligned ({' → '.join(mtf.chain)})" if mtf.aligned else "Timeframes disagree",
                bias if mtf.aligned else bias.opposite,
                self.weight("MTF_ALIGNMENT"),
                score=mtf.alignment_score,
                detail=mtf.detail,
            )
        )

        # --- 2. structure breaks on H1 and the setup timeframe --------------
        self._structure_evidence(add, count_only, h1, Timeframe.H1)
        self._structure_evidence(add, count_only, setup, setup_tf)

        # --- 3. liquidity sweep ---------------------------------------------
        self._sweep_evidence(add, count_only, setup, m5, atr)

        # --- 4. SMC points of interest --------------------------------------
        self._poi_evidence(add, count_only, setup, price, atr, bias)

        # --- 5. premium / discount and OTE ----------------------------------
        self._pd_evidence(add, count_only, setup, price, bias)

        # --- 6. displacement -------------------------------------------------
        displacement = max(setup.smc.displacement, m5.smc.displacement if m5 else 0.0)
        if displacement > 0:
            add(
                Evidence(
                    "DISPLACEMENT",
                    f"Displacement leg {displacement:.1f}x ATR",
                    bias if displacement >= 0.8 else bias.opposite,
                    self.weight("DISPLACEMENT"),
                    score=clamp(displacement / 2.0, 0.2, 1.0),
                    detail=f"{displacement:.2f} ATR",
                )
            )
        else:
            count_only("DISPLACEMENT")

        # --- 7. power of three ------------------------------------------------
        po3 = setup.smc.po3
        if po3 is not None and po3.direction is not Direction.NEUTRAL:
            add(
                Evidence(
                    "PO3",
                    f"Power of 3: {po3.phase.lower()} — {po3.detail}",
                    po3.direction,
                    self.weight("PO3"),
                    score=0.9 if po3.phase == "MANIPULATION" else 0.5,
                    detail=po3.detail,
                )
            )
        else:
            count_only("PO3")

        # --- 8. session context ----------------------------------------------
        if session.in_kill_zone:
            zone = session.kill_zones[0].replace("_", " ")
            add(
                Evidence(
                    "KILL_ZONE",
                    f"Inside the {zone} kill zone",
                    bias,
                    self.weight("KILL_ZONE"),
                    score=1.0 if session.in_london_ny_overlap else 0.85,
                    detail=session.primary,
                )
            )
        else:
            count_only("KILL_ZONE")

        if session.just_opened or session.in_london_ny_overlap:
            label = (
                "London/New York overlap — Gold's deepest liquidity"
                if session.in_london_ny_overlap
                else f"{', '.join(session.just_opened).replace('_', ' ').title()} open"
            )
            add(Evidence("SESSION", label, bias, self.weight("SESSION"), score=0.9, detail=session.primary))
        else:
            count_only("SESSION")

        # --- 9. correlation desk ----------------------------------------------
        if correlation is not None and correlation.instruments:
            agrees = correlation.agreement_score
            if abs(agrees) < 0.15:
                count_only("CORRELATION")
            else:
                names = correlation.confirmations if agrees > 0 else correlation.conflicts
                add(
                    Evidence(
                        "CORRELATION",
                        f"{', '.join(names)} {'confirm' if agrees > 0 else 'conflict'}",
                        bias if agrees > 0 else bias.opposite,
                        self.weight("CORRELATION"),
                        score=clamp(abs(agrees), 0.0, 1.0),
                        detail=correlation.detail,
                    )
                )
        else:
            count_only("CORRELATION")

        # --- 10. volatility regime --------------------------------------------
        regime = setup.volatility.regime
        if regime in (VolatilityRegime.NEWS_SPIKE, VolatilityRegime.EXTREME):
            vetoes.append(Veto("VOLATILITY", setup.volatility.detail or f"{regime.value} volatility"))
        elif regime is VolatilityRegime.EXPANSION:
            add(
                Evidence(
                    "VOLATILITY", "ATR expansion — moves have room to run", bias,
                    self.weight("VOLATILITY"), score=1.0, detail=setup.volatility.detail,
                )
            )
        elif regime is VolatilityRegime.LOW:
            add(
                Evidence(
                    "VOLATILITY", "Volatility compression — noise dominates", bias.opposite,
                    self.weight("VOLATILITY"), score=0.8, detail=setup.volatility.detail,
                )
            )
        else:
            count_only("VOLATILITY")

        # --- 11. indicators ----------------------------------------------------
        self._indicator_evidence(add, count_only, setup, h1, bias)

        # --- 12. price action --------------------------------------------------
        self._pattern_evidence(add, count_only, setup, m5, m1, bias)

        # --- tally --------------------------------------------------------------
        bull = sum(e.contribution for e in evidence if e.direction is Direction.BUY)
        bear = sum(e.contribution for e in evidence if e.direction is Direction.SELL)
        direction = Direction.from_sign(bull - bear)

        if direction is not bias and direction is not Direction.NEUTRAL:
            vetoes.append(
                Veto("EVIDENCE_CONTRADICTS_BIAS", f"Net evidence is {direction.value} against a {bias.value} HTF bias")
            )

        supporting_count = sum(
            1 for e in evidence if e.direction is direction and e.score >= 0.4
        )
        minimum = int(self._sig_cfg.get("min_evidence_count", 6))
        if supporting_count < minimum:
            vetoes.append(
                Veto("THIN_EVIDENCE", f"Only {supporting_count} confirmations, {minimum} required")
            )

        if self._sig_cfg.get("require_kill_zone", True) and not session.in_kill_zone:
            vetoes.append(Veto("NO_KILL_ZONE", "Outside every ICT kill zone"))

        if self._sig_cfg.get("require_displacement", True) and displacement < 0.8:
            vetoes.append(Veto("NO_DISPLACEMENT", f"No impulsive leg ({displacement:.2f} ATR)"))

        return self._result(direction, evidence, vetoes, bull, bear, evaluable)

    def _result(
        self,
        direction: Direction,
        evidence: list[Evidence],
        vetoes: list[Veto],
        bull: float,
        bear: float,
        evaluable: float,
    ) -> ConfluenceResult:
        return ConfluenceResult(
            direction, evidence, vetoes, bull, bear, evaluable, self.full_conviction_weight
        )

    # -- individual check groups --------------------------------------------
    @staticmethod
    def _bias_reason(analysis: TimeframeAnalysis) -> str:
        """Explain a timeframe's bias in terms of what actually produced it.

        The bias is a weighted vote across structure, EMAs, MACD, VWAP and DI,
        so it can legitimately differ from the raw swing sequence — a market
        printing lower highs while the EMAs flip up is a real, common state.
        Printing "H4 buy — lower highs / lower lows" would look like a bug and
        erode trust in every other line, so when the swing read disagrees the
        label names the components that carried the vote instead.
        """
        trend = analysis.structure.trend
        if trend.direction is analysis.bias:
            return trend.label.lower()

        agreeing = [
            name.replace("_", " ")
            for name, vote in analysis.components.items()
            if vote == analysis.bias.value and name != "structure"
        ]
        if not agreeing:
            return "mixed signals"
        return f"{', '.join(agreeing[:3])} against a {trend.label.lower()} swing sequence"

    def _structure_evidence(
        self, add, count_only, analysis: TimeframeAnalysis | None, timeframe: Timeframe
    ) -> None:
        if analysis is None or analysis.structure.last_break is None:
            count_only("BOS")
            return
        brk = analysis.structure.last_break
        bars_ago = len(analysis.candles) - 1 - brk.index
        # A break loses relevance as price travels away from it.
        freshness = clamp(1.0 - bars_ago / 30.0, 0.15, 1.0)
        code = "BOS" if brk.event is StructureEvent.BOS else "CHOCH"
        label = (
            f"{timeframe.value} {'break of structure' if brk.event is StructureEvent.BOS else 'change of character'}"
            f" {brk.direction.value.lower()} through {brk.broken_level:.2f}"
        )
        add(
            Evidence(
                code, label, brk.direction, self.weight(code),
                score=freshness, timeframe=timeframe,
                detail=f"{bars_ago} bars ago, {brk.displacement:.1f}x ATR",
            )
        )

    def _sweep_evidence(self, add, count_only, setup: TimeframeAnalysis, m5, atr: float) -> None:
        sweeps = list(setup.structure.sweeps)
        if m5 is not None:
            sweeps.extend(m5.structure.sweeps)
        if not sweeps:
            count_only("LIQUIDITY_SWEEP")
            return
        sweep = max(sweeps, key=lambda s: s.ts)
        # Penetration relative to ATR separates a genuine raid from a graze.
        depth = clamp(sweep.penetration / atr, 0.1, 1.0) if atr > 0 else 0.5
        add(
            Evidence(
                "LIQUIDITY_SWEEP",
                f"Liquidity sweep of {sweep.pool.kind.value.replace('_', ' ').lower()} at {sweep.pool.price:.2f}",
                sweep.direction,
                self.weight("LIQUIDITY_SWEEP"),
                score=clamp(0.5 + depth * 0.5, 0.0, 1.0),
                detail=f"penetration {sweep.penetration:.2f}",
            )
        )

    def _poi_evidence(self, add, count_only, setup: TimeframeAnalysis, price: float, atr: float, bias: Direction) -> None:
        tolerance = max(atr * 0.35, 0.30)

        gaps = [g for g in unfilled_gaps(setup.smc.gaps, bias)]
        touching = [g for g in gaps if g.contains(price) or abs(price - g.midpoint) <= tolerance]
        if touching:
            gap = min(touching, key=lambda g: abs(price - g.midpoint))
            add(
                Evidence(
                    "FVG",
                    f"Price reacting to a {bias.value.lower()} fair value gap "
                    f"({gap.bottom:.2f}–{gap.top:.2f})",
                    bias,
                    self.weight("FVG"),
                    score=clamp(1.0 - abs(price - gap.midpoint) / max(tolerance, 1e-9) * 0.4, 0.4, 1.0),
                    detail=f"CE {gap.midpoint:.2f}",
                )
            )
        elif gaps:
            add(
                Evidence(
                    "FVG", f"{len(gaps)} unfilled {bias.value.lower()} FVG(s) below/above price",
                    bias, self.weight("FVG"), score=0.35, detail="not yet tested",
                )
            )
        else:
            count_only("FVG")

        blocks = [b for b in setup.smc.order_blocks if b.direction is bias]
        at_block = [b for b in blocks if b.contains(price, tolerance)]
        if at_block:
            block = max(at_block, key=lambda b: b.displacement)
            add(
                Evidence(
                    "ORDER_BLOCK",
                    f"Respecting a {bias.value.lower()} order block ({block.bottom:.2f}–{block.top:.2f})",
                    bias,
                    self.weight("ORDER_BLOCK"),
                    score=clamp(0.6 + block.displacement / 4.0, 0.4, 1.0),
                    detail=f"formed {block.ts:%d %b %H:%M}, {block.displacement:.1f}x ATR leg",
                )
            )
        else:
            count_only("ORDER_BLOCK")

        breakers = [b for b in setup.smc.breakers if b.direction is bias and b.contains(price, tolerance)]
        if breakers:
            block = breakers[-1]
            add(
                Evidence(
                    "BREAKER", f"Breaker block flipped to {bias.value.lower()} support/resistance",
                    bias, self.weight("BREAKER"), score=0.85,
                    detail=f"{block.bottom:.2f}–{block.top:.2f}",
                )
            )
        else:
            count_only("BREAKER")

        mitigations = [b for b in setup.smc.mitigations if b.direction is bias and b.contains(price, tolerance)]
        if mitigations:
            add(
                Evidence(
                    "MITIGATION", "Mitigation block held on retest", bias,
                    self.weight("MITIGATION"), score=0.8,
                )
            )
        else:
            count_only("MITIGATION")

        # Untapped liquidity in the trade's direction is the natural target.
        targets = [
            p for p in setup.structure.pools
            if not p.swept and not p.internal and p.side is bias
            and ((p.price > price) if bias is Direction.BUY else (p.price < price))
        ]
        if targets:
            target = min(targets, key=lambda p: abs(p.price - price))
            add(
                Evidence(
                    "LIQUIDITY_TARGET",
                    f"Untapped {target.kind.value.replace('_', ' ').lower()} at {target.price:.2f} as the draw",
                    bias, self.weight("LIQUIDITY_TARGET"), score=0.8,
                    detail=f"{abs(target.price - price):.2f} away",
                )
            )
        else:
            count_only("LIQUIDITY_TARGET")

    def _pd_evidence(self, add, count_only, setup: TimeframeAnalysis, price: float, bias: Direction) -> None:
        dealing_range = setup.structure.dealing_range
        if dealing_range is None or dealing_range.size <= 0:
            count_only("PREMIUM_DISCOUNT")
            count_only("OTE")
            return

        position = dealing_range.position(price)
        zone = dealing_range.zone(price)
        # Buy in discount, sell in premium. Buying premium is chasing.
        correct = (bias is Direction.BUY and position < 0.5) or (bias is Direction.SELL and position > 0.5)
        distance = abs(position - 0.5) * 2.0
        add(
            Evidence(
                "PREMIUM_DISCOUNT",
                f"Price in {zone.lower()} ({position:.0%} of range) for a {bias.value.lower()}",
                bias if correct else bias.opposite,
                self.weight("PREMIUM_DISCOUNT"),
                score=clamp(0.4 + distance * 0.6, 0.2, 1.0),
                detail=f"equilibrium {dealing_range.equilibrium:.2f}",
            )
        )

        ote = setup.smc.ote
        if ote is not None and ote.direction is bias:
            if ote.contains(price):
                proximity = 1.0 - clamp(abs(price - ote.sweet_spot) / max(ote.high - ote.low, 1e-9), 0.0, 1.0)
                add(
                    Evidence(
                        "OTE", f"Inside the optimal trade entry band ({ote.low:.2f}–{ote.high:.2f})",
                        bias, self.weight("OTE"), score=clamp(0.6 + proximity * 0.4, 0.5, 1.0),
                        detail=f"sweet spot {ote.sweet_spot:.2f}",
                    )
                )
            else:
                count_only("OTE")
        else:
            count_only("OTE")

    def _indicator_evidence(self, add, count_only, setup: TimeframeAnalysis, h1, bias: Direction) -> None:
        snapshot = setup.indicators

        stack = snapshot.ema_stack
        if stack is not Direction.NEUTRAL:
            add(
                Evidence(
                    "EMA_STACK",
                    f"EMA 20/50/200 stacked {'bullish' if stack is Direction.BUY else 'bearish'}",
                    stack, self.weight("EMA_STACK"), score=1.0, timeframe=setup.timeframe,
                )
            )
        else:
            count_only("EMA_STACK")

        bull_band = list(self._ind_cfg.get("rsi_healthy_bull", [45, 68]))
        bear_band = list(self._ind_cfg.get("rsi_healthy_bear", [32, 55]))
        overbought = float(self._ind_cfg.get("rsi_overbought", 72))
        oversold = float(self._ind_cfg.get("rsi_oversold", 28))
        if snapshot.rsi is None:
            count_only("RSI")
        elif snapshot.rsi_healthy(bias, bull_band, bear_band):
            add(
                Evidence(
                    "RSI", f"RSI {snapshot.rsi:.0f} — healthy, room to run", bias,
                    self.weight("RSI"), score=1.0, timeframe=setup.timeframe,
                )
            )
        elif (bias is Direction.BUY and snapshot.rsi >= overbought) or (
            bias is Direction.SELL and snapshot.rsi <= oversold
        ):
            add(
                Evidence(
                    "RSI", f"RSI {snapshot.rsi:.0f} — stretched, chasing risk", bias.opposite,
                    self.weight("RSI"), score=0.9, timeframe=setup.timeframe,
                )
            )
        else:
            count_only("RSI")

        if snapshot.macd is None or snapshot.macd_signal is None:
            count_only("MACD")
        else:
            rising = snapshot.macd_momentum_rising
            add(
                Evidence(
                    "MACD",
                    f"MACD {'above' if snapshot.macd_bias is Direction.BUY else 'below'} signal"
                    + (" with expanding momentum" if rising else ""),
                    snapshot.macd_bias, self.weight("MACD"),
                    score=1.0 if rising else 0.6, timeframe=setup.timeframe,
                )
            )

        adx_threshold = float(self._ind_cfg.get("adx_trending", 22))
        if snapshot.adx is None:
            count_only("ADX")
        elif snapshot.adx >= adx_threshold:
            add(
                Evidence(
                    "ADX", f"ADX {snapshot.adx:.0f} — trending conditions", bias,
                    self.weight("ADX"), score=clamp((snapshot.adx - adx_threshold) / 20.0 + 0.5, 0.5, 1.0),
                )
            )
        else:
            add(
                Evidence(
                    "ADX", f"ADX {snapshot.adx:.0f} — no trend, range risk", bias.opposite,
                    self.weight("ADX"), score=0.7,
                )
            )

        if snapshot.vwap is None:
            count_only("VWAP")
        else:
            add(
                Evidence(
                    "VWAP",
                    f"Price {'above' if snapshot.vwap_bias is Direction.BUY else 'below'} session VWAP",
                    snapshot.vwap_bias, self.weight("VWAP"), score=0.8, timeframe=setup.timeframe,
                )
            )

        spike = float(self._ind_cfg.get("volume_spike_ratio", 1.6))
        if snapshot.volume_ratio is None:
            count_only("VOLUME")
        elif snapshot.volume_ratio >= spike:
            direction = bias if (snapshot.delta * bias.sign) >= 0 else bias.opposite
            add(
                Evidence(
                    "VOLUME",
                    f"Volume {snapshot.volume_ratio:.1f}x average — institutional participation",
                    direction, self.weight("VOLUME"),
                    score=clamp((snapshot.volume_ratio - 1.0) / 1.5, 0.4, 1.0),
                    detail=f"delta proxy {snapshot.delta:+.0f}",
                )
            )
        elif snapshot.volume_ratio < 0.7:
            add(
                Evidence(
                    "VOLUME", f"Volume only {snapshot.volume_ratio:.1f}x average — no participation",
                    bias.opposite, self.weight("VOLUME"), score=0.6,
                )
            )
        else:
            count_only("VOLUME")

        if None in (snapshot.bb_upper, snapshot.bb_lower, snapshot.bb_mid):
            count_only("BOLLINGER")
        else:
            price = snapshot.price
            if bias is Direction.BUY and price <= snapshot.bb_lower:      # type: ignore[operator]
                add(Evidence("BOLLINGER", "Price at the lower Bollinger band", bias, self.weight("BOLLINGER"), 0.8))
            elif bias is Direction.SELL and price >= snapshot.bb_upper:   # type: ignore[operator]
                add(Evidence("BOLLINGER", "Price at the upper Bollinger band", bias, self.weight("BOLLINGER"), 0.8))
            elif (bias is Direction.BUY and price >= snapshot.bb_upper) or (   # type: ignore[operator]
                bias is Direction.SELL and price <= snapshot.bb_lower           # type: ignore[operator]
            ):
                add(Evidence("BOLLINGER", "Price extended beyond the band", bias.opposite, self.weight("BOLLINGER"), 0.7))
            else:
                count_only("BOLLINGER")

    def _pattern_evidence(self, add, count_only, setup, m5, m1, bias: Direction) -> None:
        hits = list(setup.patterns)
        for analysis in (m5, m1):
            if analysis is not None:
                hits.extend(analysis.patterns)

        false_breaks = [h for h in hits if h.pattern in (
            CandlePattern.FALSE_BREAKOUT_BULL, CandlePattern.FALSE_BREAKOUT_BEAR
        )]
        if false_breaks:
            hit = max(false_breaks, key=lambda h: h.strength)
            add(
                Evidence(
                    "FALSE_BREAKOUT",
                    f"False breakout trapped {'sellers' if hit.direction is Direction.BUY else 'buyers'}",
                    hit.direction, self.weight("FALSE_BREAKOUT"), score=hit.strength,
                )
            )
        else:
            count_only("FALSE_BREAKOUT")

        directional = [h for h in hits if h.direction is not Direction.NEUTRAL and h.pattern not in (
            CandlePattern.FALSE_BREAKOUT_BULL, CandlePattern.FALSE_BREAKOUT_BEAR
        )]
        if directional:
            hit = max(directional, key=lambda h: h.strength)
            add(
                Evidence(
                    "PATTERN",
                    f"{hit.pattern.value.replace('_', ' ').title()} on the entry timeframe",
                    hit.direction, self.weight("PATTERN"), score=hit.strength,
                )
            )
        else:
            count_only("PATTERN")
