"""ORACLE — Shared configuration and utilities."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "nerve" / "data"
SEEDS_DIR = ROOT / "seeds"
ALERTS_DIR = ROOT / "alerts"
PREDICTIONS_DIR = ROOT / "predictions"
CALIBRATION_DIR = ROOT / "calibration"
PROMPTS_DIR = ROOT / "prompts"

for d in [DATA_DIR, SEEDS_DIR, ALERTS_DIR, PREDICTIONS_DIR / "active",
          PREDICTIONS_DIR / "archive", CALIBRATION_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── API Config ──────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "DEMO_KEY")
REDDIT_UA = "ORACLE/1.0 (Narrative Arbitrage Engine)"

# ─── Polymarket categories we care about ─────────────────────────
SENTIMENT_CATEGORIES = [
    "politics", "elections", "geopolitics", "economy", "approval",
    "tariffs", "fed", "midterms", "iran", "trump", "regulation",
    "ceasefire", "recession", "consumer", "sentiment"
]

SKIP_CATEGORIES = [
    "nba", "nfl", "nhl", "mlb", "epl", "f1", "ufc", "mma",
    "tennis", "golf", "esports", "counter-strike", "dota",
    "oscars", "grammys", "weather", "temperature",
]

# ─── Subreddits to monitor ───────────────────────────────────────
REDDIT_SUBS = {
    "political": ["politics", "conservative", "moderatepolitics", "PoliticalDiscussion"],
    "economic": ["economics", "wallstreetbets", "personalfinance"],
    "geopolitical": ["geopolitics", "worldnews"],
    "mena": ["iran", "middleeast"],
    "markets": ["polymarket"],
}

ALL_SUBS = [s for group in REDDIT_SUBS.values() for s in group]

# ─── FRED series to track ────────────────────────────────────────
FRED_SERIES = {
    "UNRATE": "Unemployment Rate",
    "CPIAUCSL": "Consumer Price Index",
    "UMCSENT": "Consumer Sentiment",
    "FEDFUNDS": "Federal Funds Rate",
    "GDP": "Real GDP",
    "DCOILWTICO": "WTI Crude Oil",
    "T10Y2Y": "Yield Curve (10Y-2Y Spread)",
}

# ─── RSS feeds ────────────────────────────────────────────────────
RSS_FEEDS = {
    "AP News": "https://rsshub.app/apnews/topics/apf-topnews",
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "NPR News": "https://feeds.npr.org/1001/rss.xml",
    "Al Jazeera EN": "https://www.aljazeera.com/xml/rss/all.xml",
    "Reuters World": "https://www.reutersagency.com/feed/?best-topics=political-general",
}

# ─── Helpers ──────────────────────────────────────────────────────
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(filepath: Path, record: dict):
    with open(filepath, "a") as f:
        f.write(json.dumps(record) + "\n")

def load_jsonl(filepath: Path, limit: int = None) -> list:
    if not filepath.exists():
        return []
    lines = filepath.read_text().strip().split("\n")
    if limit:
        lines = lines[-limit:]
    return [json.loads(l) for l in lines if l.strip()]

def print_banner(title: str):
    print(f"\n🔮 ORACLE — {title}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
