"""activity_cache.py — Per-activity enrichment fetching and caching.

Fetches detailed data (HR zones, splits, exercise sets, power zones) for each
activity exactly once by activity_id and stores it in data/activity_details.json.

The cache is append-only: once an activity_id is present it is never re-fetched.
This keeps startup fast and avoids Garmin API rate limits.

Enrichment data is NOT included in the coach's default system prompt. It is only
injected into a single conversation turn when the user explicitly requests it
(e.g. "analyze workout #1").
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .paths import user_data_dir

DETAIL_FILE = user_data_dir() / "activity_details.json"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def load_activity_details() -> dict:
    """Load activity detail cache from disk. Returns {} if missing or unreadable."""
    if not DETAIL_FILE.exists():
        return {}
    try:
        return json.loads(DETAIL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_activity_details(details: dict) -> None:
    """Persist activity detail cache. Non-fatal on failure."""
    try:
        DETAIL_FILE.write_text(
            json.dumps(details, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_missing_ids(activities: list[dict], details: dict) -> list[str]:
    """
    Return activity_ids present in the activities list that are not yet
    in the detail cache. Skips entries with empty/missing activity_id.
    """
    missing = []
    for act in activities:
        aid = act.get("activity_id", "")
        if aid and aid not in details:
            missing.append(aid)
    return missing


# ---------------------------------------------------------------------------
# Formatters — defensive against varied API response shapes
# ---------------------------------------------------------------------------

def format_hr_zones(zones_data) -> str:
    """
    Format HR zone breakdown from get_activity_hr_in_timezones() response.

    Garmin returns a list of zone objects or a dict with a zones list.
    Output: 'Z1: 8% (5m 12s) | Z2: 45% (28m 30s) | ...'
    """
    if not zones_data:
        return ""

    # Normalise to a list of zone entries
    zones = []
    if isinstance(zones_data, list):
        zones = zones_data
    elif isinstance(zones_data, dict):
        # Common keys: "heartRateZones", "zones", top-level list
        for key in ("heartRateZones", "zones", "hrZones"):
            if isinstance(zones_data.get(key), list):
                zones = zones_data[key]
                break
        if not zones and isinstance(next(iter(zones_data.values()), None), list):
            zones = next(iter(zones_data.values()))

    if not zones:
        return ""

    parts = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        # Zone number / name
        z_num = zone.get("zoneNumber") or zone.get("zone") or zone.get("zoneName", "?")
        # Time in zone (seconds)
        secs = zone.get("secsInZone") or zone.get("secondsInZone") or zone.get("timeInZone", 0)
        # Percentage
        pct = zone.get("zonePercentage") or zone.get("percentage")

        if secs is None and pct is None:
            continue

        label = f"Z{z_num}" if str(z_num).isdigit() else str(z_num)
        entry = label
        if pct is not None:
            entry += f": {round(float(pct))}%"
        if secs:
            m, s = divmod(int(secs), 60)
            entry += f" ({m}m {s:02d}s)"
        parts.append(entry)

    return " | ".join(parts) if parts else ""


def format_power_zones(zones_data) -> str:
    """
    Format power zone breakdown from get_activity_power_in_timezones() response.
    Same structure as HR zones but uses different keys.
    Output: 'Z1: 5% (3m 10s) | Z2: 38% (24m 15s) | ...'
    """
    if not zones_data:
        return ""

    zones = []
    if isinstance(zones_data, list):
        zones = zones_data
    elif isinstance(zones_data, dict):
        for key in ("powerZones", "zones", "powerTimeInZones"):
            if isinstance(zones_data.get(key), list):
                zones = zones_data[key]
                break

    if not zones:
        return ""

    parts = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        z_num = zone.get("zoneNumber") or zone.get("zone") or zone.get("zoneName", "?")
        secs  = zone.get("secsInZone") or zone.get("secondsInZone") or zone.get("timeInZone", 0)
        pct   = zone.get("zonePercentage") or zone.get("percentage")

        if secs is None and pct is None:
            continue

        label = f"Z{z_num}" if str(z_num).isdigit() else str(z_num)
        entry = label
        if pct is not None:
            entry += f": {round(float(pct))}%"
        if secs:
            m, s = divmod(int(secs), 60)
            entry += f" ({m}m {s:02d}s)"
        parts.append(entry)

    return " | ".join(parts) if parts else ""


def format_splits(splits_data) -> str:
    """
    Format lap splits from get_activity_splits() response.

    Output (one line per lap):
      Lap 1: 5.0 km | 25:30 | 5:06/km | avg HR 148 bpm
      Lap 2: 5.0 km | 26:15 | 5:15/km | avg HR 152 bpm
    """
    if not splits_data:
        return ""

    # Normalise to list of split objects
    laps = []
    if isinstance(splits_data, list):
        laps = splits_data
    elif isinstance(splits_data, dict):
        for key in ("splits", "lapDTOs", "laps", "items"):
            if isinstance(splits_data.get(key), list):
                laps = splits_data[key]
                break

    if not laps:
        return ""

    lines = []
    for i, lap in enumerate(laps, 1):
        if not isinstance(lap, dict):
            continue
        parts = [f"Lap {i}"]

        dist = lap.get("distance") or lap.get("distanceInMeters")
        if dist:
            parts.append(f"{dist / 1000:.2f} km")

        dur = lap.get("duration") or lap.get("elapsedDuration") or lap.get("movingDuration")
        if dur:
            m, s = divmod(int(dur), 60)
            parts.append(f"{m}:{s:02d}")

        # Pace (if available or derivable from distance+duration)
        pace_secs_km = lap.get("averagePaceInMinutesPerKilometer")
        if pace_secs_km:
            pm = int(pace_secs_km)
            ps = int((pace_secs_km - pm) * 60)
            parts.append(f"{pm}:{ps:02d}/km")
        elif dist and dur and dist > 0:
            pace = (dur / 60) / (dist / 1000)
            pm, ps_f = divmod(pace, 1)
            parts.append(f"{int(pm)}:{int(ps_f * 60):02d}/km")

        avg_hr = lap.get("averageHR") or lap.get("averageHeartRate")
        if avg_hr:
            parts.append(f"avg HR {int(avg_hr)} bpm")

        avg_power = lap.get("averagePower")
        if avg_power:
            parts.append(f"{int(avg_power)} W")

        lines.append(" | ".join(parts))

    # Cap at 20 laps to avoid flooding context
    if len(lines) > 20:
        lines = lines[:20] + [f"  ... ({len(laps) - 20} more laps)"]

    return "\n".join(lines) if lines else ""


def format_exercise_sets(sets_data) -> str:
    """
    Format strength training exercise sets from get_activity_exercise_sets() response.

    Output:
      Squat: 3 sets × 8 reps @ 80 kg
      Bench Press: 4 sets × 6 reps @ 75 kg
    """
    if not sets_data:
        return ""

    # Normalise to list of exercise entries
    exercises_raw = []
    if isinstance(sets_data, list):
        exercises_raw = sets_data
    elif isinstance(sets_data, dict):
        for key in ("exerciseSets", "sets", "exercises", "items"):
            if isinstance(sets_data.get(key), list):
                exercises_raw = sets_data[key]
                break

    if not exercises_raw:
        return ""

    # Group by exercise name (for cleaner output)
    from collections import defaultdict
    grouped: dict[str, list] = defaultdict(list)

    for item in exercises_raw:
        if not isinstance(item, dict):
            continue
        # Exercise name
        name = (
            item.get("exerciseName")
            or item.get("category")
            or item.get("exerciseType")
            or "Unknown exercise"
        )
        if isinstance(name, dict):
            name = name.get("exerciseName") or name.get("key") or str(name)
        grouped[str(name)].append(item)

    lines = []
    for exercise_name, sets in grouped.items():
        # Count sets, reps, weight
        set_count = len(sets)
        rep_counts = [s.get("repetitions") or s.get("reps") or s.get("repetitionCount") for s in sets]
        weights    = [s.get("weight") or s.get("weightInKilograms") for s in sets]

        rep_vals    = [r for r in rep_counts if r is not None]
        weight_vals = [w for w in weights if w is not None]

        parts = [f"  {exercise_name}: {set_count} set{'s' if set_count != 1 else ''}"]

        if rep_vals:
            avg_reps = round(sum(rep_vals) / len(rep_vals))
            parts.append(f"× {avg_reps} reps")

        if weight_vals:
            # Convert from grams to kg if values seem large
            avg_w = sum(weight_vals) / len(weight_vals)
            if avg_w > 500:   # likely grams
                avg_w = avg_w / 1000
            parts.append(f"@ {avg_w:.1f} kg")

        lines.append(" ".join(parts))

    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Combined formatter for context injection
# ---------------------------------------------------------------------------

def format_activity_detail_for_prompt(activity: dict, detail: dict, settings: dict) -> str:
    """
    Combine all available enrichments into a text block for injection into
    a single conversation turn. Returns empty string if nothing useful is cached.

    Only includes enrichments that are enabled in settings and have actual data.
    """
    if not detail:
        return ""

    act_type = (activity.get("type") or "").lower()
    is_strength = "strength" in act_type or "weight" in act_type or "gym" in act_type
    is_cycling  = "cycling" in act_type or "bike" in act_type or "zwift" in act_type
    is_running  = "running" in act_type or "run" in act_type or "trail" in act_type or "walk" in act_type

    lines = []

    # HR Zones
    if settings.get("activity_detail_hr_zones", True) and not detail.get("hr_zones_error"):
        hr_str = format_hr_zones(detail.get("hr_zones"))
        if hr_str:
            lines.append(f"HR Zones: {hr_str}")

    # Power Zones (cycling)
    if is_cycling and settings.get("activity_detail_power_zones", True) and not detail.get("power_zones_error"):
        pwr_str = format_power_zones(detail.get("power_zones"))
        if pwr_str:
            lines.append(f"Power Zones: {pwr_str}")

    # Exercise Sets (strength)
    if is_strength and settings.get("activity_detail_exercise_sets", True) and not detail.get("exercise_sets_error"):
        sets_str = format_exercise_sets(detail.get("exercise_sets"))
        if sets_str:
            lines.append("Exercise Sets:")
            lines.append(sets_str)

    # Lap Splits (non-strength activities)
    if not is_strength and settings.get("activity_detail_splits", True) and not detail.get("splits_error"):
        splits_str = format_splits(detail.get("splits"))
        if splits_str:
            lines.append("Lap Splits:")
            lines.append(splits_str)

    return "\n".join(lines) if lines else ""
