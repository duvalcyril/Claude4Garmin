# Garmin Health Coach — Roadmap

A collection of planned improvements, in no particular order.

---

## ✓ Completed

**Web UI** — FastAPI + SSE streaming chat, split-panel layout with structured health data sidebar, auto-opens browser on startup.

**Settings page** — Browser-based credential management (Garmin + Anthropic). No need to touch any files.

**Configurable data import** — Time range (7 / 14 / 30 days), category toggles (daily stats, sleep, activities, recovery, body composition), and per-metric toggles. Preferences saved in `settings.json`.

**Claude Skills** — `/` picker in chat input for pre-built prompt skills (`.json`) and coaching personas (`.skill` files). Upload via Settings. Personas overlay the system prompt; prompt skills expand into the textarea.

**HRV (Heart Rate Variability)** — Overnight HRV average and weekly baseline fetched per day. Status label (Balanced / Low / Unbalanced) shown as a colour-coded chip alongside resting HR in each daily stats card and included in the health summary.

**Training Readiness** — Garmin's 0–100 composite readiness score (from HRV, sleep, recovery time, and training load) fetched per day. Score and level chip shown in each daily stats card and included in the health summary.

**Training Status** — Rolling label (Productive, Peaking, Maintaining, Recovery, Unproductive, Strained, Detraining) shown as a colour-coded badge in the sidebar header and included in the health summary.

**Body Composition** — Weight, body fat %, and muscle mass from a Garmin-compatible smart scale. Shown as a dedicated sidebar section (3 most recent readings). Category toggle and per-metric toggles in Data Preferences.

**Daily Digest** — Morning email with yesterday's stats, sleep, HRV, readiness, training status, and a Claude-generated coaching recommendation. Sent via Gmail SMTP (app password). Scheduled via Windows Task Scheduler from the Settings page. Configurable recipient and send time. Test-send button for instant verification. All email errors logged to `digest.log`.

**MacroFactor nutrition integration** — MacroFactor daily-summary CSV import. Parses calories, macros, TDEE, trend weight, steps, and target macros. Displayed in a dedicated Nutrition sidebar tab. Independently toggleable in Claude's AI context (daily totals and/or full food log). Import via Settings → Nutrition.

---

## Expanded Garmin data (remaining)

Data streams confirmed as relevant but not yet implemented.

### Performance metrics

**HR time-in-zones per activity** — `get_activity_hr_in_timezones(activity_id)`
For each fetched activity, pull how many minutes were spent in each HR zone (Z1–Z5). Lets Claude analyse aerobic vs anaerobic distribution across the week and flag if running stays in Zone 2 as intended.
- Requires a second API call per activity (add after initial activity list fetch)
- Display as a compact zone bar in the activity card (sidebar)
- Include zone breakdown in the health summary for each activity

**Race predictions** — `get_race_predictions()`
Garmin's model-based estimates for 5k, 10k, half marathon, and marathon. Useful as a milestone tracker as running fitness rebuilds — Claude can reference trend direction even without precise numbers.
- Fetch once (not per-day); add as a "Performance" section in the sidebar
- Include in health summary: estimated times and trend vs. previous fetch

**Cycling FTP** — `get_cycling_ftp()`
Functional Threshold Power in watts. Relevant for ride intensity context if a power meter is in use. Lets Claude interpret ride effort more precisely than HR alone.
- Fetch once; include as a static field in the sidebar and health summary
- Only display if a non-null value is returned

### Additional body metrics

**Overnight respiration rate** — `get_respiration_data(cdate)`
Average breaths per minute during sleep. Elevated respiration often correlates with illness, overtraining, or high stress before other metrics catch it.
- Add alongside sleep metrics in the sidebar
- Add a `metric_respiration` toggle to Data Preferences

---

## Multi-model AI support

Currently hard-wired to Claude via the Anthropic SDK. The goal is a pluggable model layer so users can bring their own API key for any supported provider.

