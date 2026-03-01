# garmin_coach.spec — PyInstaller build specification
#
# Build with:
#   pyinstaller garmin_coach.spec --clean
#
# Output:
#   Windows → dist/GarminHealthCoach/GarminHealthCoach.exe  (one-folder)
#   macOS   → dist/GarminHealthCoach.app                    (app bundle)

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        # Read-only assets bundled inside the app
        ("templates", "templates"),
        ("static", "static"),
        ("assets", "assets"),
        # digest.py is imported lazily via server.py; include it explicitly
        ("digest.py", "."),
    ],
    hiddenimports=[
        # uvicorn internals that auto-discovery misses
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # keyring platform backends
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.fail",
        # garminconnect uses garth for auth
        "garth",
        "garth.exc",
        # anthropic streaming
        "anthropic",
        "httpx",
        "httpcore",
        # coach package modules (lazy imports missed by static analysis)
        "coach.gemini_coach",
        # pystray platform backends
        "pystray._win32",
        "pystray._darwin",
        "pystray._gtk",
        "pystray._xorg",
        # email / smtplib used by digest.py
        "email.mime.multipart",
        "email.mime.text",
        "smtplib",
        # jinja2
        "jinja2",
        "jinja2.ext",
        # misc
        "multipart",
        "dotenv",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GarminHealthCoach",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if sys.platform == "win32" else "assets/icon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GarminHealthCoach",
)

# macOS: wrap the collected folder into a .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="GarminHealthCoach.app",
        icon="assets/icon.icns",
        bundle_identifier="com.garmin-health-coach.app",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
            "LSUIElement": True,   # Hides from Dock (tray-only app)
        },
    )
