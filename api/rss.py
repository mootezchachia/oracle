"""Vercel serverless function — serves ORACLE predictions as RSS feed"""
import json
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler


PREDICTIONS_DIR = Path(__file__).parent.parent / "predictions" / "active"


def load_predictions():
    preds = []
    if not PREDICTIONS_DIR.exists():
        return preds
    for f in sorted(PREDICTIONS_DIR.glob("ORACLE_*.md")):
        if "simulation" in f.name or "summary" in f.name:
            continue
        try:
            text = f.read_text()
            pred = {"file": f.name}
            num_match = re.search(r'ORACLE_(\d+)', f.name)
            if num_match:
                pred["number"] = int(num_match.group(1))

            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("Market:"):
                    pred["market"] = line.split(":", 1)[1].strip()
                elif line.startswith("URL:"):
                    url = line.split("URL:", 1)[1].strip()
                    if not url.startswith("http"):
                        url = "https:" + url
                    pred["url"] = url.replace("/event/", "/market/")
                elif line.startswith("Date:"):
                    pred["date"] = line.split(":", 1)[1].strip()
                elif "## Primary Call" in line and i + 1 < len(lines):
                    pred["call"] = lines[i + 1].strip().strip("*").strip()
                elif "## Our Probability Distribution" in line:
                    for j in range(i + 1, min(i + 4, len(lines))):
                        m = re.search(r'\*\*(\d+)%\*\*', lines[j])
                        if m:
                            pred["our_yes"] = int(m.group(1))
                            break
                elif "Current Polymarket Odds" in line:
                    for j in range(i + 1, min(i + 5, len(lines))):
                        if "yes" in lines[j].lower():
                            m = re.search(r'(\d+\.?\d*)(?:¢|%)', lines[j])
                            if m:
                                pred["market_yes"] = float(m.group(1))
                                break
                elif "## Tweet" in line:
                    tweet_lines = []
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith("#"):
                            break
                        if lines[j].strip():
                            tweet_lines.append(lines[j].strip())
                    pred["tweet"] = " ".join(tweet_lines[:6])

            if pred.get("market"):
                # Determine direction
                call = pred.get("call", "").lower()
                our = pred.get("our_yes", 50)
                mkt = pred.get("market_yes", 50)
                if "buy yes" in call:
                    direction = "BUY YES"
                elif "buy no" in call or "sell yes" in call or "no ceasefire" in call or our < mkt:
                    direction = "BUY NO"
                else:
                    direction = "HOLD"
                pred["direction"] = direction
                pred["edge"] = abs(our - mkt)
                preds.append(pred)
        except:
            pass
    return preds


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        preds = load_predictions()
        preds.sort(key=lambda x: x.get("number", 0), reverse=True)

        items = ""
        for p in preds:
            num = p.get("number", 0)
            market = p.get("market", "")
            url = p.get("url", "")
            date = p.get("date", "2026-03-15")
            our = p.get("our_yes", "?")
            mkt = p.get("market_yes", "?")
            direction = p.get("direction", "HOLD")
            edge = p.get("edge", 0)
            tweet = p.get("tweet", "")

            if not tweet:
                tweet = f"ORACLE #{num:03d}: {market}\n{direction} | Market: {mkt}% | ORACLE: {our}% | Edge: {edge}%\n{url}"

            items += f"""
    <item>
      <title>ORACLE #{num:03d}: {market}</title>
      <link>{url}</link>
      <description><![CDATA[{tweet}]]></description>
      <pubDate>{date}</pubDate>
      <guid isPermaLink="false">oracle-{num:03d}</guid>
    </item>"""

        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>ORACLE — Narrative Arbitrage Engine</title>
    <link>https://oracle-predictions.vercel.app</link>
    <description>AI-powered prediction market analysis by @TheSwarmCall</description>
    <language>en-us</language>{items}
  </channel>
</rss>"""

        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Cache-Control", "s-maxage=300, stale-while-revalidate")
        self.end_headers()
        self.wfile.write(rss.encode())
