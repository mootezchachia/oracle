"""ORACLE — Signal Fusion Engine
Inspired by aulekator's 7-phase pipeline and multi-agent ensemble approaches.
Combines narrative, technical, sentiment, and price signals into a unified
weighted prediction with confidence scoring.

Architecture:
  Signal Sources → Normalization → Weighted Fusion → Confidence Gate → Output
"""

import json
import math
import time
from datetime import datetime, timezone
from config import *

FUSION_LOG = DATA_DIR / "signal_fusion.jsonl"
FUSION_STATE = DATA_DIR / "fusion_state.json"


# ─── Signal Types ─────────────────────────────────────────────────

class Signal:
    """A single directional signal from any source."""

    def __init__(self, source: str, direction: float, strength: float,
                 timeframe: str = "short", metadata: dict = None):
        """
        Args:
            source: e.g. "narrative", "rsi_15m", "reddit_velocity", "ensemble_gpt"
            direction: -1.0 (strong NO/DOWN) to +1.0 (strong YES/UP)
            strength: 0.0 to 1.0 confidence in this signal
            timeframe: "instant" (<5m), "short" (5m-1h), "medium" (1h-24h), "long" (>24h)
            metadata: any extra context
        """
        self.source = source
        self.direction = max(-1.0, min(1.0, direction))
        self.strength = max(0.0, min(1.0, strength))
        self.timeframe = timeframe
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "source": self.source,
            "direction": self.direction,
            "strength": self.strength,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


# ─── Signal Weights (inspired by aulekator's weighted voting) ────

# Default weights per source category — tuned by calibration over time
DEFAULT_WEIGHTS = {
    # Narrative signals (ORACLE's unique edge)
    "narrative_dominance": 0.25,
    "narrative_ambiguity": 0.10,

    # Technical analysis signals
    "rsi": 0.08,
    "macd": 0.08,
    "vwap": 0.06,
    "bollinger": 0.05,
    "heiken_ashi": 0.04,

    # Sentiment/velocity signals
    "reddit_velocity": 0.08,
    "news_velocity": 0.06,

    # Price action signals
    "price_momentum": 0.06,
    "volume_spike": 0.04,
    "spread_widening": 0.03,

    # External model signals (ensemble)
    "ensemble_model": 0.07,
}

# Timeframe decay — older signals worth less
TIMEFRAME_HALF_LIFE = {
    "instant": 300,     # 5 min half-life
    "short": 1800,      # 30 min
    "medium": 14400,    # 4 hours
    "long": 86400,      # 24 hours
}


def time_decay(signal: Signal) -> float:
    """Apply exponential time decay to signal strength."""
    try:
        ts = datetime.fromisoformat(signal.timestamp)
        age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        age_seconds = 0

    half_life = TIMEFRAME_HALF_LIFE.get(signal.timeframe, 3600)
    decay = math.exp(-0.693 * age_seconds / half_life)  # ln(2) ≈ 0.693
    return decay


# ─── Fusion Engine ────────────────────────────────────────────────

