"""ORACLE — Event Scorer & Seed Assembler
Maps real-world events to Polymarket markets, scores narrative ambiguity,
and assembles seed documents for simulation.
"""

import json
import re
import time
from datetime import datetime, timezone
from config import *

# Import sibling modules
from polymarket_ws import scan_all as scan_markets, MARKETS_CACHE
from reddit_velocity import scan_reddit, fetch_top_comments
from fred_watcher import scan_fred
from news_rss import scan_news


# ─── Narrative Ambiguity Detection ────────────────────────────────

# Events that can be interpreted multiple ways score highest
AMBIGUOUS_PATTERNS = {
    # Pattern: (keywords, competing narratives)
    "fed_hold": {
        "keywords": ["fed", "rate", "hold", "pause", "unchanged"],
        "narratives": [
            "Economy too weak to raise → bearish signal",
            "Inflation under control, steady hand → bullish signal"
        ],
        "ambiguity": 0.85,
    },
    "fed_cut": {
        "keywords": ["fed", "rate cut", "lower rates", "easing"],
        "narratives": [
            "Economy needs rescue → recession fear",
            "Inflation beaten, growth ahead → bullish"
        ],
        "ambiguity": 0.8,
    },
    "military_action": {
        "keywords": ["strike", "military", "bomb", "attack", "troops", "operation"],
        "narratives": [
            "Strength projection → rally around the flag → approval up",
            "Costly escalation → war fatigue → approval down"
        ],
        "ambiguity": 0.9,
    },
    "ceasefire_signal": {
        "keywords": ["ceasefire", "peace", "negotiate", "diplomatic", "talks"],
        "narratives": [
            "De-escalation → stability → positive for incumbents",
            "Weakness/capitulation → seen as giving in → negative for hawks"
        ],
        "ambiguity": 0.75,
    },
    "economic_data": {
        "keywords": ["jobs", "unemployment", "cpi", "inflation", "gdp", "retail"],
        "narratives": [
            "Data better than expected → economy resilient → bullish",
            "Data worse than expected → recession approaching → bearish"
        ],
        "ambiguity": 0.7,
    },
    "tariff_ruling": {
        "keywords": ["tariff", "trade", "court", "ruling", "scotus", "import"],
        "narratives": [
            "Tariffs blocked → free trade wins → business positive",
            "Tariffs blocked → presidential authority weakened → political crisis"
        ],
        "ambiguity": 0.7,
    },
    "approval_shift": {
        "keywords": ["approval", "poll", "survey", "favorability", "disapproval"],
        "narratives": [
            "Temporary fluctuation → reversion to mean",
            "Structural shift → new baseline → cascading effects on midterms"
        ],
        "ambiguity": 0.6,
    },
    "election_development": {
        "keywords": ["midterm", "election", "ballot", "candidate", "primary", "campaign"],
        "narratives": [
            "Enthusiasm gap favors challengers → wave incoming",
            "Incumbency advantage holds → status quo prevails"
        ],
        "ambiguity": 0.65,
    },
}


def detect_narrative_ambiguity(text: str) -> tuple:
    """Detect if text matches ambiguous event patterns.
    Returns (ambiguity_score, matched_pattern, narratives)."""
    text_lower = text.lower()
    best_match = None
    best_score = 0.0

    for pattern_name, pattern in AMBIGUOUS_PATTERNS.items():
        match_count = sum(1 for kw in pattern["keywords"] if kw in text_lower)
        if match_count >= 2:  # need at least 2 keyword matches
            score = pattern["ambiguity"] * (match_count / len(pattern["keywords"]))
            if score > best_score:
                best_score = score
                best_match = pattern_name

    if best_match:
        return (
            round(best_score, 3),
            best_match,
            AMBIGUOUS_PATTERNS[best_match]["narratives"]
        )
    return (0.1, "unknown", ["Standard interpretation"])


def map_event_to_markets(event_text: str, markets: list) -> list:
    """Find Polymarket markets affected by this event."""
    event_lower = event_text.lower()
    affected = []

    # Keyword extraction from event
    event_words = set(re.findall(r'\b[a-z]{4,}\b', event_lower))

    for m in markets:
        q_lower = m["question"].lower()
        q_words = set(re.findall(r'\b[a-z]{4,}\b', q_lower))

        # Calculate overlap
        overlap = event_words & q_words
        relevance = len(overlap) / max(len(event_words), 1)

        # Boost for high-sentiment markets
        relevance *= (1 + m.get("sentiment_weight", 0))

        if relevance > 0.15:
            affected.append({
                **m,
                "relevance": round(relevance, 3),
                "matching_words": list(overlap)[:10],
            })

    affected.sort(key=lambda x: x["relevance"], reverse=True)
    return affected[:5]


