"""Multi-timeframe analysis engine.

This module turns raw candles into a complete per-timeframe read, then answers
the only question that matters for a top-down institutional model:

    H4 trend → H1 confirmation → M15 setup → M5 entry → M1 precision

A signal is never derived from a single timeframe. The higher timeframes set
the *bias* and act as a hard gate; the lower timeframes are only allowed to
refine the entry inside that bias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..config import Config
from ..models import (
    Candle,
    Direction,
    PatternHit,
    Timeframe,
    clamp,
)
from . import indicators as ind
from .price_action import net_pattern_bias, scan_patterns
from .smc import SMCReport, analyse_smc, asian_range, power_of_three
from .structure import StructureReport, analyse_structure
from .volatility import VolatilityState, classify_volatility


# ---------------------------------------------------------------------------
# Indicator snapshot
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class IndicatorSnapshot:
    """Latest value of every indicator on one timeframe."""

    ema_fast: float | None = None
    ema_mid: float | None = None
    ema_slow: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_hist_prev: float | None = None
    atr: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    bb_width: float | None = None
    vwap: float | None = None
    volume_ratio: float | None = None
    delta: float = 0.0
    price: float = 0.0

    # -- interpretations ----------------------------------------------------
    @property
    def ema_stack(self) -> Direction:
        """20 > 50 > 200 is a textbook bullish stack (and the mirror for bears)."""
        if None in (self.ema_fast, self.ema_mid, self.ema_slow):
            return Direction.NEUTRAL
        if self.ema_fast > self.ema_mid > self.ema_slow:  # type: ignore[operator]
            return Direction.BUY
        if self.ema_fast < self.ema_mid < self.ema_slow:  # type: ignore[operator]
            return Direction.SELL
        return Direction.NEUTRAL

    @property
    def macd_bias(self) -> Direction:
        if self.macd is None or self.macd_signal is None:
            return Direction.NEUTRAL
        return Direction.BUY if self.macd > self.macd_signal else Direction.SELL

    @property
    def macd_momentum_rising(self) -> bool | None:
        if self.macd_hist is None or self.macd_hist_prev is None:
            return None
        return abs(self.macd_hist) > abs(self.macd_hist_prev)

    @property
    def vwap_bias(self) -> Direction:
        if self.vwap is None or not self.price:
            return Direction.NEUTRAL
        return Direction.BUY if self.price > self.vwap else Direction.SELL

    @property
    def trending(self) -> bool:
        return self.adx is not None and self.adx >= 22.0

    def rsi_healthy(self, direction: Direction, bull: Sequence[float], bear: Sequence[float]) -> bool:
        """Healthy means 'has room to run', not 'oversold/overbought'.

        Buying a 78 RSI into resistance is chasing; buying a 55 RSI in an
        uptrend is participating.
        """
        if self.rsi is None:
            return False
        if direction is Direction.BUY:
            return bull[0] <= self.rsi <= bull[1]
        if direction is Direction.SELL:
            return bear[0] <= self.rsi <= bear[1]
        return False

    def to_dict(self) -> dict[str, object]:
        def r(value: float | None, digits: int = 2) -> float | None:
            return None if value is None else round(value, digits)

        return {
            "ema20": r(self.ema_fast),
            "ema50": r(self.ema_mid),
            "ema200": r(self.ema_slow),
            "rsi": r(self.rsi, 1),
            "macd": r(self.macd, 3),
            "macd_signal": r(self.macd_signal, 3),
            "macd_hist": r(self.macd_hist, 3),
            "atr": r(self.atr, 3),
            "adx": r(self.adx, 1),
            "bb_width": r(self.bb_width, 2),
            "vwap": r(self.vwap),
            "volume_ratio": r(self.volume_ratio),
            "ema_stack": self.ema_stack.value,
            "macd_bias": self.macd_bias.value,
            "trending": self.trending,
        }


def compute_indicators(candles: Sequence[Candle], cfg: Config) -> tuple[IndicatorSnapshot, list[float | None]]:
    """Compute every configured indicator; return the snapshot and the ATR series."""
    closes = [c.close for c in candles]
    snapshot = IndicatorSnapshot(price=closes[-1] if closes else 0.0)

    atr_series: list[float | None] = []
    if not candles:
        return snapshot, atr_series

    ema_fast_p = int(cfg.get("ema_fast", 20))
    ema_mid_p = int(cfg.get("ema_mid", 50))
    ema_slow_p = int(cfg.get("ema_slow", 200))

    snapshot.ema_fast = ind.last_valid(ind.ema(closes, ema_fast_p))
    snapshot.ema_mid = ind.last_valid(ind.ema(closes, ema_mid_p))
    snapshot.ema_slow = ind.last_valid(ind.ema(closes, ema_slow_p))
    snapshot.rsi = ind.last_valid(ind.rsi(closes, int(cfg.get("rsi_period", 14))))

    macd_cfg = cfg.section("macd")
    macd_line, signal_line, hist = ind.macd(
        closes,
        int(macd_cfg.get("fast", 12)),
        int(macd_cfg.get("slow", 26)),
        int(macd_cfg.get("signal", 9)),
    )
    snapshot.macd = ind.last_valid(macd_line)
    snapshot.macd_signal = ind.last_valid(signal_line)
    snapshot.macd_hist = ind.last_valid(hist)
    snapshot.macd_hist_prev = ind.last_valid(hist[:-1]) if len(hist) > 1 else None

    atr_series = ind.atr(candles, int(cfg.get("atr_period", 14)))
    snapshot.atr = ind.last_valid(atr_series)

    adx_line, plus_di, minus_di = ind.adx(candles, int(cfg.get("adx_period", 14)))
    snapshot.adx = ind.last_valid(adx_line)
    snapshot.plus_di = ind.last_valid(plus_di)
    snapshot.minus_di = ind.last_valid(minus_di)

    bb_cfg = cfg.section("bb")
    upper, middle, lower = ind.bollinger(
        closes, int(bb_cfg.get("period", 20)), float(bb_cfg.get("stddev", 2.0))
    )
    snapshot.bb_upper = ind.last_valid(upper)
    snapshot.bb_mid = ind.last_valid(middle)
    snapshot.bb_lower = ind.last_valid(lower)
    snapshot.bb_width = ind.last_valid(ind.bollinger_bandwidth(upper, middle, lower))

    snapshot.vwap = ind.last_valid(ind.vwap(candles, ind.daily_anchor_indices(candles)))
    snapshot.volume_ratio = ind.last_valid(ind.volume_ratio(candles, int(cfg.get("volume_ma", 20))))

    deltas = ind.delta_proxy(candles)
    snapshot.delta = sum(deltas[-5:]) if deltas else 0.0

    return snapshot, atr_series


# ---------------------------------------------------------------------------
# Per-timeframe analysis
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TimeframeAnalysis:
    timeframe: Timeframe
    candles: list[Candle]
    indicators: IndicatorSnapshot
    atr_series: list[float | None]
    structure: StructureReport
    smc: SMCReport
    patterns: list[PatternHit]
    volatility: VolatilityState
    bias: Direction
    bias_strength: float
    components: dict[str, str] = field(default_factory=dict)

    @property
    def price(self) -> float:
        return self.candles[-1].close if self.candles else 0.0

    @property
    def atr(self) -> float:
        return self.indicators.atr or 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe.value,
            "price": round(self.price, 3),
            "bias": self.bias.value,
            "bias_strength": round(self.bias_strength, 2),
            "components": self.components,
            "indicators": self.indicators.to_dict(),
            "structure": self.structure.to_dict(),
            "smc": self.smc.to_dict(),
            "volatility": self.volatility.to_dict(),
            "patterns": [p.to_dict() for p in self.patterns[:5]],
        }


# Relative importance of each bias component within a single timeframe.
_COMPONENT_WEIGHTS: dict[str, float] = {
    "structure": 3.0,      # swing sequence — the backbone
    "last_break": 2.5,     # most recent BOS/CHOCH
    "ema_stack": 2.0,
    "macd": 1.0,
    "vwap": 1.0,
    "di": 1.0,
    "patterns": 1.0,
}


def analyse_timeframe(
    timeframe: Timeframe,
    candles: Sequence[Candle],
    config: Config,
) -> TimeframeAnalysis:
    """Full analysis of one timeframe: indicators, structure, SMC, patterns."""
    ind_cfg = config.section("indicators")
    vol_cfg = config.section("volatility")
    swing_length = int(config.get(f"mtf.swing_length.{timeframe.value}", 5))

    snapshot, atr_series = compute_indicators(candles, ind_cfg)
    atr_value = snapshot.atr or 0.0

    # Equal-highs tolerance scales with volatility: "equal" on a $6 ATR day is
    # not the same tolerance as on a $1.50 ATR day.
    tolerance = max(atr_value * 0.15, 0.20)

    structure = analyse_structure(
        candles, timeframe.value, swing_length, atr_series, equal_tolerance=tolerance
    )

    # A provisional bias from structure alone seeds the OTE calculation.
    provisional = structure.trend.direction
    if structure.last_break is not None:
        provisional = structure.last_break.direction

    smc = analyse_smc(
        candles,
        timeframe.value,
        structure.breaks,
        structure.dealing_range,
        atr_series,
        bias=provisional,
        min_gap_size=atr_value * 0.15,
    )

    if timeframe in (Timeframe.M15, Timeframe.M5, Timeframe.M1):
        smc.po3 = power_of_three(candles, asian_range(candles), snapshot.price)

    patterns = scan_patterns(candles, atr_series)

    volatility = classify_volatility(
        candles,
        period=int(ind_cfg.get("atr_period", 14)),
        lookback=int(vol_cfg.get("atr_lookback", 100)),
        low_percentile=float(vol_cfg.get("low_percentile", 25)),
        expansion_percentile=float(vol_cfg.get("expansion_percentile", 75)),
        spike_atr_ratio=float(vol_cfg.get("spike_atr_ratio", 2.6)),
        min_atr=float(vol_cfg.get("min_atr_m15", 0.0)) if timeframe is Timeframe.M15 else 0.0,
        max_atr=float(vol_cfg.get("max_atr_m15", float("inf"))) if timeframe is Timeframe.M15 else float("inf"),
    )

    bias, strength, components = _resolve_bias(snapshot, structure, patterns)

    return TimeframeAnalysis(
        timeframe=timeframe,
        candles=list(candles),
        indicators=snapshot,
        atr_series=atr_series,
        structure=structure,
        smc=smc,
        patterns=patterns,
        volatility=volatility,
        bias=bias,
        bias_strength=strength,
        components=components,
    )


def _resolve_bias(
    snapshot: IndicatorSnapshot,
    structure: StructureReport,
    patterns: Sequence[PatternHit],
) -> tuple[Direction, float, dict[str, str]]:
    """Weighted vote across the timeframe's independent bias components."""
    votes: dict[str, Direction] = {
        "structure": structure.trend.direction,
        "ema_stack": snapshot.ema_stack,
        "macd": snapshot.macd_bias,
        "vwap": snapshot.vwap_bias,
    }

    if structure.last_break is not None:
        votes["last_break"] = structure.last_break.direction

    if snapshot.plus_di is not None and snapshot.minus_di is not None:
        votes["di"] = Direction.BUY if snapshot.plus_di > snapshot.minus_di else Direction.SELL

    pattern_direction, pattern_strength = net_pattern_bias(patterns)
    if pattern_strength >= 0.3:
        votes["patterns"] = pattern_direction

    bull = sum(_COMPONENT_WEIGHTS.get(k, 1.0) for k, v in votes.items() if v is Direction.BUY)
    bear = sum(_COMPONENT_WEIGHTS.get(k, 1.0) for k, v in votes.items() if v is Direction.SELL)
    total = sum(_COMPONENT_WEIGHTS.get(k, 1.0) for k in votes)

    if total == 0:
        return Direction.NEUTRAL, 0.0, {}

    net = bull - bear
    direction = Direction.from_sign(net)
    strength = clamp(abs(net) / total, 0.0, 1.0)
    return direction, strength, {k: v.value for k, v in votes.items()}


