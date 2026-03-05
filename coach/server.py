"""server.py — FastAPI web server for Garmin Health Coach.

Entry point for the browser-based UI. Keeps main.py (CLI) untouched.

Startup sequence:
  1. Load credentials from OS keychain
  2. Authenticate with Garmin Connect (reuses cached session tokens)
  3. Fetch 7 days of health data
  4. Create ClaudeCoach with health context baked into system prompt
  5. Launch uvicorn on localhost:8000 and open the browser automatically

If credentials are missing or Garmin fails, the server still starts and
redirects the user to /settings to enter credentials from the browser.

Run with:
    python launcher.py
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, date
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

from . import activity_cache as ac
from . import credentials_manager as cm
from . import data_cache as dc
from . import nutrition_parser as np_
from . import settings_manager as sm
from . import skills_manager as skm
from . import memory_manager as mm
from . import token_tracker as tt
from .garmin_client import get_garmin_client, fetch_health_data, format_health_summary, format_trend_summary
from .claude_client import ClaudeCoach
from .paths import bundle_dir, user_data_dir


# ---------------------------------------------------------------------------
# Port selection — try 8000 first, fall back if it's already in use
# ---------------------------------------------------------------------------

def find_free_port(start: int = 8000, end: int = 8010) -> int:
    """Return the first available TCP port in [start, end)."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found between {start} and {end - 1}.")


# Resolved once at import time so launcher.py can read server.APP_PORT
APP_PORT: int = find_free_port()


# ---------------------------------------------------------------------------
# Server state — single-user app, module-level variables are fine
# ---------------------------------------------------------------------------

coach: ClaudeCoach | None = None
health_summary: str | None = None
health_data: dict | None = None
nutrition_data: dict = {}
nutrition_log: dict = {}
garmin_connected: bool = False
connection_error: str | None = None
coach_memory: dict = {}       # loaded/updated by _connect() and memory extraction
activity_details: dict = {}   # keyed by activity_id; loaded/updated by _connect()


# ---------------------------------------------------------------------------
# Coach factory — returns the right coach based on settings
# ---------------------------------------------------------------------------

def _make_coach(health_summary: str, history_file: Path):
    """Instantiate the coach configured in settings (Claude or Gemini)."""
    settings = sm.load_settings()
    provider = settings.get("ai_provider", "claude")
    model    = settings.get("ai_model",    "claude-sonnet-4-6")
    if provider == "gemini":
        from .gemini_coach import GeminiCoach
        api_key = cm.load_credential("gemini_api_key") or ""
        if not api_key:
            raise ValueError(
                "Gemini API key not configured. Add it in Settings → Connection."
            )
        return GeminiCoach(
            health_summary=health_summary,
            history_file=history_file,
            api_key=api_key,
            model=model,
        )
    return ClaudeCoach(health_summary=health_summary, history_file=history_file)


# ---------------------------------------------------------------------------
# Connection helper — called on startup and after credential updates
# ---------------------------------------------------------------------------

