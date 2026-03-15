"""ORACLE — Reddit Velocity Tracker
Monitors subreddits for accelerating posts that signal breaking events.
"""

import time
import requests
from config import *

VELOCITY_LOG = DATA_DIR / "reddit_velocity.jsonl"


def fetch_subreddit(sub: str, sort="hot", limit=10) -> list:
    """Fetch posts from a subreddit via public JSON endpoint."""
    url = f"https://www.reddit.com/r/{sub}/{sort}.json"
    params = {"limit": limit, "raw_json": 1}
    headers = {"User-Agent": REDDIT_UA}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        posts = resp.json()["data"]["children"]
        return [{
            "id": p["data"]["name"],
            "subreddit": sub,
            "title": p["data"]["title"],
            "score": p["data"]["score"],
            "upvote_ratio": p["data"].get("upvote_ratio", 0),
            "num_comments": p["data"]["num_comments"],
            "created_utc": p["data"]["created_utc"],
            "permalink": p["data"]["permalink"],
            "url": f"https://reddit.com{p['data']['permalink']}",
            "age_hours": (time.time() - p["data"]["created_utc"]) / 3600,
        } for p in posts if p["kind"] == "t3"]
    except Exception as e:
        print(f"  r/{sub} failed: {e}")
        return []


def fetch_top_comments(permalink: str, limit=5) -> list:
    """Fetch top comments from a Reddit post."""
    url = f"https://www.reddit.com{permalink}.json"
    headers = {"User-Agent": REDDIT_UA}
    try:
        resp = requests.get(url, headers=headers, params={"limit": limit, "sort": "top"}, timeout=10)
        resp.raise_for_status()
        comments = resp.json()[1]["data"]["children"]
        return [{
            "body": c["data"].get("body", "")[:400],
            "score": c["data"].get("score", 0),
        } for c in comments if c["kind"] == "t1"][:limit]
    except Exception:
        return []


def calculate_velocity(post: dict) -> float:
    """Calculate post velocity: score per hour, with comments weighted 2x.
    Also applies an acceleration bonus for very young, high-engagement posts."""
    age = max(post["age_hours"], 0.1)
    score_velocity = post["score"] / age
    # Comments weighted 2x as they indicate deeper engagement
    comment_velocity = (post["num_comments"] * 2) / age

    base_velocity = score_velocity + comment_velocity

    # Acceleration bonus: posts under 2 hours old with high engagement
    # are likely breaking events and get an extra boost
    acceleration = 1.0
    if age < 2.0 and post["score"] > 50:
        acceleration = 1.5
    elif age < 1.0 and post["score"] > 20:
        acceleration = 2.0

    # Upvote ratio bonus: controversial posts (ratio near 0.5) or
    # strongly upvoted posts (ratio > 0.95) get a boost
    ratio = post.get("upvote_ratio", 0.5)
    if ratio > 0.95:
        acceleration *= 1.2  # highly agreed upon = strong signal
    elif ratio < 0.55 and ratio > 0:
        acceleration *= 1.3  # controversial = ambiguous narrative

    return round(base_velocity * acceleration, 2)


def scan_reddit(subs: list = None, limit_per_sub: int = 10) -> list:
    """Scan all monitored subreddits and return posts ranked by velocity."""
    if subs is None:
        subs = ALL_SUBS

    all_posts = []
    for sub in subs:
        print(f"  Scanning r/{sub}...")
        posts = fetch_subreddit(sub, limit=limit_per_sub)
        for p in posts:
            p["velocity"] = calculate_velocity(p)
            all_posts.append(p)
            append_jsonl(VELOCITY_LOG, {
                "timestamp": now_iso(),
                "subreddit": p["subreddit"],
                "post_id": p["id"],
                "title": p["title"],
                "score": p["score"],
                "velocity": p["velocity"],
                "num_comments": p["num_comments"],
                "age_hours": round(p["age_hours"], 1),
            })
        time.sleep(1.5)  # respect rate limits

    all_posts.sort(key=lambda x: x["velocity"], reverse=True)
    return all_posts


def print_top_posts(posts: list, limit=15):
    """Pretty-print top accelerating posts."""
    top = posts[:limit]
    print(f"\n  TOP {limit} ACCELERATING POSTS:\n")
    print(f"  {'#':<4} {'Vel':>8} {'Score':>7} {'Comments':>9} {'Age':>6}  {'Sub':<20} Title")
    print(f"  {'---'*34}")
    for i, p in enumerate(top, 1):
        title = p["title"][:45] + ("..." if len(p["title"]) > 45 else "")
        print(f"  {i:<4} {p['velocity']:>8.1f} {p['score']:>7} {p['num_comments']:>9} "
              f"{p['age_hours']:>5.1f}h  r/{p['subreddit']:<18} {title}")


def main():
    print_banner("Reddit Velocity Tracker")
    posts = scan_reddit()
    print_top_posts(posts)
    print(f"\n  Log: {VELOCITY_LOG}\n")
    return posts


if __name__ == "__main__":
    main()
