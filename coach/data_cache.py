"""data_cache.py — Garmin health data persistence and incremental fetch planning.

Caches the processed health_data dict to data/garmin_data.json so each startup
only fetches what has actually changed:

  Per-day data (daily_stats, sleep, hrv, training_readiness):
    - Today      — always re-fetched (steps/battery/stress change through the day)
    - Yesterday  — re-fetched once per calendar day (sleep score syncs overnight)
    - Older days — served from cache (data is final, never changes)

  Shared data (activities, training_status, body_composition):
    - Re-fetched whenever at least one per-day date is being updated.
    - On a day where everything is already cached (rare edge case), skipped.

Digest and CLI bypass this module entirely — they always do a full fresh fetch.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .paths import user_data_dir

CACHE_FILE = user_data_dir() / "garmin_data.json"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_cache() -> dict | None:
    """
    Load cached Garmin data from disk.

    Returns the full cache envelope (with metadata), or None if the cache is
    missing, unreadable, or from an incompatible schema version.
    """
    if not CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if raw.get("schema_version") != SCHEMA_VERSION:
            return None     # Schema changed — trigger a full re-fetch
        return raw
    except Exception:
        return None


def save_cache(health_data: dict) -> None:
    """
    Persist health data to disk.

    Records today's date as yesterday_fetched_on so the next startup knows
    yesterday's data was already collected today and doesn't need re-fetching
    (unless it's a new calendar day).
    """
    cache = {
        "schema_version": SCHEMA_VERSION,
        "last_saved": datetime.now().isoformat(timespec="seconds"),
        "yesterday_fetched_on": date.today().isoformat(),
        "health_data": health_data,
    }
    try:
        CACHE_FILE.write_text(
            json.dumps(cache, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass    # Cache write failure is non-fatal; next run just re-fetches everything


# ---------------------------------------------------------------------------
# Fetch planning
# ---------------------------------------------------------------------------

def plan_fetch(cache: dict | None, days_back: int) -> tuple[list, bool]:
    """
    Decide what to fetch from Garmin given the current cache state.

    Returns:
        dates  — list of date objects requiring per-day API calls
        shared — True if activities / training_status / body_composition
                 should also be re-fetched
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    full_window = [today - timedelta(days=i) for i in range(days_back)]

    if cache is None:
        return full_window, True    # No cache → full fetch

    cached_health = cache.get("health_data", {})

    # Treat any date that has an entry (even an error entry) as "present".
    # Error entries mean "we tried; Garmin had nothing" — no point retrying the same day.
    cached_dates = {e["date"] for e in cached_health.get("daily_stats", [])}

    # Yesterday is re-fetched once per calendar day so overnight sleep data is captured.
    yesterday_fetched_today = cache.get("yesterday_fetched_on") == today.isoformat()

    dates = []
    for day in full_window:
        if day == today:
            dates.append(day)                           # Always re-fetch today
        elif day == yesterday and not yesterday_fetched_today:
            dates.append(day)                           # First fetch of the day for yesterday
        elif day.isoformat() not in cached_dates:
            dates.append(day)                           # Missing from cache
        # else: cached and older than yesterday → skip

    shared = bool(dates)    # Re-fetch shared data whenever any per-day date changes
    return dates, shared


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge(cached_health: dict, new_health: dict, days_back: int) -> dict:
    """
    Merge freshly fetched data into cached data and trim to the days_back window.

    Per-day lists: cached entries are updated with new ones (new wins on conflict).
    Shared data:   new values replace cached ones when present.
    """
    today = date.today()
    cutoff = (today - timedelta(days=days_back - 1)).isoformat()

    result = dict(cached_health)

    # Per-day lists — merge by date, trim to window, sort most-recent-first
    for key in ("daily_stats", "sleep", "hrv", "training_readiness"):
        by_date = {e["date"]: e for e in cached_health.get(key, [])}
        by_date.update({e["date"]: e for e in new_health.get(key, [])})
        result[key] = sorted(
            (e for e in by_date.values() if e.get("date", "") >= cutoff),
            key=lambda e: e["date"],
            reverse=True,
        )

    # Body composition — merge by date, trim to window
    bc_by_date = {e["date"]: e for e in cached_health.get("body_composition", [])}
    bc_by_date.update({e["date"]: e for e in new_health.get("body_composition", [])})
    result["body_composition"] = sorted(
        (e for e in bc_by_date.values() if e.get("date", "") >= cutoff),
        key=lambda e: e["date"],
        reverse=True,
    )

    # Shared data — replace with fresh values when available
    for key in ("activities", "training_status"):
        if new_health.get(key):
            result[key] = new_health[key]

    result["fetch_date"] = new_health.get("fetch_date") or cached_health.get("fetch_date")
    return result
