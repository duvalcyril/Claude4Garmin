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
import threading
import webbrowser

from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

import credentials_manager as cm
from garmin_client import get_garmin_client, fetch_health_data, format_health_summary
from claude_client import ClaudeCoach


# ---------------------------------------------------------------------------
# Server state — single-user app, module-level variables are fine
# ---------------------------------------------------------------------------

coach: ClaudeCoach | None = None
health_summary: str | None = None
garmin_connected: bool = False
connection_error: str | None = None


# ---------------------------------------------------------------------------
# Connection helper — called on startup and after credential updates
# ---------------------------------------------------------------------------

async def _connect() -> None:
    """
    Load credentials, auth Garmin, fetch health data, create coach.
    Updates module-level state. Never raises — errors are stored in
    connection_error so the UI can display them gracefully.
    """
    global coach, health_summary, garmin_connected, connection_error

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
        garmin = await asyncio.to_thread(get_garmin_client, email, password)
        health_data = await asyncio.to_thread(fetch_health_data, garmin)
        health_summary = format_health_summary(health_data)
        coach = ClaudeCoach(health_summary=health_summary)
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
    # Open the browser half a second after startup (gives uvicorn time to bind)
    target = "http://localhost:8000" if garmin_connected else "http://localhost:8000/settings"
    threading.Timer(0.5, lambda: webbrowser.open(target)).start()
    yield


app = FastAPI(lifespan=lifespan, title="Garmin Health Coach")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
    })


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "garmin_connected": garmin_connected,
        "coach_ready": coach is not None,
        "connection_error": connection_error,
    })


class ChatRequest(BaseModel):
    message: str


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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
