#!/usr/bin/env python3
"""ORACLE Dashboard Server — Serves live data + dashboard UI"""

import json
import os
import re
import time
import threading
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import requests
import feedparser
from flask import Flask, jsonify, send_from_directory, Response

# ─── Config ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "nerve" / "data"
SEEDS_DIR = ROOT / "seeds"
ALERTS_DIR = ROOT / "alerts"
PREDICTIONS_DIR = ROOT / "predictions" / "active"

GAMMA_API = "https://gamma-api.polymarket.com"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "DEMO_KEY")
REDDIT_UA = "ORACLE/1.0 (Narrative Arbitrage Engine)"

# Sentiment filter keywords
SENTIMENT_KW = [
    "approval", "election", "ceasefire", "peace", "recession", "tariff",
    "fed", "iran", "ukraine", "trump", "biden", "midterm", "congress",
    "senate", "governor", "nato", "china", "taiwan", "oil", "inflation",
    "interest rate", "gdp", "war", "nuclear", "sanction", "regime",
    "impeach", "indictment", "poll", "favorability", "ban", "policy",
    "abortion", "immigration", "border", "debt ceiling", "shutdown",
]
SKIP_KW = [
    "nba", "nfl", "nhl", "mlb", "ufc", "mma", "premier league", "champions league",
    "la liga", "bundesliga", "serie a", "ligue 1", "epl", "cricket",
    "temperature", "weather", "rain", "netflix", "spotify", "tiktok",
    "youtube", "subscriber", "follower", "airdrop", "token launch",
    "esports", "csgo", "valorant", "dota",
]

REDDIT_SUBS = {
    "political": ["politics", "worldnews", "geopolitics"],
    "economic": ["economics", "wallstreetbets"],
    "markets": ["polymarket"],
    "mena": ["iran", "middleeast"],
}

FRED_SERIES = {
    "UNRATE": "Unemployment Rate",
    "CPIAUCSL": "Consumer Price Index",
    "UMCSENT": "Consumer Sentiment",
    "FEDFUNDS": "Federal Funds Rate",
    "DCOILWTICO": "WTI Crude Oil",
    "T10Y2Y": "Yield Curve (10Y-2Y)",
}

RSS_FEEDS = {
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "NPR News": "https://feeds.npr.org/1001/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
}

# ─── Tracked predictions (slug → our estimate) ──────────────────────────────
TRACKED_MARKETS = {
    "us-x-iran-ceasefire-by-march-31": {"oracle": 8, "label": "#001 Iran Ceasefire", "direction": "NO"},
    "will-crude-oil-cl-hit-high-120-by-end-of-march-766-813-597": {"oracle": 52, "label": "#002 Oil $120", "direction": "YES"},
}

# ─── In-memory cache ─────────────────────────────────────────────────────────
cache = {
    "markets": {"data": [], "ts": 0},
    "reddit": {"data": [], "ts": 0},
    "news": {"data": [], "ts": 0},
    "fred": {"data": [], "ts": 0},
    "predictions": {"data": [], "ts": 0},
    "events": {"data": [], "ts": 0},
    "status": {"last_scan": None, "scans": 0, "errors": []},
}

# Price history: slug → [{ts, price}]
price_history = {}
HISTORY_FILE = DATA_DIR / "price_history.json"

cache_lock = threading.Lock()
CACHE_TTL = 120  # seconds

def load_price_history():
    global price_history
    if HISTORY_FILE.exists():
        try:
            price_history = json.loads(HISTORY_FILE.read_text())
        except:
            price_history = {}

def save_price_history():
    try:
        HISTORY_FILE.write_text(json.dumps(price_history))
    except:
        pass

def record_prices(markets):
    """Record current prices for tracked markets"""
    ts = time.time()
    for m in markets:
        slug = m.get("slug", "")
        if slug in TRACKED_MARKETS:
            if slug not in price_history:
                price_history[slug] = []
            outcomes = m.get("outcomes", [])
            yes_price = None
            for o in outcomes:
                if o.get("name", "").lower() == "yes":
                    yes_price = o.get("price")
                    break
            if yes_price is None and outcomes:
                yes_price = outcomes[0].get("price")
            if yes_price is not None:
                price_history[slug].append({"ts": ts, "price": round(float(yes_price), 1)})
                # Keep last 500 data points per market
                price_history[slug] = price_history[slug][-500:]
    save_price_history()

load_price_history()


# ─── Data fetchers ───────────────────────────────────────────────────────────

