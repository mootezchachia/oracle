"""ORACLE — Alerts & Notification System
Monitors tracked predictions, detects edge changes, and emits alerts.

Usage:
    python3 notifier.py check     # Check all tracked markets, print alerts
    python3 notifier.py history   # Show recent alerts
"""

import json
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# Allow standalone execution from nerve/ directory
sys.path.insert(0, str(Path(__file__).parent))
from config import (
    GAMMA_API, ALERTS_DIR, now_iso, append_jsonl, load_jsonl, print_banner,
)

# ─── Alert file ──────────────────────────────────────────────────
NOTIFICATIONS_FILE = ALERTS_DIR / "notifications.jsonl"

# ─── Thresholds ──────────────────────────────────────────────────
EDGE_WIDE_THRESHOLD = 10   # alert when edge > 10%
EDGE_NARROW_THRESHOLD = 3  # alert when edge < 3%

# ─── ANSI colors ─────────────────────────────────────────────────
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

ALERT_COLORS = {
    "edge_wide": C_GREEN,
    "edge_narrow": C_YELLOW,
    "target_hit": C_CYAN,
    "resolved": C_RED,
}

# ─── Tracked predictions ────────────────────────────────────────
TRACKED = {
    "us-x-iran-ceasefire-by-march-31": {
        "number": 1,
        "oracle_yes": 8,
        "direction": "NO",
        "entry": 85,
        "target_no": 90,
        "market_name": "Iran Ceasefire March 31",
    },
    "will-crude-oil-cl-hit-high-120-by-end-of-march-766-813-597": {
        "number": 2,
        "oracle_yes": 52,
        "direction": "YES",
        "entry": 45,
        "target_yes": 58,
        "market_name": "Oil $120 March 31",
    },
}

# ─── Polymarket price fetch ─────────────────────────────────────

