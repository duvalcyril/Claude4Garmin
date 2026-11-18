"""mf_workouts.py — MacroFactor strength-training data (from the .xlsx export).

Sheets used:
  Workout Log            — one row per set → sessions grouped by date + workout
  Muscle Groups - Sets   — sets per muscle group per training day
  Active Program         — the current program template (first cycle only)

Persisted to workouts.json:
  {
    "sessions":    {date: [{name, duration_s, exercises: [{name, sets: [...]}]}]},
    "muscle_sets": {date: {muscle: sets}},
    "program":     {name, cycles, deload, days: [{name, exercises: [{name, sets: [...]}]}]} | None,
    "load_unit":   "lb" | "kg"   — unit the user appears to load the bar in
  }
Set dicts: {type, kg?, reps?, rir?, seconds?, meters?}. Loads are always stored in kg.
"""

import json
import re
from datetime import date, datetime, timedelta

from .paths import user_data_dir

WORKOUTS_FILE = user_data_dir() / "workouts.json"
LBS_TO_KG = 0.45359237


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(v) -> str | None:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(ws) -> list[tuple]:
    """Non-empty rows of a worksheet, padded so index access never fails."""
    out = []
    for r in ws.iter_rows(values_only=True):
        if r and any(c is not None and c != "" for c in r):
            out.append(tuple(r) + (None,) * 40)
    return out


def _clean_exercise_name(raw) -> str:
    """'Pec Deck Fly ∈ SS1' → 'Pec Deck Fly (superset 1)'."""
    name = str(raw).strip()
    m = re.match(r"^(.*?)\s*∈\s*SS(\d+)\s*$", name)
    return f"{m.group(1)} (superset {m.group(2)})" if m else name


def _header_index(header: tuple) -> dict:
    return {str(h).strip(): i for i, h in enumerate(header) if h is not None}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_workout_log(ws) -> dict:
    rows = _rows(ws)
    if not rows:
        return {}
    h = _header_index(rows[0])

    def col(r, name):
        i = h.get(name)
        return r[i] if i is not None else None

    sessions: dict = {}           # date → {workout name → session}
    for r in rows[1:]:
        d = _iso(col(r, "Date"))
        exercise = col(r, "Exercise")
        if not d or not exercise:
            continue
        wname = str(col(r, "Workout") or "Workout").strip()
        day = sessions.setdefault(d, {})
        sess = day.setdefault(wname, {
            "name": wname,
            "duration_s": int(_f(col(r, "Workout Duration")) or 0) or None,
            "exercises": [],
        })
        # Superset sets alternate between exercises ("Pec Deck Fly ∈ SS1"), so
        # group by name rather than by consecutive rows.
        ex_name = _clean_exercise_name(exercise)
        ex = next((e for e in sess["exercises"] if e["name"] == ex_name), None)
        if ex is None:
            ex = {"name": ex_name, "sets": []}
            sess["exercises"].append(ex)

        st: dict = {"type": str(col(r, "Set Type") or "Standard Set").strip()}
        kg   = _f(col(r, "Weight (kg)"))
        reps = _f(col(r, "Reps"))
        rir  = _f(col(r, "RIR"))
        secs = _f(col(r, "Duration"))
        m    = _f(col(r, "Distance short (m)"))
        km   = _f(col(r, "Distance long (km)"))
        if kg is not None:   st["kg"] = round(kg, 3)
        if reps is not None: st["reps"] = int(reps)
        if rir is not None:  st["rir"] = rir if rir % 1 else int(rir)
        if secs is not None: st["seconds"] = int(secs)
        if m is not None:    st["meters"] = m
        elif km is not None: st["meters"] = km * 1000
        ex["sets"].append(st)

    return {d: list(by_name.values()) for d, by_name in sessions.items()}


def _parse_muscle_sets(ws) -> dict:
    rows = _rows(ws)
    if not rows:
        return {}
    header = rows[0]
    out: dict = {}
    for r in rows[1:]:
        d = _iso(r[0])
        if not d:
            continue
        muscles = {}
        for i, h in enumerate(header[1:], start=1):
            if h is None:
                continue
            v = _f(r[i])
            if v:
                muscles[re.sub(r"\s*\(sets\)\s*$", "", str(h)).strip()] = v
        if muscles:
            out[d] = muscles
    return out


