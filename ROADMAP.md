# Garmin Health Coach — Roadmap

A collection of planned improvements, in no particular order.

---

## ✓ Completed

**Web UI** — FastAPI + SSE streaming chat, split-panel layout with structured health data sidebar, auto-opens browser on startup.

**Settings page** — Browser-based credential management (Garmin + Anthropic). No need to touch any files.

**Configurable data import** — Time range (7 / 14 / 30 days), category toggles (daily stats, sleep, activities), and per-metric toggles. Preferences saved in `settings.json`.

---

## Claude Skills integration

Add support for pre-built, slash-command-style coaching skills that can be invoked directly in the chat interface.

**What this means:**
- Slash commands like `/weekly-report`, `/training-plan`, `/sleep-analysis`, `/recovery-check` that trigger structured, deep-dive analyses
- Each skill is a reusable prompt template with optional parameters, assembled automatically with the right context before being sent to Claude
- Skills can be defined locally (user-authored) or loaded from a shared library

**Why it's valuable:**
- Surfaces insights the user might not think to ask for explicitly
- Makes repeated analyses (e.g. weekly check-ins) one-click instead of re-typing the same prompt
- Opens the door to more structured outputs (e.g. a formatted training plan rather than a freeform chat response)

**Implementation sketch:**
- Skills defined as YAML or JSON files with a trigger, description, and prompt template
- Chat input intercepts `/command` and resolves it to the full skill prompt before sending
- A skill picker UI (e.g. `/` menu) surfaces available skills in the chat input
- Could leverage the Anthropic SDK's tool use or structured outputs for richer responses

---

## MacroFactor food log integration

Add nutrition context alongside activity and recovery data so Claude can give advice on the full picture (fueling, deficits, protein targets, etc.).

MacroFactor doesn't have a public API, but offers a **CSV data export**. Approach:
- Add a `macrofactor_client.py` that reads and parses the exported CSV
- Pull in recent days of food log entries (calories, macros, food names)
- Append a nutrition summary section to the data sent to Claude

Things to figure out:
- Export format (MacroFactor exports diary as CSV with date, meal, food, macros)
- How often the user re-exports (manual step vs. watching a folder for new files)
- Whether to store the file path in the credential manager or `settings.json`

---

## Parking lot

- **Daily digest mode**: run on a schedule (e.g. Task Scheduler / cron), generate a
  morning summary, and send it via email or desktop notification
- **Trend alerts**: flag when a metric crosses a threshold (e.g. resting HR up 5+ bpm
  for 3 days, sleep score below 60 two nights in a row)
- **HRV support**: pull HRV data if the device supports it — strong signal for recovery
- **Conversation export**: save chat sessions to a markdown or PDF file
- **Multiple Garmin accounts**: support family/coach use cases
- **Custom date range**: freeform date picker in addition to the preset 7/14/30 day options