### Target models
- **OpenAI** — GPT-4o, GPT-4o-mini (chat completions API, SSE streaming)
- **Google Gemini** — Gemini 1.5 Pro / Flash (Google AI Studio key or Vertex AI)
- **Ollama** — local models (Llama 3, Mistral, etc.) with no API key required; useful for full offline / privacy-first setups

### Design approach
- Extract `ClaudeCoach` into a base `Coach` interface with `chat_stream_async()` and `reset_history()` methods
- Add `OpenAICoach`, `GeminiCoach`, and `OllamaCoach` implementations in a `coaches/` module
- Settings page: "AI Model" section with a provider dropdown and API key field (stored in keychain per provider)
- `settings.json` stores the active provider and model name; `server.py` instantiates the right coach on startup / after settings change
- System prompt and health summary injection stays identical across all providers — only the SDK call differs

### Things to figure out
- Streaming response format differs between providers (OpenAI delta chunks, Gemini candidates, Ollama line-delimited JSON) — needs a normalised async iterator
- Token / context limits vary; the health summary can be long — may need truncation logic per provider
- Ollama requires the user to have it installed and a model pulled locally; needs graceful error messaging if the server isn't running

---

## Apple Watch / Apple Health support

Apple provides no public web API for Health data (HealthKit is sandboxed to on-device iOS/macOS apps), so integration requires one of these approaches:

**Option A — XML export (lowest friction)**
User exports from iPhone: *Settings → Health → Export All Health Data* → uploads the `.zip` on the Settings page. The app parses `export.xml` and normalises Apple Health fields into the existing `health_data` schema.
- Covers all Apple Watch metrics (steps, heart rate, HRV, sleep via Sleep app or AutoSleep, etc.)
- Manual re-export needed for fresh data — best suited for periodic review rather than daily coaching

**Option B — Health Auto Export push**
Third-party iOS app (Health Auto Export, ~$4) auto-pushes HealthKit data on a schedule to a configurable webhook. Add a `/api/apple-health` POST endpoint that receives and stores the payload.
- Near-real-time data without manual steps once configured
- Requires the iOS app and the server to be reachable from the phone (local network or ngrok)

**Option C — iPhone Shortcuts**
Native Shortcuts automation reads HealthKit and POSTs JSON to a local URL. Free but requires manual setup per metric and has limited scheduling flexibility.

Things to figure out:
- Whether to support Garmin + Apple Watch simultaneously or as mutually exclusive sources
- How to normalise divergent metric names (Apple's `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` vs Garmin's `lastNight5MinHighRmssd`, etc.)
- Sleep stage mapping (Apple Health stages vs Garmin stages)

---

## Custom workout creation and upload

Generate personalised running and cycling workouts from Claude based on your current fitness level, training status, and goals — then push them directly to Garmin Connect so they appear on your device ready to follow.

### What it would do
- Ask Claude to create a structured workout (e.g. "build me a Z2 long run for Sunday" or "give me a tempo session based on my current readiness")
- Claude returns a workout definition: steps, target HR zones or pace ranges, duration/distance per step
- The app converts this to a Garmin workout structure and uploads it via the Garmin Connect API
- The workout appears in Garmin Connect and syncs to your device

### Things to figure out
- Garmin Connect workout upload API (`post_workout()` in garminconnect) and the required JSON schema for run vs. bike workouts
- Prompt design to get Claude to reliably output structured workout data (JSON schema vs. natural language + parser)
- How to handle pace/power targets vs. HR zone targets depending on what the user's device supports
- UI: dedicated "Create Workout" button or chat command like `/workout`

---

## Parking lot

- **Trend alerts**: flag when a metric crosses a threshold (e.g. resting HR up 5+ bpm
  for 3 days, sleep score below 60 two nights in a row)
- **Conversation export**: save chat sessions to a markdown or PDF file
- **Multiple Garmin accounts**: support family/coach use cases
- **Custom date range**: freeform date picker in addition to the preset 7/14/30 day options