def _parse_program(ws) -> dict | None:
    """Extract the program header and the day templates of its first cycle."""
    rows = _rows(ws)
    if not rows:
        return None
    program = {"name": None, "cycles": None, "deload": None, "days": []}
    for c in rows[0]:
        s = str(c or "")
        if s.startswith("Program:"):
            program["name"] = s.split(":", 1)[1].strip()
        elif s.startswith("Cycles:"):
            program["cycles"] = s.split(":", 1)[1].strip()
        elif s.startswith("Deload:"):
            program["deload"] = s.split(":", 1)[1].strip()

    in_cycle = False
    day = None
    for r in rows[1:]:
        first = str(r[0] or "").strip()
        if first.startswith("Cycle "):
            if in_cycle:
                break                    # only the first cycle is needed
            in_cycle = True
            continue
        if not in_cycle or first.startswith("Block "):
            continue
        if first:
            day = {"name": first, "exercises": []}
            program["days"].append(day)
        if day is None or not r[1]:
            continue
        if str(r[2] or "").strip().lower() == "yes":   # skipped exercise
            continue
        sets = []
        i = 4
        while r[i] is not None or r[i + 1] is not None:
            reps = str(r[i + 1] or "").replace(" ", "")
            if not reps and r[i + 4] is not None:      # timed set (e.g. plank)
                secs = _f(r[i + 4])
                reps = f"{secs:g}s" if secs is not None else str(r[i + 4]).strip()
            sets.append({
                "type":  str(r[i] or "Standard Set").strip(),
                "reps":  reps or "max",
                "rir":   r[i + 2],
            })
            i += 5
        day["exercises"].append({"name": _clean_exercise_name(r[1]), "sets": sets})

    program["days"] = [d for d in program["days"] if d["exercises"]]   # drop rest days
    return program if program["days"] else None


def _detect_load_unit(sessions: dict) -> str:
    """Guess whether the user loads in lb (exported kg are lb conversions) or kg."""
    lb_hits = kg_hits = 0
    for day in sessions.values():
        for sess in day:
            for ex in sess["exercises"]:
                for st in ex["sets"]:
                    kg = st.get("kg")
                    if not kg:
                        continue
                    lb = kg / LBS_TO_KG
                    if abs(lb * 2 - round(lb * 2)) < 0.05:
                        lb_hits += 1
                    if abs(kg * 4 - round(kg * 4)) < 0.02:
                        kg_hits += 1
    return "lb" if lb_hits > kg_hits else "kg"


