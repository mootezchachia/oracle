"""ORACLE — Polymarket Market Feed
Polls Gamma API, tracks price changes, identifies sentiment-driven markets.
"""

import json
import math
import sys
import time
import requests
from config import *

FEED_LOG = DATA_DIR / "polymarket_feed.jsonl"
MARKETS_CACHE = DATA_DIR / "markets_snapshot.json"


def fetch_markets(limit=100, offset=0) -> list:
    """Fetch active markets from Polymarket Gamma API."""
    params = {
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume",
        "ascending": "false",
    }
    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def is_sentiment_driven(question: str) -> float:
    """Score how sentiment-driven a market is (0-1)."""
    q = question.lower()
    for skip in SKIP_CATEGORIES:
        if skip in q:
            return 0.0

    high = ["approval", "favorability", "popularity", "public opinion"]
    med_high = ["midterm", "election", "win the house", "senate", "sweep",
                "ceasefire", "peace deal", "protests", "boycott", "recession"]
    med = ["tariff", "regulation", "ban", "impeach", "resign", "policy",
           "executive order", "supreme court", "consumer confidence"]
    low = ["fed decision", "interest rate", "gdp", "inflation", "cpi",
           "earnings", "stock price"]

    for kw in high:
        if kw in q: return 1.0
    for kw in med_high:
        if kw in q: return 0.8
    for kw in med:
        if kw in q: return 0.6
    for kw in low:
        if kw in q: return 0.3
    return 0.1


def score_market(m: dict) -> dict | None:
    """Score a market for ORACLE relevance. Returns enriched dict or None."""
    question = m.get("question", "")
    volume = float(m.get("volume", 0) or 0)
    end_date = m.get("endDate", "")
    sentiment = is_sentiment_driven(question)

    if sentiment < 0.2 or volume < 1000:
        return None

    # Timing score
    timing = 0.5
    if end_date:
        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days = (end - datetime.now(timezone.utc)).total_seconds() / 86400
            if days < 1: timing = 0.1
            elif days <= 3: timing = 0.6
            elif days <= 7: timing = 1.0
            elif days <= 14: timing = 0.9
            elif days <= 30: timing = 0.7
            elif days <= 90: timing = 0.4
            else: timing = 0.2
        except: pass

    vol_score = min(1.0, math.log10(max(volume, 1)) / 7)
    edge_score = sentiment * vol_score * timing

    if edge_score < 0.05:
        return None

    return {
        "question": question,
        "slug": m.get("slug", ""),
        "market_id": m.get("id", ""),
        "outcomes": m.get("outcomes", ""),
        "prices": m.get("outcomePrices", ""),
        "volume": volume,
        "end_date": end_date,
        "sentiment_weight": sentiment,
        "volume_score": round(vol_score, 3),
        "timing_score": round(timing, 3),
        "edge_score": round(edge_score, 3),
        "url": f"https://polymarket.com/event/{m.get('slug', '')}",
        "description": (m.get("description") or "")[:500],
    }


def scan_all(pages=4, per_page=50) -> list:
    """Scan Polymarket and return scored, sorted market list."""
    all_scored = []
    for page in range(pages):
        print(f"  Fetching page {page+1}/{pages}...")
        try:
            markets = fetch_markets(limit=per_page, offset=page * per_page)
        except Exception as e:
            print(f"  ⚠ Page {page+1} failed: {e}")
            continue

        for m in markets:
            scored = score_market(m)
            if scored:
                all_scored.append(scored)
                append_jsonl(FEED_LOG, {
                    "timestamp": now_iso(),
                    "slug": scored["slug"],
                    "question": scored["question"],
                    "prices": scored["prices"],
                    "volume": scored["volume"],
                    "edge_score": scored["edge_score"],
                })

    all_scored.sort(key=lambda x: x["edge_score"], reverse=True)

    # Save snapshot
    with open(MARKETS_CACHE, "w") as f:
        json.dump(all_scored, f, indent=2)

    return all_scored


def print_targets(markets: list, limit=15):
    """Pretty-print top market targets."""
    top = markets[:limit]
    print(f"  Found {len(markets)} sentiment-driven markets.\n")
    print(f"  {'#':<4} {'Score':<7} {'S':<5} {'V':<5} {'T':<5} {'Vol':>12}  Question")
    print(f"  {'─'*80}")
    for i, m in enumerate(top, 1):
        q = m["question"][:50] + ("..." if len(m["question"]) > 50 else "")
        print(f"  {i:<4} {m['edge_score']:<7} {m['sentiment_weight']:<5} "
              f"{m['volume_score']:<5} {m['timing_score']:<5} "
              f"${m['volume']:>11,.0f}  {q}")


def main():
    print_banner("Polymarket Scanner")
    markets = scan_all()
    print_targets(markets)
    print(f"\n  Snapshot saved to: {MARKETS_CACHE}")
    print(f"  Feed log: {FEED_LOG}\n")
    return markets


if __name__ == "__main__":
    main()