async def _connect() -> None:
    """
    Load credentials, auth Garmin, fetch health data, create coach.
    Updates module-level state. Never raises — errors are stored in
    connection_error so the UI can display them gracefully.
    """
    global coach, health_summary, health_data, nutrition_data, nutrition_log, garmin_connected, connection_error, coach_memory, activity_details

    # Keychain → env var fallback, then load .env for any remaining gaps
    cm.inject_into_env()
    load_dotenv()

    if not cm.credentials_complete():
        connection_error = "Credentials not configured. Please fill in the form below."
        garmin_connected = False
        return

    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    try:
        # Garmin auth and data fetching are blocking — run in thread pool
        settings = sm.load_settings()
        days_back = int(settings.get("days_back", 7))

        # Incremental fetch: only hit the Garmin API for stale or missing dates
        cache = dc.load_cache()
        dates, fetch_shared = dc.plan_fetch(cache, days_back)

        garmin = await asyncio.to_thread(get_garmin_client, email, password)
        raw = await asyncio.to_thread(
            fetch_health_data, garmin, settings, dates, fetch_shared
        )

        # Merge fresh data into the cached baseline, then persist
        if cache is not None:
            raw = dc.merge(cache["health_data"], raw, days_back)
        dc.save_cache(raw)

        health_data = raw
        nutrition_data = np_.load_nutrition()
        nutrition_log = np_.load_nutrition_log()

        # Build trend summary and load persistent memory for system prompt injection
        trend_summary = format_trend_summary(raw)
        coach_memory  = mm.load_memory()
        memory_notes  = mm.format_memory_for_prompt(coach_memory)

        health_summary = format_health_summary(
            raw, settings, nutrition_data, nutrition_log,
            memory_notes=memory_notes,
            trend_summary=trend_summary,
        )
        _provider = settings.get("ai_provider", "claude")
        coach = _make_coach(health_summary, user_data_dir() / f"chat_history_{_provider}.json")
        garmin_connected = True
        connection_error = None
        print("✓ Connected to Garmin. Web server ready.")

        # Load activity detail cache and enrich new activity IDs in background
        activity_details = ac.load_activity_details()
        missing_ids = ac.get_missing_ids(raw.get("activities", []), activity_details)
        if missing_ids:
            asyncio.create_task(_enrich_activities_background(garmin, missing_ids, settings))

        # Launch background memory extraction if enough new turns have accumulated
        if coach and mm.should_extract(coach.history, coach_memory):
            asyncio.create_task(_extract_memory_background(coach))
    except Exception as e:
        garmin_connected = False
        connection_error = str(e)
        coach = None
        print(f"✗ Garmin connection failed: {e}")


# ---------------------------------------------------------------------------
# Background memory extraction
# ---------------------------------------------------------------------------