def parse_workbook(wb) -> dict:
    """Parse workout-related sheets of an openpyxl workbook. Missing sheets are fine."""
    names = set(wb.sheetnames)
    sessions = _parse_workout_log(wb["Workout Log"]) if "Workout Log" in names else {}
    muscle   = _parse_muscle_sets(wb["Muscle Groups - Sets"]) if "Muscle Groups - Sets" in names else {}
    program  = _parse_program(wb["Active Program"]) if "Active Program" in names else None
    return {
        "sessions":    sessions,
        "muscle_sets": muscle,
        "program":     program,
        "load_unit":   _detect_load_unit(sessions) if sessions else None,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_workouts() -> dict:
    try:
        return json.loads(WORKOUTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_workouts(data: dict) -> None:
    try:
        WORKOUTS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def merge_workouts(existing: dict, incoming: dict) -> dict:
    """Incoming wins per date; program / unit replaced when the new export has them."""
    merged = {
        "sessions":    dict(existing.get("sessions") or {}),
        "muscle_sets": dict(existing.get("muscle_sets") or {}),
        "program":     existing.get("program"),
        "load_unit":   existing.get("load_unit"),
    }
    merged["sessions"].update(incoming.get("sessions") or {})
    merged["muscle_sets"].update(incoming.get("muscle_sets") or {})
    if incoming.get("program"):
        merged["program"] = incoming["program"]
    if incoming.get("load_unit"):
        merged["load_unit"] = incoming["load_unit"]
    return merged


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def _num(x: float) -> str:
    return f"{x:g}"


def _load(kg: float, unit: str) -> str:
    if unit == "lb":
        return _num(round(kg / LBS_TO_KG * 2) / 2)
    return _num(round(kg * 4) / 4)


def _set_str(st: dict, unit: str) -> str:
    if st.get("kg") is not None and st.get("reps") is not None:
        s = f"{_load(st['kg'], unit)}×{st['reps']}"
    elif st.get("reps") is not None:
        s = f"BW×{st['reps']}"
    elif st.get("seconds") is not None:
        s = f"{st['seconds']}s"
    elif st.get("meters") is not None:
        s = f"{_num(round(st['meters']))}m"
    else:
        s = "?"
    if st.get("rir") is not None:
        s += f"@{_num(st['rir'])}"
    if st["type"].startswith("Failure"):
        s = "F" + s
    return s


def _side(st: dict) -> str | None:
    m = re.search(r"\((L|R)\)\s*$", st.get("type", ""))
    return m.group(1) if m else None


def _exercise_line(ex: dict, unit: str) -> str:
    working = [st for st in ex["sets"] if not st["type"].startswith("Warm")]
    parts, i, unilateral = [], 0, False
    while i < len(working):
        st = working[i]
        nxt = working[i + 1] if i + 1 < len(working) else None
        if _side(st) == "L" and nxt and _side(nxt) == "R":
            unilateral = True
            a, b = _set_str(st, unit), _set_str(nxt, unit)
            parts.append(a if a == b else f"L {a} / R {b}")
            i += 2
            continue
        parts.append(_set_str(st, unit))
        i += 1
    name = ex["name"] + (" (per side)" if unilateral else "")
    return f"    {name}: {', '.join(parts) if parts else 'warm-up only'}"


def _short_workout_name(name: str, program_name: str | None) -> str:
    if program_name and name.startswith(program_name):
        m = re.search(r"\(([^)]*)\)\s*$", name)
        if m:
            return m.group(1)
    return name


def _program_set_summary(sets: list[dict]) -> str:
    groups: list[list] = []
    for st in sets:
        key = (st["type"].startswith("Failure"), st["reps"], st["rir"])
        if groups and groups[-1][0] == key:
            groups[-1][1] += 1
        else:
            groups.append([key, 1])
    out = []
    for (failure, reps, rir), n in groups:
        s = f"{n}×{reps}" if n > 1 else reps
        if rir is not None:
            s += f"@{_num(float(rir))}"
        out.append(("F" if failure else "") + s)
    return " + ".join(out)


def format_for_prompt(
    workouts: dict,
    days_back: int,
    include_program: bool = True,
    today: date | None = None,
) -> str:
    """Compact text block for Claude's system prompt. Empty string if no data."""
    if not workouts:
        return ""
    unit     = workouts.get("load_unit") or "kg"
    program  = workouts.get("program") or {}
    sessions = workouts.get("sessions") or {}
    muscle   = workouts.get("muscle_sets") or {}
    lines: list[str] = []

    if sessions:
        recent = sorted(sessions, reverse=True)[:max(days_back, 1)]
        cutoff = ((today or date.today()) - timedelta(days=days_back)).isoformat()
        recent = [d for d in recent if d > cutoff] or sorted(sessions, reverse=True)[:1]
        lines.append(
            f"STRENGTH TRAINING — MacroFactor workout log (most recent first). "
            f"Working sets as load×reps@RIR in {unit}; F = failure set; warm-ups omitted:"
        )
        for d in recent:
            for sess in sessions[d]:
                label = _short_workout_name(sess["name"], program.get("name"))
                dur = f" ({round(sess['duration_s'] / 60)} min)" if sess.get("duration_s") else ""
                lines.append(f"  {d} {label}{dur}:")
                for ex in sess["exercises"]:
                    lines.append(_exercise_line(ex, unit))
        lines.append("")

    if muscle:
        end = max(muscle)
        start = (datetime.strptime(end, "%Y-%m-%d").date() - timedelta(days=6)).isoformat()
        totals: dict = {}
        for d, m in muscle.items():
            if start <= d <= end:
                for k, v in m.items():
                    totals[k] = totals.get(k, 0) + v
        if totals:
            ranked = sorted(totals.items(), key=lambda kv: -kv[1])
            lines.append(
                f"SETS PER MUSCLE GROUP ({start} to {end}): "
                + ", ".join(f"{k} {_num(v)}" for k, v in ranked)
            )
            lines.append("")

    if include_program and program.get("days"):
        head = f"ACTIVE PROGRAM: {program.get('name') or 'Unnamed'}"
        extras = [x for x in (
            f"{program['cycles']} cycles" if program.get("cycles") else None,
            f"deload: {program['deload']}" if program.get("deload") else None,
        ) if x]
        if extras:
            head += f" ({', '.join(extras)})"
        lines.append(head + " — target sets as reps@RIR, F = failure set:")
        for day in program["days"]:
            exs = "; ".join(
                f"{ex['name']} {_program_set_summary(ex['sets'])}" for ex in day["exercises"]
            )
            lines.append(f"  {day['name'].strip()}: {exs}")
        lines.append("")

    return "\n".join(lines)
