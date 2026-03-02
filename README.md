# Garmin Health Coach

A personal AI health coach that pulls your real Garmin data and lets you have a conversation with Claude about it. Ask about your sleep trends, recovery, training load, stress patterns — and get answers grounded in your actual numbers, not generic advice.

---

## What it does

On startup, the app connects to your Garmin Connect account, fetches your health data, and opens a chat interface in your browser. Claude acts as your personal coach with full context of your metrics. The sidebar shows a live, structured view of your data while you chat.

**Data fetched from Garmin Connect:**
- Daily steps, total and active calories, distance
- Average and max stress levels, body battery
- Resting heart rate
- HRV — overnight average, weekly baseline, and status label (Balanced / Low / Unbalanced)
- Training Readiness — Garmin's 0–100 daily composite score (HRV + sleep + recovery + load)
- Training Status — rolling label (Productive, Peaking, Maintaining, Recovery, Strained, etc.)
- Sleep breakdown — total, deep, REM, light, and sleep score
- Recent activities — type, duration, distance, average HR, calories
- Body composition — weight, body fat %, and muscle mass from a smart scale

**Nutrition data from MacroFactor:**
- Daily calories, protein, carbs, fat, fiber, and alcohol
- TDEE (Total Daily Energy Expenditure) and trend weight
- Target macros from your MacroFactor programme
- Shown in a dedicated Nutrition tab in the sidebar; optionally included in Claude's context

**What you can ask:**
- *"How has my sleep been this week?"*
- *"My stress has been high — what do you recommend?"*
- *"Am I recovering well enough to train hard tomorrow?"*
- *"Which day this week had my best body battery?"*
- *"My training status is Strained — should I take a rest day?"*
- *"How is my body composition trending over the past month?"*

Claude maintains the full conversation history so follow-up questions work naturally.

---

## Before you start — what you'll need

You need two things before installing:

### 1. A Garmin Connect account
You almost certainly already have this if you own a Garmin device. It's the same account you use in the Garmin Connect app on your phone. If not, create one free at [connect.garmin.com](https://connect.garmin.com).

You'll need your **Garmin email address** and **Garmin password**.

### 2. An Anthropic API key
This is what lets the app talk to Claude. It costs money to use, but for a single person chatting about their health data the cost is very small — typically a few cents per day of active use.

**How to get one:**
1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Click **API Keys** in the left sidebar
3. Click **Create Key**, give it a name like "Garmin Coach", and copy the key
4. Add a payment method under **Billing** — you only pay for what you use

The key looks like `sk-ant-api03-...` and is shown only once, so copy it somewhere safe.

---

## Installation

There are two ways to install. **Option A is recommended** — no technical knowledge required.

---

### Option A — Download the app (recommended for most users)

> No Python, no terminal, no setup. Just download and double-click.

**Step 1 — Download**

