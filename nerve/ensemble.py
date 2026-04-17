"""ORACLE — Multi-Model Ensemble Predictor
Inspired by Fully-Autonomous bot (GPT 40% + Claude 35% + Gemini 25%)
and the multi-agent LLM research paper (specialized agents per signal type).

Supports:
  1. Multi-model weighted voting (any LLM via API or local)
  2. Specialist agents (technical, narrative, macro, sentiment)
  3. Calibration-adjusted weights (learn from past accuracy)
  4. Confidence-gated output (only recommend when models agree)
"""

import json
import math
import os
import time
from datetime import datetime, timezone
from config import *

ENSEMBLE_LOG = DATA_DIR / "ensemble_predictions.jsonl"
ENSEMBLE_WEIGHTS = CALIBRATION_DIR / "ensemble_weights.json"


# ─── Model Definitions ───────────────────────────────────────────

MODELS = {
    "claude": {
        "name": "Claude (Narrative Specialist)",
        "weight": 0.35,
        "specialty": "narrative_analysis",
        "description": "Best at narrative competition, public discourse simulation, geopolitical reasoning",
    },
    "gpt": {
        "name": "GPT (Quantitative Specialist)",
        "weight": 0.30,
        "specialty": "quantitative_analysis",
        "description": "Best at structured data analysis, probability estimation, statistical reasoning",
    },
    "gemini": {
        "name": "Gemini (News Specialist)",
        "weight": 0.20,
        "specialty": "news_analysis",
        "description": "Best at real-time news processing, multi-source synthesis, breaking event analysis",
    },
    "local": {
        "name": "Local Model (Technical Specialist)",
        "weight": 0.15,
        "specialty": "technical_analysis",
        "description": "Best at pattern recognition, indicator analysis, fast inference. Runs via Ollama.",
    },
}


# ─── Specialist Prompts (per-agent instructions) ──────────────────

SPECIALIST_PROMPTS = {
    "narrative_analysis": """You are ORACLE's Narrative Specialist. Given market and event data:
1. Identify the 2-3 competing narratives
2. Simulate how each narrative propagates across public discourse
3. Predict which narrative dominates at 4h, 12h, and 48h marks
4. Estimate probability shift: will the market move UP or DOWN?

Output JSON: {"direction": float(-1 to 1), "confidence": float(0-1), "dominant_narrative": str, "reasoning": str}""",

    "quantitative_analysis": """You are ORACLE's Quantitative Specialist. Given market data:
1. Analyze price history, volume patterns, and spread dynamics
2. Apply Bayesian reasoning to update market probability
3. Calculate expected value of YES vs NO positions
4. Estimate fair price and edge vs current market

Output JSON: {"direction": float(-1 to 1), "confidence": float(0-1), "fair_price": float, "edge_pct": float, "reasoning": str}""",

    "news_analysis": """You are ORACLE's News Specialist. Given recent headlines and context:
1. Classify news as: confirming, contradicting, or orthogonal to market thesis
2. Assess information cascade potential (will this go viral?)
3. Predict next 6-24h information trajectory
4. Estimate market impact timing and magnitude

Output JSON: {"direction": float(-1 to 1), "confidence": float(0-1), "cascade_potential": float, "impact_timing_hours": int, "reasoning": str}""",

    "technical_analysis": """You are ORACLE's Technical Specialist. Given candle/indicator data:
1. Identify current trend (bullish/bearish/sideways)
2. Check for key pattern formations (double top/bottom, breakout, etc)
3. Assess support/resistance levels
4. Generate short-term directional prediction

Output JSON: {"direction": float(-1 to 1), "confidence": float(0-1), "trend": str, "key_levels": list, "reasoning": str}""",
}


# ─── Ensemble Engine ──────────────────────────────────────────────

