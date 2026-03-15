"""ORACLE — News RSS Aggregator
Pulls headlines from major news sources for event detection.
"""

import hashlib
import requests
from config import *

NEWS_LOG = DATA_DIR / "news_feed.jsonl"
SEEN_HASHES = set()


def parse_feed_manual(url: str, source_name: str) -> list:
    """Parse RSS feed using simple XML extraction (no feedparser dependency)."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": REDDIT_UA})
        resp.raise_for_status()
        text = resp.text
        items = []
        
        # Simple XML parsing for <item> or <entry> tags
        import re
        # Try RSS format (<item>)
        item_blocks = re.findall(r'<item>(.*?)</item>', text, re.DOTALL)
        if not item_blocks:
            # Try Atom format (<entry>)
            item_blocks = re.findall(r'<entry>(.*?)</entry>', text, re.DOTALL)
        
        for block in item_blocks[:10]:
            title_match = re.search(r'<title[^>]*>(.*?)</title>', block, re.DOTALL)
            desc_match = re.search(r'<description[^>]*>(.*?)</description>', block, re.DOTALL)
            if not desc_match:
                desc_match = re.search(r'<summary[^>]*>(.*?)</summary>', block, re.DOTALL)
            link_match = re.search(r'<link[^>]*>(.*?)</link>', block, re.DOTALL)
            if not link_match:
                link_match = re.search(r'<link[^>]*href="([^"]*)"', block)
            pub_match = re.search(r'<pubDate>(.*?)</pubDate>', block, re.DOTALL)
            if not pub_match:
                pub_match = re.search(r'<published>(.*?)</published>', block, re.DOTALL)
            
            title = title_match.group(1).strip() if title_match else ""
            # Strip CDATA and HTML tags
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title)
            title = re.sub(r'<[^>]+>', '', title).strip()
            
            desc = desc_match.group(1).strip()[:300] if desc_match else ""
            desc = re.sub(r'<!\[CDATA\[|\]\]>', '', desc)
            desc = re.sub(r'<[^>]+>', '', desc).strip()
            
            link = ""
            if link_match:
                link = link_match.group(1).strip()
                link = re.sub(r'<!\[CDATA\[|\]\]>', '', link)
            
            pub = pub_match.group(1).strip() if pub_match else ""
            
            if title:
                items.append({
                    "source": source_name,
                    "title": title,
                    "description": desc,
                    "url": link,
                    "published": pub,
                })
        
        return items
    except Exception as e:
        print(f"  ⚠ {source_name} failed: {e}")
        return []


def dedup_title(title: str) -> bool:
    """Check if we've seen a similar title. Returns True if duplicate."""
    h = hashlib.md5(title.lower().strip()[:60].encode()).hexdigest()
    if h in SEEN_HASHES:
        return True
    SEEN_HASHES.add(h)
    return False


def scan_news() -> list:
    """Fetch headlines from all RSS feeds."""
    all_articles = []
    
    for name, url in RSS_FEEDS.items():
        print(f"  Fetching {name}...")
        articles = parse_feed_manual(url, name)
        for a in articles:
            if not dedup_title(a["title"]):
                all_articles.append(a)
                append_jsonl(NEWS_LOG, {
                    "timestamp": now_iso(),
                    **a,
                })
    
    return all_articles


def print_headlines(articles: list, limit=20):
    """Pretty-print headlines."""
    print(f"\n  LATEST HEADLINES ({len(articles)} unique):\n")
    for i, a in enumerate(articles[:limit], 1):
        title = a["title"][:70] + ("..." if len(a["title"]) > 70 else "")
        print(f"  {i:>3}. [{a['source']:<15}] {title}")


def main():
    print_banner("News RSS Aggregator")
    articles = scan_news()
    print_headlines(articles)
    print(f"\n  Log: {NEWS_LOG}\n")
    return articles


if __name__ == "__main__":
    main()