def get_repricing_window() -> tuple:
    """Estimate current repricing window based on time of day."""
    now = datetime.now(timezone.utc)
    hour_utc = now.hour
    day = now.strftime("%A")

    # US market hours: roughly 14:00-22:00 UTC (9am-5pm ET)
    if 14 <= hour_utc <= 22:
        return (0.3, "US active trading — fast absorption, small window")
    elif 22 < hour_utc or hour_utc < 6:
        return (0.9, "US sleeping — slow absorption, large window (6-10 hours)")
    elif 6 <= hour_utc < 14:
        return (0.6, "US pre-market — moderate absorption, medium window")

    if day in ["Saturday", "Sunday"]:
        return (0.85, f"Weekend ({day}) — thin market, large window")

    return (0.5, "Normal trading conditions")


def score_event(event_text: str, markets: list) -> dict:
    """Score an event for ORACLE simulation potential."""
    ambiguity, pattern, narratives = detect_narrative_ambiguity(event_text)
    affected = map_event_to_markets(event_text, markets)
    window_score, window_desc = get_repricing_window()

    market_relevance = min(1.0, sum(m["relevance"] for m in affected[:3]))
    volume_potential = 0.0
    if affected:
        max_vol = max(m.get("volume", 0) for m in affected)
        volume_potential = min(1.0, (max_vol / 1_000_000) ** 0.3)

    event_score = ambiguity * market_relevance * window_score * max(volume_potential, 0.1)

    return {
        "event": event_text,
        "event_score": round(event_score, 3),
        "narrative_ambiguity": ambiguity,
        "pattern": pattern,
        "competing_narratives": narratives,
        "market_relevance": round(market_relevance, 3),
        "repricing_window": window_score,
        "repricing_desc": window_desc,
        "volume_potential": round(volume_potential, 3),
        "affected_markets": [{
            "question": m["question"],
            "slug": m["slug"],
            "prices": m.get("prices", ""),
            "volume": m.get("volume", 0),
            "relevance": m["relevance"],
            "url": m.get("url", ""),
        } for m in affected],
    }


# ─── Seed Document Builder ────────────────────────────────────────

def build_seed(event_score: dict, reddit_posts: list = None,
               fred_data: dict = None, news: list = None) -> str:
    """Build a comprehensive seed document for Claude Code simulation."""
    lines = []
    lines.append("# ORACLE SIMULATION SEED DOCUMENT")
    lines.append(f"# Generated: {now_iso()}")
    lines.append(f"# Event Score: {event_score['event_score']}")
    lines.append("")

    # Event details
    lines.append("## TRIGGERING EVENT")
    lines.append(f"**Event:** {event_score['event']}")
    lines.append(f"**Pattern:** {event_score['pattern']}")
    lines.append(f"**Narrative Ambiguity:** {event_score['narrative_ambiguity']}")
    lines.append(f"**Repricing Window:** {event_score['repricing_desc']}")
    lines.append("")
    lines.append("### Competing Narratives")
    for i, n in enumerate(event_score["competing_narratives"], 1):
        lines.append(f"  {i}. {n}")
    lines.append("")

    # Affected markets
    lines.append("## AFFECTED POLYMARKET MARKETS")
    lines.append("")
    for m in event_score["affected_markets"]:
        lines.append(f"### {m['question']}")
        lines.append(f"- URL: {m['url']}")
        lines.append(f"- Current prices: {m['prices']}")
        lines.append(f"- Volume: ${m['volume']:,.0f}")
        lines.append(f"- Relevance: {m['relevance']}")
        lines.append("")

    # Reddit context
    if reddit_posts:
        lines.append("## REDDIT COMMUNITY CONTEXT")
        lines.append("")
        relevant_posts = [p for p in reddit_posts if p.get("velocity", 0) > 5][:15]
        for p in relevant_posts:
            lines.append(f"- [r/{p['subreddit']} | {p['score']}pts | vel:{p['velocity']}] {p['title']}")
        lines.append("")

    # Economic data
    if fred_data:
        lines.append("## ECONOMIC INDICATORS")
        lines.append("")
        for sid, info in fred_data.items():
            latest = info["data"][0] if info["data"] else None
            if latest:
                lines.append(f"- {info['name']}: {latest['value']} ({latest['date']})")
        lines.append("")

    # News context
    if news:
        lines.append("## RECENT NEWS CONTEXT")
        lines.append("")
        for a in news[:15]:
            lines.append(f"- [{a['source']}] {a['title']}")
            if a.get("description"):
                lines.append(f"  {a['description'][:200]}")
        lines.append("")

    return "\n".join(lines)