def fetch_market_price(slug: str) -> dict | None:
    """Fetch current market data from Polymarket Gamma API by slug."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug, "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            return None
        m = markets[0]
        # outcomePrices is a JSON-encoded list like "[\"0.85\",\"0.15\"]"
        prices = json.loads(m.get("outcomePrices", "[\"0.5\",\"0.5\"]"))
        yes_price = round(float(prices[0]) * 100, 1)
        closed = m.get("closed", False)
        resolved = m.get("resolved", False)
        return {
            "yes_price": yes_price,
            "no_price": round(100 - yes_price, 1),
            "closed": closed,
            "resolved": resolved,
            "question": m.get("question", ""),
            "slug": slug,
        }
    except Exception as e:
        print(f"  {C_DIM}[warn] Could not fetch {slug}: {e}{C_RESET}")
        return None


# ─── Edge calculation ────────────────────────────────────────────

def compute_edge(pred: dict, market: dict) -> dict:
    """Compute the edge between ORACLE estimate and current market price."""
    oracle_yes = pred["oracle_yes"]
    current_yes = market["yes_price"]
    direction = pred["direction"]

    if direction == "YES":
        # We think YES is underpriced
        edge = oracle_yes - current_yes
        current_relevant = current_yes
        target = pred.get("target_yes")
    else:
        # We think NO is underpriced (YES is overpriced)
        oracle_no = 100 - oracle_yes
        current_no = market["no_price"]
        edge = oracle_no - current_no  # positive = NO still underpriced
        current_relevant = current_no
        target = pred.get("target_no")

    return {
        "edge": round(edge, 1),
        "current_yes": current_yes,
        "current_relevant": round(current_relevant, 1),
        "target": target,
        "direction": direction,
        "oracle_yes": oracle_yes,
    }


# ─── Alert emission ─────────────────────────────────────────────

def emit_alert(
    alert_type: str,
    prediction: int,
    market_name: str,
    message: str,
    current_price: float,
    oracle_price: float,
    edge: float,
    slug: str,
):
    """Write alert to JSONL file and print to stdout."""
    record = {
        "ts": now_iso(),
        "type": alert_type,
        "prediction": prediction,
        "market": market_name,
        "message": message,
        "current_price": current_price,
        "oracle_price": oracle_price,
        "edge": edge,
        "slug": slug,
    }
    append_jsonl(NOTIFICATIONS_FILE, record)

    color = ALERT_COLORS.get(alert_type, C_RESET)
    label = alert_type.upper().replace("_", " ")
    print(f"  {color}{C_BOLD}[{label}]{C_RESET} {C_BOLD}#{prediction:03d}{C_RESET} {market_name}")
    print(f"         {message}")
    print(f"         {C_DIM}Edge: {edge:+.1f}%  |  Market: {current_price}%  |  ORACLE: {oracle_price}%{C_RESET}")
    print()


# ─── Check logic ─────────────────────────────────────────────────

def check_market(slug: str, pred: dict) -> list:
    """Check a single tracked market and emit any alerts. Returns alert records."""
    alerts = []
    market = fetch_market_price(slug)
    if market is None:
        return alerts

    num = pred["number"]
    name = pred["market_name"]
    info = compute_edge(pred, market)
    edge = info["edge"]
    current_yes = info["current_yes"]
    oracle_yes = info["oracle_yes"]
    direction = info["direction"]
    target = info["target"]

    # Market resolved
    if market.get("resolved"):
        emit_alert(
            "resolved", num, name,
            "Market has resolved.",
            current_yes, oracle_yes, edge, slug,
        )
        alerts.append("resolved")
        return alerts

    # Edge wide — opportunity growing
    if abs(edge) >= EDGE_WIDE_THRESHOLD:
        emit_alert(
            "edge_wide", num, name,
            f"Edge widened to {edge:+.1f}% — opportunity growing.",
            current_yes, oracle_yes, edge, slug,
        )
        alerts.append("edge_wide")

    # Edge narrow — market catching up
    if 0 < abs(edge) < EDGE_NARROW_THRESHOLD:
        emit_alert(
            "edge_narrow", num, name,
            f"Edge narrowed to {edge:+.1f}% — market catching up, consider exit.",
            current_yes, oracle_yes, edge, slug,
        )
        alerts.append("edge_narrow")

    # Target hit — take profit signal
    if target is not None:
        if direction == "YES" and current_yes >= target:
            emit_alert(
                "target_hit", num, name,
                f"YES price hit target {target}% (now {current_yes}%) — take profit.",
                current_yes, oracle_yes, edge, slug,
            )
            alerts.append("target_hit")
        elif direction == "NO" and market["no_price"] >= target:
            emit_alert(
                "target_hit", num, name,
                f"NO price hit target {target}% (now {market['no_price']}%) — take profit.",
                current_yes, oracle_yes, edge, slug,
            )
            alerts.append("target_hit")

    # No alerts — print status line
    if not alerts:
        tracking = "on track" if edge > 0 else "against us"
        print(f"  {C_DIM}[OK]{C_RESET} #{num:03d} {name}")
        print(f"       Edge: {edge:+.1f}%  |  YES: {current_yes}%  |  ORACLE YES: {oracle_yes}%  ({tracking})")
        print()

    return alerts


def check_all():
    """Check all tracked markets."""
    print_banner("Alert Check")
    total_alerts = 0
    for slug, pred in TRACKED.items():
        alerts = check_market(slug, pred)
        total_alerts += len(alerts)
    print(f"  {'─' * 50}")
    print(f"  {len(TRACKED)} markets checked, {total_alerts} alert(s) emitted.")
    print()


# ─── History ─────────────────────────────────────────────────────

def show_history(limit: int = 20):
    """Show recent alerts from the notifications file."""
    print_banner("Alert History")
    alerts = load_jsonl(NOTIFICATIONS_FILE, limit=limit)
    if not alerts:
        print("  No alerts yet.")
        print()
        return

    for a in alerts:
        ts = a.get("ts", "")[:19].replace("T", " ")
        atype = a.get("type", "unknown").upper().replace("_", " ")
        color = ALERT_COLORS.get(a.get("type", ""), C_RESET)
        num = a.get("prediction", 0)
        name = a.get("market", "")
        msg = a.get("message", "")
        edge = a.get("edge", 0)
        print(f"  {C_DIM}{ts}{C_RESET}  {color}[{atype}]{C_RESET}  #{num:03d} {name}")
        print(f"       {msg}  (edge: {edge:+.1f}%)")
        print()


# ─── API function ────────────────────────────────────────────────

def get_alerts(limit: int = 20) -> list:
    """Return the last N alerts as a list of dicts."""
    return load_jsonl(NOTIFICATIONS_FILE, limit=limit)


# ─── Tweet generator ────────────────────────────────────────────

def generate_tweet(prediction_number: int) -> str | None:
    """Generate a ready-to-post tweet for a tracked prediction with live prices."""
    # Find the prediction
    slug = None
    pred = None
    for s, p in TRACKED.items():
        if p["number"] == prediction_number:
            slug = s
            pred = p
            break

    if pred is None:
        print(f"  Prediction #{prediction_number} not found in TRACKED.")
        return None

    market = fetch_market_price(slug)
    if market is None:
        print(f"  Could not fetch market data for {slug}.")
        return None

    info = compute_edge(pred, market)
    edge = info["edge"]
    current_yes = info["current_yes"]
    oracle_yes = info["oracle_yes"]
    direction = info["direction"]

    # Determine status
    if edge > 5:
        status = "Tracking correctly"
        icon = "check"
    elif edge > 0:
        status = "Converging"
        icon = "arrow"
    else:
        status = "Against us"
        icon = "warning"

    # Build the call line
    if direction == "NO":
        call_line = f"Called: {oracle_yes}% (BUY NO at {pred['entry']}c)"
        now_line = f"Now: {current_yes}%"
    else:
        call_line = f"Called: {oracle_yes}% (BUY YES at {pred['entry']}c)"
        now_line = f"Now: {current_yes}%"

    url = f"https://polymarket.com/event/{slug}"

    tweet = (
        f"ORACLE Update #{prediction_number:03d}: {pred['market_name']}\n"
        f"\n"
        f"{call_line}\n"
        f"{now_line} | Edge: {edge:+.1f}%\n"
        f"Status: {status}\n"
        f"\n"
        f"{url}"
    )

    return tweet


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 notifier.py [check|history|tweet <N>]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "check":
        check_all()
    elif cmd == "history":
        show_history()
    elif cmd == "tweet":
        num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        tweet = generate_tweet(num)
        if tweet:
            print()
            print(f"  {C_BOLD}Generated tweet:{C_RESET}")
            print(f"  {'─' * 50}")
            for line in tweet.split("\n"):
                print(f"  {line}")
            print(f"  {'─' * 50}")
            print(f"  {C_DIM}{len(tweet)} chars{C_RESET}")
            print()
    else:
        print("Usage: python3 notifier.py [check|history|tweet <N>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
