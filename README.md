# Garmin Health Coach

A personal AI health coach that pulls your real Garmin data and lets you have a conversation with Claude about it. Ask about your sleep trends, recovery, training load, stress patterns — and get answers grounded in your actual numbers, not generic advice.

---

## What it does

On startup, the app connects to your Garmin Connect account, fetches the last 7 days of health data, and displays a summary. It then opens a chat session where Claude acts as your personal coach with full context of your metrics.

**Data fetched from Garmin Connect:**
- Daily steps, total and active calories
- Average and max stress levels
- Body battery (most recent value per day)
- Resting heart rate
- Sleep breakdown — total, deep, REM, light, awake time, and sleep score
- Last 10 activities — type, duration, distance, average and max HR, calories

**What you can ask:**
- *"How has my sleep been this week?"*
- *"My stress has been high — what do you recommend?"*
- *"Am I recovering well enough to train hard tomorrow?"*
- *"Which day this week had my best body battery?"*
- *"Compare my activity level to my sleep quality."*

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
python main.py
```

On first launch, a setup wizard will appear and ask for your Garmin email, password (entered twice to catch typos), and Anthropic API key. All three are saved to your OS keychain — you won't be asked again on future runs.

---

## Usage

```bash
python main.py                    # Normal run — fetch data and start coaching
python main.py --setup            # Update or rotate credentials
python main.py --status           # Check which credentials are stored
python main.py --clear-credentials  # Remove all stored credentials
```

**In-session commands:**

| Type | Effect |
|---|---|
| Any question | Get a coaching response from Claude |
| `reset` | Clear conversation history (Garmin data context is kept) |
| `quit` / `exit` | Exit the app |
| `Ctrl+C` | Exit the app |

---

## Project structure

```
garmin-health-coach/
├── main.py                 # Entry point — orchestrates startup and chat loop
├── garmin_client.py        # Garmin Connect auth, data fetching, and formatting
├── claude_client.py        # Claude API wrapper with conversation history
├── credentials_manager.py  # OS keychain read/write via the keyring library
├── setup_ui.py             # Interactive credential setup wizard (rich + getpass)
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
Your email or password is incorrect. Run `python main.py --setup` to re-enter them. The password field asks for confirmation to prevent typos.

**Anthropic API errors**
Check that your API key is valid and has available credits at [console.anthropic.com](https://console.anthropic.com).

**Missing data fields**
Not all Garmin devices record all metrics. Fields that are unavailable show as `[no data]` and are simply omitted from Claude's context — they don't cause errors.

**Garmin session expired**
The `.garth_session/` folder holds OAuth tokens that can expire. If login fails despite correct credentials, delete that folder and run the app again to trigger a fresh login.

```bash
# Windows PowerShell
Remove-Item -Recurse -Force .garth_session
```

---

## What's coming

See [ROADMAP.md](ROADMAP.md) for the full list. The main planned additions are:

**Proper UI** — The CLI is functional but not ideal for daily use. The plan is to move to either a desktop app or a local web app with a proper chat interface.

**MacroFactor nutrition integration** — Connect your food log data so Claude can coach across the full picture: training, recovery, and fueling. MacroFactor doesn't have a public API, but supports CSV export which the app will be able to read.

**Configurable data scope** — Choose which Garmin metrics to import and over what time range (7, 14, 30 days, or custom). Useful for focusing on specific aspects of health or for devices that don't support every metric.

---

## Dependencies

| Package | Purpose |
|---|---|
| `garminconnect` | Garmin Connect API client |
| `anthropic` | Claude API client |
| `keyring` | Secure OS keychain storage |
| `rich` | Terminal formatting for the setup wizard |
| `python-dotenv` | Optional `.env` file fallback |