def run_full_scan() -> dict:
    """Run complete ORACLE scan: markets + reddit + fred + news + scoring."""
    results = {}

    # 1. Scan Polymarket
    print("─── STEP 1/5: Scanning Polymarket ───")
    markets = scan_markets()
    results["markets"] = markets
    print(f"  → {len(markets)} sentiment-driven markets found\n")

    # 2. Scan Reddit
    print("─── STEP 2/5: Scanning Reddit ───")
    reddit_posts = scan_reddit()
    results["reddit"] = reddit_posts
    print(f"  → {len(reddit_posts)} posts tracked\n")

    # 3. Scan FRED
    print("─── STEP 3/5: Fetching Economic Data ───")
    fred_data = scan_fred()
    results["fred"] = fred_data
    print(f"  → {len(fred_data)} series fetched\n")

    # 4. Scan News
    print("─── STEP 4/5: Fetching News ───")
    news = scan_news()
    results["news"] = news
    print(f"  → {len(news)} headlines collected\n")

    # 5. Score top events
    print("─── STEP 5/5: Scoring Events & Building Seeds ───")

    # Use top Reddit posts + news headlines as potential events
    event_candidates = []

    for p in reddit_posts[:20]:
        scored = score_event(p["title"], markets)
        scored["source"] = f"reddit/r/{p['subreddit']}"
        scored["source_velocity"] = p.get("velocity", 0)
        event_candidates.append(scored)

    for a in news[:20]:
        scored = score_event(a["title"], markets)
        scored["source"] = a["source"]
        event_candidates.append(scored)

    event_candidates.sort(key=lambda x: x["event_score"], reverse=True)
    results["events"] = event_candidates

    # Build seeds for top 3 events
    top_events = [e for e in event_candidates if e["event_score"] > 0.05][:3]
    results["seeds"] = []

    for i, event in enumerate(top_events, 1):
        seed_text = build_seed(event, reddit_posts, fred_data, news)
        slug = re.sub(r'[^a-z0-9]+', '-', event["event"][:50].lower()).strip('-')
        seed_path = SEEDS_DIR / f"{slug}.md"
        seed_path.write_text(seed_text)
        results["seeds"].append({"path": str(seed_path), "event": event})
        print(f"  Seed #{i}: {event['event'][:60]}...")
        print(f"    Score: {event['event_score']} | Pattern: {event['pattern']}")
        print(f"    Saved: {seed_path}")

        # Also save as alert
        alert_path = ALERTS_DIR / f"alert-{i}.json"
        with open(alert_path, "w") as f:
            json.dump(event, f, indent=2)
        print(f"    Alert: {alert_path}\n")

    return results


def main():
    print_banner("Full System Scan")
    print("=" * 60)
    results = run_full_scan()

    # Summary
    print("\n" + "=" * 60)
    print("  ORACLE SCAN COMPLETE\n")
    print(f"  Markets tracked:  {len(results['markets'])}")
    print(f"  Reddit posts:     {len(results['reddit'])}")
    print(f"  News headlines:   {len(results['news'])}")
    print(f"  Events scored:    {len(results['events'])}")
    print(f"  Seeds generated:  {len(results['seeds'])}")

    if results["seeds"]:
        print(f"\n  TOP SIMULATION TARGETS:")
        for i, s in enumerate(results["seeds"], 1):
            e = s["event"]
            print(f"  #{i} [Score: {e['event_score']}] {e['event'][:60]}...")
            if e["affected_markets"]:
                m = e["affected_markets"][0]
                print(f"      Market: {m['question'][:50]}... (${m['volume']:,.0f})")

        print(f"\n  NEXT STEP:")
        print(f"  Open Claude Code in this directory and paste prompts/oracle.md")
        print(f"  along with the seed file for your chosen target.\n")
    else:
        print(f"\n  No high-scoring events detected. Check back later.\n")

    return results


if __name__ == "__main__":
    main()