async def _extract_memory_background(current_coach) -> None:
    """
    Background task: extract key facts from recent conversation turns using
    Claude Haiku, update coach_memory.json, and rebuild the active coach's
    system prompt so new facts take effect immediately.

    Uses a snapshot of the coach object passed at task creation time — safe
    against coach being replaced by a concurrent _connect() call.
    """
    global coach_memory, health_summary, coach

    try:
        memory           = mm.load_memory()
        history_snapshot = list(current_coach.history)   # snapshot to avoid mutation
        updated          = await asyncio.to_thread(mm.extract_memory, history_snapshot, memory)
        mm.save_memory(updated)
        coach_memory = updated

        # Rebuild system prompt in-place — only if coach hasn't been replaced
        if coach is current_coach and health_data is not None:
            settings      = sm.load_settings()
            trend_summary = format_trend_summary(health_data)
            memory_notes  = mm.format_memory_for_prompt(updated)
            new_summary   = format_health_summary(
                health_data, settings, nutrition_data, nutrition_log,
                memory_notes=memory_notes,
                trend_summary=trend_summary,
            )
            health_summary = new_summary
            current_coach._base_system_prompt = current_coach._build_system_prompt(new_summary)
            if not current_coach.active_persona:
                current_coach.system_prompt = current_coach._base_system_prompt

        note_count = len([
            l for l in (updated.get("notes") or "").splitlines()
            if l.strip().startswith("- ")
        ])
        print(f"✓ Coach memory updated — {note_count} notes stored.")
    except Exception as e:
        print(f"✗ Memory extraction failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Background activity enrichment
# ---------------------------------------------------------------------------

async def _enrich_activities_background(garmin, missing_ids: list, settings: dict) -> None:
    """
    Fetch per-activity enrichments (HR zones, splits, exercise sets, power zones)
    for new activity IDs and cache them to data/activity_details.json.

    Runs in background after startup. Saves after each activity — fault-tolerant
    if interrupted. Updates the module-level activity_details dict when done.
    """
    global activity_details

    details = ac.load_activity_details()

    for activity_id in missing_ids:
        entry = {"fetched_at": datetime.now().isoformat(timespec="seconds")}

        if settings.get("activity_detail_hr_zones", True):
            try:
                entry["hr_zones"] = await asyncio.to_thread(
                    garmin.get_activity_hr_in_timezones, activity_id
                )
            except Exception as e:
                entry["hr_zones_error"] = str(e)

        if settings.get("activity_detail_splits", True):
            try:
                entry["splits"] = await asyncio.to_thread(
                    garmin.get_activity_splits, activity_id
                )
            except Exception as e:
                entry["splits_error"] = str(e)

        if settings.get("activity_detail_exercise_sets", True):
            try:
                entry["exercise_sets"] = await asyncio.to_thread(
                    garmin.get_activity_exercise_sets, activity_id
                )
            except Exception as e:
                entry["exercise_sets_error"] = str(e)

        if settings.get("activity_detail_power_zones", True):
            try:
                entry["power_zones"] = await asyncio.to_thread(
                    garmin.get_activity_power_in_timezones, activity_id
                )
            except Exception as e:
                entry["power_zones_error"] = str(e)

        details[activity_id] = entry
        ac.save_activity_details(details)   # save after each — fault-tolerant

    activity_details = details
    print(f"✓ Activity enrichment done — {len(missing_ids)} new activities cached.")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _connect()
    # Open the browser half a second after startup (gives uvicorn time to bind).
    # When launched via launcher.py the browser open is handled there instead,
    # so this only fires when running server.py directly (dev mode).
    if not getattr(sys, "frozen", False) and "launcher" not in sys.modules:
        target = (
            f"http://localhost:{APP_PORT}"
            if garmin_connected
            else f"http://localhost:{APP_PORT}/settings"
        )
        threading.Timer(0.5, lambda: webbrowser.open(target)).start()
    yield


app = FastAPI(lifespan=lifespan, title="Garmin Health Coach")
app.mount("/static", StaticFiles(directory=str(bundle_dir() / "static")), name="static")
templates = Jinja2Templates(directory=str(bundle_dir() / "templates"))


# ── Jinja2 template filters ───────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """YYYY-MM-DD → 'Today', 'Yesterday', or 'Mon, Feb 28'."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        delta = (date.today() - d).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Yesterday"
        return d.strftime("%a, %b %d")
    except Exception:
        return date_str


def _fmt_date_short(date_str: str) -> str:
    """YYYY-MM-DD → 'Today', 'Yest', or weekday abbreviation ('Mon', 'Tue'…)."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        delta = (date.today() - d).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Yest"
        return d.strftime("%a")
    except Exception:
        return date_str


def _hm(seconds) -> str:
    """Seconds → '7h 22m'."""
    if not seconds:
        return "—"
    return f"{int(seconds) // 3600}h {(int(seconds) % 3600) // 60}m"


def _dur(seconds) -> str:
    """Seconds → '45:23' (mm:ss)."""
    if not seconds:
        return "—"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _compact(n) -> str:
    """Abbreviate large numbers for compact table cells: 8,234 → '8.2k'."""
    if n is None:
        return "—"
    n = int(n)
    if n >= 10000:
        return f"{n // 1000}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


templates.env.filters["fmt_date"]       = _fmt_date
templates.env.filters["fmt_date_short"] = _fmt_date_short
templates.env.filters["compact"]        = _compact
templates.env.filters["hm"]            = _hm
templates.env.filters["dur"]           = _dur


# ---------------------------------------------------------------------------
# Digest — Task Scheduler helpers
# ---------------------------------------------------------------------------

DIGEST_TASK_NAME = "GarminHealthCoachDigest"
_DIGEST_SCRIPT   = bundle_dir() / "digest.py"


def _register_digest_task(send_time: str) -> None:
    """Create or overwrite a daily Task Scheduler entry for the digest."""
    tr = f'"{sys.executable}" "{_DIGEST_SCRIPT}"'
    subprocess.run(
        ["schtasks", "/Create", "/F",
         "/TN", DIGEST_TASK_NAME,
         "/TR", tr,
         "/SC", "DAILY",
         "/ST", send_time],
        check=True, capture_output=True, text=True,
    )


def _unregister_digest_task() -> None:
    """Remove the scheduled task. Silently ignores if it doesn't exist."""
    subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", DIGEST_TASK_NAME],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _profile_complete(settings: dict) -> bool:
    """Profile is considered complete when at least sport and goal are filled in."""
    p = settings.get("athlete_profile") or {}
    return bool(p.get("sports") and p.get("goal"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not garmin_connected or not coach:
        return RedirectResponse("/settings")
    settings = sm.load_settings()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "health_summary": health_summary,
        "health_data": health_data,
        "nutrition_data": nutrition_data,
        "athlete_profile": settings.get("athlete_profile") or {},
        "profile_complete": _profile_complete(settings),
    })


def _nutrition_status() -> dict | None:
    """Return display metadata about the stored nutrition data, or None if empty."""
    data = np_.load_nutrition()
    if not data:
        return None
    dates = sorted(data.keys())
    return {"days": len(data), "from": dates[0], "to": dates[-1]}


def _get_local_ip() -> str:
    """Detect the machine's primary LAN IP (the IP other devices on the network can reach)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, error: str = "", success: str = ""):
    existing = cm.load_all_credentials()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "garmin_email": existing.get("garmin_email") or "",
        "has_password": bool(existing.get("garmin_password")),
        "has_api_key": bool(existing.get("anthropic_api_key")),
        "garmin_connected": garmin_connected,
        "connection_error": connection_error,
        "error": error,
        "success": success,
        "data_settings": sm.load_settings(),
        "skills": (_skills := skm.load_skills()),
        "persona_bodies": json.dumps({
            s["trigger"]: s.get("body", "") for s in _skills if s.get("type") == "persona"
        }),
        "has_digest_sender":       bool(cm.load_credential("digest_gmail_sender")),
        "has_digest_app_password": bool(cm.load_credential("digest_gmail_app_password")),
        "has_gemini_key":          bool(cm.load_credential("gemini_api_key")),
        "nutrition_status":        _nutrition_status(),
        "athlete_profile":         sm.load_settings().get("athlete_profile") or {},
        "coach_memory":            mm.load_memory(),
        "lan_ip":                  _get_local_ip(),
        "app_port":                APP_PORT,
    })


@app.post("/api/save-network-settings")
async def api_save_network_settings(request: Request):
    form = await request.form()
    existing = sm.load_settings()
    existing["lan_access"] = "lan_access" in form
    sm.save_settings(existing)
    return RedirectResponse("/settings?success=network_saved", status_code=303)


@app.post("/api/restart")
async def api_restart():
    """Restart the app process to apply settings that require it (e.g. LAN access)."""
    import os
    import subprocess

    def _do_restart():
        import time
        time.sleep(0.8)  # let the JSON response reach the browser first

        if getattr(sys, "frozen", False):
            # Packaged exe — re-exec the exe with no extra args
            os.execv(sys.executable, [sys.executable])
        else:
            # Running from source — explicitly relaunch launcher.py
            # (avoids relying on sys.argv which can be unreliable in venv/conda setups)
            project_root = str(Path(__file__).parent.parent)
            launcher = str(Path(__file__).parent.parent / "launcher.py")
            subprocess.Popen([sys.executable, launcher], cwd=project_root)
            os._exit(0)  # exit current process; new one will take over

    threading.Thread(target=_do_restart, daemon=True).start()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Readiness probe — used by launcher.py to know when the server is up."""
    return JSONResponse({"status": "ok"})


@app.get("/api/sidebar-html", response_class=HTMLResponse)
async def api_sidebar_html(request: Request):
    """Return the rendered sidebar partial for in-place DOM refresh (no page reload)."""
    return templates.TemplateResponse("sidebar_content.html", {
        "request":        request,
        "health_data":    health_data,
        "nutrition_data": nutrition_data,
    })


@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "garmin_connected": garmin_connected,
        "coach_ready": coach is not None,
        "connection_error": connection_error,
    })


