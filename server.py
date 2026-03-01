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
    python server.py
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

import credentials_manager as cm
import data_cache as dc
import settings_manager as sm
import skills_manager as skm
from garmin_client import get_garmin_client, fetch_health_data, format_health_summary
from claude_client import ClaudeCoach
from paths import bundle_dir, user_data_dir


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
garmin_connected: bool = False
connection_error: str | None = None


# ---------------------------------------------------------------------------
# Coach factory — returns the right coach based on settings
# ---------------------------------------------------------------------------

def _make_coach(health_summary: str, history_file: Path):
    """Instantiate the coach configured in settings (Claude or Gemini)."""
    settings = sm.load_settings()
    provider = settings.get("ai_provider", "claude")
    model    = settings.get("ai_model",    "claude-sonnet-4-6")
    if provider == "gemini":
        from gemini_coach import GeminiCoach
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
    global coach, health_summary, health_data, garmin_connected, connection_error

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
        health_summary = format_health_summary(raw, settings)
        _provider = settings.get("ai_provider", "claude")
        coach = _make_coach(health_summary, user_data_dir() / f"chat_history_{_provider}.json")
        garmin_connected = True
        connection_error = None
        print("✓ Connected to Garmin. Web server ready.")
    except Exception as e:
        garmin_connected = False
        connection_error = str(e)
        coach = None
        print(f"✗ Garmin connection failed: {e}")


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

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not garmin_connected or not coach:
        return RedirectResponse("/settings")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "health_summary": health_summary,
        "health_data": health_data,
    })


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
    })


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Readiness probe — used by launcher.py to know when the server is up."""
    return JSONResponse({"status": "ok"})


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
    if not coach:
        raise HTTPException(503, detail="Coach not ready. Please check Settings.")

    async def generate():
        try:
            async for chunk in coach.chat_stream_async(body.message):
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

    # Checkboxes are only present in form data when checked; absence means False
    settings = {
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
    }

    sm.save_settings(settings)

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
    from digest import run_digest   # lazy import — keeps errors scoped to this endpoint
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