def fetch_markets():
    """Fetch sentiment-driven markets from Polymarket"""
    try:
        markets = []
        for offset in range(0, 200, 50):
            r = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "false", "limit": 50, "offset": offset,
                "order": "volume", "ascending": "false"
            }, timeout=15)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            markets.extend(batch)

        results = []
        for m in markets:
            q = (m.get("question") or "").lower()
            if any(k in q for k in SKIP_KW):
                continue
            if not any(k in q for k in SENTIMENT_KW):
                continue

            try:
                prices = json.loads(m.get("outcomePrices", "[]"))
            except (json.JSONDecodeError, TypeError):
                prices = []

            outcomes = m.get("outcomes", "")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except:
                    outcomes = outcomes.split(",") if outcomes else []

            outcome_data = []
            for i, name in enumerate(outcomes):
                price = float(prices[i]) * 100 if i < len(prices) else 0
                outcome_data.append({"name": name.strip(), "price": round(price, 1)})

            vol = float(m.get("volume", 0) or 0)
            results.append({
                "id": m.get("id"),
                "question": m.get("question"),
                "outcomes": outcome_data,
                "volume": vol,
                "volume_fmt": f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K",
                "end_date": m.get("endDate", ""),
                "slug": m.get("slug", ""),
                "url": f"https://polymarket.com/market/{m.get('slug', '')}",
                "liquidity": float(m.get("liquidity", 0) or 0),
            })

        results.sort(key=lambda x: x["volume"], reverse=True)
        return results[:30]
    except Exception as e:
        cache["status"]["errors"].append(f"Markets: {str(e)[:100]}")
        return cache["markets"]["data"] or []


def fetch_tracked_markets():
    """Fetch prices specifically for our tracked prediction markets"""
    for slug in TRACKED_MARKETS:
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
            r.raise_for_status()
            markets = r.json()
            if markets:
                m = markets[0] if isinstance(markets, list) else markets
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                except:
                    prices = []
                outcomes = m.get("outcomes", "")
                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except:
                        outcomes = []
                yes_price = None
                for i, name in enumerate(outcomes if isinstance(outcomes, list) else []):
                    n = name.strip() if isinstance(name, str) else name
                    if (isinstance(n, str) and n.lower() == "yes") and i < len(prices):
                        yes_price = round(float(prices[i]) * 100, 1)
                        break
                if yes_price is None and prices:
                    yes_price = round(float(prices[0]) * 100, 1)
                if yes_price is not None:
                    if slug not in price_history:
                        price_history[slug] = []
                    price_history[slug].append({"ts": time.time(), "price": yes_price})
                    price_history[slug] = price_history[slug][-500:]
            time.sleep(0.3)
        except Exception as e:
            cache["status"]["errors"].append(f"Tracked {slug[:30]}: {str(e)[:60]}")
    save_price_history()


def fetch_reddit():
    """Fetch hot posts from tracked subreddits with velocity scoring"""
    results = []
    all_subs = [s for group in REDDIT_SUBS.values() for s in group]

    for sub in all_subs:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=15",
                headers={"User-Agent": REDDIT_UA}, timeout=10
            )
            if r.status_code == 429:
                time.sleep(2)
                continue
            r.raise_for_status()
            data = r.json().get("data", {}).get("children", [])

            for post in data:
                d = post.get("data", {})
                if d.get("stickied"):
                    continue

                created = d.get("created_utc", time.time())
                age_hours = max((time.time() - created) / 3600, 0.1)
                score = d.get("score", 0)
                comments = d.get("num_comments", 0)
                velocity = (score / age_hours) + (comments / age_hours * 2)

                results.append({
                    "title": d.get("title", ""),
                    "subreddit": sub,
                    "score": score,
                    "comments": comments,
                    "velocity": round(velocity, 1),
                    "age_hours": round(age_hours, 1),
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "created": created,
                })
            time.sleep(0.5)  # rate limit
        except Exception as e:
            cache["status"]["errors"].append(f"Reddit r/{sub}: {str(e)[:80]}")

    results.sort(key=lambda x: x["velocity"], reverse=True)
    return results[:25]


