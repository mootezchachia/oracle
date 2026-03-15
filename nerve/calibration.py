"""ORACLE — Calibration Tracker

Tracks prediction accuracy, calculates Brier scores, and maintains a scoreboard
of all ORACLE predictions vs actual market outcomes.

Usage:
    python3 calibration.py register   # Scan predictions/active/ and register new predictions
    python3 calibration.py check      # Poll Polymarket for resolutions and score them
    python3 calibration.py report     # Print the scoreboard
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ─── Import shared config ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from config import (
    PREDICTIONS_DIR,
    CALIBRATION_DIR,
    GAMMA_API,
    print_banner,
    now_iso,
)

SCOREBOARD_PATH = CALIBRATION_DIR / "scoreboard.json"


# ─── Scoreboard I/O ──────────────────────────────────────────────

def _load_scoreboard() -> dict:
    """Load scoreboard from disk, or return empty scaffold."""
    if SCOREBOARD_PATH.exists():
        return json.loads(SCOREBOARD_PATH.read_text())
    return {
        "predictions": [],
        "summary": _empty_summary(),
    }


def _save_scoreboard(sb: dict):
    """Persist scoreboard to disk."""
    SCOREBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD_PATH.write_text(json.dumps(sb, indent=2) + "\n")


def _empty_summary() -> dict:
    return {
        "total": 0,
        "resolved": 0,
        "correct_direction": 0,
        "avg_brier_oracle": None,
        "avg_brier_market": None,
        "total_pnl_cents": 0,
        "win_rate": None,
    }


# ─── Prediction file parser ──────────────────────────────────────

def _extract_slug_from_url(url: str) -> str:
    """Extract Polymarket slug from a URL like
    https://polymarket.com/event/us-x-iran-ceasefire-by-march-31
    """
    # Take the last path segment
    return url.rstrip("/").split("/")[-1]


def _parse_prediction_file(filepath: Path) -> dict | None:
    """Parse an ORACLE prediction markdown file and extract key fields.

    Returns a dict with: number, market, slug, oracle_yes, market_yes,
    direction, entry_price, date_predicted.  Returns None on parse failure.
    """
    text = filepath.read_text()

    # Prediction number: # ORACLE PREDICTION #001
    m = re.search(r"ORACLE PREDICTION #(\d+)", text)
    if not m:
        return None
    number = int(m.group(1))

    # Date
    m = re.search(r"^Date:\s*(.+)", text, re.MULTILINE)
    date_predicted = m.group(1).strip() if m else None

    # Market name
    m = re.search(r"^Market:\s*(.+)", text, re.MULTILINE)
    market = m.group(1).strip() if m else filepath.stem

    # URL -> slug
    m = re.search(r"^URL:\s*(https?://\S+)", text, re.MULTILINE)
    if not m:
        return None
    slug = _extract_slug_from_url(m.group(1))

    # Current Polymarket odds — Yes price
    # Looks for lines like "- Yes (...): 15.0¢" or "- Yes (...): 45.0¢"
    m = re.search(r"-\s*Yes\s*[^:]*:\s*([\d.]+)", text)
    market_yes = int(float(m.group(1))) if m else None

    # Our probability — first bold percentage after "Our Probability"
    our_yes = None
    prob_section = re.search(
        r"Our Probability Distribution.*?\n(.*?)(?:\n##|\Z)", text, re.DOTALL
    )
    if prob_section:
        # Find lines with percentages, take the first (Yes probability)
        pcts = re.findall(r"\*\*(\d+)%\*\*", prob_section.group(1))
        if pcts:
            our_yes = int(pcts[0])

    # Direction: look for "BUY YES" or "BUY NO" in Primary Call section
    direction = None
    m = re.search(r"Primary Call.*?BUY\s+(YES|NO)", text, re.DOTALL | re.IGNORECASE)
    if m:
        direction = m.group(1).upper()
    else:
        # Fallback: look for trade recommendation
        m = re.search(r"Trade recommendation.*?BUY\s+(YES|NO)", text, re.IGNORECASE)
        if m:
            direction = m.group(1).upper()

    # Entry price: "buy No at 85¢" or "buy Yes at 45¢"
    entry_price = None
    m = re.search(
        r"(?:buy|BUY)\s+(?:Yes|No|YES|NO)\s+at\s+([\d.]+)", text
    )
    if m:
        entry_price = int(float(m.group(1)))

    if our_yes is None or market_yes is None or direction is None:
        print(f"  Warning: could not fully parse {filepath.name}")
        return None

    return {
        "number": number,
        "market": market,
        "slug": slug,
        "oracle_yes": our_yes,
        "market_yes": market_yes,
        "direction": direction,
        "entry_price": entry_price,
        "resolved": False,
        "outcome": None,
        "brier_oracle": None,
        "brier_market": None,
        "beat_market": None,
        "pnl_cents": None,
        "date_predicted": date_predicted,
        "date_resolved": None,
    }


# ─── Commands ─────────────────────────────────────────────────────

def cmd_register():
    """Scan predictions/active/ and register any new predictions."""
    print_banner("Calibration — Register Predictions")

    active_dir = PREDICTIONS_DIR / "active"
    if not active_dir.exists():
        print("  No predictions/active/ directory found.")
        return

    sb = _load_scoreboard()
    existing_numbers = {p["number"] for p in sb["predictions"]}

    files = sorted(active_dir.glob("ORACLE_*_*.md"))
    new_count = 0

    for f in files:
        # Skip non-prediction files (summaries, simulations, etc.)
        if "simulation" in f.name.lower() or "summary" in f.name.lower():
            continue

        parsed = _parse_prediction_file(f)
        if parsed is None:
            print(f"  Skipped (could not parse): {f.name}")
            continue

        if parsed["number"] in existing_numbers:
            print(f"  Already registered: ORACLE #{parsed['number']:03d}")
            continue

        sb["predictions"].append(parsed)
        existing_numbers.add(parsed["number"])
        new_count += 1
        print(
            f"  Registered: ORACLE #{parsed['number']:03d} — {parsed['market']}"
        )
        print(
            f"    Oracle YES: {parsed['oracle_yes']}% | "
            f"Market YES: {parsed['market_yes']}% | "
            f"Direction: BUY {parsed['direction']} @ {parsed['entry_price']}c"
        )

    # Rebuild summary
    _rebuild_summary(sb)
    _save_scoreboard(sb)

    if new_count == 0:
        print("  No new predictions to register.")
    else:
        print(f"\n  Registered {new_count} new prediction(s).")
    print(f"  Scoreboard saved to {SCOREBOARD_PATH}")


def cmd_check():
    """Poll Polymarket API for resolutions and score any resolved markets."""
    print_banner("Calibration — Check Resolutions")

    sb = _load_scoreboard()
    if not sb["predictions"]:
        print("  No predictions registered. Run: python3 calibration.py register")
        return

    unresolved = [p for p in sb["predictions"] if not p["resolved"]]
    if not unresolved:
        print("  All predictions already resolved.")
        return

    print(f"  Checking {len(unresolved)} unresolved prediction(s)...\n")

    newly_resolved = 0
    for pred in unresolved:
        slug = pred["slug"]
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"slug": slug},
                timeout=15,
            )
            resp.raise_for_status()
            markets = resp.json()
        except Exception as e:
            print(f"  Error fetching {slug}: {e}")
            continue

        if not markets:
            print(f"  #{pred['number']:03d} {slug} — not found on Polymarket")
            continue

        market = markets[0]
        resolved = market.get("resolved", False)

        if not resolved:
            print(f"  #{pred['number']:03d} {slug} — still open")
            continue

        # Market resolved!
        outcome_str = market.get("outcome", "").strip()
        # outcome is "Yes" or "No"
        actual = 1.0 if outcome_str.lower() == "yes" else 0.0

        oracle_prob = pred["oracle_yes"] / 100.0
        market_prob = pred["market_yes"] / 100.0

        brier_oracle = round((oracle_prob - actual) ** 2, 6)
        brier_market = round((market_prob - actual) ** 2, 6)
        beat_market = brier_oracle < brier_market

        # Direction correct?
        direction_correct = False
        if pred["direction"] == "YES" and actual == 1.0:
            direction_correct = True
        elif pred["direction"] == "NO" and actual == 0.0:
            direction_correct = True

        # P&L in cents
        # If BUY YES at entry_price cents: payout = 100 if Yes wins, 0 if No wins
        # If BUY NO at entry_price cents: payout = 100 if No wins, 0 if Yes wins
        entry = pred["entry_price"] or 0
        if pred["direction"] == "YES":
            pnl_cents = (100 - entry) if actual == 1.0 else -entry
        else:  # BUY NO
            pnl_cents = (100 - entry) if actual == 0.0 else -entry

        pred["resolved"] = True
        pred["outcome"] = outcome_str
        pred["brier_oracle"] = brier_oracle
        pred["brier_market"] = brier_market
        pred["beat_market"] = beat_market
        pred["pnl_cents"] = pnl_cents
        pred["date_resolved"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        newly_resolved += 1
        win_loss = "WIN" if direction_correct else "LOSS"
        print(
            f"  #{pred['number']:03d} RESOLVED: {outcome_str} — "
            f"{win_loss} | Brier: {brier_oracle:.4f} (us) vs {brier_market:.4f} (mkt) | "
            f"P&L: {pnl_cents:+d}c"
        )

    _rebuild_summary(sb)
    _save_scoreboard(sb)

    if newly_resolved:
        print(f"\n  {newly_resolved} prediction(s) newly resolved.")
    else:
        print("\n  No new resolutions.")
    print(f"  Scoreboard saved to {SCOREBOARD_PATH}")


def cmd_report():
    """Print the scoreboard to stdout."""
    print_banner("Calibration — Scoreboard")

    sb = _load_scoreboard()
    if not sb["predictions"]:
        print("  No predictions registered. Run: python3 calibration.py register")
        return

    summary = sb["summary"]

    # Header
    print(f"  Total predictions:   {summary['total']}")
    print(f"  Resolved:            {summary['resolved']}")

    if summary["resolved"] > 0:
        print(f"  Correct direction:   {summary['correct_direction']}")
        wr = summary.get("win_rate")
        print(f"  Win rate:            {wr:.1%}" if wr is not None else "  Win rate:            N/A")
        ab = summary.get("avg_brier_oracle")
        print(f"  Avg Brier (Oracle):  {ab:.4f}" if ab is not None else "  Avg Brier (Oracle):  N/A")
        am = summary.get("avg_brier_market")
        print(f"  Avg Brier (Market):  {am:.4f}" if am is not None else "  Avg Brier (Market):  N/A")
        print(f"  Total P&L:           {summary['total_pnl_cents']:+d}c")

    print()
    print("  " + "-" * 90)
    print(
        f"  {'#':<5} {'Market':<45} {'Dir':<5} {'Ours':>5} {'Mkt':>5} "
        f"{'Entry':>6} {'Status':<10} {'P&L':>6}"
    )
    print("  " + "-" * 90)

    for p in sb["predictions"]:
        status = p["outcome"] if p["resolved"] else "OPEN"
        pnl_str = f"{p['pnl_cents']:+d}c" if p["pnl_cents"] is not None else "  -"
        market_name = p["market"][:43]
        print(
            f"  {p['number']:<5} {market_name:<45} {p['direction']:<5} "
            f"{p['oracle_yes']:>4}% {p['market_yes']:>4}% "
            f"{p['entry_price'] or '-':>5}c {status:<10} {pnl_str:>6}"
        )

    if any(p["resolved"] for p in sb["predictions"]):
        print()
        print("  " + "-" * 90)
        print("  Resolved details:")
        for p in sb["predictions"]:
            if not p["resolved"]:
                continue
            beat = "BEAT MKT" if p["beat_market"] else "LOST TO MKT"
            print(
                f"    #{p['number']:03d}: Brier {p['brier_oracle']:.4f} vs market {p['brier_market']:.4f} "
                f"({beat}) | Outcome: {p['outcome']} | P&L: {p['pnl_cents']:+d}c"
            )

    print()


# ─── Summary rebuild ─────────────────────────────────────────────

def _rebuild_summary(sb: dict):
    """Recalculate summary stats from the prediction list."""
    preds = sb["predictions"]
    resolved = [p for p in preds if p["resolved"]]

    total = len(preds)
    n_resolved = len(resolved)

    correct = 0
    brier_sum_oracle = 0.0
    brier_sum_market = 0.0
    total_pnl = 0

    for p in resolved:
        actual = 1.0 if p["outcome"].lower() == "yes" else 0.0
        if (p["direction"] == "YES" and actual == 1.0) or \
           (p["direction"] == "NO" and actual == 0.0):
            correct += 1
        brier_sum_oracle += p["brier_oracle"]
        brier_sum_market += p["brier_market"]
        total_pnl += p["pnl_cents"] or 0

    sb["summary"] = {
        "total": total,
        "resolved": n_resolved,
        "correct_direction": correct if n_resolved else 0,
        "avg_brier_oracle": round(brier_sum_oracle / n_resolved, 6) if n_resolved else None,
        "avg_brier_market": round(brier_sum_market / n_resolved, 6) if n_resolved else None,
        "total_pnl_cents": total_pnl,
        "win_rate": round(correct / n_resolved, 4) if n_resolved else None,
    }


# ─── API-ready function ──────────────────────────────────────────

def get_scoreboard() -> dict:
    """Return the full scoreboard data, suitable for an API endpoint.

    Returns the same structure as scoreboard.json:
        {
            "predictions": [...],
            "summary": {...}
        }
    """
    return _load_scoreboard()


# ─── Main ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 calibration.py [register|check|report]")
        print()
        print("  register  - Scan predictions/active/ and register new predictions")
        print("  check     - Poll Polymarket for resolutions and score")
        print("  report    - Print the scoreboard")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "register":
        cmd_register()
    elif cmd == "check":
        cmd_check()
    elif cmd == "report":
        cmd_report()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
