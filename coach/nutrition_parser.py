"""nutrition_parser.py — MacroFactor CSV parser and nutrition data persistence.

Supports two MacroFactor export formats:
  .xlsx — "Quick Export" (daily summary), "Food Log", plus workout sheets
          (handed to mf_workouts → workouts.json)
  .csv  — daily summary only (older export)
Only the headline columns are ingested — calories, macros, fiber, alcohol,
energy expenditure, weight and targets. Micronutrients are ignored.

  nutrition.json — Daily nutrition {date: {calories?, protein?, carbs?, fat?, fiber?,
                    expenditure?, trend_weight?, weight?, steps?, alcohol?,
                    target_calories?, target_protein?, target_fat?, target_carbs?}}
                   Macro keys are absent on days with no food logged.

  nutrition_log.json — Always empty ({}) for this format; retained so the rest
                       of the codebase doesn't need to change.

  nutrition_import_state.json — Remembers which export file was last
                                auto-imported from the watched folder.

Date input format: M/D/YYYY (e.g. 2/24/2026) → stored as YYYY-MM-DD.
"""

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path

from .paths import user_data_dir

NUTRITION_FILE     = user_data_dir() / "nutrition.json"
NUTRITION_LOG_FILE = user_data_dir() / "nutrition_log.json"
IMPORT_STATE_FILE  = user_data_dir() / "nutrition_import_state.json"

# Filename patterns MacroFactor uses for exports, e.g. MacroFactor-20261118161300.xlsx
EXPORT_GLOBS = ("MacroFactor*.xlsx", "MacroFactor*.csv")

LBS_TO_KG = 0.45359237