def fetch_fred():
    """Fetch economic indicators from FRED"""
    results = []
    for series_id, name in FRED_SERIES.items():
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": "5",
            }, timeout=10)
            r.raise_for_status()
            obs = r.json().get("observations", [])

            if obs:
                latest = obs[0]
                prev = obs[1] if len(obs) > 1 else None
                val = latest.get("value", ".")
                prev_val = prev.get("value", ".") if prev else "."

                try:
                    v = float(val)
                    pv = float(prev_val) if prev_val != "." else v
                    direction = "up" if v > pv else ("down" if v < pv else "flat")
                    change = v - pv
                except (ValueError, TypeError):
                    v, pv, direction, change = val, prev_val, "flat", 0

                results.append({
                    "id": series_id,
                    "name": name,
                    "value": v,
                    "prev_value": pv,
                    "date": latest.get("date", ""),
                    "direction": direction,
                    "change": round(change, 3) if isinstance(change, float) else 0,
                })
        except Exception as e:
            # Try loading from cache file
            cache_file = DATA_DIR / "fred_releases.jsonl"
            if cache_file.exists():
                try:
                    lines = cache_file.read_text().strip().split("\n")
                    for line in reversed(lines):
                        entry = json.loads(line)
                        if entry.get("series_id") == series_id:
                            results.append({
                                "id": series_id,
                                "name": name,
                                "value": entry.get("value", "N/A"),
                                "prev_value": "N/A",
                                "date": entry.get("date", ""),
                                "direction": "flat",
                                "change": 0,
                                "cached": True,
                            })
                            break
                except:
                    pass
            cache["status"]["errors"].append(f"FRED {series_id}: {str(e)[:80]}")

    return results


def fetch_news():
    """Fetch headlines from RSS feeds"""
    results = []
    seen = set()

    for source, url in RSS_FEEDS.items():
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": REDDIT_UA})
            r.raise_for_status()
            feed = feedparser.parse(r.text)

            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                h = hashlib.md5(title.encode()).hexdigest()[:8]
                if h in seen:
                    continue
                seen.add(h)

                pub = entry.get("published", entry.get("updated", ""))
                results.append({
                    "title": title,
                    "source": source,
                    "url": entry.get("link", ""),
                    "published": pub,
                    "summary": (entry.get("summary", "") or "")[:200],
                })
        except Exception as e:
            cache["status"]["errors"].append(f"News {source}: {str(e)[:80]}")

    return results[:40]