class ChatRequest(BaseModel):
    message: str

class PersonaRequest(BaseModel):
    trigger: str


@app.post("/api/chat")
async def api_chat(body: ChatRequest):
    import re
    if not coach:
        raise HTTPException(503, detail="Coach not ready. Please check Settings.")

    # Detect "#N" workout references and inject cached detail into this turn only
    api_message     = body.message   # what gets sent to the AI (may be enriched)
    display_message = None           # what gets stored in history (clean version)

    match = re.search(r"#(\d+)", body.message)
    if match:
        idx = int(match.group(1)) - 1   # 1-indexed → 0-indexed
        activities = (health_data or {}).get("activities", [])
        if 0 <= idx < len(activities):
            act    = activities[idx]
            act_id = act.get("activity_id", "")
            if act_id and act_id in activity_details:
                detail_text = ac.format_activity_detail_for_prompt(
                    act, activity_details[act_id], sm.load_settings()
                )
                if detail_text:
                    act_label = act.get("name") or act.get("type") or "Activity"
                    act_date  = act.get("date", "")
                    api_message = (
                        f"[WORKOUT DETAIL for {act_label} on {act_date}:\n{detail_text}]\n\n"
                        f"{body.message}"
                    )
                    display_message = body.message

    async def generate():
        try:
            async for chunk in coach.chat_stream_async(api_message, display_message=display_message):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/reset")
