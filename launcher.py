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
import paths  # noqa: F401 — side-effect: ensures user_data_dir exists
from paths import bundle_dir, user_data_dir


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------

LOCK_FILE = user_data_dir() / "app.lock"


def _is_process_running(pid: int) -> bool:
    """Return True if a process with the given PID is currently alive."""
    try:
        import os
        import signal
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _acquire_lock() -> bool:
    """
    Try to acquire the single-instance lock.

    Returns True if we are the first instance, False if another is already running.
    On success, writes our PID to the lock file.
    """
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if _is_process_running(pid):
                return False        # Another instance is alive
        except (ValueError, OSError):
            pass                    # Stale or corrupt lock file — overwrite it

    LOCK_FILE.write_text(str(_get_pid()))
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
    import server  # noqa: F401 — registers the FastAPI app
    from server import app
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
    import server as srv
    port = srv.APP_PORT
    app_url = f"http://127.0.0.1:{port}"

    # Single-instance check
    if not _acquire_lock():
        # Another instance is running — just bring up the browser and exit
        webbrowser.open(app_url)
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
