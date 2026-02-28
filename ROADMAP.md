# Garmin Health Coach — Roadmap

A collection of planned improvements, in no particular order.

---

## UI: Move from CLI to a proper interface

The current CLI works but is friction-heavy for daily use. Two realistic options:

**Desktop app** (e.g. Tkinter, PyQt, or Tauri + Python backend)
- Ships as a standalone executable, no browser needed
- Better for a local/private tool that talks to personal health data
- Can live in the system tray and show a quick summary on demand

**Web app** (e.g. FastAPI backend + React or plain HTML frontend)
- Easier to style and extend
- Accessible from any device on your network
- Chat UI can be rendered as a proper message thread (like ChatGPT)
- More work to set up securely if exposed outside localhost

Leaning toward web app for the chat experience, desktop app if portability/privacy matters more. Decide when ready to build.

---

## Data source: MacroFactor food log integration

Add nutrition context alongside activity and recovery data so Claude can give advice on the full picture (fueling, deficits, protein targets, etc.).

MacroFactor doesn't have a public API, but offers a **CSV data export**. Approach:
- Add a `macrofactor_client.py` that reads and parses the exported CSV
- Pull in recent days of food log entries (calories, macros, food names)
- Append a nutrition summary section to the data sent to Claude

Things to figure out:
- Export format (MacroFactor exports diary as CSV with date, meal, food, macros)
- How often the user re-exports (manual step vs. watching a folder for new files)
- Whether to store the file path in the credential manager or a config file

---

## Configurable data import (Garmin scope + time range)

Right now the app always fetches a fixed 7-day window of all metrics. Make this user-configurable:

**Time range**
- Let the user choose: 7 days, 14 days, 30 days, or a custom date range
- Store preference so it persists between sessions

**Metric selection**
- Let the user toggle which data types are fetched: steps, sleep, HRV, stress, body battery, activities, resting HR
- Useful for devices that don't support all metrics (avoids empty/error fields)
- Could be presented as a checklist in the setup wizard or a config file

**Implementation sketch**
- Add a `config.json` (or extend the keychain approach) to store user preferences
- Pass the selected metrics and date range into `fetch_health_data()` as parameters
- The formatter already handles None values gracefully, so skipping a metric is low-risk

---

## Other ideas (parking lot)

- **Daily digest mode**: run on a schedule (e.g. Task Scheduler / cron), generate a
  morning summary, and send it via email or desktop notification
- **Trend alerts**: flag when a metric crosses a threshold (e.g. resting HR up 5+ bpm
  for 3 days, sleep score below 60 two nights in a row)
- **HRV support**: pull HRV data if the device supports it — strong signal for recovery
- **Conversation export**: save chat sessions to a markdown or PDF file
- **Multiple Garmin accounts**: support family/coach use cases