async def api_reset():
    if coach:
        coach.reset_history()
    return JSONResponse({"ok": True})


@app.get("/api/memory")
async def api_get_memory():
    """Return current coach memory notes and metadata."""
    memory = mm.load_memory()
    return JSONResponse({
        "notes":                    memory.get("notes") or "",
        "last_updated":             memory.get("last_updated"),
        "last_extracted_from_turn": memory.get("last_extracted_from_turn", 0),
    })


@app.post("/api/memory")
async def api_save_memory(request: Request):
    """Save manually edited coach memory notes and rebuild system prompt."""
    global coach_memory, health_summary, coach
    body  = await request.json()
    notes = (body.get("notes") or "").strip()

    memory = mm.load_memory()
    memory["notes"]        = notes
    memory["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    mm.save_memory(memory)
    coach_memory = memory

    # Rebuild system prompt with updated notes
    if coach and health_data is not None:
        settings      = sm.load_settings()
        trend_summary = format_trend_summary(health_data)
        memory_notes  = mm.format_memory_for_prompt(memory)
        new_summary   = format_health_summary(
            health_data, settings, nutrition_data, nutrition_log,
            memory_notes=memory_notes,
            trend_summary=trend_summary,
        )
        health_summary = new_summary
        coach._base_system_prompt = coach._build_system_prompt(new_summary)
        if not coach.active_persona:
            coach.system_prompt = coach._base_system_prompt

    return JSONResponse({"ok": True})


@app.post("/api/memory/extract-now")
async def api_extract_memory_now():
    """Manually trigger a memory extraction pass (runs in background)."""
    if not coach:
        raise HTTPException(503, detail="Coach not ready.")
    current_memory = mm.load_memory()
    if mm.should_extract(coach.history, current_memory):
        asyncio.create_task(_extract_memory_background(coach))
        return JSONResponse({"ok": True, "message": "Extraction started in background."})
    return JSONResponse({"ok": False, "message": "Not enough new conversation turns to extract yet."})


@app.get("/api/token-usage")
async def api_token_usage():
    """Return aggregated token usage stats for the frontend monitor."""
    return JSONResponse(tt.get_usage_summary())


@app.get("/api/activity-detail/{activity_id}")
async def api_get_activity_detail(activity_id: str):
    """Return cached enrichment data for a specific activity."""
    if activity_id not in activity_details:
        raise HTTPException(404, detail="Activity detail not cached yet.")
    return JSONResponse(activity_details[activity_id])


@app.get("/api/skills")
async def api_skills():
    return JSONResponse(skm.load_skills())


@app.post("/api/upload-skill")
async def api_upload_skill(file: UploadFile = File(...)):
    """Save an uploaded .skill (persona) or .json (prompt) skill file to the right directory."""
    from pathlib import Path
    filename = Path(file.filename).name  # strip any path components

    if not filename:
        raise HTTPException(400, detail="No filename provided.")

    content = await file.read()

    if filename.endswith(".skill"):
        dest_dir = skm.CLAUDE_DIR
        dest_dir.mkdir(exist_ok=True)
        (dest_dir / filename).write_bytes(content)
        return JSONResponse({"ok": True, "type": "persona", "filename": filename})

    elif filename.endswith(".json"):
        try:
            data = json.loads(content)
        except ValueError:
            raise HTTPException(400, detail="Invalid JSON file.")
        if "trigger" not in data or "prompt" not in data:
            raise HTTPException(400, detail='Skill JSON must have "trigger" and "prompt" fields.')
        dest_dir = skm.SKILLS_DIR
        dest_dir.mkdir(exist_ok=True)
        (dest_dir / filename).write_bytes(content)
        return JSONResponse({"ok": True, "type": "prompt", "filename": filename})

    else:
        raise HTTPException(400, detail="Unsupported file type. Upload a .skill or .json file.")


@app.post("/api/upload-nutrition")
async def api_upload_nutrition(file: UploadFile = File(...)):
    """Parse a MacroFactor CSV, merge with existing nutrition data, rebuild coach."""
    global nutrition_data, nutrition_log, health_summary, coach
    content = await file.read()
    try:
        new_totals, new_log = np_.parse_csv(content)
    except Exception as exc:
        raise HTTPException(400, detail=f"Could not parse CSV: {exc}")

    # Merge and persist daily totals
    existing_totals = np_.load_nutrition()
    merged_totals   = np_.merge_nutrition(existing_totals, new_totals)
    np_.save_nutrition(merged_totals)
    nutrition_data = merged_totals

    # Merge and persist full food log
    existing_log = np_.load_nutrition_log()
    merged_log   = np_.merge_nutrition_log(existing_log, new_log)
    np_.save_nutrition_log(merged_log)
    nutrition_log = merged_log

    # Rebuild coach context with updated nutrition data
    if health_data:
        settings      = sm.load_settings()
        trend_summary = format_trend_summary(health_data)
        memory_notes  = mm.format_memory_for_prompt(mm.load_memory())
        health_summary = format_health_summary(
            health_data, settings, nutrition_data, nutrition_log,
            memory_notes=memory_notes,
            trend_summary=trend_summary,
        )
        _provider = settings.get("ai_provider", "claude")
        coach = _make_coach(health_summary, user_data_dir() / f"chat_history_{_provider}.json")

    return JSONResponse({"ok": True, "days_imported": len(new_totals), "total_days": len(merged_totals)})


@app.post("/api/save-profile")
async def api_save_profile(request: Request):
    """Save the athlete profile and rebuild the coach context."""
    global health_summary, coach
    form = await request.form()
    settings = sm.load_settings()
    settings["athlete_profile"] = {
        "name":            (form.get("name") or "").strip(),
        "sports":          (form.get("sports") or "").strip(),
        "level":           (form.get("level") or "").strip(),
        "goal":            (form.get("goal") or "").strip(),
        "training_days":   (form.get("training_days") or "").strip(),
        "training_plan":   (form.get("training_plan") or "").strip(),
        "upcoming_events": (form.get("upcoming_events") or "").strip(),
        "health_notes":    (form.get("health_notes") or "").strip(),
    }
    sm.save_settings(settings)
    if health_data:
        trend_summary = format_trend_summary(health_data)
        memory_notes  = mm.format_memory_for_prompt(mm.load_memory())
        health_summary = format_health_summary(
            health_data, settings, nutrition_data, nutrition_log,
            memory_notes=memory_notes,
            trend_summary=trend_summary,
        )
        _provider = settings.get("ai_provider", "claude")
        coach = _make_coach(health_summary, user_data_dir() / f"chat_history_{_provider}.json")
    return RedirectResponse("/settings?success=profile_saved#profile", status_code=303)


@app.post("/api/nutrition-settings")
async def api_save_nutrition_settings(request: Request):
    """Save nutrition AI-context toggles and rebuild coach with updated settings."""
    global health_summary, coach
    form = await request.form()
    settings = sm.load_settings()
    settings["nutrition_enabled"]     = "nutrition_enabled" in form
    settings["nutrition_log_enabled"] = "nutrition_log_enabled" in form
    sm.save_settings(settings)
    if health_data:
        trend_summary = format_trend_summary(health_data)
        memory_notes  = mm.format_memory_for_prompt(mm.load_memory())
        health_summary = format_health_summary(
            health_data, settings, nutrition_data, nutrition_log,
            memory_notes=memory_notes,
            trend_summary=trend_summary,
        )
        _provider = settings.get("ai_provider", "claude")
        coach = _make_coach(health_summary, user_data_dir() / f"chat_history_{_provider}.json")
    return RedirectResponse("/settings?success=nutrition_settings_saved#nutrition", status_code=303)


@app.post("/api/create-persona")
async def api_create_persona(request: Request):
    """Create a new .skill file from trigger, description, and persona content."""
    import io, zipfile as zf
    form        = await request.form()
    trigger     = (form.get("trigger") or "").strip().lower().replace(" ", "-")
    description = (form.get("description") or "").strip()
    content     = (form.get("content") or "").strip()

    if not trigger:
        return JSONResponse({"ok": False, "error": "Trigger name is required."}, status_code=400)
    if not content:
        return JSONResponse({"ok": False, "error": "Persona instructions are required."}, status_code=400)

    skill_md = f"---\nname: {trigger}\ndescription: {description}\n---\n{content}"

    buf = io.BytesIO()
    with zf.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", skill_md.encode("utf-8"))
    buf.seek(0)

    skm.CLAUDE_DIR.mkdir(exist_ok=True)
    (skm.CLAUDE_DIR / f"{trigger}.skill").write_bytes(buf.read())

    return JSONResponse({"ok": True, "trigger": trigger})


@app.post("/api/persona")
async def api_set_persona(body: PersonaRequest):
    if not coach:
        raise HTTPException(503, detail="Coach not ready.")
    skill = skm.get_skill_by_trigger(body.trigger)
    if not skill or skill.get("type") != "persona":
        raise HTTPException(404, detail="Persona skill not found.")
    coach.set_persona(skill["content"])
    return JSONResponse({"ok": True, "trigger": skill["trigger"]})


@app.post("/api/persona/clear")
async def api_clear_persona():
    if coach:
        coach.clear_persona()
    return JSONResponse({"ok": True})


@app.post("/api/refresh")
async def api_refresh():
    """Re-fetch Garmin data and rebuild the coach with fresh context."""
    await _connect()
    return JSONResponse({
        "ok": garmin_connected,
        "error": connection_error,
    })


@app.post("/api/credentials")
async def api_save_credentials(
    garmin_email: str = Form(...),
    garmin_password: str = Form(""),
    garmin_password_confirm: str = Form(""),
    anthropic_api_key: str = Form(""),
):
    existing = cm.load_all_credentials()

    # Validate password confirmation if a new password was provided
    if garmin_password and garmin_password != garmin_password_confirm:
        return RedirectResponse("/settings?error=passwords_mismatch", status_code=303)

    new_creds = {
        "garmin_email": garmin_email,
        # Use new value if provided, otherwise keep existing
        "garmin_password": garmin_password or existing.get("garmin_password") or "",
        "anthropic_api_key": anthropic_api_key or existing.get("anthropic_api_key") or "",
    }

    if not all(new_creds.values()):
        return RedirectResponse("/settings?error=missing_fields", status_code=303)

    cm.save_all_credentials(new_creds)

    # Reconnect with the new credentials
    await _connect()

    if garmin_connected:
        return RedirectResponse("/?success=connected", status_code=303)
    else:
        return RedirectResponse(
            f"/settings?error={connection_error or 'connection_failed'}",
            status_code=303,
        )


@app.post("/api/data-settings")
async def api_save_data_settings(request: Request):
    """Save data sync preferences and re-fetch Garmin data with the new config."""
    form = await request.form()

    # Load-merge-save: preserves keys managed by other settings forms
    # (athlete_profile, digest_*, ai_provider, etc.) that aren't in this form.
    existing = sm.load_settings()
    existing.update({
        "days_back": int(form.get("days_back", 7)),
        "daily_stats_enabled": "daily_stats_enabled" in form,
        "sleep_enabled": "sleep_enabled" in form,
        "activities_enabled": "activities_enabled" in form,
        "activity_count": int(form.get("activity_count", 10)),
        "hrv_enabled": "hrv_enabled" in form,
        "training_readiness_enabled": "training_readiness_enabled" in form,
        "training_status_enabled": "training_status_enabled" in form,
        "body_enabled": "body_enabled" in form,
        "metric_steps": "metric_steps" in form,
        "metric_calories_total": "metric_calories_total" in form,
        "metric_calories_active": "metric_calories_active" in form,
        "metric_stress": "metric_stress" in form,
        "metric_body_battery": "metric_body_battery" in form,
        "metric_resting_hr": "metric_resting_hr" in form,
        "metric_distance": "metric_distance" in form,
        "metric_sleep_total": "metric_sleep_total" in form,
        "metric_sleep_deep": "metric_sleep_deep" in form,
        "metric_sleep_light": "metric_sleep_light" in form,
        "metric_sleep_rem": "metric_sleep_rem" in form,
        "metric_sleep_score": "metric_sleep_score" in form,
        "metric_body_weight": "metric_body_weight" in form,
        "metric_body_fat": "metric_body_fat" in form,
        "metric_body_muscle": "metric_body_muscle" in form,
        # Activity detail enrichment toggles
        "activity_detail_hr_zones":      "activity_detail_hr_zones"      in form,
        "activity_detail_splits":        "activity_detail_splits"        in form,
        "activity_detail_exercise_sets": "activity_detail_exercise_sets" in form,
        "activity_detail_power_zones":   "activity_detail_power_zones"   in form,
    })
    sm.save_settings(existing)

    # Re-fetch Garmin data with the new configuration if we're connected
    if garmin_connected:
        await _connect()

    return RedirectResponse("/settings?success=data_saved", status_code=303)


@app.post("/api/ai-settings")
async def api_save_ai_settings(request: Request):
    """Save AI provider / model selection and reconnect the coach."""
    form = await request.form()
    settings = sm.load_settings()
    settings["ai_provider"] = form.get("ai_provider", "claude")
    settings["ai_model"]    = form.get("ai_model",    "claude-sonnet-4-6")
    sm.save_settings(settings)
    if form.get("gemini_api_key"):
        cm.save_credential("gemini_api_key", form["gemini_api_key"])
    if garmin_connected:
        await _connect()
    return RedirectResponse("/settings?success=ai_saved", status_code=303)


@app.post("/api/digest-settings")
async def api_save_digest_settings(request: Request):
    """Save Daily Digest preferences and update the Windows Task Scheduler entry."""
    form    = await request.form()
    enabled = "digest_enabled" in form   # checkbox absent from POST = unchecked

    settings = sm.load_settings()
    settings["digest_enabled"]   = enabled
    settings["digest_recipient"] = form.get("digest_recipient", "").strip()
    settings["digest_send_time"] = form.get("digest_send_time", "07:00")
    sm.save_settings(settings)

    # Persist Gmail credentials only if new values were supplied
    if form.get("digest_gmail_sender"):
        cm.save_credential("digest_gmail_sender", form["digest_gmail_sender"])
    if form.get("digest_gmail_app_password"):
        cm.save_credential("digest_gmail_app_password", form["digest_gmail_app_password"])

    # Register or remove the Task Scheduler task
    try:
        if enabled:
            _register_digest_task(settings["digest_send_time"])
        else:
            _unregister_digest_task()
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "schtasks command failed").strip()
        return RedirectResponse(f"/settings?error={err}", status_code=303)

    return RedirectResponse("/settings?success=digest_saved", status_code=303)


@app.post("/api/digest-test")
async def api_digest_test():
    """Send a test digest email immediately, ignoring the digest_enabled toggle."""
    from datetime import date, timedelta
    from digest import run_digest   # lazy import — digest.py lives at project root
    try:
        yesterday = date.today() - timedelta(days=1)
        await asyncio.to_thread(run_digest, yesterday)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=APP_PORT, log_level="warning")
