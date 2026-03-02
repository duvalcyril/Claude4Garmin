"""launcher.py — Desktop entry point for the packaged Garmin Health Coach app.

Responsibilities:
  - Single-instance check: if already running, open the browser and exit
  - Start the FastAPI/uvicorn server in a background thread
  - Poll /health until the server is ready, then open the browser
  - Keep a system tray icon alive so the user can open or quit the app

This file is the PyInstaller entry point (garmin_coach.spec).
It is also runnable directly in dev mode: python launcher.py
"""

import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# paths must be imported before server so APP_PORT is resolved on the correct port
import coach.paths  # noqa: F401 — side-effect: ensures user_data_dir exists
from coach.paths import bundle_dir, user_data_dir


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------

LOCK_FILE = user_data_dir() / "app.lock"


def _is_server_responding(port: int) -> bool:
    """Return True if our server's /health endpoint answers on the given port."""
    try:
        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _acquire_lock(port: int) -> bool:
    """
    Try to acquire the single-instance lock.

    Returns True if we are the first instance, False if another is confirmed running.
    On success, writes our PID and port to the lock file as "PID:PORT".

    PID alone is unreliable on Windows (PIDs are recycled), so we also verify
    that the server is actually responding before declaring another instance live.
    """
    if LOCK_FILE.exists():
        try:
            text = LOCK_FILE.read_text().strip()
            pid_str, _, port_str = text.partition(":")
            existing_port = int(port_str) if port_str else None
            if existing_port and _is_server_responding(existing_port):
                return False        # Confirmed: another instance is alive and serving
        except (ValueError, OSError):
            pass                    # Stale or corrupt lock file — overwrite it

    LOCK_FILE.write_text(f"{_get_pid()}:{port}")
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _get_pid() -> int:
    import os
    return os.getpid()


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

def _start_server(port: int) -> None:
    """Run uvicorn in this thread (blocks until shutdown)."""
    import uvicorn
    import coach.server  # noqa: F401 — registers the FastAPI app
    from coach.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# Readiness polling
# ---------------------------------------------------------------------------

def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """Poll the /health endpoint until it responds 200 or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

def _load_icon_image():
    """Load the tray icon image from the bundle assets directory."""
    from PIL import Image
    icon_path = bundle_dir() / "assets" / "icon.png"
    return Image.open(icon_path).convert("RGBA")


def _run_tray(app_url: str) -> None:
    """
    Show a system tray icon with Open and Quit menu items.
    Blocks until the user selects Quit.
    """
    import pystray
    from pystray import MenuItem as Item

    image = _load_icon_image()

    def on_open(icon, item):
        webbrowser.open(app_url)

    def on_quit(icon, item):
        _release_lock()
        icon.stop()
        # Give the tray icon time to clean up before hard-exiting
        threading.Timer(0.5, lambda: sys.exit(0)).start()

    menu = (
        Item("Open Garmin Health Coach", on_open, default=True),
        pystray.Menu.SEPARATOR,
        Item("Quit", on_quit),
    )

    icon = pystray.Icon(
        name="GarminHealthCoach",
        icon=image,
        title="Garmin Health Coach",
        menu=pystray.Menu(*menu),
    )
    icon.run()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Resolve the port (server.APP_PORT is set at import time via find_free_port)
    import coach.server as srv
    port = srv.APP_PORT
    app_url = f"http://127.0.0.1:{port}"

    # Single-instance check — pass port so it's stored in the lock file
    if not _acquire_lock(port):
        # Confirmed live instance: read its port from the lock file and open that URL
        try:
            text = LOCK_FILE.read_text().strip()
            _, _, existing_port_str = text.partition(":")
            live_url = f"http://127.0.0.1:{existing_port_str}"
        except Exception:
            live_url = app_url
        webbrowser.open(live_url)
        sys.exit(0)

    # Start the FastAPI server in a daemon thread
    server_thread = threading.Thread(
        target=_start_server,
        args=(port,),
        daemon=True,
        name="uvicorn",
    )
    server_thread.start()

    # Wait for the server to be ready, then open the browser
    health_url = f"{app_url}/health"
    ready = _wait_for_server(health_url, timeout=30.0)
    if ready:
        webbrowser.open(app_url)
    else:
        # Server failed to start — open settings so user can see an error
        webbrowser.open(f"{app_url}/settings")

    # Hand off to the tray icon (blocks until Quit)
    try:
        _run_tray(app_url)
    except Exception:
        # pystray failed (e.g. no display on headless system) — just keep server alive
        server_thread.join()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
