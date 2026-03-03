"""garmin_client.py — Garmin Connect authentication and data fetching.

Authentication strategy:
- On first run, logs in with email/password and saves OAuth tokens to data/.garth_session/
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

from .paths import user_data_dir

# Directory where garth saves OAuth session tokens
SESSION_DIR = user_data_dir() / ".garth_session"

# How many days of history to fetch
DAYS_BACK = 7

# Training status integer → label (last-resort fallback only).
# Garmin's integer encoding is undocumented; we've seen it be inconsistent
# across firmware versions (e.g., 7 mapped to "Productive", not "Unproductive").
# The preferred source is trainingStatusFeedbackPhrase — see fetch_health_data().
TRAINING_STATUS_LABELS = {
    0: "Unknown",
    1: "Not Active",
    2: "Detraining",
    3: "Recovery",
    4: "Maintaining",
    5: "Peaking",
    6: "Productive",
    7: "Unproductive",
    8: "Strained",
    9: "Overreaching",
}

# Training status string key → display label.
# Used when the API returns trainingStatusFeedbackPhrase (e.g. "PRODUCTIVE_3").
TRAINING_STATUS_STR_LABELS = {
    "UNKNOWN":       "Unknown",
    "NOT_ACTIVE":    "Not Active",
    "DETRAINING":    "Detraining",
    "RECOVERY":      "Recovery",
    "MAINTAINING":   "Maintaining",
    "PEAKING":       "Peaking",
    "PRODUCTIVE":    "Productive",
    "UNPRODUCTIVE":  "Unproductive",
    "STRAINED":      "Strained",
    "OVERREACHING":  "Overreaching",
}

# Training readiness level → short display label
READINESS_LEVEL_LABELS = {
    "LOW": "Low",
    "MODERATE": "Moderate",
    "HIGH": "High",
    "VERY_HIGH": "Very High",
}


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
            return client
        except Exception:
            pass  # Session expired or invalid — fall through to fresh login

    # Fresh login; prompt_mfa is only called if Garmin requires 2FA
    client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    client.login()

    # Persist tokens so the next run skips re-authentication
    SESSION_DIR.mkdir(exist_ok=True)
    client.garth.dump(str(SESSION_DIR))

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
# Trend pre-computation (uses 90-day cached archive)
# ---------------------------------------------------------------------------

def format_trend_summary(health_data: dict) -> str:
    """
    Compute weekly and monthly trend averages from up to 90 days of cached data.

    Metrics summarised: HRV, training readiness, sleep hours/score, steps/day,
    body weight. Also flags notable directional changes vs prior 30-day period.

    Returns an empty string if fewer than 14 days of data are available so the
    section is cleanly omitted on first install (cold start graceful fallback).
    """
    today = date.today()

    # Build per-date lookup dicts from all available data
    hrv_map: dict[str, float] = {}
    for e in health_data.get("hrv", []):
        if e.get("last_night_avg") and not e.get("error"):
            hrv_map[e["date"]] = float(e["last_night_avg"])

    readiness_map: dict[str, float] = {}
    for e in health_data.get("training_readiness", []):
        if e.get("score") is not None and not e.get("error"):
            readiness_map[e["date"]] = float(e["score"])

    sleep_map: dict[str, dict] = {}
    for e in health_data.get("sleep", []):
        if e.get("total_seconds") and not e.get("error"):
            sleep_map[e["date"]] = e

    steps_map: dict[str, int] = {}
    for e in health_data.get("daily_stats", []):
        if e.get("steps") and not e.get("error"):
            steps_map[e["date"]] = int(e["steps"])

    weight_map: dict[str, float] = {}
    for e in health_data.get("body_composition", []):
        if e.get("weight_kg") and not e.get("error"):
            weight_map[e["date"]] = float(e["weight_kg"])

    all_dates = set(hrv_map) | set(readiness_map) | set(sleep_map) | set(steps_map)
    if len(all_dates) < 14:
        return ""

    def _week_dates(week_num: int) -> list[str]:
        end_offset = (week_num - 1) * 7
        return [(today - timedelta(days=end_offset + i)).isoformat() for i in range(7)]

    def _month_dates(month_num: int) -> list[str]:
        end_offset = (month_num - 1) * 30
        return [(today - timedelta(days=end_offset + i)).isoformat() for i in range(30)]

    def _avg(data_map: dict, dates: list[str]) -> Optional[float]:
        vals = [data_map[d] for d in dates if d in data_map]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _sleep_hours_avg(dates: list[str]) -> Optional[float]:
        vals = [sleep_map[d]["total_seconds"] / 3600 for d in dates if d in sleep_map]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _sleep_score_avg(dates: list[str]) -> Optional[float]:
        vals = [sleep_map[d]["score"] for d in dates if d in sleep_map and sleep_map[d].get("score")]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _fmt(val: Optional[float], unit: str = "") -> str:
        return f"{val}{unit}" if val is not None else "N/A"

    # Weekly averages (4 most-recent complete weeks)
    weeks = [_week_dates(i) for i in range(1, 5)]
    week_labels = ["This week", "Last week", "2 wks ago", "3 wks ago"]

    hrv_weekly       = [_avg(hrv_map, w) for w in weeks]
    readiness_weekly = [_avg(readiness_map, w) for w in weeks]
    sleep_h_weekly   = [_sleep_hours_avg(w) for w in weeks]
    sleep_s_weekly   = [_sleep_score_avg(w) for w in weeks]
    steps_weekly     = [_avg(steps_map, w) for w in weeks]

    # Monthly averages (3 × 30-day buckets)
    months = [_month_dates(i) for i in range(1, 4)]
    month_labels = ["Last 30 days", "30-60 days ago", "60-90 days ago"]

    hrv_monthly       = [_avg(hrv_map, m) for m in months]
    readiness_monthly = [_avg(readiness_map, m) for m in months]
    sleep_h_monthly   = [_sleep_hours_avg(m) for m in months]
    weight_monthly    = [_avg(weight_map, m) for m in months]

    # Notable trend detection (compare most-recent 30 days vs prior 30 days)
    trend_notes = []

    def _trend_note(label: str, cur: Optional[float], prev: Optional[float],
                    threshold_pct: float = 5.0, higher_is_better: bool = True) -> None:
        if cur is None or prev is None or prev == 0:
            return
        change_pct = ((cur - prev) / abs(prev)) * 100
        if abs(change_pct) < threshold_pct:
            return
        direction = "up" if change_pct > 0 else "down"
        good      = (change_pct > 0) == higher_is_better
        flag      = "improving" if good else "declining"
        trend_notes.append(
            f"  {label}: {flag} ({direction} {abs(change_pct):.0f}% vs prior month)"
        )

    _trend_note("HRV",         hrv_monthly[0],       hrv_monthly[1] if len(hrv_monthly) > 1 else None)
    _trend_note("Readiness",   readiness_monthly[0],  readiness_monthly[1] if len(readiness_monthly) > 1 else None)
    _trend_note("Sleep hours", sleep_h_monthly[0],    sleep_h_monthly[1] if len(sleep_h_monthly) > 1 else None)
    _trend_note("Body weight", weight_monthly[0],      weight_monthly[1] if len(weight_monthly) > 1 else None,
                threshold_pct=1.5, higher_is_better=False)

    # Build formatted output
    lines = ["=== HEALTH TRENDS (computed from up to 90 days of cached data) ===", ""]

    lines.append("WEEKLY AVERAGES (most recent first):")
    col_w = 12
    header = f"  {'Metric':<22}" + "".join(f"{lab:>{col_w}}" for lab in week_labels)
    lines.append(header)
    lines.append("  " + "-" * (22 + col_w * len(week_labels)))

    def _row(label: str, values: list, fmt_fn=None) -> str:
        row = f"  {label:<22}"
        for v in values:
            if v is None:
                cell = "N/A"
            elif fmt_fn:
                cell = fmt_fn(v)
            else:
                cell = str(v)
            row += f"{cell:>{col_w}}"
        return row

    if any(v is not None for v in hrv_weekly):
        lines.append(_row("HRV (ms)", hrv_weekly))
    if any(v is not None for v in readiness_weekly):
        lines.append(_row("Readiness (/100)", readiness_weekly))
    if any(v is not None for v in sleep_h_weekly):
        lines.append(_row("Sleep (hrs)", sleep_h_weekly))
    if any(v is not None for v in sleep_s_weekly):
        lines.append(_row("Sleep score", sleep_s_weekly))
    if any(v is not None for v in steps_weekly):
        lines.append(_row("Steps/day", [int(v) if v is not None else None for v in steps_weekly]))

    lines.append("")
    lines.append("MONTHLY AVERAGES:")
    col_m = 16
    header_m = f"  {'Metric':<22}" + "".join(f"{lab:>{col_m}}" for lab in month_labels)
    lines.append(header_m)
    lines.append("  " + "-" * (22 + col_m * len(month_labels)))

    def _row_m(label: str, values: list) -> str:
        row = f"  {label:<22}"
        for v in values:
            cell = str(v) if v is not None else "N/A"
            row += f"{cell:>{col_m}}"
        return row

    if any(v is not None for v in hrv_monthly):
        lines.append(_row_m("HRV (ms)", hrv_monthly))
    if any(v is not None for v in readiness_monthly):
        lines.append(_row_m("Readiness (/100)", readiness_monthly))
    if any(v is not None for v in sleep_h_monthly):
        lines.append(_row_m("Sleep (hrs)", sleep_h_monthly))
    if any(v is not None for v in weight_monthly):
        lines.append(_row_m("Weight (kg)", weight_monthly))

    if trend_notes:
        lines.append("")
        lines.append("NOTABLE TRENDS (vs prior 30-day period):")
        lines.extend(trend_notes)

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_health_data(
    client: Garmin,
    settings: dict | None = None,
    specific_dates: list | None = None,
    fetch_shared: bool = True,
) -> dict:
    """
    Fetch health metrics from Garmin Connect.

    Returns a structured dict with:
      - daily_stats: steps, calories, stress, body battery, resting HR per day
      - sleep: total/deep/REM/light sleep duration + sleep score per day
      - activities: recent activities (type, duration, distance, HR, calories)
      - hrv: overnight HRV average and status per day
      - training_readiness: daily readiness score (0-100) and level
      - training_status: single rolling label (Productive / Unproductive / etc.)
      - body_composition: weight, body fat %, muscle mass per day from scale

    Args:
        client:         Authenticated Garmin client.
        settings:       Controls which categories are fetched and how many days
                        of history to include. Each day's entry stores None for
                        any metric the device didn't record.
        specific_dates: When provided, only fetch per-day data for these dates
                        instead of the full days_back window. Used for incremental
                        cache updates where only a few days need refreshing.
        fetch_shared:   When False, skip activities, training_status, and
                        body_composition (shared/rolling data that doesn't
                        need updating if no per-day dates changed).
    """
    s = settings or {}
    days_back = int(s.get("days_back", DAYS_BACK))
    fetch_daily = s.get("daily_stats_enabled", True)
    fetch_sleep = s.get("sleep_enabled", True)
    fetch_activities = s.get("activities_enabled", True)
    activity_count = int(s.get("activity_count", 10))
    fetch_hrv = s.get("hrv_enabled", True)
    fetch_readiness = s.get("training_readiness_enabled", True)
    fetch_status = s.get("training_status_enabled", True)
    fetch_body = s.get("body_enabled", True)

    today = date.today()
    date_range = specific_dates if specific_dates is not None else [
        today - timedelta(days=i) for i in range(days_back)
    ]

    health_data: dict = {
        "fetch_date": today.isoformat(),
        "daily_stats": [],
        "sleep": [],
        "activities": [],
        "hrv": [],
        "training_readiness": [],
        "training_status": None,
        "body_composition": [],
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
                    # Newer API: nested under sleepScores.overall.value
                    # Older API: sleepScoreValue at top level of dto
                    "score": (
                        _get(dto, "sleepScores", "overall", "value")
                        or _get(dto, "sleepScoreValue")
                    ),
                }
            except Exception as e:
                sleep = {"date": date_str, "error": str(e)}

            health_data["sleep"].append(sleep)

        # --- HRV (overnight average + status) ---
        if fetch_hrv:
            try:
                raw = client.get_hrv_data(date_str)
                summary = _get(raw, "hrvSummary") or {}
                hrv = {
                    "date": date_str,
                    "last_night_avg": _get(summary, "lastNightAvg"),
                    "weekly_avg": _get(summary, "weeklyAvg"),
                    "status": _get(summary, "status"),  # BALANCED / LOW / UNBALANCED
                }
            except Exception as e:
                hrv = {"date": date_str, "error": str(e)}
            health_data["hrv"].append(hrv)

        # --- Training Readiness (daily score) ---
        if fetch_readiness:
            try:
                raw = client.get_training_readiness(date_str)
                reading = None
                if isinstance(raw, list):
                    # Prefer morning wakeup reading from primary device
                    for r in raw:
                        if r.get("primaryActivityTracker") and r.get("inputContext") == "AFTER_WAKEUP_RESET":
                            reading = r
                            break
                    # Fallback: any primary device reading
                    if reading is None:
                        for r in raw:
                            if r.get("primaryActivityTracker"):
                                reading = r
                                break
                if reading:
                    recovery_min = reading.get("recoveryTime")
                    readiness = {
                        "date": date_str,
                        "score": reading.get("score"),
                        "level": reading.get("level"),   # LOW / MODERATE / HIGH
                        "recovery_time_h": round(recovery_min / 60, 1) if recovery_min else None,
                    }
                else:
                    readiness = {"date": date_str, "score": None, "level": None, "recovery_time_h": None}
            except Exception as e:
                readiness = {"date": date_str, "error": str(e)}
            health_data["training_readiness"].append(readiness)

    # --- Training Status (fetch once — rolling label, not per-day) ---
    if fetch_status and fetch_shared:
        try:
            raw = client.get_training_status(today.isoformat())
            ts_map = _get(raw, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}

            status_val = None
            feedback_phrase = None
            for device_data in ts_map.values():
                status_val = _get(device_data, "trainingStatus")
                feedback_phrase = _get(device_data, "trainingStatusFeedbackPhrase")
                if status_val is not None:
                    break  # take the primary device

            # Prefer trainingStatusFeedbackPhrase — it's the human-readable source of truth.
            # e.g. "PRODUCTIVE_3" → status_key "PRODUCTIVE" → label "Productive"
            # The trailing _N is a sub-level indicator, not part of the category name.
            if feedback_phrase:
                parts = feedback_phrase.rsplit("_", 1)
                status_key = parts[0] if (len(parts) == 2 and parts[1].isdigit()) else feedback_phrase
                label = TRAINING_STATUS_STR_LABELS.get(
                    status_key.upper(),
                    status_key.replace("_", " ").title(),
                )
            elif isinstance(status_val, int):
                label = TRAINING_STATUS_LABELS.get(status_val, "Unknown")
            elif isinstance(status_val, str):
                label = TRAINING_STATUS_STR_LABELS.get(
                    status_val.upper(),
                    status_val.replace("_", " ").title(),
                )
            else:
                label = "Unknown"

            health_data["training_status"] = {
                "code": status_val,
                "label": label,
                "date": today.isoformat(),
            }
        except Exception as e:
            health_data["training_status"] = {"error": str(e)}

    # --- Recent activities (not day-by-day, just a flat list) ---
    if fetch_activities and fetch_shared:
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

    # --- Body composition (fetch entire range at once) ---
    if fetch_body and fetch_shared:
        try:
            start_str = (today - timedelta(days=days_back)).isoformat()
            raw = client.get_body_composition(start_str, today.isoformat())
            entries = raw.get("dateWeightList") or []
            health_data["body_composition"] = [
                {
                    "date": e.get("calendarDate"),
                    # Garmin stores weight in grams
                    "weight_kg": round(e["weight"] / 1000, 1) if e.get("weight") else None,
                    "bmi": round(e["bmi"], 1) if e.get("bmi") else None,
                    "body_fat_pct": e.get("bodyFat"),
                    "body_water_pct": e.get("bodyWater"),
                    "muscle_mass_kg": round(e["muscleMass"] / 1000, 1) if e.get("muscleMass") else None,
                    "bone_mass_kg": round(e["boneMass"] / 1000, 1) if e.get("boneMass") else None,
                }
                for e in entries
            ]
        except Exception as e:
            health_data["body_composition_error"] = str(e)

    return health_data


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_health_summary(
    health_data: dict,
    settings: dict | None = None,
    nutrition_data: dict | None = None,
    nutrition_log: dict | None = None,
    memory_notes: str = "",
    trend_summary: str = "",
) -> str:
    """
    Convert raw Garmin data into a clean, readable text block.

    This is injected into Claude's system prompt as context, so it needs to
    be dense enough to be useful but structured enough for Claude to parse.

    The `settings` dict controls which sections and individual metrics appear
    in the output. Disabled metrics are silently omitted.

    Optional memory_notes and trend_summary are injected at the top when provided,
    giving Claude accumulated coaching memory and pre-computed 90-day trend data.
    """
    s = settings or {}

    lines = []

    # ── Coach Memory (accumulated facts from past conversations) ──────────────
    if memory_notes and memory_notes.strip():
        lines.append(memory_notes.strip())
        lines.append("")

    # ── Health Trends (pre-computed from 90-day archive) ─────────────────────
    if trend_summary and trend_summary.strip():
        lines.append(trend_summary.strip())
        lines.append("")

    # ── Athlete Profile ───────────────────────────────────────────────────────
    profile = s.get("athlete_profile") or {}
    if any(profile.get(k) for k in ("name", "sports", "goal", "level", "training_days",
                                     "training_plan", "upcoming_events", "health_notes")):
        lines.append("=== ATHLETE PROFILE ===")
        if profile.get("name"):
            lines.append(f"Name: {profile['name']} (always address them by this name)")
        if profile.get("sports"):
            lines.append(f"Sport(s): {profile['sports']}")
        if profile.get("level"):
            lines.append(f"Level: {profile['level']}")
        if profile.get("goal"):
            lines.append(f"Current goal: {profile['goal']}")
        if profile.get("training_days"):
            day_part = f"{profile['training_days']} days/week"
            if profile.get("training_plan"):
                day_part += f" | Plan: {profile['training_plan']}"
            lines.append(f"Training: {day_part}")
        elif profile.get("training_plan"):
            lines.append(f"Plan: {profile['training_plan']}")
        if profile.get("upcoming_events"):
            lines.append(f"Upcoming events: {profile['upcoming_events']}")
        if profile.get("health_notes"):
            lines.append(f"Health notes: {profile['health_notes']}")
        lines.append("")

    lines += [
        f"=== GARMIN HEALTH SUMMARY (fetched {health_data['fetch_date']}) ===",
        "",
    ]

    # ── Training Status (single rolling label) ────────────────────────────────
    ts = health_data.get("training_status")
    if ts and not ts.get("error") and ts.get("label") and s.get("training_status_enabled", True):
        lines.append(f"TRAINING STATUS: {ts['label']} (as of {ts['date']})")
        lines.append("")

    # ── Daily stats ───────────────────────────────────────────────────────────
    if health_data.get("daily_stats") or health_data.get("hrv") or health_data.get("training_readiness"):
        lines.append("DAILY STATS (most recent first):")

        # Build lookup dicts for HRV and readiness keyed by date
        hrv_by_date = {e["date"]: e for e in health_data.get("hrv", [])}
        readiness_by_date = {e["date"]: e for e in health_data.get("training_readiness", [])}

        # Use daily_stats as the primary loop driver; fall back to HRV dates
        all_dates = []
        seen = set()
        for e in health_data.get("daily_stats", []):
            if e["date"] not in seen:
                all_dates.append(e["date"])
                seen.add(e["date"])
        # Ensure any HRV-only dates are included too
        for e in health_data.get("hrv", []):
            if e["date"] not in seen:
                all_dates.append(e["date"])
                seen.add(e["date"])

        daily_by_date = {e["date"]: e for e in health_data.get("daily_stats", [])}

        for date_str in all_dates:
            day = daily_by_date.get(date_str, {"date": date_str})
            if "error" in day:
                lines.append(f"  {date_str}: [data unavailable]")
                continue

            parts = [f"  {date_str}:"]
            if s.get("metric_steps", True) and day.get("steps") is not None:
                parts.append(f"{day['steps']:,} steps")
            if s.get("metric_calories_total", True) and day.get("calories_total") is not None:
                parts.append(f"{day['calories_total']:,} kcal")
            if s.get("metric_calories_active", True) and day.get("calories_active") is not None:
                parts.append(f"{day['calories_active']:,} active kcal")
            if s.get("metric_stress", True) and day.get("stress_avg") is not None:
                parts.append(f"stress {day['stress_avg']}/100")
            if s.get("metric_body_battery", True) and day.get("body_battery") is not None:
                parts.append(f"body battery {day['body_battery']}%")
            if s.get("metric_resting_hr", True) and day.get("resting_hr") is not None:
                parts.append(f"RHR {day['resting_hr']} bpm")
            if s.get("metric_distance", True) and day.get("distance_m") is not None:
                parts.append(f"{day['distance_m'] / 1000:.1f} km")

            # HRV for this day
            hrv = hrv_by_date.get(date_str, {})
            if s.get("hrv_enabled", True) and hrv.get("last_night_avg") is not None:
                status_str = f" ({hrv['status'].title()})" if hrv.get("status") else ""
                parts.append(f"HRV {hrv['last_night_avg']} ms{status_str}")

            # Training readiness for this day
            rdy = readiness_by_date.get(date_str, {})
            if s.get("training_readiness_enabled", True) and rdy.get("score") is not None:
                level_str = READINESS_LEVEL_LABELS.get(rdy.get("level", ""), rdy.get("level", ""))
                rec_str = f", {rdy['recovery_time_h']}h recovery" if rdy.get("recovery_time_h") else ""
                parts.append(f"readiness {rdy['score']}/100 ({level_str}{rec_str})")

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
        lines.append("")

    # ── Body Composition ──────────────────────────────────────────────────────
    if health_data.get("body_composition") and s.get("body_enabled", True):
        lines.append("BODY COMPOSITION (most recent first):")
        for entry in health_data["body_composition"]:
            parts = [f"  {entry['date']}:"]
            if s.get("metric_body_weight", True) and entry.get("weight_kg") is not None:
                parts.append(f"{entry['weight_kg']} kg")
            if s.get("metric_body_fat", True) and entry.get("body_fat_pct") is not None:
                parts.append(f"body fat {entry['body_fat_pct']}%")
            if s.get("metric_body_muscle", True) and entry.get("muscle_mass_kg") is not None:
                parts.append(f"muscle {entry['muscle_mass_kg']} kg")
            if entry.get("bmi") is not None:
                parts.append(f"BMI {entry['bmi']}")
            lines.append(" | ".join(parts))
        if "body_composition_error" in health_data:
            lines.append(f"  [Error: {health_data['body_composition_error']}]")
        lines.append("")

    # ── Nutrition ─────────────────────────────────────────────────────────────
    if nutrition_data or nutrition_log:
        days_back = int(s.get("days_back", DAYS_BACK))

        # Daily macro totals — compact summary
        if nutrition_data and s.get("nutrition_enabled", True):
            sorted_dates = sorted(nutrition_data.keys(), reverse=True)[:days_back]
            if sorted_dates:
                lines.append("NUTRITION — Daily Summary (most recent first):")
                for d in sorted_dates:
                    n = nutrition_data[d]
                    parts = [f"  {d}:"]
                    parts.append(f"{int(n['calories'])} kcal")
                    if n.get("expenditure"):
                        parts.append(f"TDEE {n['expenditure']} kcal")
                    parts.append(f"P {int(n['protein'])}g")
                    parts.append(f"C {int(n['carbs'])}g")
                    parts.append(f"F {int(n['fat'])}g")
                    if n.get("fiber"):
                        parts.append(f"fiber {n['fiber']}g")
                    if n.get("alcohol"):
                        parts.append(f"alcohol {n['alcohol']}g")
                    if n.get("weight"):
                        parts.append(f"weight {n['weight']} kg")
                    if n.get("target_calories"):
                        parts.append(
                            f"target {n['target_calories']} kcal"
                            f" / P {n.get('target_protein', '?')}g"
                            f" C {n.get('target_carbs', '?')}g"
                            f" F {n.get('target_fat', '?')}g"
                        )
                    lines.append(" | ".join(parts))
                lines.append("")

        # Full food item log — detailed per-meal breakdown for Claude
        if nutrition_log and s.get("nutrition_log_enabled", True):
            sorted_dates = sorted(nutrition_log.keys(), reverse=True)[:days_back]
            if sorted_dates:
                lines.append("NUTRITION — Full Food Log (most recent first):")
                for d in sorted_dates:
                    lines.append(f"  {d}:")
                    for item in nutrition_log[d]:
                        t = item.get("time") or "?"
                        name = item.get("name", "Unknown")
                        parts = [f"    {t}  {name}"]
                        parts.append(f"{int(item.get('calories', 0))} kcal")
                        parts.append(f"P {int(item.get('protein', 0))}g")
                        parts.append(f"C {int(item.get('carbs', 0))}g")
                        parts.append(f"F {int(item.get('fat', 0))}g")
                        if item.get("fiber"):
                            parts.append(f"fiber {item['fiber']}g")
                        lines.append(" | ".join(parts))
                lines.append("")

    return "\n".join(lines)