# ---------------------------------------------------------------------------
# Alignment across timeframes
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MTFResult:
    analyses: dict[Timeframe, TimeframeAnalysis]
    htf_bias: Direction
    aligned: bool
    alignment_score: float           # 0..1
    chain: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    detail: str = ""

    def get(self, timeframe: Timeframe) -> TimeframeAnalysis | None:
        return self.analyses.get(timeframe)

    @property
    def price(self) -> float:
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4):
            analysis = self.analyses.get(timeframe)
            if analysis and analysis.candles:
                return analysis.price
        return 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "htf_bias": self.htf_bias.value,
            "aligned": self.aligned,
            "alignment_score": round(self.alignment_score, 2),
            "chain": self.chain,
            "conflicts": self.conflicts,
            "detail": self.detail,
            "timeframes": {tf.value: a.to_dict() for tf, a in self.analyses.items()},
        }


def analyse_all(candles_by_tf: Mapping[Timeframe, Sequence[Candle]], config: Config) -> MTFResult:
    """Analyse every available timeframe and resolve the top-down chain."""
    analyses: dict[Timeframe, TimeframeAnalysis] = {}
    for timeframe, candles in candles_by_tf.items():
        if len(candles) < 30:
            continue
        analyses[timeframe] = analyse_timeframe(timeframe, candles, config)

    return resolve_alignment(analyses, config)


