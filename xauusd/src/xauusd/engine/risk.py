"""Risk management: entry, stop, targets, sizing and trade management.

The stop is placed where the idea is *wrong*, not at a round number of dollars.
Concretely, it goes beyond the structural level that invalidates the setup —
the swing that a liquidity sweep took, or the far edge of the order block —
with an ATR-derived buffer and a spread allowance on top. A stop that survives
normal noise but dies on invalidation is the whole point.

Targets are expressed in R multiples and then snapped toward real liquidity
where a pool sits nearby, because that is where price is actually drawn.

Position size always solves the same equation: risk exactly ``max_risk_percent``
of the account, never more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..analysis.mtf import TimeframeAnalysis
from ..config import Config
from ..logging_setup import get_logger
from ..models import (
    Direction,
    LiquidityPool,
    OrderBlock,
    RiskPlan,
    Timeframe,
    clamp,
)

log = get_logger("engine.risk")


@dataclass(slots=True)
class StopContext:
    """Everything that can define where the trade is invalidated."""

    swing_level: float | None = None
    block_edge: float | None = None
    sweep_extreme: float | None = None


class RiskManager:
    def __init__(self, config: Config) -> None:
        self._cfg = config.section("risk")
        self._config = config

    # -- helpers -------------------------------------------------------------
    def _round_lot(self, lots: float) -> float:
        step = float(self._cfg.get("lot_step", 0.01))
        minimum = float(self._cfg.get("min_lot", 0.01))
        maximum = float(self._cfg.get("max_lot", 10.0))
        if step <= 0:
            return clamp(lots, minimum, maximum)
        stepped = round(lots / step) * step
        return clamp(round(stepped, 2), minimum, maximum)

    def position_size(self, risk_per_unit: float, balance: float | None = None) -> tuple[float, float]:
        """Returns ``(lots, risk_amount)`` for the configured risk percentage.

        1 lot of XAUUSD is 100 troy ounces, so a $1 stop distance is $100 of
        risk per lot.
        """
        balance = balance if balance is not None else float(self._cfg.get("account_balance", 10000.0))
        risk_percent = float(self._cfg.get("max_risk_percent", 1.0))
        contract = float(self._cfg.get("contract_size", 100))

        risk_amount = balance * risk_percent / 100.0
        if risk_per_unit <= 0 or contract <= 0:
            return float(self._cfg.get("min_lot", 0.01)), risk_amount

        raw_lots = risk_amount / (risk_per_unit * contract)
        lots = self._round_lot(raw_lots)
        # Report the risk the *rounded* size actually carries, not the target.
        actual_risk = lots * contract * risk_per_unit
        return lots, actual_risk

    # -- stop placement ------------------------------------------------------
    def stop_distance(
        self, direction: Direction, entry: float, atr: float, context: StopContext
    ) -> tuple[float, str]:
        """Distance from entry to stop, and the reason it sits there."""
        atr_multiple = float(self._cfg.get("sl_atr_multiple", 1.5))
        buffer_multiple = float(self._cfg.get("sl_structure_buffer_atr", 0.25))
        spread = float(self._cfg.get("spread_allowance_usd", 0.35))
        min_sl = float(self._cfg.get("min_sl_usd", 1.5))
        max_sl = float(self._cfg.get("max_sl_usd", 12.0))

        atr_distance = atr * atr_multiple if atr > 0 else min_sl
        buffer = max(atr * buffer_multiple, 0.20) + spread

        candidates: list[tuple[float, str]] = [(atr_distance, f"{atr_multiple:.1f}x ATR")]

        levels = [
            (context.sweep_extreme, "beyond the swept extreme"),
            (context.swing_level, "beyond the invalidating swing"),
            (context.block_edge, "beyond the order block"),
        ]
        for level, label in levels:
            if level is None:
                continue
            distance = (entry - level) if direction is Direction.BUY else (level - entry)
            if distance > 0:
                candidates.append((distance + buffer, label))

        # Take the widest structural stop — being right about direction and
        # stopped out by noise is the most expensive way to be right.
        distance, reason = max(candidates, key=lambda item: item[0])
        clamped = clamp(distance, min_sl, max_sl)
        if clamped != distance:
            reason = f"{reason} (clamped to ${clamped:.2f})"
        return clamped, reason

    # -- targets -------------------------------------------------------------
    def targets(
        self,
        direction: Direction,
        entry: float,
        risk_per_unit: float,
        pools: Sequence[LiquidityPool] = (),
    ) -> tuple[list[float], list[float]]:
        """Returns ``(prices, rr_multiples)``.

        Configured R multiples are the baseline; when a liquidity pool sits
        just short of a target, the target is pulled to just in front of it,
        since that is where price is actually drawn and where fills happen.
        """
        multiples = [float(m) for m in self._cfg.get("tp_r_multiples", [1.5, 2.5, 4.0])]
        prices: list[float] = []
        realised: list[float] = []

        candidates = [
            p.price for p in pools
            if not p.swept and ((p.price > entry) if direction is Direction.BUY else (p.price < entry))
        ]

        for multiple in multiples:
            base = entry + direction.sign * risk_per_unit * multiple
            target = base

            nearby = [
                price for price in candidates
                if abs(price - base) <= risk_per_unit * 0.6
                and ((price > entry) if direction is Direction.BUY else (price < entry))
            ]
            if nearby:
                pool_price = min(nearby, key=lambda p: abs(p - base))
                # Sit in front of the pool, not on it — that is where the fill is.
                offset = risk_per_unit * 0.08
                target = pool_price - direction.sign * offset

            achieved = abs(target - entry) / risk_per_unit if risk_per_unit > 0 else 0.0
            prices.append(target)
            realised.append(achieved)

        return prices, realised

    # -- full plan -----------------------------------------------------------
    def build_plan(
        self,
        direction: Direction,
        entry: float,
        setup: TimeframeAnalysis,
        atr_override: float | None = None,
        balance: float | None = None,
    ) -> RiskPlan:
        """Assemble the complete risk plan for a signal."""
        atr_tf = Timeframe(self._cfg.get("atr_timeframe", "M15"))
        atr = atr_override if atr_override is not None else setup.atr
        if atr <= 0:
            atr = max(entry * 0.0008, 0.5)   # ~0.08% of price as a last resort

        context = self._stop_context(direction, setup)
        risk_per_unit, _reason = self.stop_distance(direction, entry, atr, context)
        stop = entry - direction.sign * risk_per_unit

        prices, rr = self.targets(direction, entry, risk_per_unit, setup.structure.pools)
        lots, risk_amount = self.position_size(risk_per_unit, balance)

        be_r = float(self._cfg.get("break_even_at_r", 1.0))
        trail_r = float(self._cfg.get("trail_after_r", 1.5))
        trail_atr = float(self._cfg.get("trail_atr_multiple", 1.2))

        balance_used = balance if balance is not None else float(self._cfg.get("account_balance", 10000.0))
        partials = [float(p) for p in self._cfg.get("partial_percents", [50, 30, 20])]
        return RiskPlan(
            entry=entry,
            stop_loss=stop,
            take_profits=prices,
            risk_per_unit=risk_per_unit,
            rr_targets=rr,
            lot_size=lots,
            risk_amount=risk_amount,
            risk_percent=(risk_amount / balance_used * 100.0) if balance_used else 0.0,
            break_even_price=entry + direction.sign * risk_per_unit * be_r,
            trail_trigger_price=entry + direction.sign * risk_per_unit * trail_r,
            trail_distance=atr * trail_atr,
            atr=atr,
            partial_percents=partials,
        )

    def _stop_context(self, direction: Direction, setup: TimeframeAnalysis) -> StopContext:
        """Find the structural levels that would invalidate this idea."""
        context = StopContext()
        candles = setup.candles

        if candles:
            window = candles[-20:]
            context.swing_level = (
                min(c.low for c in window) if direction is Direction.BUY else max(c.high for c in window)
            )

        sweeps = [s for s in setup.structure.sweeps if s.direction is direction]
        if sweeps and candles:
            sweep = max(sweeps, key=lambda s: s.ts)
            candle = candles[sweep.index] if sweep.index < len(candles) else candles[-1]
            context.sweep_extreme = candle.low if direction is Direction.BUY else candle.high

        blocks: list[OrderBlock] = [b for b in setup.smc.order_blocks if b.direction is direction]
        if blocks:
            block = blocks[-1]
            context.block_edge = block.bottom if direction is Direction.BUY else block.top

        return context

    # -- trade management text ------------------------------------------------
    def management_notes(self, plan: RiskPlan, direction: Direction) -> list[str]:
        partials = [int(p) for p in self._cfg.get("partial_percents", [50, 30, 20])]
        be_r = float(self._cfg.get("break_even_at_r", 1.0))
        trail_r = float(self._cfg.get("trail_after_r", 1.5))
        notes = [
            f"Risk {plan.risk_percent:.2f}% (${plan.risk_amount:.0f}) — {plan.lot_size:.2f} lots",
            f"Stop ${plan.risk_per_unit:.2f} away ({plan.risk_per_unit / plan.atr:.1f}x ATR)",
            f"Blended R:R {plan.blended_rr:.2f} across the scale-out plan",
        ]
        for i, (price, share) in enumerate(zip(plan.take_profits, partials), start=1):
            rr = plan.rr_targets[i - 1] if i - 1 < len(plan.rr_targets) else 0.0
            notes.append(f"TP{i} {price:.2f} ({rr:.1f}R) — close {share}%")
        notes.append(f"Move to break-even at {plan.break_even_price:.2f} (+{be_r:.1f}R)")
        notes.append(
            f"Trail by ${plan.trail_distance:.2f} once {plan.trail_trigger_price:.2f} (+{trail_r:.1f}R) trades"
        )
        return notes
