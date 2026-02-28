"""settings_manager.py — User preferences for Garmin data sync.

Settings are stored as JSON in settings.json (project directory).
Non-sensitive values — not stored in the OS keychain.

Settings structure (all keys with defaults):
  days_back              int   — how many days of history to fetch
  daily_stats_enabled    bool  — include daily stats section
  sleep_enabled          bool  — include sleep section
  activities_enabled     bool  — include activities section
  activity_count         int   — how many recent activities to fetch

  metric_steps           bool  — show steps in daily stats
  metric_calories_total  bool  — show total calories
  metric_calories_active bool  — show active calories
  metric_stress          bool  — show stress level
  metric_body_battery    bool  — show body battery
  metric_resting_hr      bool  — show resting heart rate
  metric_distance        bool  — show distance

  metric_sleep_total     bool  — show total sleep duration
  metric_sleep_deep      bool  — show deep sleep
  metric_sleep_light     bool  — show light sleep
  metric_sleep_rem       bool  — show REM sleep
  metric_sleep_score     bool  — show sleep score
"""

import json
from pathlib import Path

SETTINGS_FILE = Path("settings.json")

DEFAULTS: dict = {
    "days_back": 7,
    # Category toggles
    "daily_stats_enabled": True,
    "sleep_enabled": True,
    "activities_enabled": True,
    "activity_count": 10,
    # Daily stats metric toggles
    "metric_steps": True,
    "metric_calories_total": True,
    "metric_calories_active": True,
    "metric_stress": True,
    "metric_body_battery": True,
    "metric_resting_hr": True,
    "metric_distance": True,
    # Sleep metric toggles
    "metric_sleep_total": True,
    "metric_sleep_deep": True,
    "metric_sleep_light": True,
    "metric_sleep_rem": True,
    "metric_sleep_score": True,
}


def load_settings() -> dict:
    """Load settings from JSON, filling in missing keys with defaults."""
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULTS, **saved}
        except Exception:
            pass
    return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    """Persist settings to settings.json."""
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
