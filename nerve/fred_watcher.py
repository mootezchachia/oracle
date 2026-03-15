"""ORACLE — FRED Economic Data Watcher
Monitors Federal Reserve Economic Data for new releases.
Falls back to cached data when the API is unavailable.
"""

import requests
from config import *

FRED_LOG = DATA_DIR / "fred_releases.jsonl"


def _load_cached_fred() -> dict:
    """Load previously cached FRED data from the JSONL log file."""
    cached = {}
    entries = load_jsonl(FRED_LOG, limit=200)
    for entry in reversed(entries):
        sid = entry.get("series_id")
        if sid and sid not in cached:
            cached[sid] = entry
    return cached


def fetch_series(series_id: str, limit: int = 5) -> list:
    """Fetch latest observations for a FRED series."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(limit),
        "observation_start": "2020-01-01",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 400:
            print(f"  FRED {series_id}: 400 Bad Request (invalid API key or params), using cache")
            return []
        if resp.status_code == 429:
            print(f"  FRED {series_id}: rate limited, using cache")
            return []
        resp.raise_for_status()
        data = resp.json()
        return data.get("observations", [])
    except requests.exceptions.Timeout:
        print(f"  FRED {series_id}: request timed out, using cache")
        return []
    except requests.exceptions.ConnectionError:
        print(f"  FRED {series_id}: connection error, using cache")
        return []
    except Exception as e:
        print(f"  FRED {series_id} failed: {e}")
        return []


def scan_fred() -> dict:
    """Fetch all monitored FRED series. Returns dict of series_id -> data.
    Falls back to cached data from fred_releases.jsonl when the API fails."""
    cached = _load_cached_fred()
    results = {}

    for series_id, name in FRED_SERIES.items():
        print(f"  Fetching {name} ({series_id})...")
        obs = fetch_series(series_id, limit=3)
        data = [{"date": o["date"], "value": o["value"]} for o in obs if o.get("value") != "."]

        if data:
            results[series_id] = {"name": name, "data": data}
            append_jsonl(FRED_LOG, {
                "timestamp": now_iso(),
                "series_id": series_id,
                "name": name,
                "latest": data[0],
            })
        elif series_id in cached:
            # Fallback to cached data
            entry = cached[series_id]
            latest = entry.get("latest")
            if latest:
                results[series_id] = {"name": name, "data": [latest]}
                print(f"    Using cached value: {latest.get('value')} ({latest.get('date')})")
            else:
                results[series_id] = {"name": name, "data": []}
        else:
            results[series_id] = {"name": name, "data": []}

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
