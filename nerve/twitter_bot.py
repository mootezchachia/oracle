#!/usr/bin/env python3
"""ORACLE Twitter Bot — Social media manager for @TheSwarmCall"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

import tweepy

# ─── Auth ────────────────────────────────────────────────────────────────────
def get_client():
    return tweepy.Client(
        consumer_key=os.getenv("X_CONSUMER_KEY"),
        consumer_secret=os.getenv("X_CONSUMER_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    )

PREDICTIONS_DIR = ROOT / "predictions" / "active"
PUBLISH_LOG = ROOT / "alerts" / "published_tweets.jsonl"

# ─── Tweet Crafting ──────────────────────────────────────────────────────────

def craft_prediction_tweet(pred_file):
    """Craft a tweet from a prediction markdown file"""
    text = pred_file.read_text()
    lines = text.split("\n")

    # Extract fields
    info = {}
    for line in lines:
        if line.startswith("Market:"):
            info["market"] = line.split(":", 1)[1].strip()
        elif line.startswith("URL:"):
            url = line.split("URL:", 1)[1].strip()
            if not url.startswith("http"):
                url = "https:" + url
            info["url"] = url.replace("/event/", "/market/")
        elif line.startswith("Date:"):
            info["date"] = line.split(":", 1)[1].strip()

    # Extract prediction number
    import re
    num_match = re.search(r'ORACLE_(\d+)', pred_file.name)
    info["number"] = int(num_match.group(1)) if num_match else 0

    # Extract our odds
    for line in lines:
        if "Our Probability Distribution" in line:
            idx = lines.index(line)
            for j in range(idx + 1, min(idx + 4, len(lines))):
                m = re.search(r'\*\*(\d+)%\*\*', lines[j])
                if m:
                    info["our_yes"] = int(m.group(1))
                    break

    # Extract current price from "## Current Polymarket Odds" section
    for i, line in enumerate(lines):
        if "Current Polymarket Odds" in line:
            for j in range(i + 1, min(i + 5, len(lines))):
                if "yes" in lines[j].lower() or lines[j].strip().startswith("- Yes"):
                    m = re.search(r'(\d+\.?\d*)(?:¢|%)', lines[j])
                    if m:
                        info["market_yes"] = float(m.group(1))
                        break
            break

    # Extract primary call
    for i, line in enumerate(lines):
        if "## Primary Call" in line and i + 1 < len(lines):
            info["call"] = lines[i + 1].strip().strip("*").strip()
            break

    # Extract confidence
    call = info.get("call", "")
    conf_m = re.search(r'(\d+)/100', call)
    info["confidence"] = int(conf_m.group(1)) if conf_m else 0

    # Determine direction
    cl = call.lower()
    is_buy_yes = "buy yes" in cl
    is_buy_no = "buy no" in cl or "no ceasefire" in cl or "sell yes" in cl or "regime survives" in cl or "no —" in cl
    # Also check if our_yes < market_yes → we think YES is overpriced → BUY NO
    if not is_buy_yes and not is_buy_no:
        our = info.get("our_yes", 50)
        mkt = info.get("market_yes", 50)
        if our < mkt:
            is_buy_no = True
        elif our > mkt:
            is_buy_yes = True
    info["direction"] = "BUY YES" if is_buy_yes else "BUY NO" if is_buy_no else "HOLD"

    # Calculate edge
    our = info.get("our_yes", 50)
    mkt = info.get("market_yes", 50)
    info["edge"] = abs(our - mkt)

    return info


def format_single_tweet(info):
    """Format a single prediction tweet (280 char limit)"""
    n = info.get("number", 0)
    market = info.get("market", "Unknown")
    direction = info.get("direction", "HOLD")
    our = info.get("our_yes", "?")
    mkt = info.get("market_yes", "?")
    edge = info.get("edge", 0)
    conf = info.get("confidence", 0)
    url = info.get("url", "")

    # Shorten market name if needed
    if len(market) > 45:
        market = market[:42] + "..."

    tweet = (
        f"ORACLE #{n:03d}: {market}\n"
        f"\n"
        f"{direction} | Conf: {conf}%\n"
        f"Market: {mkt}% | ORACLE: {our}%\n"
        f"Edge: {edge}%\n"
        f"\n"
        f"{url}"
    )

    # Trim if over 280
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet


def format_intro_tweet():
    """The first tweet in the launch thread"""
    return (
        "Introducing ORACLE — a narrative arbitrage engine for prediction markets.\n"
        "\n"
        "We simulate 20 public personas + 10 trader archetypes to find where "
        "Polymarket is mispriced.\n"
        "\n"
        "7 active predictions. Every call timestamped. No hiding.\n"
        "\n"
        "Thread below"
    )


def format_portfolio_tweet():
    """Portfolio summary tweet"""
    return (
        "ORACLE Portfolio — March 15, 2026\n"
        "\n"
        "#001 Iran CF Mar31: BUY NO (7% edge)\n"
        "#002 Oil $120: BUY YES (9.5% edge)\n"
        "#003 Iran CF May31: BUY NO (13.5% edge)\n"
        "#004 Oil $140: BUY NO (6.5% edge)\n"
        "#005 Iran Regime: BUY NO (20.5% edge)\n"
        "#006 Iran CF Jun30: SELL YES (11.5% edge)\n"
        "#007 Russia Druzhkivka: BUY NO (11.5% edge)"
    )


def format_thesis_tweet():
    """Thesis tweet"""
    return (
        "ORACLE thesis: markets systematically overprice dramatic outcomes.\n"
        "\n"
        "Regime fall, fast ceasefires, extreme oil spikes — narrative excitement "
        "beats historical base rates every time.\n"
        "\n"
        "We fade the crowd. We bet on the boring outcome.\n"
        "\n"
        "Track record starts now."
    )


def format_closing_tweet():
    """CTA tweet"""
    return (
        "Every prediction is timestamped and public.\n"
        "\n"
        "Follow @TheSwarmCall for daily updates:\n"
        "- Live edge alerts when markets move\n"
        "- New predictions as events break\n"
        "- Calibration scores after resolution\n"
        "\n"
        "No paid signals. No Discord alpha. Just transparent, "
        "AI-powered prediction market analysis."
    )


# ─── Publishing ──────────────────────────────────────────────────────────────

def post_tweet(client, text, reply_to=None):
    """Post a tweet, optionally as a reply"""
    kwargs = {"text": text}
    if reply_to:
        kwargs["in_reply_to_tweet_id"] = reply_to
    resp = client.create_tweet(**kwargs)
    tweet_id = resp.data["id"]
    print(f"  Posted: {tweet_id}")
    print(f"  https://x.com/TheSwarmCall/status/{tweet_id}")

    # Log
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tweet_id": tweet_id,
        "text": text[:100],
        "reply_to": reply_to,
    }
    with open(PUBLISH_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return tweet_id


def post_thread(client, tweets):
    """Post a thread (list of tweet texts)"""
    prev_id = None
    for i, tweet in enumerate(tweets):
        print(f"\n  [{i+1}/{len(tweets)}] Posting...")
        prev_id = post_tweet(client, tweet, reply_to=prev_id)
        if i < len(tweets) - 1:
            time.sleep(2)  # Rate limit buffer
    return prev_id


def launch_thread():
    """Post the full ORACLE launch thread"""
    client = get_client()

    # Build thread
    tweets = [format_intro_tweet(), format_portfolio_tweet()]

    # Add individual prediction tweets for top 3 by edge
    pred_files = sorted(PREDICTIONS_DIR.glob("ORACLE_0*.md"))
    pred_files = [f for f in pred_files if "simulation" not in f.name and "summary" not in f.name]

    preds = []
    for f in pred_files:
        try:
            info = craft_prediction_tweet(f)
            if info.get("market"):
                preds.append(info)
        except Exception as e:
            print(f"  Skip {f.name}: {e}")

    # Sort by edge, take top 3 for the thread
    preds.sort(key=lambda x: x.get("edge", 0), reverse=True)
    for info in preds[:3]:
        tweets.append(format_single_tweet(info))

    tweets.append(format_thesis_tweet())
    tweets.append(format_closing_tweet())

    print(f"\n  ORACLE Launch Thread — {len(tweets)} tweets")
    print("  " + "=" * 50)
    for i, t in enumerate(tweets):
        print(f"\n  [{i+1}] ({len(t)} chars)")
        for line in t.split("\n"):
            print(f"    {line}")
    print("\n  " + "=" * 50)

    confirm = input("\n  Post this thread to @TheSwarmCall? (y/n): ").strip().lower()
    if confirm == "y":
        post_thread(client, tweets)
        print("\n  Thread posted!")
    else:
        print("\n  Cancelled.")


def post_single(pred_number):
    """Post a single prediction tweet"""
    client = get_client()
    pred_files = sorted(PREDICTIONS_DIR.glob(f"ORACLE_{pred_number:03d}*.md"))
    pred_files = [f for f in pred_files if "simulation" not in f.name and "summary" not in f.name]

    if not pred_files:
        print(f"  No prediction #{pred_number} found")
        return

    info = craft_prediction_tweet(pred_files[0])
    tweet = format_single_tweet(info)
    print(f"\n  Tweet ({len(tweet)} chars):")
    print(f"  {tweet}")
    post_tweet(client, tweet)


def post_update():
    """Post a portfolio update with current edges"""
    client = get_client()
    tweet = format_portfolio_tweet()
    print(f"\n  Update ({len(tweet)} chars):")
    print(f"  {tweet}")
    post_tweet(client, tweet)


def preview_thread():
    """Preview the launch thread without posting"""
    tweets = [format_intro_tweet(), format_portfolio_tweet()]

    pred_files = sorted(PREDICTIONS_DIR.glob("ORACLE_0*.md"))
    pred_files = [f for f in pred_files if "simulation" not in f.name and "summary" not in f.name]

    preds = []
    for f in pred_files:
        try:
            info = craft_prediction_tweet(f)
            if info.get("market"):
                preds.append(info)
        except:
            pass

    preds.sort(key=lambda x: x.get("edge", 0), reverse=True)
    for info in preds[:3]:
        tweets.append(format_single_tweet(info))

    tweets.append(format_thesis_tweet())
    tweets.append(format_closing_tweet())

    print(f"\n  ORACLE Launch Thread Preview — {len(tweets)} tweets")
    print("  " + "=" * 50)
    for i, t in enumerate(tweets):
        print(f"\n  [{i+1}] ({len(t)} chars)")
        for line in t.split("\n"):
            print(f"    {line}")
    print("\n  " + "=" * 50)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"

    if cmd == "launch":
        launch_thread()
    elif cmd == "preview":
        preview_thread()
    elif cmd == "post":
        num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        post_single(num)
    elif cmd == "update":
        post_update()
    elif cmd == "test":
        client = get_client()
        me = client.get_me()
        print(f"  Connected: @{me.data.username}")
    else:
        print("Usage:")
        print("  python3 twitter_bot.py preview   — Preview launch thread")
        print("  python3 twitter_bot.py launch    — Post launch thread")
        print("  python3 twitter_bot.py post 3    — Post prediction #3")
        print("  python3 twitter_bot.py update    — Post portfolio update")
        print("  python3 twitter_bot.py test      — Test connection")