class SignalFusionEngine:
    """Combines multiple signals into a single prediction."""

    def __init__(self, weights: dict = None):
        self.weights = weights or DEFAULT_WEIGHTS
        self.signals: list[Signal] = []
        self.fusion_history: list[dict] = []

    def add_signal(self, signal: Signal):
        """Add a signal to the fusion pool."""
        self.signals.append(signal)

    def clear(self):
        """Clear all signals for a fresh fusion cycle."""
        self.signals = []

    def fuse(self, market_id: str = "") -> dict:
        """Run weighted fusion across all signals.

        Returns:
            {
                "direction": float (-1 to 1),
                "confidence": float (0 to 1),
                "signal_count": int,
                "agreement": float (0 to 1),  # how much signals agree
                "breakdown": [...],
                "recommendation": "BUY YES" | "BUY NO" | "HOLD",
                "edge_pct": float,
            }
        """
        if not self.signals:
            return self._empty_result(market_id)

        weighted_sum = 0.0
        total_weight = 0.0
        breakdown = []

        for sig in self.signals:
            # Look up weight by source prefix
            w = self._get_weight(sig.source)
            decay = time_decay(sig)
            effective_weight = w * sig.strength * decay

            weighted_sum += sig.direction * effective_weight
            total_weight += effective_weight

            breakdown.append({
                "source": sig.source,
                "direction": round(sig.direction, 3),
                "strength": round(sig.strength, 3),
                "weight": round(w, 3),
                "decay": round(decay, 3),
                "effective": round(effective_weight, 4),
                "contribution": round(sig.direction * effective_weight, 4),
            })

        # Normalize direction to [-1, 1]
        if total_weight > 0:
            direction = weighted_sum / total_weight
        else:
            direction = 0.0

        # Agreement score: how aligned are signals?
        if len(self.signals) > 1:
            directions = [s.direction for s in self.signals]
            mean_dir = sum(directions) / len(directions)
            variance = sum((d - mean_dir) ** 2 for d in directions) / len(directions)
            agreement = max(0.0, 1.0 - math.sqrt(variance))
        else:
            agreement = 0.5  # single signal = uncertain

        # Confidence = f(agreement, signal_count, total_weight)
        count_factor = min(1.0, len(self.signals) / 5)  # more signals = more confident
        confidence = agreement * 0.4 + count_factor * 0.3 + min(total_weight, 1.0) * 0.3
        confidence = max(0.0, min(1.0, confidence))

        # Recommendation
        edge_pct = abs(direction) * confidence * 100
        if edge_pct > 5 and confidence > 0.4:
            recommendation = "BUY YES" if direction > 0 else "BUY NO"
        else:
            recommendation = "HOLD"

        result = {
            "market_id": market_id,
            "direction": round(direction, 4),
            "confidence": round(confidence, 4),
            "signal_count": len(self.signals),
            "agreement": round(agreement, 4),
            "edge_pct": round(edge_pct, 2),
            "recommendation": recommendation,
            "breakdown": sorted(breakdown, key=lambda x: abs(x["contribution"]), reverse=True),
            "timestamp": now_iso(),
        }

        # Log
        append_jsonl(FUSION_LOG, result)
        self.fusion_history.append(result)

        return result

    def _get_weight(self, source: str) -> float:
        """Look up weight by exact match or prefix match."""
        if source in self.weights:
            return self.weights[source]
        # Try prefix: "rsi_15m" → "rsi"
        prefix = source.split("_")[0]
        if prefix in self.weights:
            return self.weights[prefix]
        return 0.05  # default small weight for unknown sources

    def _empty_result(self, market_id: str) -> dict:
        return {
            "market_id": market_id,
            "direction": 0.0,
            "confidence": 0.0,
            "signal_count": 0,
            "agreement": 0.0,
            "edge_pct": 0.0,
            "recommendation": "HOLD",
            "breakdown": [],
            "timestamp": now_iso(),
        }


# ─── Signal Generators (plug into existing ORACLE modules) ────────

def signals_from_narrative(event_score: dict) -> list:
    """Convert ORACLE event scoring into fusion signals."""
    signals = []

    ambiguity = event_score.get("narrative_ambiguity", 0)
    narratives = event_score.get("competing_narratives", [])

    # Higher ambiguity = more opportunity but less directional clarity
    if ambiguity > 0.7:
        signals.append(Signal(
            source="narrative_ambiguity",
            direction=0.0,  # ambiguous = no clear direction
            strength=ambiguity,
            timeframe="medium",
            metadata={"narratives": narratives},
        ))

    # If event has clear affected markets with known direction
    for market in event_score.get("affected_markets", []):
        relevance = market.get("relevance", 0)
        if relevance > 0.3:
            signals.append(Signal(
                source="narrative_dominance",
                direction=0.0,  # direction determined by simulation
                strength=relevance * ambiguity,
                timeframe="medium",
                metadata={"market": market.get("question", "")},
            ))

    return signals