# CSV column names from MacroFactor daily-summary export
_COL_DATE             = "Date"
_COL_CALORIES         = "Calories (kcal)"
_COL_PROTEIN          = "Protein (g)"
_COL_FAT              = "Fat (g)"
_COL_CARBS            = "Carbs (g)"
_COL_FIBER            = "Fiber (g)"
_COL_ALCOHOL          = "Alcohol (g)"
_COL_EXPENDITURE      = "Expenditure"
_COL_STEPS            = "Steps"
_COL_TARGET_CALORIES  = "Target Calories (kcal)"
_COL_TARGET_PROTEIN   = "Target Protein (g)"
_COL_TARGET_FAT       = "Target Fat (g)"
_COL_TARGET_CARBS     = "Target Carbs (g)"
# Weight columns come in kg or lbs depending on the app's unit setting
_COL_TREND_WEIGHT     = ("Trend Weight (kg)", "Trend Weight (lbs)")
_COL_WEIGHT           = ("Weight (kg)", "Weight (lbs)")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_date(raw) -> str | None:
    """Convert M/D/YYYY, ISO, or a datetime → YYYY-MM-DD. Returns None on failure."""
    if isinstance(raw, (datetime, date)):
        return raw.strftime("%Y-%m-%d")
    raw = str(raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _num(row: dict, col: str) -> float | None:
    v = row.get(col)
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _weight_kg(row: dict, cols: tuple[str, str]) -> float | None:
    kg_col, lbs_col = cols
    v = _num(row, kg_col)
    if v is not None:
        return v
    v = _num(row, lbs_col)
    return v * LBS_TO_KG if v is not None else None


def parse_csv(data: bytes) -> tuple[dict, dict]:
    """
    Parse MacroFactor daily-summary CSV export bytes into:
      - daily nutrition dict  {date: {...}} keyed by ISO date
      - food item log         {} (always empty — this format has no per-item rows)

    Raises ValueError if the file doesn't look like a MacroFactor daily export.
    """
    text   = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return _parse_daily_rows(reader.fieldnames or [], reader), {}


def _parse_daily_rows(headers, rows) -> dict:
    """Parse daily-summary rows (dicts keyed by column name) into {date: {...}}."""
    headers = set(headers)
    if _COL_DATE not in headers or _COL_CALORIES not in headers:
        raise ValueError(
            "Not a MacroFactor daily export (missing 'Date' or 'Calories (kcal)' column)."
        )

    totals: dict = {}
    for row in rows:
        d = _parse_date(row.get(_COL_DATE) or "")
        if not d:
            continue

        day: dict = {}

        # Macros — only when food was actually logged that day. A blank
        # calories cell means "nothing logged", not "ate zero".
        calories = _num(row, _COL_CALORIES)
        if calories is not None:
            day["calories"] = round(calories)
            day["protein"]  = round(_num(row, _COL_PROTEIN) or 0)
            day["carbs"]    = round(_num(row, _COL_CARBS) or 0)
            day["fat"]      = round(_num(row, _COL_FAT) or 0)
            fiber = _num(row, _COL_FIBER)
            if fiber is not None:
                day["fiber"] = round(fiber, 1)
            alcohol = _num(row, _COL_ALCOHOL)
            if alcohol:
                day["alcohol"] = round(alcohol, 1)

        ints = {
            "expenditure":     _COL_EXPENDITURE,
            "steps":           _COL_STEPS,
            "target_calories": _COL_TARGET_CALORIES,
            "target_protein":  _COL_TARGET_PROTEIN,
            "target_fat":      _COL_TARGET_FAT,
            "target_carbs":    _COL_TARGET_CARBS,
        }
        for key, col in ints.items():
            v = _num(row, col)
            if v is not None:
                day[key] = int(round(v))

        trend = _weight_kg(row, _COL_TREND_WEIGHT)
        if trend is not None:
            day["trend_weight"] = round(trend, 2)
        weight = _weight_kg(row, _COL_WEIGHT)
        if weight is not None:
            day["weight"] = round(weight, 2)

        if day:
            totals[d] = day

    return totals


# ---------------------------------------------------------------------------
# XLSX export (Quick Export + Food Log + workout sheets)
# ---------------------------------------------------------------------------

def _sheet_dicts(ws) -> tuple[list[str], list[dict]]:
    it = ws.iter_rows(values_only=True)
    header = next(it, None) or ()
    cols = [str(h).strip() if h is not None else "" for h in header]
    rows = []
    for r in it:
        if not r or all(c is None or c == "" for c in r):
            continue
        rows.append({c: r[i] if i < len(r) else None for i, c in enumerate(cols) if c})
    return cols, rows


def _parse_food_log(ws) -> dict:
    """Food Log sheet → {date: [{time, name, calories, protein, carbs, fat, fiber?}]}."""
    _, rows = _sheet_dicts(ws)
    log: dict = {}
    for row in rows:
        d = _parse_date(row.get(_COL_DATE))
        name = str(row.get("Food Name") or "").strip()
        if not d or not name:
            continue
        item = {
            "time":     str(row.get("Time") or "").strip() or None,
            "name":     name,
            "calories": round(_num(row, _COL_CALORIES) or 0),
            "protein":  round(_num(row, _COL_PROTEIN) or 0, 1),
            "carbs":    round(_num(row, _COL_CARBS) or 0, 1),
            "fat":      round(_num(row, _COL_FAT) or 0, 1),
        }
        fiber = _num(row, _COL_FIBER)
        if fiber:
            item["fiber"] = round(fiber, 1)
        log.setdefault(d, []).append(item)
    return log


def parse_xlsx(data: bytes) -> tuple[dict, dict, dict]:
    """Parse a MacroFactor .xlsx export → (daily totals, food log, workouts)."""
    from openpyxl import load_workbook
    from . import mf_workouts

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        names = set(wb.sheetnames)
        if "Quick Export" in names:
            cols, rows = _sheet_dicts(wb["Quick Export"])
            totals = _parse_daily_rows(cols, rows)
        else:
            totals = {}
        log = _parse_food_log(wb["Food Log"]) if "Food Log" in names else {}
        workouts = mf_workouts.parse_workbook(wb)
    finally:
        wb.close()

    if not totals and not log and not workouts["sessions"] and not workouts["program"]:
        raise ValueError("No MacroFactor sheets found in this workbook.")
    return totals, log, workouts


# ---------------------------------------------------------------------------
# Persistence — daily totals
# ---------------------------------------------------------------------------

def _normalize_keys(data: dict) -> dict:
    """Convert any legacy M/D/YYYY keys (from older imports) to ISO dates."""
    out: dict = {}
    for k, v in data.items():
        iso = _parse_date(k)
        if iso and isinstance(v, dict):
            out[iso] = v
    return out


def load_nutrition() -> dict:
    """Load daily nutrition from disk. Returns {} if missing or corrupt."""
    if not NUTRITION_FILE.exists():
        return {}
    try:
        return _normalize_keys(json.loads(NUTRITION_FILE.read_text(encoding="utf-8")))
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
    merged = _normalize_keys(existing)
    merged.update(incoming)
    return merged


def import_bytes(data: bytes, filename: str = "") -> dict:
    """Parse a .csv or .xlsx export, merge it into stored data, persist. Returns a result dict."""
    from . import mf_workouts

    is_xlsx = filename.lower().endswith(".xlsx") or data[:2] == b"PK"   # xlsx is a zip
    if is_xlsx:
        new_totals, new_log, new_workouts = parse_xlsx(data)
    else:
        new_totals, new_log = parse_csv(data)
        new_workouts = None

    merged = merge_nutrition(load_nutrition(), new_totals)
    save_nutrition(merged)
    if new_log:
        save_nutrition_log(merge_nutrition_log(load_nutrition_log(), new_log))

    sessions = 0
    if new_workouts:
        mf_workouts.save_workouts(
            mf_workouts.merge_workouts(mf_workouts.load_workouts(), new_workouts)
        )
        sessions = sum(len(v) for v in new_workouts["sessions"].values())

    dates = sorted(new_totals)
    return {
        "days_imported":     len(new_totals),
        "total_days":        len(merged),
        "from":              dates[0] if dates else None,
        "to":                dates[-1] if dates else None,
        "food_log_days":     len(new_log),
        "workout_sessions":  sessions,
    }


# ---------------------------------------------------------------------------
# Auto-import from a watched folder (e.g. Downloads or a Google Drive sync folder)
# ---------------------------------------------------------------------------

def default_import_dir() -> str:
    return str(Path.home() / "Downloads")


def find_latest_export(folder: str | Path) -> Path | None:
    """Return the most recently modified MacroFactor*.xlsx / *.csv in folder, or None."""
    try:
        p = Path(folder).expanduser()
        if not p.is_dir():
            return None
        files = [f for pat in EXPORT_GLOBS for f in p.glob(pat) if f.is_file()]
    except OSError:
        return None
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def load_import_state() -> dict:
    try:
        return json.loads(IMPORT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_import_state(state: dict) -> None:
    try:
        IMPORT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def import_latest_from_folder(folder: str | Path, force: bool = False) -> dict | None:
    """
    Import the newest MacroFactor export in folder if it hasn't been imported yet
    (or always, when force=True). Returns the import result, or None if there was
    nothing new. Raises ValueError for an unparseable file.
    """
    latest = find_latest_export(folder)
    if latest is None:
        return None
    stat  = latest.stat()
    state = load_import_state()
    if not force and state.get("file") == str(latest) and state.get("mtime") == stat.st_mtime:
        return None

    result = import_bytes(latest.read_bytes(), latest.name)
    result["file"] = latest.name
    _save_import_state({
        "file":        str(latest),
        "mtime":       stat.st_mtime,
        "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    return result


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
