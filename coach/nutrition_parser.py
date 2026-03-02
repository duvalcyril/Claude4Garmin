"""nutrition_parser.py — MacroFactor CSV parser and nutrition data persistence.

Supports the daily-summary export format (one row per day):

  nutrition.json — Daily nutrition {date: {calories, protein, carbs, fat, fiber,
                    expenditure?, trend_weight?, weight?, steps?,
                    target_calories?, target_protein?, target_fat?, target_carbs?}}
                   Used for the sidebar display and Claude's macro context.

  nutrition_log.json — Always empty ({}) for this format; retained so the rest
                       of the codebase doesn't need to change.

Date input format: M/D/YYYY (e.g. 2/24/2026) → stored as YYYY-MM-DD.
"""

import csv
import io
import json
from datetime import datetime

from .paths import user_data_dir

NUTRITION_FILE     = user_data_dir() / "nutrition.json"
NUTRITION_LOG_FILE = user_data_dir() / "nutrition_log.json"

# CSV column names from MacroFactor daily-summary export
_COL_DATE             = "Date"
_COL_CALORIES         = "Calories (kcal)"
_COL_PROTEIN          = "Protein (g)"
_COL_FAT              = "Fat (g)"
_COL_CARBS            = "Carbs (g)"
_COL_FIBER            = "Fiber (g)"
_COL_EXPENDITURE      = "Expenditure"
_COL_TREND_WEIGHT     = "Trend Weight (kg)"
_COL_WEIGHT           = "Weight (kg)"
_COL_STEPS            = "Steps"
_COL_TARGET_CALORIES  = "Target Calories (kcal)"
_COL_TARGET_PROTEIN   = "Target Protein (g)"
_COL_TARGET_FAT       = "Target Fat (g)"
_COL_TARGET_CARBS     = "Target Carbs (g)"
_COL_ALCOHOL          = "Alcohol (g)"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """Convert M/D/YYYY → YYYY-MM-DD. Returns None on failure."""
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_csv(data: bytes) -> tuple[dict, dict]:
    """
    Parse MacroFactor daily-summary CSV export bytes into:
      - daily nutrition dict  {date: {calories, protein, carbs, fat, fiber, ...}}
      - food item log         {} (always empty — this format has no per-item rows)

    Both dicts are keyed by ISO date string (YYYY-MM-DD).
    """
    totals: dict = {}

    text   = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    for row in reader:
        raw_date = row.get(_COL_DATE) or ""
        d = _parse_date(raw_date)
        if not d:
            continue

        def _f(col: str) -> float | None:
            v = (row.get(col) or "").strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        def _fi(col: str) -> int | None:
            v = _f(col)
            return int(v) if v is not None else None

        day: dict = {
            "calories": round(_f(_COL_CALORIES) or 0, 1),
            "protein":  round(_f(_COL_PROTEIN)  or 0, 1),
            "carbs":    round(_f(_COL_CARBS)     or 0, 1),
            "fat":      round(_f(_COL_FAT)       or 0, 1),
            "fiber":    round(_f(_COL_FIBER)     or 0, 1),
        }

        # Optional enrichment columns — only stored when present
        expenditure   = _fi(_COL_EXPENDITURE)
        trend_weight  = _f(_COL_TREND_WEIGHT)
        weight        = _f(_COL_WEIGHT)
        steps         = _fi(_COL_STEPS)
        tgt_cal       = _fi(_COL_TARGET_CALORIES)
        tgt_pro       = _fi(_COL_TARGET_PROTEIN)
        tgt_fat       = _fi(_COL_TARGET_FAT)
        tgt_carbs     = _fi(_COL_TARGET_CARBS)
        alcohol       = _f(_COL_ALCOHOL)

        if expenditure  is not None: day["expenditure"]      = expenditure
        if trend_weight is not None: day["trend_weight"]     = round(trend_weight, 2)
        if weight       is not None: day["weight"]           = round(weight, 1)
        if steps        is not None: day["steps"]            = steps
        if tgt_cal      is not None: day["target_calories"]  = tgt_cal
        if tgt_pro      is not None: day["target_protein"]   = tgt_pro
        if tgt_fat      is not None: day["target_fat"]       = tgt_fat
        if tgt_carbs    is not None: day["target_carbs"]     = tgt_carbs
        if alcohol and alcohol > 0:  day["alcohol"]          = round(alcohol, 1)

        totals[d] = day

    return totals, {}   # empty log — this format has no per-item rows


# ---------------------------------------------------------------------------
# Persistence — daily totals
# ---------------------------------------------------------------------------

def load_nutrition() -> dict:
    """Load daily nutrition from disk. Returns {} if missing or corrupt."""
    if not NUTRITION_FILE.exists():
        return {}
    try:
        return json.loads(NUTRITION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_nutrition(data: dict) -> None:
    """Persist daily nutrition to disk (sorted by date for readability)."""
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

    Each date is a single aggregated dict, so uploading the same CSV twice
    produces identical results. A newer export for an existing date correctly
    replaces the old entry.
    """
    merged = dict(existing)
    merged.update(incoming)
    return merged


# ---------------------------------------------------------------------------
# Persistence — food item log (always empty for this format, kept for compat)
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
    """Merge incoming log into existing. Incoming wins on date conflict."""
    merged = dict(existing)
    merged.update(incoming)
    return merged
