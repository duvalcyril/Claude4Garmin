# Garmin Health Coach

A personal AI health coach that pulls your real Garmin data and lets you have a conversation with Claude about it. Ask about your sleep trends, recovery, training load, stress patterns — and get answers grounded in your actual numbers, not generic advice.

---

## What it does

On startup, the app connects to your Garmin Connect account, fetches your health data, and opens a browser-based chat interface. Claude acts as your personal coach with full context of your metrics. The sidebar shows a live, structured view of your data while you chat.

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

**What you can ask:**
- *"How has my sleep been this week?"*
- *"My stress has been high — what do you recommend?"*
- *"Am I recovering well enough to train hard tomorrow?"*
- *"Which day this week had my best body battery?"*
- *"My training status is Strained — should I take a rest day?"*
- *"How is my body composition trending over the past month?"*

Claude maintains the full conversation history so follow-up questions work naturally.

---

## How credentials are stored

Your Garmin email/password and Anthropic API key are stored in your **OS keychain** — Windows Credential Manager on Windows, Keychain on macOS. They are never written to any file on disk. The app uses the `keyring` library to read and write them securely.

The only file that touches authentication is `.garth_session/`, which holds Garmin OAuth tokens (so the app doesn't have to re-login on every run). This folder is excluded from Git via `.gitignore`.

---

## Requirements

- Python 3.10 or higher
- A [Garmin Connect](https://connect.garmin.com) account
- An [Anthropic API key](https://console.anthropic.com)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/garmin-health-coach.git
cd garmin-health-coach
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):** `.\.venv\Scripts\Activate.ps1`
- **Windows (Command Prompt):** `.\.venv\Scripts\activate.bat`
- **macOS / Linux:** `source .venv/bin/activate`

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the app

```bash
python server.py
```

The browser opens automatically at `http://localhost:8000`. On first launch, you'll be taken to the Settings page to enter your Garmin credentials and Anthropic API key. Everything is saved to your OS keychain — you won't be asked again on future runs.

**CLI mode** (no browser, terminal-only):
```bash
python main.py
```

---

## Usage

### Web interface (recommended)

| Element | What it does |
|---|---|
| Sidebar | Live structured view of your Garmin data — training status badge, daily stats (with HRV and readiness), sleep, activities, body composition |
| Chat panel | Ask Claude anything about your health data; responses stream in real time |
| `/` picker | Type `/` in the chat input to browse and invoke coaching skills and personas |
| Refresh data | Re-fetch Garmin data without restarting the server |
| Reset conversation | Clear chat history while keeping your Garmin data context |
| Settings | Update credentials, change data sync preferences, upload skills and personas |

### CLI commands

```bash
python server.py                  # Start the web app (recommended)
python main.py                    # CLI mode — terminal chat loop
python main.py --setup            # Update or rotate credentials
python main.py --status           # Check which credentials are stored
python main.py --clear-credentials  # Remove all stored credentials
```

**In-session CLI commands:**

| Type | Effect |
|---|---|
| Any question | Get a coaching response from Claude |
| `reset` | Clear conversation history (Garmin data context is kept) |
| `quit` / `exit` | Exit the app |
| `Ctrl+C` | Exit the app |

---

## Data Preferences

In the web UI, go to **Settings → Data Preferences** to configure:

- **Time range** — 7, 14, or 30 days of history
- **Daily Stats** — toggle the whole section and individual metrics (steps, calories, stress, body battery, resting HR, distance)
- **Sleep** — toggle the whole section and individual metrics (total duration, deep, REM, light, sleep score)
- **Activities** — toggle the whole section and choose how many recent activities to include (5, 10, or 20)
- **HRV** — overnight HRV average, weekly baseline, and status label added to each daily stats card
- **Training Readiness** — 0–100 readiness score and level added to each daily stats card
- **Training Status** — rolling training load label shown in the sidebar header
- **Body Composition** — weight, body fat %, and muscle mass from a smart scale (toggle individual metrics)

Changes take effect immediately — the app re-fetches your data and rebuilds the coach's context after saving.

Preferences are stored in `settings.json` in the project directory (excluded from Git).

---

## Claude Skills & Personas

Type `/` in the chat input to open the skill picker. Two types are supported:

- **Prompt skills** (`.json`) — expand a pre-written prompt into the textarea for review and editing before sending. Good for structured analyses like weekly reports or training plan requests.
- **Personas** (`.skill`) — overlay the coach's system prompt with a coaching persona, shifting Claude's style and focus for the entire conversation. A chip in the input row shows the active persona; click × to deactivate.

Upload skills and personas from **Settings → Skills & Personas** (drag-and-drop or browse). Installed skills appear in the list immediately.

---

## Project structure

```
garmin-health-coach/
├── server.py               # Web entry point — FastAPI app, all routes
├── main.py                 # CLI entry point — terminal chat loop
├── garmin_client.py        # Garmin Connect auth, data fetching, formatting
├── claude_client.py        # Claude API wrapper with streaming, history, personas
├── credentials_manager.py  # OS keychain read/write via the keyring library
├── settings_manager.py     # Data sync preferences stored in settings.json
├── skills_manager.py       # Loads prompt skills (.json) and personas (.skill)
├── setup_ui.py             # Interactive credential setup wizard (rich + getpass)
├── templates/
│   ├── index.html          # Main chat UI — split panel with sidebar + chat
│   └── settings.html       # Settings page — credentials, data prefs, skills
├── static/
│   ├── style.css           # App styles
│   └── app.js              # SSE streaming consumer, chat logic, skill picker
├── skills/                 # Prompt skill JSON files
├── .claude/                # Persona .skill files (Claude ZIP format)
├── requirements.txt
├── .env.example            # Reference for environment variable names (optional fallback)
├── .gitignore
└── ROADMAP.md
```

### Credential loading priority

```
OS keychain  →  environment variables  →  .env file
```

This means you can also pass credentials as environment variables (useful for CI or scripting) and the app will pick them up without running the wizard.

---

## Two-factor authentication (2FA)

If your Garmin account has 2FA enabled, you will be prompted to enter your code during the first login. After that, the saved OAuth tokens are reused and 2FA is not asked again until the session expires.

---

## Troubleshooting

**401 Unauthorized from Garmin**
Your email or password is incorrect. Go to Settings in the web UI (or run `python main.py --setup`) to re-enter them. The password field asks for confirmation to prevent typos.

**Anthropic API errors**
Check that your API key is valid and has available credits at [console.anthropic.com](https://console.anthropic.com).

**Missing data fields**
Not all Garmin devices record all metrics. Fields that are unavailable show as `[no data]` in the sidebar and are omitted from Claude's context — they don't cause errors. Body composition requires a Garmin-compatible smart scale.

**Garmin session expired**
The `.garth_session/` folder holds OAuth tokens that can expire. If login fails despite correct credentials, delete that folder and run the app again to trigger a fresh login.

```bash
# Windows PowerShell
Remove-Item -Recurse -Force .garth_session
```

**Port 8000 already in use**
Another process is using port 8000. Either stop that process or edit `server.py` to use a different port (`port=8001`).

---

## What's coming

See [ROADMAP.md](ROADMAP.md) for the full list. The main planned additions are:

**HR time-in-zones** — Zone breakdown per activity (Z1–Z5 minutes) so Claude can analyse aerobic vs. anaerobic distribution and flag if easy runs are drifting out of Zone 2.

**Race predictions & Cycling FTP** — Garmin's estimated race times (5k–marathon) and functional threshold power for deeper performance context.

**MacroFactor nutrition integration** — Connect your food log data so Claude can coach across the full picture: training, recovery, and fueling. MacroFactor supports CSV export which the app will be able to read.

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
| `rich` | Terminal formatting for the CLI setup wizard |
| `python-dotenv` | Optional `.env` file fallback |