def resolve_alignment(
    analyses: Mapping[Timeframe, TimeframeAnalysis], config: Config
) -> MTFResult:
    """Apply the top-down gate.

    The bias timeframes (H4, H1 by default) must agree with each other — that
    is non-negotiable. The setup timeframe must not contradict them. Entry
    timeframes contribute to the alignment score and at least
    ``min_entry_agreement`` of them must confirm.
    """
    mtf_cfg = config.section("mtf")
    bias_tfs = [Timeframe(t) for t in mtf_cfg.get("bias_timeframes", ["H4", "H1"])]
    setup_tf = Timeframe(mtf_cfg.get("setup_timeframe", "M15"))
    entry_tfs = [Timeframe(t) for t in mtf_cfg.get("entry_timeframes", ["M5", "M1"])]
    min_entry_agreement = int(mtf_cfg.get("min_entry_agreement", 1))

    chain: list[str] = []
    conflicts: list[str] = []

    available_bias = [analyses[t] for t in bias_tfs if t in analyses]
    if len(available_bias) < len(bias_tfs):
        missing = [t.value for t in bias_tfs if t not in analyses]
        return MTFResult(
            dict(analyses), Direction.NEUTRAL, False, 0.0,
            chain, [f"missing {','.join(missing)} data"],
            "Higher-timeframe data unavailable",
        )

    bias_directions = {a.bias for a in available_bias}
    if len(bias_directions) != 1 or Direction.NEUTRAL in bias_directions:
        for analysis in available_bias:
            chain.append(f"{analysis.timeframe.value}:{analysis.bias.value}")
        conflicts.append("HTF bias split")
        return MTFResult(
            dict(analyses), Direction.NEUTRAL, False, 0.0, chain, conflicts,
            "H4 and H1 do not agree — no directional edge",
        )

    htf_bias = available_bias[0].bias
    for analysis in available_bias:
        chain.append(f"{analysis.timeframe.value} {analysis.bias.value} ({analysis.bias_strength:.0%})")

    aligned = True

    setup = analyses.get(setup_tf)
    if setup is None:
        aligned = False
        conflicts.append(f"missing {setup_tf.value}")
    else:
        chain.append(f"{setup_tf.value} {setup.bias.value} ({setup.bias_strength:.0%})")
        if setup.bias is htf_bias.opposite:
            aligned = False
            conflicts.append(f"{setup_tf.value} opposes HTF bias")

    entry_agreement = 0
    for timeframe in entry_tfs:
        analysis = analyses.get(timeframe)
        if analysis is None:
            continue
        chain.append(f"{timeframe.value} {analysis.bias.value} ({analysis.bias_strength:.0%})")
        if analysis.bias is htf_bias:
            entry_agreement += 1
        elif analysis.bias is htf_bias.opposite:
            conflicts.append(f"{timeframe.value} opposes")

    if entry_agreement < min_entry_agreement:
        aligned = False
        conflicts.append("no entry-timeframe confirmation")

    # Score: weighted share of timeframes agreeing, HTFs weighted heaviest.
    weights = {Timeframe.H4: 3.0, Timeframe.H1: 2.5, Timeframe.M15: 2.0, Timeframe.M5: 1.5, Timeframe.M1: 1.0}
    earned = 0.0
    possible = 0.0
    for timeframe, analysis in analyses.items():
        weight = weights.get(timeframe, 1.0)
        possible += weight
        if analysis.bias is htf_bias:
            earned += weight * clamp(0.5 + analysis.bias_strength / 2.0, 0.0, 1.0)
        elif analysis.bias is Direction.NEUTRAL:
            earned += weight * 0.25
    score = clamp(earned / possible, 0.0, 1.0) if possible else 0.0

    detail = (
        f"Top-down chain agrees on {htf_bias.value}"
        if aligned
        else f"Chain broken: {'; '.join(conflicts)}"
    )
    return MTFResult(dict(analyses), htf_bias, aligned, score, chain, conflicts, detail)
