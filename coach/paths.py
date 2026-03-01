"""paths.py — Centralised path resolution for dev and packaged (PyInstaller) modes.

Two categories of paths:

  bundle_dir()    Read-only assets bundled with the app (templates/, static/, assets/).
                  In dev mode: the project root directory.
                  In frozen mode: sys._MEIPASS (the PyInstaller temp extraction dir).

  user_data_dir() Writable user data that must survive across app launches.
                  In dev mode: data/ subdirectory under the project root.
                  In frozen mode: OS-appropriate app-data directory.
                    Windows → %APPDATA%/GarminHealthCoach
                    macOS   → ~/Library/Application Support/GarminHealthCoach

All other modules should import from here rather than constructing paths directly.
"""

import os
import sys
from pathlib import Path


def bundle_dir() -> Path:
    """Directory containing read-only bundled assets (templates, static, assets)."""
    if getattr(sys, "frozen", False):
        # PyInstaller extracts bundled files to sys._MEIPASS at runtime
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev mode: this file lives at project_root/coach/paths.py
    return Path(__file__).parent.parent


def user_data_dir() -> Path:
    """
    Writable directory for all user-specific runtime files.

    Created on first access if it doesn't exist.
    In dev mode returns project_root/data/ to keep runtime files out of the source tree.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            # macOS / Linux
            base = Path.home() / "Library" / "Application Support"
        data_dir = base / "GarminHealthCoach"
    else:
        # Dev mode: use the data/ subdirectory under the project root
        data_dir = Path(__file__).parent.parent / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