Go to the [Releases page](https://github.com/duvalcyril/Claude4Garmin/releases) and download the file for your computer:

| Your computer | Download |
|---|---|
| Windows 10 or 11 | `GarminHealthCoach-windows.zip` |
| Mac (any model) | `GarminHealthCoach.dmg` |

**Step 2 — Install**

*On Windows:*
1. Right-click the downloaded `.zip` file and choose **Extract All**
2. Open the extracted folder called `GarminHealthCoach`
3. Double-click `GarminHealthCoach.exe` to launch

*On Mac:*
1. Double-click the downloaded `.dmg` file
2. Drag `Garmin Health Coach` into your **Applications** folder
3. Open your Applications folder and double-click `Garmin Health Coach`

> **Mac security note:** The first time you open the app, macOS may say it "can't be verified". This happens with apps that aren't from the App Store. To allow it: go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**.

**Step 3 — First launch**

When you launch the app:
1. A small icon appears in your system tray (bottom-right on Windows, top-right menu bar on Mac)
2. Your browser opens automatically to the Settings page
3. Enter your Garmin email, Garmin password, and Anthropic API key
4. Click **Save & Connect**

The app will connect to Garmin, fetch your data, and open the chat. This takes about 10–20 seconds on first run.

**From now on**, just double-click the app icon. Your credentials are saved securely in your OS keychain — you'll never be asked for them again unless you change your password.

**To close the app**, right-click the tray icon and choose **Quit**. Closing the browser tab does not stop the app.

---

### Option B — Run from source (for developers)

> This option requires Python installed on your computer and comfort with the terminal.

**Step 1 — Install Python**

Download Python 3.10 or higher from [python.org](https://python.org/downloads). During installation on Windows, check the box that says **"Add Python to PATH"**.

**Step 2 — Download the code**

```bash
git clone https://github.com/duvalcyril/Claude4Garmin.git
cd Claude4Garmin
```

Or download the ZIP from GitHub and extract it.

**Step 3 — Create a virtual environment**

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):** `.\.venv\Scripts\Activate.ps1`
- **Windows (Command Prompt):** `.\.venv\Scripts\activate.bat`
- **macOS / Linux:** `source .venv/bin/activate`

**Step 4 — Install dependencies**

```bash
pip install -r requirements.txt
```

**Step 5 — Launch the app**

```bash
python launcher.py
```

The app starts, a tray icon appears, and your browser opens. On first launch you'll be taken to Settings to enter your credentials.

You can also run the web server directly (no tray icon, browser opens automatically):
```bash
python server.py
```

---

## Using the app

### The chat

Type any question about your health data in the chat box and press Enter. Claude has full context of your Garmin data and will give you specific, personalised answers based on your actual numbers.

The sidebar on the left shows your current data — training status, daily stats, sleep, recent activities, and body composition.

| Button | What it does |
|---|---|
| **Refresh data** | Re-fetch your latest Garmin data without restarting |
| **Reset conversation** | Clear the chat history and start fresh (your data stays loaded) |
| **Settings** | Update credentials, change what data is synced, manage skills |

### The `/` skill picker

Type `/` in the chat input to open a menu of coaching shortcuts. Two types are available:

- **Prompt skills** — pre-written prompts that expand into the text box, ready for you to review and send. Good for weekly summaries or training plan requests.
- **Personas** — activate a coaching personality that changes Claude's style and focus for the whole conversation. A badge shows the active persona; click × to deactivate.

You can upload your own skills and personas from **Settings → Skills & Personas**.

---

## Settings

Go to **Settings** (gear icon or the Settings button) to configure the app.

### Connection
Update your Garmin email, password, or Anthropic API key. You only need to do this if you change your password or want to use a different API key.

### Data Preferences
Choose what data the app fetches and how far back it looks:

- **Time range** — 7, 14, or 30 days of history
- **Daily Stats** — steps, calories, stress, body battery, resting HR, distance
- **Sleep** — total duration, deep, REM, light sleep, sleep score
- **Activities** — toggle on/off, choose how many recent activities to show (5, 10, or 20)
- **HRV** — overnight HRV average and status shown alongside daily stats
- **Training Readiness** — 0–100 score shown alongside daily stats
- **Training Status** — rolling label shown in the sidebar header
- **Body Composition** — weight, body fat %, muscle mass (requires a Garmin-compatible smart scale)

Changes take effect immediately — the app re-fetches your data after saving.

### Nutrition
Import your food log data from MacroFactor to give Claude full context across training, recovery, and fueling.

**Importing MacroFactor data:**
1. Open the MacroFactor app and go to **Profile → Export Data → Quick Export**
2. Choose **Daily Summary** export and select your desired date range
3. Save or share the CSV file to your computer
4. In the app, go to **Settings → Nutrition** and drag the CSV into the upload area
5. The Nutrition tab in the sidebar will populate with your macro data

For detailed export instructions see the [MacroFactor user guide](https://macrofactorapp.com/support/).

**AI Context controls** (under Settings → Nutrition → AI Context):
- **Include daily macro totals** — sends your daily calorie and macro breakdown to Claude (low token cost)
- **Include full food log** — sends individual food entries; toggle off to reduce token usage if costs are a concern

### Skills & Personas
Upload `.json` prompt skills or `.skill` persona files by dragging them into the upload area.

### Daily Digest
Set up a morning email with yesterday's key stats and a short coaching summary from Claude. See the [Daily Digest](#daily-digest) section below.

---

## Daily Digest

Get a morning email every day with yesterday's key stats and a Claude-generated coaching take — no need to open the app.

**What's in the digest:**
- Steps, calories, body battery, resting HR
- HRV (overnight average + status), Training Readiness score, Training Status label
- Sleep breakdown — total, deep, REM, score
- A short Claude-written paragraph: how yesterday went and what to focus on today

**Setup** (in Settings → Daily Digest):
1. Enter your recipient email address and preferred send time
2. Enter a Gmail address to send from
3. Enter a **Gmail App Password** — this is *not* your regular Gmail password. To generate one:
   - Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - You must have 2-Step Verification enabled on your Google account first
   - Select **Mail** as the app and your device, then click **Generate**
   - Copy the 16-character password shown
4. Click **Save Digest Settings**
5. Click **Send Test** to make sure everything works before the first scheduled send

> **Note:** The digest is currently only supported on **Windows** — it uses Windows Task Scheduler to send the email at the scheduled time. Mac support is planned.

Errors are logged to `digest.log` in `%APPDATA%\GarminHealthCoach\` (packaged app) or the project folder (running from source).

---

## How your credentials are stored

Your Garmin email/password and Anthropic API key are stored in your **OS keychain** — Windows Credential Manager on Windows, Keychain Access on macOS. They are **never written to any file on disk**.

Your other settings (which metrics to show, date range, etc.) are saved in a file called `settings.json`:
- **Packaged app (Option A):** `%APPDATA%\GarminHealthCoach\settings.json` on Windows, `~/Library/Application Support/GarminHealthCoach/settings.json` on macOS
- **Running from source (Option B):** `settings.json` in the project folder

---

## Two-factor authentication (2FA)

If your Garmin account has 2FA enabled, you'll be prompted to enter your code the first time you log in. After that, the app saves a session token so you won't be asked again until the session expires (usually several weeks).

---

## Troubleshooting

### "Wrong email or password" / 401 error from Garmin
Your Garmin credentials are incorrect. Go to **Settings → Connection** and re-enter them. Make sure you're using the email and password for [connect.garmin.com](https://connect.garmin.com), not your Garmin device PIN.

### Anthropic API errors / "Coach not ready"
Your API key may be invalid or have no credits. Check at [console.anthropic.com](https://console.anthropic.com) that the key is active and your account has a payment method. Go to **Settings → Connection** to update the key.

### Some data is missing
Not all Garmin devices record all metrics. Fields the app can't find are shown as `—` in the sidebar and are silently omitted from Claude's context — they don't cause errors. Body composition data requires a Garmin-compatible smart scale.

### Garmin session expired
If Garmin login fails despite correct credentials, the saved session token may have expired. Delete the session folder and the app will log in fresh:

- **Packaged app:**
  - Windows: delete `%APPDATA%\GarminHealthCoach\.garth_session`
  - macOS: delete `~/Library/Application Support/GarminHealthCoach/.garth_session`
- **Running from source:** delete the `.garth_session` folder in the project directory

### App won't open (Mac security warning)
macOS blocks apps from unidentified developers by default. Go to **System Settings → Privacy & Security**, scroll to the bottom, and click **Open Anyway** next to the Garmin Health Coach entry.

### Digest email not arriving
Click **Send Test** in Settings → Daily Digest to see the error. Common causes:
- Gmail App Password not set up (2-Step Verification must be enabled first)
- Recipient email address is missing
- On Windows: the Task Scheduler task wasn't registered (try saving Digest Settings again)

Check `digest.log` for the full error message. On the packaged app it's in `%APPDATA%\GarminHealthCoach\digest.log`.

### App already running / tray icon missing
If you see a tray icon but the browser doesn't open, click the tray icon and choose **Open Garmin Health Coach**. If you launched the app twice, the second launch will just open the browser — only one instance runs at a time.

---

## Project structure (for developers)

```
garmin-health-coach/
├── launcher.py             # Desktop entry point — tray icon, server thread, browser open
├── server.py               # FastAPI web server — all routes and API endpoints
├── main.py                 # CLI entry point — terminal chat loop (no browser)
├── paths.py                # Path resolution for dev vs. packaged (PyInstaller) modes
├── digest.py               # Standalone daily digest emailer (Windows Task Scheduler)
├── garmin_client.py        # Garmin Connect auth, data fetching, formatting
├── claude_client.py        # Claude API wrapper with streaming, history, personas
├── credentials_manager.py  # OS keychain read/write via the keyring library
├── settings_manager.py     # Data sync preferences stored in settings.json
├── skills_manager.py       # Loads prompt skills (.json) and personas (.skill)
├── data_cache.py           # Incremental Garmin data cache (avoids re-fetching old data)
├── nutrition_parser.py     # MacroFactor CSV parser and nutrition data persistence
├── setup_ui.py             # Interactive credential setup wizard for CLI mode
├── templates/
│   ├── index.html          # Main chat UI — split panel with sidebar + chat
│   ├── settings.html       # Settings page — credentials, data prefs, skills, digest
│   └── digest_email.html   # HTML email template for the daily digest
├── static/
│   ├── style.css           # App styles
│   └── app.js              # SSE streaming, chat logic, skill picker
├── assets/
│   ├── icon.png            # App icon (512×512 PNG)
│   ├── icon.ico            # Windows icon
│   └── icon.icns           # macOS icon
├── garmin_coach.spec       # PyInstaller build specification
├── build_windows.bat       # Windows build script → GarminHealthCoach-windows.zip
├── build_macos.sh          # macOS build script → GarminHealthCoach.dmg
├── .github/
│   └── workflows/
│       └── release.yml     # GitHub Actions: build both platforms on tag push
├── requirements.txt
├── .env.example            # Reference for environment variable names
├── .gitignore
└── ROADMAP.md
```

### Credential loading priority

```
OS keychain  →  environment variables  →  .env file
```

You can also pass credentials as environment variables (useful for scripting):
```bash
GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=secret ANTHROPIC_API_KEY=sk-ant-... python server.py
```

### Building the app yourself

**Windows:**
```bat
build_windows.bat
```
Output: `dist\GarminHealthCoach\GarminHealthCoach.exe` and `GarminHealthCoach-windows.zip`

**macOS:**
```bash
chmod +x build_macos.sh
./build_macos.sh
```
Output: `dist/GarminHealthCoach.app` and `GarminHealthCoach.dmg`

**Automated releases via GitHub Actions:**
Push a tag in the format `v1.0.0` and GitHub will automatically build both platforms and attach the files to a GitHub Release.

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## What's coming

See [ROADMAP.md](ROADMAP.md) for the full list. The main planned additions are:

**HR time-in-zones** — Zone breakdown per activity (Z1–Z5 minutes) so Claude can analyse aerobic vs. anaerobic distribution and flag if easy runs are drifting out of Zone 2.

**Race predictions & Cycling FTP** — Garmin's estimated race times (5k–marathon) and functional threshold power for deeper performance context.

**Custom workout creation** — Generate personalised running and cycling workouts tailored to your current fitness level and goals, then upload them directly to Garmin Connect so they appear on your device.

**Trend alerts** — Flag when a metric crosses a threshold (e.g. resting HR up 5+ bpm for 3 consecutive days, sleep score below 60 two nights in a row).

**macOS Daily Digest** — Scheduled digest emails via launchd (macOS equivalent of Task Scheduler).

---

## Dependencies

| Package | Purpose |
|---|---|
| `garminconnect` | Garmin Connect API client |
| `anthropic` | Claude API client (sync + async) |
| `fastapi` | Web framework for the browser UI |
| `uvicorn` | ASGI server that runs FastAPI |
| `jinja2` | HTML templating for server-rendered pages |
| `python-multipart` | Form data parsing for FastAPI |
| `keyring` | Secure OS keychain storage |
| `pystray` | System tray icon (cross-platform) |
| `Pillow` | Image handling for the tray icon |
| `rich` | Terminal formatting for the CLI setup wizard |
| `python-dotenv` | Optional `.env` file fallback |