def signals_from_reddit(posts: list, threshold_velocity: float = 10.0) -> list:
    """Convert Reddit velocity data into fusion signals."""
    signals = []

    # High-velocity posts indicate breaking narrative
    hot_posts = [p for p in posts if p.get("velocity", 0) > threshold_velocity]

    if hot_posts:
        avg_velocity = sum(p["velocity"] for p in hot_posts) / len(hot_posts)
        # Normalize velocity to strength (log scale)
        strength = min(1.0, math.log10(max(avg_velocity, 1)) / 3)

        signals.append(Signal(
            source="reddit_velocity",
            direction=0.0,  # velocity doesn't tell direction, just intensity
            strength=strength,
            timeframe="short",
            metadata={
                "hot_count": len(hot_posts),
                "avg_velocity": round(avg_velocity, 1),
                "top_sub": hot_posts[0].get("subreddit", ""),
            },
        ))

    return signals


def signals_from_price_action(price_data: list) -> list:
    """Convert price history into momentum and volume signals."""
    signals = []

    if not price_data or len(price_data) < 3:
        return signals

    # Price momentum: compare last 3 data points
    recent = price_data[-3:]
    if all("price" in p for p in recent):
        prices = [p["price"] for p in recent]
        momentum = (prices[-1] - prices[0]) / max(prices[0], 0.01)

        signals.append(Signal(
            source="price_momentum",
            direction=max(-1.0, min(1.0, momentum * 10)),  # amplify small moves
            strength=min(1.0, abs(momentum) * 5),
            timeframe="short",
            metadata={"prices": prices},
        ))

    # Volume spike detection (inspired by discountry's flash crash bot)
    if all("volume" in p for p in recent):
        volumes = [p["volume"] for p in recent]
        avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        if avg_vol > 0 and volumes[-1] > avg_vol * 2:
            signals.append(Signal(
                source="volume_spike",
                direction=0.0,  # spike = something happening, direction unclear
                strength=min(1.0, volumes[-1] / avg_vol / 5),
                timeframe="instant",
                metadata={"spike_ratio": round(volumes[-1] / avg_vol, 2)},
            ))

    return signals


# ─── Convenience: Full Fusion Pipeline ────────────────────────────

def run_fusion(event_score: dict = None, reddit_posts: list = None,
               price_data: list = None, extra_signals: list = None,
               market_id: str = "") -> dict:
    """Run a complete fusion cycle from available data."""
    engine = SignalFusionEngine()

    if event_score:
        for sig in signals_from_narrative(event_score):
            engine.add_signal(sig)

    if reddit_posts:
        for sig in signals_from_reddit(reddit_posts):
            engine.add_signal(sig)

    if price_data:
        for sig in signals_from_price_action(price_data):
            engine.add_signal(sig)

    if extra_signals:
        for sig in extra_signals:
            engine.add_signal(sig)

    return engine.fuse(market_id)


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("Signal Fusion Engine")

    # Demo: load existing data and fuse
    import os
    events_path = ALERTS_DIR / "alert-1.json"
    reddit_path = DATA_DIR / "reddit_velocity.jsonl"

    event_score = None
    if events_path.exists():
        with open(events_path) as f:
            event_score = json.load(f)
        print(f"  Loaded event: {event_score.get('event', 'unknown')[:60]}...")

    reddit_posts = load_jsonl(reddit_path, limit=50)
    if reddit_posts:
        print(f"  Loaded {len(reddit_posts)} Reddit posts")

    result = run_fusion(
        event_score=event_score,
        reddit_posts=reddit_posts,
        market_id="demo",
    )

    print(f"\n  ═══ FUSION RESULT ═══")
    print(f"  Direction:      {result['direction']:+.4f}")
    print(f"  Confidence:     {result['confidence']:.1%}")
    print(f"  Agreement:      {result['agreement']:.1%}")
    print(f"  Edge:           {result['edge_pct']:.2f}%")
    print(f"  Recommendation: {result['recommendation']}")
    print(f"  Signals:        {result['signal_count']}")

    if result["breakdown"]:
        print(f"\n  Signal Breakdown:")
        for b in result["breakdown"][:8]:
            arrow = "↑" if b["direction"] > 0 else "↓" if b["direction"] < 0 else "→"
            print(f"    {arrow} {b['source']:<24} dir:{b['direction']:+.3f}  "
                  f"str:{b['strength']:.3f}  eff:{b['effective']:.4f}")

    print()


if __name__ == "__main__":
    main()
