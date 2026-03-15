"""ORACLE — FRED Economic Data Watcher
Monitors Federal Reserve Economic Data for new releases.
"""

import requests
from config import *

FRED_LOG = DATA_DIR / "fred_releases.jsonl"


def fetch_series(series_id: str, limit: int = 5) -> list:
    """Fetch latest observations for a FRED series."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("observations", [])
    except Exception as e:
        print(f"  ⚠ FRED {series_id} failed: {e}")
        return []


def scan_fred() -> dict:
    """Fetch all monitored FRED series. Returns dict of series_id -> data."""
    results = {}
    for series_id, name in FRED_SERIES.items():
        print(f"  Fetching {name} ({series_id})...")
        obs = fetch_series(series_id, limit=3)
        data = [{"date": o["date"], "value": o["value"]} for o in obs]
        results[series_id] = {"name": name, "data": data}
        append_jsonl(FRED_LOG, {
            "timestamp": now_iso(),
            "series_id": series_id,
            "name": name,
            "latest": data[0] if data else None,
        })
    return results


def print_fred(results: dict):
    """Pretty-print FRED data."""
    print(f"\n  ECONOMIC INDICATORS:\n")
    for sid, info in results.items():
        latest = info["data"][0] if info["data"] else {"date": "N/A", "value": "N/A"}
        print(f"  {info['name']:<30} ({sid}): {latest['value']} ({latest['date']})")


def main():
    print_banner("FRED Economic Watcher")
    results = scan_fred()
    print_fred(results)
    print(f"\n  Log: {FRED_LOG}\n")
    return results


if __name__ == "__main__":
    main()
