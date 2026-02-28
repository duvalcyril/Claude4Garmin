"""garmin_client.py — Garmin Connect authentication and data fetching.

Authentication strategy:
- On first run, logs in with email/password and saves OAuth tokens to .garth_session/
- On subsequent runs, loads saved tokens (avoids re-login and rate limits)
- If tokens expire, automatically falls back to credential login
- Supports 2FA via an interactive prompt callback passed to the Garmin constructor
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# Directory where garth saves OAuth session tokens
SESSION_DIR = Path(".garth_session")

# How many days of history to fetch
DAYS_BACK = 7


def _prompt_mfa() -> str:
    """Callback for Garmin 2FA — prompts the user interactively."""
    return input("Enter your Garmin 2FA code: ").strip()


def get_garmin_client(email: str, password: str) -> Garmin:
    """
    Return an authenticated Garmin client.

    Tries loading saved session tokens first to avoid unnecessary logins.
    Falls back to full credential auth if session is missing or expired,
    then saves fresh tokens for next run.
    """
    # Attempt to reuse a previously saved session
    if SESSION_DIR.exists():
        try:
            client = Garmin()
            client.login(str(SESSION_DIR))
            print("✓ Loaded Garmin session from cache.")
            return client
        except Exception:
            print("Cached session expired or invalid — re-authenticating...")

    # Fresh login; prompt_mfa is only called if Garmin requires 2FA
    client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    client.login()

    # Persist tokens so the next run skips this step
    SESSION_DIR.mkdir(exist_ok=True)
    client.garth.dump(str(SESSION_DIR))
    print("✓ Session tokens saved to cache.")

    return client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(data: dict, *keys, default=None):
    """
    Safely traverse a nested dict without raising KeyError or TypeError.
    Usage: _get(stats, "dailySleepDTO", "sleepTimeSeconds")
    """
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None:
            return default
    return current


def _seconds_to_hm(seconds: Optional[int]) -> str:
    """Convert a seconds value to a human-readable 'Xh Ym' string."""
    if seconds is None:
        return "N/A"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_health_data(client: Garmin, settings: dict | None = None) -> dict:
    """
    Fetch health metrics from Garmin Connect.

    Returns a structured dict with:
      - daily_stats: steps, calories, stress, body battery, resting HR per day
      - sleep: total/deep/REM/light sleep duration + sleep score per day
      - activities: recent activities (type, duration, distance, HR, calories)

    The `settings` dict controls which categories are fetched and how many days
    of history are included. Each day's entry stores None for any metric the
    device didn't record, rather than raising an exception.
    """
    s = settings or {}
    days_back = int(s.get("days_back", DAYS_BACK))
    fetch_daily = s.get("daily_stats_enabled", True)
    fetch_sleep = s.get("sleep_enabled", True)
    fetch_activities = s.get("activities_enabled", True)
    activity_count = int(s.get("activity_count", 10))

    today = date.today()
    date_range = [today - timedelta(days=i) for i in range(days_back)]

    health_data: dict = {
        "fetch_date": today.isoformat(),
        "daily_stats": [],
        "sleep": [],
        "activities": [],
    }

    for day in date_range:
        date_str = day.isoformat()

        # --- Daily stats (steps, calories, stress, body battery, resting HR) ---
        if fetch_daily:
            try:
                raw = client.get_stats(date_str)
                stats = {
                    "date": date_str,
                    "steps": _get(raw, "totalSteps"),
                    "calories_total": _get(raw, "totalKilocalories"),
                    "calories_active": _get(raw, "activeKilocalories"),
                    "stress_avg": _get(raw, "averageStressLevel"),
                    "stress_max": _get(raw, "maxStressLevel"),
                    "body_battery": _get(raw, "bodyBatteryMostRecentValue"),
                    "resting_hr": _get(raw, "restingHeartRate"),
                    "distance_m": _get(raw, "totalDistanceMeters"),
                }
            except Exception as e:
                stats = {"date": date_str, "error": str(e)}

            health_data["daily_stats"].append(stats)

        # --- Sleep data ---
        if fetch_sleep:
            try:
                raw = client.get_sleep_data(date_str)
                # Garmin nests sleep metrics inside dailySleepDTO
                dto = _get(raw, "dailySleepDTO") or {}
                sleep = {
                    "date": date_str,
                    "total_seconds": _get(dto, "sleepTimeSeconds"),
                    "deep_seconds": _get(dto, "deepSleepSeconds"),
                    "light_seconds": _get(dto, "lightSleepSeconds"),
                    "rem_seconds": _get(dto, "remSleepSeconds"),
                    "awake_seconds": _get(dto, "awakeSleepSeconds"),
                    "score": _get(dto, "sleepScoreValue"),
                }
            except Exception as e:
                sleep = {"date": date_str, "error": str(e)}

            health_data["sleep"].append(sleep)

    # --- Recent activities (not day-by-day, just a flat list) ---
    if fetch_activities:
        try:
            raw_acts = client.get_activities(0, activity_count)
            for act in raw_acts:
                health_data["activities"].append({
                    "name": _get(act, "activityName"),
                    # activityType is a nested dict; typeKey is the human label
                    "type": _get(act, "activityType", "typeKey"),
                    "date": (_get(act, "startTimeLocal") or "")[:10],
                    "duration_seconds": _get(act, "duration"),
                    "distance_meters": _get(act, "distance"),
                    "avg_hr": _get(act, "averageHR"),
                    "max_hr": _get(act, "maxHR"),
                    "calories": _get(act, "calories"),
                })
        except Exception as e:
            health_data["activities_error"] = str(e)

    return health_data


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_health_summary(health_data: dict, settings: dict | None = None) -> str:
    """
    Convert raw Garmin data into a clean, readable text block.

    This is injected into Claude's system prompt as context, so it needs to
    be dense enough to be useful but structured enough for Claude to parse.

    The `settings` dict controls which sections and individual metrics appear
    in the output. Disabled metrics are silently omitted.
    """
    s = settings or {}

    lines = [
        f"=== GARMIN HEALTH SUMMARY (fetched {health_data['fetch_date']}) ===",
        "",
    ]

    # ── Daily stats ───────────────────────────────────────────────────────────
    if health_data.get("daily_stats"):
        lines.append("DAILY STATS (most recent first):")
        for day in health_data["daily_stats"]:
            if "error" in day:
                lines.append(f"  {day['date']}: [data unavailable]")
                continue

            parts = [f"  {day['date']}:"]
            if s.get("metric_steps", True) and day.get("steps") is not None:
                parts.append(f"{day['steps']:,} steps")
            if s.get("metric_calories_total", True) and day.get("calories_total") is not None:
                parts.append(f"{day['calories_total']:,} kcal")
            if s.get("metric_calories_active", True) and day.get("calories_active") is not None:
                parts.append(f"{day['calories_active']:,} active kcal")
            if s.get("metric_stress", True) and day.get("stress_avg") is not None:
                parts.append(f"stress {day['stress_avg']}/100 avg")
            if s.get("metric_body_battery", True) and day.get("body_battery") is not None:
                parts.append(f"body battery {day['body_battery']}%")
            if s.get("metric_resting_hr", True) and day.get("resting_hr") is not None:
                parts.append(f"RHR {day['resting_hr']} bpm")
            if s.get("metric_distance", True) and day.get("distance_m") is not None:
                parts.append(f"{day['distance_m'] / 1000:.1f} km")
            lines.append(" | ".join(parts))
        lines.append("")

    # ── Sleep ─────────────────────────────────────────────────────────────────
    if health_data.get("sleep"):
        lines.append("SLEEP (most recent first):")
        for entry in health_data["sleep"]:
            if "error" in entry or not entry.get("total_seconds"):
                lines.append(f"  {entry['date']}: [no data]")
                continue

            parts = [f"  {entry['date']}:"]
            if s.get("metric_sleep_total", True):
                parts.append(_seconds_to_hm(entry["total_seconds"]) + " total")
            if s.get("metric_sleep_deep", True) and entry.get("deep_seconds"):
                parts.append("deep " + _seconds_to_hm(entry["deep_seconds"]))
            if s.get("metric_sleep_rem", True) and entry.get("rem_seconds"):
                parts.append("REM " + _seconds_to_hm(entry["rem_seconds"]))
            if s.get("metric_sleep_light", True) and entry.get("light_seconds"):
                parts.append("light " + _seconds_to_hm(entry["light_seconds"]))
            if s.get("metric_sleep_score", True) and entry.get("score") is not None:
                parts.append(f"score {entry['score']}/100")
            lines.append(" | ".join(parts))
        lines.append("")

    # ── Activities ────────────────────────────────────────────────────────────
    if health_data.get("activities") is not None:
        lines.append("RECENT ACTIVITIES:")
        if not health_data["activities"]:
            lines.append("  No activities found.")
        else:
            for i, act in enumerate(health_data["activities"], 1):
                label = act.get("name") or act.get("type") or "Activity"
                parts = [f"  {i}. {label}"]
                if act.get("date"):
                    parts.append(act["date"])
                if act.get("duration_seconds"):
                    dur_m = int(act["duration_seconds"] // 60)
                    dur_s = int(act["duration_seconds"] % 60)
                    parts.append(f"{dur_m}:{dur_s:02d}")
                if act.get("distance_meters"):
                    parts.append(f"{act['distance_meters'] / 1000:.1f} km")
                if act.get("avg_hr"):
                    parts.append(f"avg HR {int(act['avg_hr'])} bpm")
                if act.get("calories"):
                    parts.append(f"{int(act['calories'])} kcal")
                lines.append(" | ".join(parts))

        if "activities_error" in health_data:
            lines.append(
                f"  [Error fetching activities: {health_data['activities_error']}]"
            )

    return "\n".join(lines)
