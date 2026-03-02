"""nutrition_parser.py — MacroFactor CSV parser and nutrition data persistence.

Two complementary datasets are extracted from the same CSV export:

  nutrition.json     — Daily macro totals {date: {calories, protein, carbs, fat, fiber}}
                       Used for the sidebar display and as a quick summary.

  nutrition_log.json — Full per-day food item lists {date: [{time, name, macros}, ...]}
                       Injected into Claude's context so it can reason about
                       specific foods, meal timing, and dietary patterns.
"""

import csv
import io
import json

from .paths import user_data_dir

NUTRITION_FILE     = user_data_dir() / "nutrition.json"
NUTRITION_LOG_FILE = user_data_dir() / "nutrition_log.json"

# CSV column names from MacroFactor export
_COL_DATE     = "Date"
_COL_TIME     = "Time"
_COL_NAME     = "Food Name"
_COL_CALORIES = "Calories (kcal)"
_COL_PROTEIN  = "Protein (g)"
_COL_CARBS    = "Carbs (g)"
_COL_FAT      = "Fat (g)"
_COL_FIBER    = "Fiber (g)"


# ---------------------------------------------------------------------------
# Parsing — called once per upload, produces both datasets from the same pass
# ---------------------------------------------------------------------------

def parse_csv(data: bytes) -> tuple[dict, dict]:
    """
    Parse MacroFactor CSV export bytes into:
      - daily macro totals  {date: {calories, protein, carbs, fat, fiber}}
      - full food item log  {date: [{time, name, calories, protein, carbs, fat, fiber?}]}

    Both dicts are keyed by ISO date string (YYYY-MM-DD).
    """
    totals: dict = {}
    log: dict    = {}

    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    for row in reader:
        d = (row.get(_COL_DATE) or "").strip()
        if not d:
            continue

        name = (row.get(_COL_NAME) or "").strip()

        def _f(col: str) -> float:
            v = (row.get(col) or "").strip()
            try:
                return float(v)
            except ValueError:
                return 0.0

        # ── Daily totals ──────────────────────────────────────────────────────
        day = totals.setdefault(d, {
            "calories": 0.0,
            "protein":  0.0,
            "carbs":    0.0,
            "fat":      0.0,
            "fiber":    0.0,
        })
        day["calories"] += _f(_COL_CALORIES)
        day["protein"]  += _f(_COL_PROTEIN)
        day["carbs"]    += _f(_COL_CARBS)
        day["fat"]      += _f(_COL_FAT)
        day["fiber"]    += _f(_COL_FIBER)

        # ── Per-item food log ─────────────────────────────────────────────────
        if name:
            item: dict = {
                "time":     (row.get(_COL_TIME) or "")[:5],  # HH:MM
                "name":     name,
                "calories": round(_f(_COL_CALORIES), 1),
                "protein":  round(_f(_COL_PROTEIN),  1),
                "carbs":    round(_f(_COL_CARBS),     1),
                "fat":      round(_f(_COL_FAT),       1),
            }
            fiber = _f(_COL_FIBER)
            if fiber:
                item["fiber"] = round(fiber, 1)
            log.setdefault(d, []).append(item)

    # Round daily totals after summing all rows
    for day in totals.values():
        for k in day:
            day[k] = round(day[k], 1)

    return totals, log


# ---------------------------------------------------------------------------
# Persistence — daily totals
# ---------------------------------------------------------------------------

def load_nutrition() -> dict:
    """Load daily macro totals from disk. Returns {} if missing or corrupt."""
    if not NUTRITION_FILE.exists():
        return {}
    try:
        return json.loads(NUTRITION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_nutrition(data: dict) -> None:
    """Persist daily macro totals to disk (sorted by date for readability)."""
    try:
        NUTRITION_FILE.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass


def merge_nutrition(existing: dict, incoming: dict) -> dict:
    """
    Merge incoming data into existing. Incoming wins on date conflict.

    No duplicates are possible: each date is a single aggregated dict, not a
    list. Uploading the same CSV twice overwrites each date with the same
    values. Uploading a newer export with updated entries for an existing date
    correctly replaces the old totals for that date only.
    """
    merged = dict(existing)
    merged.update(incoming)
    return merged


# ---------------------------------------------------------------------------
# Persistence — full food log
# ---------------------------------------------------------------------------

def load_nutrition_log() -> dict:
    """Load per-day food item log from disk. Returns {} if missing or corrupt."""
    if not NUTRITION_LOG_FILE.exists():
        return {}
    try:
        return json.loads(NUTRITION_LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_nutrition_log(data: dict) -> None:
    """Persist food item log to disk (sorted by date for readability)."""
    try:
        NUTRITION_LOG_FILE.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass


def merge_nutrition_log(existing: dict, incoming: dict) -> dict:
    """
    Merge incoming log into existing. Incoming wins on date conflict.

    Each date's list is replaced wholesale — no partial merging within a day.
    This ensures the log always reflects the most recent export for that date.
    """
    merged = dict(existing)
    merged.update(incoming)
    return merged