class EnsemblePredictor:
    """Combines predictions from multiple models/specialists."""

    def __init__(self):
        self.predictions = {}  # model_id -> prediction dict
        self.weights = self._load_weights()

    def _load_weights(self) -> dict:
        """Load calibration-adjusted weights, or use defaults."""
        if ENSEMBLE_WEIGHTS.exists():
            with open(ENSEMBLE_WEIGHTS) as f:
                return json.load(f)
        return {m: info["weight"] for m, info in MODELS.items()}

    def save_weights(self):
        """Persist current weights after calibration update."""
        with open(ENSEMBLE_WEIGHTS, "w") as f:
            json.dump(self.weights, f, indent=2)

    def add_prediction(self, model_id: str, direction: float, confidence: float,
                       metadata: dict = None):
        """Add a model's prediction to the ensemble."""
        self.predictions[model_id] = {
            "model_id": model_id,
            "direction": max(-1.0, min(1.0, direction)),
            "confidence": max(0.0, min(1.0, confidence)),
            "timestamp": now_iso(),
            "metadata": metadata or {},
        }

    def vote(self, market_id: str = "") -> dict:
        """Run weighted voting across all submitted predictions.

        Returns ensemble prediction with confidence and agreement metrics.
        """
        if not self.predictions:
            return {
                "market_id": market_id,
                "direction": 0.0,
                "confidence": 0.0,
                "model_count": 0,
                "agreement": 0.0,
                "recommendation": "HOLD",
                "votes": [],
                "timestamp": now_iso(),
            }

        weighted_direction = 0.0
        total_weight = 0.0
        votes = []

        for model_id, pred in self.predictions.items():
            w = self.weights.get(model_id, 0.1)
            # Weight also by model's own confidence
            effective_weight = w * pred["confidence"]

            weighted_direction += pred["direction"] * effective_weight
            total_weight += effective_weight

            votes.append({
                "model": model_id,
                "name": MODELS.get(model_id, {}).get("name", model_id),
                "direction": pred["direction"],
                "confidence": pred["confidence"],
                "weight": w,
                "effective_weight": round(effective_weight, 4),
            })

        # Normalize
        if total_weight > 0:
            direction = weighted_direction / total_weight
        else:
            direction = 0.0

        # Agreement: do models point the same way?
        directions = [p["direction"] for p in self.predictions.values()]
        if len(directions) > 1:
            same_sign = all(d >= 0 for d in directions) or all(d <= 0 for d in directions)
            if same_sign:
                agreement = 1.0 - (max(abs(d) for d in directions) - min(abs(d) for d in directions))
            else:
                agreement = 0.0  # models disagree on direction
        else:
            agreement = 0.5

        # Ensemble confidence
        avg_confidence = sum(p["confidence"] for p in self.predictions.values()) / len(self.predictions)
        ensemble_confidence = avg_confidence * agreement

        # Recommendation
        edge_pct = abs(direction) * ensemble_confidence * 100
        if edge_pct > 5 and ensemble_confidence > 0.4 and agreement > 0.5:
            recommendation = "BUY YES" if direction > 0 else "BUY NO"
        elif edge_pct > 3:
            recommendation = "LEAN YES" if direction > 0 else "LEAN NO"
        else:
            recommendation = "HOLD"

        result = {
            "market_id": market_id,
            "direction": round(direction, 4),
            "confidence": round(ensemble_confidence, 4),
            "model_count": len(self.predictions),
            "agreement": round(agreement, 4),
            "edge_pct": round(edge_pct, 2),
            "recommendation": recommendation,
            "votes": sorted(votes, key=lambda v: v["effective_weight"], reverse=True),
            "timestamp": now_iso(),
        }

        # Log
        append_jsonl(ENSEMBLE_LOG, result)

        return result

    def update_weights_from_calibration(self, results: list):
        """Adjust model weights based on historical accuracy.

        Args:
            results: list of {"model_id": str, "predicted_direction": float, "actual_direction": float}
        """
        accuracy = {}
        for model_id in MODELS:
            model_results = [r for r in results if r["model_id"] == model_id]
            if not model_results:
                continue

            correct = sum(1 for r in model_results
                          if (r["predicted_direction"] > 0) == (r["actual_direction"] > 0))
            accuracy[model_id] = correct / len(model_results)

        if not accuracy:
            return

        # Normalize accuracies to weights
        total = sum(accuracy.values())
        if total > 0:
            for model_id, acc in accuracy.items():
                # Blend: 70% new accuracy, 30% old weight (smooth transitions)
                old_weight = self.weights.get(model_id, 0.1)
                new_weight = acc / total
                self.weights[model_id] = round(old_weight * 0.3 + new_weight * 0.7, 4)

        self.save_weights()


# ─── Convenience Functions ────────────────────────────────────────

def build_specialist_prompt(specialty: str, context: dict) -> str:
    """Build a complete prompt for a specialist agent."""
    base = SPECIALIST_PROMPTS.get(specialty, "Analyze the following market data and predict direction.")

    context_str = json.dumps(context, indent=2, default=str)
    return f"""{base}

## Market Context
```json
{context_str}
```

Respond ONLY with the JSON object. No markdown, no explanation outside the JSON."""


def parse_model_response(response_text: str) -> dict:
    """Parse a model's JSON response, handling common formatting issues."""
    text = response_text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from text
        import re
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    return {"direction": 0.0, "confidence": 0.0, "error": "Failed to parse response"}


# ─── CLI Demo ─────────────────────────────────────────────────────

def main():
    print_banner("Multi-Model Ensemble Predictor")

    # Demo: simulate predictions from each specialist
    ensemble = EnsemblePredictor()

    # Simulated model predictions for "Iran ceasefire by March 31"
    demo_predictions = [
        ("claude", -0.7, 0.78, {"dominant_narrative": "No ceasefire - diplomatic stalemate"}),
        ("gpt", -0.5, 0.65, {"fair_price": 0.08, "edge_pct": 7.0}),
        ("gemini", -0.6, 0.70, {"cascade_potential": 0.3, "impact_timing_hours": 12}),
        ("local", -0.4, 0.55, {"trend": "bearish", "key_levels": [0.10, 0.20]}),
    ]

    print(f"  ═══ ENSEMBLE DEMO: Iran Ceasefire March 31 ═══\n")

    for model_id, direction, confidence, meta in demo_predictions:
        ensemble.add_prediction(model_id, direction, confidence, meta)
        name = MODELS[model_id]["name"]
        arrow = "↑" if direction > 0 else "↓"
        print(f"  {arrow} {name}")
        print(f"    Direction: {direction:+.2f}  Confidence: {confidence:.0%}")

    result = ensemble.vote(market_id="iran-ceasefire-march-31")

    print(f"\n  ═══ ENSEMBLE RESULT ═══")
    print(f"  Direction:      {result['direction']:+.4f}")
    print(f"  Confidence:     {result['confidence']:.1%}")
    print(f"  Agreement:      {result['agreement']:.1%}")
    print(f"  Edge:           {result['edge_pct']:.2f}%")
    print(f"  Recommendation: {result['recommendation']}")
    print(f"  Models:         {result['model_count']}")

    print(f"\n  Vote Weights:")
    for v in result["votes"]:
        print(f"    {v['name']:<35} eff_w: {v['effective_weight']:.4f}")

    print()


if __name__ == "__main__":
    main()
