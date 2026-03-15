"""ORACLE — Event Scorer & Seed Assembler
Maps real-world events to Polymarket markets, scores narrative ambiguity,
and assembles seed documents for simulation.
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from config import *

# Import sibling modules
from polymarket_ws import scan_all as scan_markets, MARKETS_CACHE
from reddit_velocity import scan_reddit, fetch_top_comments
from fred_watcher import scan_fred
from news_rss import scan_news


# --- Narrative Ambiguity Detection ------------------------------------

# Events that can be interpreted multiple ways score highest
AMBIGUOUS_PATTERNS = {
    "fed_hold": {
        "keywords": ["fed", "rate", "hold", "pause", "unchanged", "fomc", "federal reserve", "powell"],
        "narratives": [
            "Economy too weak to raise -> bearish signal",
            "Inflation under control, steady hand -> bullish signal"
        ],
        "ambiguity": 0.85,
    },
    "fed_cut": {
        "keywords": ["fed", "rate cut", "lower rates", "easing", "fed rate", "dovish", "pivot"],
        "narratives": [
            "Economy needs rescue -> recession fear",
            "Inflation beaten, growth ahead -> bullish"
        ],
        "ambiguity": 0.8,
    },
    "fed_hike": {
        "keywords": ["fed", "rate hike", "raise rates", "hawkish", "tightening", "higher rates"],
        "narratives": [
            "Inflation still hot -> pain ahead -> bearish",
            "Economy strong enough to handle it -> confidence signal"
        ],
        "ambiguity": 0.75,
    },
    "military_action": {
        "keywords": ["strike", "military", "bomb", "attack", "troops", "operation", "missile",
                      "airstrike", "invasion", "deploy", "war"],
        "narratives": [
            "Strength projection -> rally around the flag -> approval up",
            "Costly escalation -> war fatigue -> approval down"
        ],
        "ambiguity": 0.9,
    },
    "ceasefire_signal": {
        "keywords": ["ceasefire", "peace", "negotiate", "diplomatic", "talks", "truce",
                      "de-escalation", "deal", "accord", "agreement"],
        "narratives": [
            "De-escalation -> stability -> positive for incumbents",
            "Weakness/capitulation -> seen as giving in -> negative for hawks"
        ],
        "ambiguity": 0.75,
    },
    "iran_tensions": {
        "keywords": ["iran", "tehran", "irgc", "sanctions", "nuclear", "enrichment",
                      "strait of hormuz", "persian gulf", "proxy"],
        "narratives": [
            "Escalation -> oil spike -> inflation -> bearish for markets",
            "Contained tensions -> rally on resolution -> bullish"
        ],
        "ambiguity": 0.85,
    },
    "oil_energy": {
        "keywords": ["oil", "crude", "opec", "brent", "wti", "petroleum", "energy",
                      "barrel", "gasoline", "fuel", "natural gas"],
        "narratives": [
            "Price spike -> inflation -> consumer squeeze -> bearish",
            "Price drop -> lower input costs -> growth boost -> bullish"
        ],
        "ambiguity": 0.8,
    },
    "economic_data": {
        "keywords": ["jobs", "unemployment", "cpi", "inflation", "gdp", "retail",
                      "payrolls", "nonfarm", "pce", "manufacturing", "services",
                      "housing", "consumer spending", "wages", "labor"],
        "narratives": [
            "Data better than expected -> economy resilient -> bullish",
            "Data worse than expected -> recession approaching -> bearish"
        ],
        "ambiguity": 0.7,
    },
    "recession_fear": {
        "keywords": ["recession", "downturn", "contraction", "slowdown", "layoffs",
                      "default", "debt ceiling", "yield curve", "inversion"],
        "narratives": [
            "Hard landing -> deep recession -> bearish across board",
            "Soft landing -> brief dip -> buying opportunity -> bullish"
        ],
        "ambiguity": 0.8,
    },
    "tariff_trade": {
        "keywords": ["tariff", "trade", "import", "export", "duties", "trade war",
                      "trade deal", "customs", "wto", "trade deficit", "protectionism"],
        "narratives": [
            "Tariffs protect domestic industry -> bullish for local",
            "Trade war escalation -> supply chain disruption -> bearish"
        ],
        "ambiguity": 0.75,
    },
    "tariff_ruling": {
        "keywords": ["tariff", "court", "ruling", "scotus", "supreme court",
                      "unconstitutional", "legal challenge", "overturn"],
        "narratives": [
            "Tariffs blocked -> free trade wins -> business positive",
            "Tariffs blocked -> presidential authority weakened -> political crisis"
        ],
        "ambiguity": 0.7,
    },
    "trump_action": {
        "keywords": ["trump", "executive order", "truth social", "mar-a-lago",
                      "indictment", "trial", "conviction", "pardon", "maga"],
        "narratives": [
            "Base energized -> fundraising surge -> stronger position",
            "Legal jeopardy -> distraction -> weakened position"
        ],
        "ambiguity": 0.85,
    },
    "approval_shift": {
        "keywords": ["approval", "poll", "survey", "favorability", "disapproval",
                      "approval rating", "job approval", "net approval", "gallup"],
        "narratives": [
            "Temporary fluctuation -> reversion to mean",
            "Structural shift -> new baseline -> cascading effects on midterms"
        ],
        "ambiguity": 0.65,
    },
    "election_development": {
        "keywords": ["midterm", "election", "ballot", "candidate", "primary", "campaign",
                      "vote", "electoral", "swing state", "battleground", "turnout",
                      "early voting", "absentee"],
        "narratives": [
            "Enthusiasm gap favors challengers -> wave incoming",
            "Incumbency advantage holds -> status quo prevails"
        ],
        "ambiguity": 0.7,
    },
    "ukraine_conflict": {
        "keywords": ["ukraine", "kyiv", "zelensky", "nato", "putin", "russia",
                      "donbas", "crimea", "offensive", "counteroffensive"],
        "narratives": [
            "Escalation -> energy crisis -> global instability -> bearish",
            "Resolution in sight -> reconstruction boom -> bullish"
        ],
        "ambiguity": 0.85,
    },
    "china_taiwan": {
        "keywords": ["china", "taiwan", "beijing", "xi jinping", "strait",
                      "pla", "chips", "semiconductor", "decoupling", "south china sea"],
        "narratives": [
            "Military escalation -> supply chain catastrophe -> bearish",
            "Diplomatic thaw -> trade normalization -> bullish"
        ],
        "ambiguity": 0.9,
    },
    "nato_alliance": {
        "keywords": ["nato", "alliance", "article 5", "defense spending",
                      "collective defense", "member", "expansion"],
        "narratives": [
            "Alliance strengthening -> deterrence -> stability",
            "Burden-sharing tensions -> weakened unity -> instability"
        ],
        "ambiguity": 0.7,
    },
}


def detect_narrative_ambiguity(text: str) -> tuple:
    """Detect if text matches ambiguous event patterns.
    Returns (ambiguity_score, matched_pattern, narratives).
    Uses partial matching: 1 keyword = reduced score, 2+ = full scoring."""
    text_lower = text.lower()
    best_match = None
    best_score = 0.0

    for pattern_name, pattern in AMBIGUOUS_PATTERNS.items():
        match_count = sum(1 for kw in pattern["keywords"] if kw in text_lower)
        if match_count >= 1:
            # With 1 match, give 60% credit; with 2+ scale up
            keyword_ratio = match_count / len(pattern["keywords"])
            coverage = max(0.6, keyword_ratio * 2.0)  # boost coverage
            coverage = min(coverage, 1.0)
            score = pattern["ambiguity"] * coverage
            if score > best_score:
                best_score = score
                best_match = pattern_name

    if best_match:
        return (
            round(best_score, 3),
            best_match,
            AMBIGUOUS_PATTERNS[best_match]["narratives"]
        )
    return (0.15, "unknown", ["Standard interpretation"])


def map_event_to_markets(event_text: str, markets: list) -> list:
    """Find Polymarket markets affected by this event."""
    event_lower = event_text.lower()
    affected = []

    # Keyword extraction from event (include 3-letter words too)
    event_words = set(re.findall(r'\b[a-z]{3,}\b', event_lower))

    for m in markets:
        q_lower = m["question"].lower()
        q_words = set(re.findall(r'\b[a-z]{3,}\b', q_lower))

        # Calculate overlap
        overlap = event_words & q_words
        relevance = len(overlap) / max(len(event_words), 1)

        # Boost for high-sentiment markets
        relevance *= (1 + m.get("sentiment_weight", 0))

        if relevance > 0.1:
            affected.append({
                **m,
                "relevance": round(min(relevance, 1.0), 3),
                "matching_words": list(overlap)[:10],
            })

    affected.sort(key=lambda x: x["relevance"], reverse=True)
    return affected[:5]


def get_repricing_window() -> tuple:
    """Estimate current repricing window based on time of day and day of week."""
    now = datetime.now(timezone.utc)
    hour_utc = now.hour
    day = now.strftime("%A")

    # Check weekend first
    if day in ("Saturday", "Sunday"):
        return (0.85, f"Weekend ({day}) -- thin market, large window")

    # US market hours: roughly 14:00-21:00 UTC (9am-4pm ET)
    if 14 <= hour_utc < 21:
        return (0.35, "US active trading -- fast absorption, small window")
    elif 21 <= hour_utc or hour_utc < 6:
        return (0.9, "US closed/sleeping -- slow absorption, large window (6-10 hours)")
    elif 6 <= hour_utc < 10:
        return (0.7, "US overnight/Asia active -- moderate-large window")
    elif 10 <= hour_utc < 14:
        return (0.55, "US pre-market -- moderate absorption, medium window")

    return (0.5, "Normal trading conditions")


def score_event(event_text: str, markets: list) -> dict:
    """Score an event for ORACLE simulation potential.
    Produces scores in the 0.3-0.9 range for genuinely important events."""
    ambiguity, pattern, narratives = detect_narrative_ambiguity(event_text)
    affected = map_event_to_markets(event_text, markets)
    window_score, window_desc = get_repricing_window()

    # Market relevance: sum top 3, but use a floor so matched events score decently
    raw_relevance = sum(m["relevance"] for m in affected[:3])
    market_relevance = min(1.0, raw_relevance)
    # Give a minimum relevance if we have any affected markets
    if affected:
        market_relevance = max(market_relevance, 0.3)

    # Volume potential with logarithmic scaling
    volume_potential = 0.1  # floor
    if affected:
        max_vol = max(m.get("volume", 0) for m in affected)
        if max_vol > 0:
            # log10(1000)=3 -> 0.43, log10(100000)=5 -> 0.71, log10(1M)=6 -> 0.86
            volume_potential = min(1.0, math.log10(max(max_vol, 1)) / 7)
            volume_potential = max(volume_potential, 0.15)

    # Weighted combination instead of pure multiplication (which crushes scores)
    # Ambiguity and market_relevance are most important
    event_score = (
        0.35 * ambiguity +
        0.25 * market_relevance +
        0.20 * window_score +
        0.10 * volume_potential +
        0.10 * (ambiguity * market_relevance)  # interaction term
    )

    # Clamp to reasonable range
    event_score = max(0.05, min(0.95, event_score))

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


# --- Seed Document Builder --------------------------------------------

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
    print("--- STEP 1/5: Scanning Polymarket ---")
    markets = scan_markets()
    results["markets"] = markets
    print(f"  -> {len(markets)} sentiment-driven markets found\n")

    # 2. Scan Reddit
    print("--- STEP 2/5: Scanning Reddit ---")
    reddit_posts = scan_reddit()
    results["reddit"] = reddit_posts
    print(f"  -> {len(reddit_posts)} posts tracked\n")

    # 3. Scan FRED
    print("--- STEP 3/5: Fetching Economic Data ---")
    fred_data = scan_fred()
    results["fred"] = fred_data
    print(f"  -> {len(fred_data)} series fetched\n")

    # 4. Scan News
    print("--- STEP 4/5: Fetching News ---")
    news = scan_news()
    results["news"] = news
    print(f"  -> {len(news)} headlines collected\n")

    # 5. Score top events
    print("--- STEP 5/5: Scoring Events & Building Seeds ---")

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