def load_predictions():
    """Load active predictions from markdown files"""
    preds = []
    if PREDICTIONS_DIR.exists():
        # Load ALL main prediction files (not simulations or summaries)
        for f in sorted(PREDICTIONS_DIR.glob("ORACLE_*.md")):
            if "simulation" in f.name or "summary" in f.name:
                continue
            try:
                text = f.read_text()
                pred = {"file": f.name, "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
                for line in text.split("\n"):
                    if line.startswith("Market:"):
                        pred["market"] = line.split(":", 1)[1].strip()
                    elif line.startswith("URL:"):
                        url = line.split("URL:", 1)[1].strip()
                        if not url.startswith("http"):
                            url = "https:" + url
                        pred["url"] = url.replace("/event/", "/market/")
                    elif line.startswith("Current price:"):
                        pred["current_price"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Target price:"):
                        pred["target_price"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Date:"):
                        pred["date"] = line.split(":", 1)[1].strip()
                    elif "Primary Call" in line:
                        pass
                    elif line.startswith("**") and "Confidence" in line:
                        pred["call"] = line.strip("*").strip()
                    elif line.startswith("Dominant narrative:"):
                        pred["narrative"] = line.split(":", 1)[1].strip().strip("*").strip()
                    elif line.startswith("Dominance score:"):
                        pred["dominance"] = line.split(":", 1)[1].strip().strip("*").strip()
                    elif line.startswith("**Trade recommendation:"):
                        pred["trade"] = line.strip("*").strip()
                # Extract prediction number from filename
                num_match = re.search(r'ORACLE_(\d+)', f.name)
                if num_match:
                    pred["number"] = int(num_match.group(1))
                # Extract primary call from next line after ## Primary Call
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if "## Primary Call" in line and i + 1 < len(lines):
                        pred["primary_call"] = lines[i + 1].strip().strip("*").strip()
                    elif "## Our Probability Distribution" in line:
                        # Grab next 2 lines for our odds
                        dist = []
                        for j in range(i + 1, min(i + 4, len(lines))):
                            if lines[j].strip().startswith("-"):
                                dist.append(lines[j].strip("- ").strip())
                        pred["our_odds"] = dist
                    elif "## Tweet" in line and i + 1 < len(lines):
                        tweet_lines = []
                        for j in range(i + 1, len(lines)):
                            if lines[j].strip().startswith("#"):
                                break
                            if lines[j].strip():
                                tweet_lines.append(lines[j].strip())
                        pred["tweet"] = " ".join(tweet_lines[:6])
                preds.append(pred)
            except:
                pass
    return preds


def load_events():
    """Load event scores from alerts"""
    events = []
    if ALERTS_DIR.exists():
        for f in sorted(ALERTS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict):
                    events.append(data)
                elif isinstance(data, list):
                    events.extend(data)
            except:
                pass
    return events[:20]


# ─── Background scanner ─────────────────────────────────────────────────────

def refresh_cache():
    """Refresh all data sources"""
    with cache_lock:
        cache["status"]["errors"] = []

    markets = fetch_markets()
    with cache_lock:
        cache["markets"] = {"data": markets, "ts": time.time()}
    record_prices(markets)

    # Also fetch tracked markets directly
    fetch_tracked_markets()

    reddit = fetch_reddit()
    with cache_lock:
        cache["reddit"] = {"data": reddit, "ts": time.time()}

    news = fetch_news()
    with cache_lock:
        cache["news"] = {"data": news, "ts": time.time()}

    fred = fetch_fred()
    with cache_lock:
        cache["fred"] = {"data": fred, "ts": time.time()}

    preds = load_predictions()
    with cache_lock:
        cache["predictions"] = {"data": preds, "ts": time.time()}

    events = load_events()
    with cache_lock:
        cache["events"] = {"data": events, "ts": time.time()}

    with cache_lock:
        cache["status"]["last_scan"] = datetime.now(timezone.utc).isoformat()
        cache["status"]["scans"] += 1


def background_scanner():
    """Run scans periodically"""
    while True:
        try:
            refresh_cache()
        except Exception as e:
            cache["status"]["errors"].append(f"Scanner: {str(e)[:100]}")
        time.sleep(300)  # 5 minutes


# ─── Flask App ───────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="dashboard")


@app.route("/")
def index():
    resp = send_from_directory("dashboard", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/v2")
def index_v2():
    """Cache-bust redirect"""
    resp = send_from_directory("dashboard", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/api/markets")
def api_markets():
    return jsonify(cache["markets"]["data"])


@app.route("/api/reddit")
def api_reddit():
    return jsonify(cache["reddit"]["data"])


@app.route("/api/news")
def api_news():
    return jsonify(cache["news"]["data"])


@app.route("/api/fred")
def api_fred():
    return jsonify(cache["fred"]["data"])


@app.route("/api/predictions")
def api_predictions():
    return jsonify(cache["predictions"]["data"])


@app.route("/api/events")
def api_events():
    return jsonify(cache["events"]["data"])


@app.route("/api/price-history")
def api_price_history():
    result = {}
    for slug, info in TRACKED_MARKETS.items():
        result[slug] = {
            "label": info["label"],
            "oracle": info["oracle"],
            "direction": info["direction"],
            "history": price_history.get(slug, []),
        }
    return jsonify(result)


@app.route("/api/status")
def api_status():
    return jsonify({
        "status": "online",
        "last_scan": cache["status"]["last_scan"],
        "scans": cache["status"]["scans"],
        "errors": cache["status"]["errors"][-10:],
        "cache_ages": {
            k: round(time.time() - v["ts"], 1) if v.get("ts") else None
            for k, v in cache.items() if isinstance(v, dict) and "ts" in v
        },
        "counts": {
            k: len(v["data"]) for k, v in cache.items()
            if isinstance(v, dict) and "data" in v
        },
    })


@app.route("/api/rss")
def api_rss():
    """RSS feed of ORACLE predictions — for IFTTT/dlvr.it auto-posting"""
    preds = load_predictions()
    active = [p for p in preds if p.get("market")]
    active.sort(key=lambda x: x.get("number", 0), reverse=True)

    items = ""
    for p in active:
        num = p.get("number", 0)
        market = p.get("market", "Unknown")
        url = p.get("url", "")
        date = p.get("date", "2026-03-15")
        tweet = p.get("tweet", "")

        # Build tweet text if not available
        if not tweet:
            our_yes = "?"
            if p.get("our_odds"):
                import re as _re
                m = _re.search(r'(\d+)%', p["our_odds"][0])
                if m:
                    our_yes = m.group(1)
            cp = p.get("current_price", "")
            cpm_match = __import__('re').search(r'(\d+)', cp)
            mkt = cpm_match.group(1) if cpm_match else "?"
            tweet = f"ORACLE #{num:03d}: {market}\nMarket: {mkt}% | ORACLE: {our_yes}%\n{url}"

        items += f"""
    <item>
      <title>ORACLE #{num:03d}: {market}</title>
      <link>{url}</link>
      <description><![CDATA[{tweet}]]></description>
      <pubDate>{date}</pubDate>
      <guid>oracle-{num:03d}</guid>
    </item>"""

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>ORACLE — Narrative Arbitrage Engine</title>
    <link>http://localhost:3000</link>
    <description>AI-powered prediction market analysis. Simulates narratives to find Polymarket mispricing.</description>
    <language>en-us</language>
    {items}
  </channel>
</rss>"""

    return Response(rss, mimetype="application/rss+xml",
                    headers={"Cache-Control": "no-cache"})


@app.route("/api/strategy100")
def api_strategy100():
    """$100 strategy portfolio with live prices"""
    portfolio_path = DATA_DIR / "strategy_100_portfolio.json"

    if not portfolio_path.exists():
        return jsonify({
            "account": {"starting_balance": 100, "total_cash": 100},
            "allocations": {
                "bonds": {"budget": 60, "cash": 60, "invested": 0},
                "expertise": {"budget": 30, "cash": 30, "invested": 0},
                "flash_crash": {"budget": 10, "cash": 10, "invested": 0},
            },
            "trades": [],
            "positions": [],
            "closed_trades": [],
            "stats": {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0},
        })

    portfolio = json.loads(portfolio_path.read_text())
    open_trades = [t for t in portfolio.get("trades", []) if t.get("status") == "open"]
    closed_trades = [t for t in portfolio.get("trades", []) if t.get("status") == "closed"]

    positions = []
    total_invested = 0
    positions_value = 0

    for trade in open_trades:
        pos = dict(trade)
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={"slug": trade["slug"]}, timeout=10)
            r.raise_for_status()
            markets = r.json()
            if markets:
                prices = json.loads(markets[0].get("outcomePrices", "[]"))
                yes_price = float(prices[0]) if prices else None
                no_price = float(prices[1]) if len(prices) > 1 else None
                current_price = yes_price if trade["side"] == "yes" else no_price
                if current_price is not None:
                    current_value = round(trade["shares"] * current_price, 2)
                    pnl = round(current_value - trade["invested"], 2)
                    pnl_pct = round(((current_value / trade["invested"]) - 1) * 100, 2) if trade["invested"] > 0 else 0
                    pos["current_price"] = current_price
                    pos["current_value"] = current_value
                    pos["pnl"] = pnl
                    pos["pnl_pct"] = pnl_pct
                    total_invested += trade["invested"]
                    positions_value += current_value
        except Exception:
            pass
        positions.append(pos)

    total_cash = portfolio.get("account", {}).get("total_cash", 100)
    total_value = round(total_cash + positions_value, 2)
    total_return = round(total_value - portfolio.get("account", {}).get("starting_balance", 100), 2)
    starting = portfolio.get("account", {}).get("starting_balance", 100)

    return jsonify({
        "account": {
            **portfolio.get("account", {}),
            "positions_value": round(positions_value, 2),
            "total_value": total_value,
            "total_return": total_return,
            "total_return_pct": round((total_return / starting) * 100, 2) if starting else 0,
        },
        "allocations": portfolio.get("allocations", {}),
        "positions": positions,
        "closed_trades": closed_trades,
        "stats": portfolio.get("stats", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({"status": "scan_started"})


@app.route("/api/stream")
def api_stream():
    """SSE stream for live updates — auto-closes after 2 minutes to prevent thread exhaustion"""
    def generate():
        last_ts = 0
        start = time.time()
        while time.time() - start < 120:  # Close after 2 min, client will reconnect
            ts = cache["markets"].get("ts", 0)
            if ts > last_ts:
                last_ts = ts
                data = json.dumps({
                    "type": "update",
                    "ts": ts,
                    "counts": {
                        k: len(v["data"]) for k, v in cache.items()
                        if isinstance(v, dict) and "data" in v
                    }
                })
                yield f"data: {data}\n\n"
            time.sleep(10)
        yield "data: {\"type\":\"reconnect\"}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║  ORACLE — Narrative Arbitrage Dashboard      ║")
    print("  ║  http://localhost:3000                       ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()

    # Initial data load
    print("  Loading data sources...")
    refresh_cache()
    m = len(cache["markets"]["data"])
    r = len(cache["reddit"]["data"])
    n = len(cache["news"]["data"])
    f = len(cache["fred"]["data"])
    print(f"  Markets: {m} | Reddit: {r} | News: {n} | FRED: {f}")
    print(f"  Errors: {len(cache['status']['errors'])}")
    print()

    # Start background scanner
    scanner = threading.Thread(target=background_scanner, daemon=True)
    scanner.start()

    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
