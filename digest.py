"""digest.py — Standalone daily health digest emailer.

Fetches yesterday's Garmin data, asks Claude for a one-paragraph coaching
recommendation, renders an HTML email, and sends it via Gmail SMTP.

Designed to be run by Windows Task Scheduler every morning. Also importable
by server.py so the "Send Test" button can invoke run_digest() directly
without spawning a subprocess.

Usage:
    python digest.py                  # sends for yesterday (respects digest_enabled)
    python digest.py --date 2026-03-01  # specific date, bypasses enabled check
"""

import argparse
import json as _json
import logging
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Ensure project root is on sys.path when run directly by Task Scheduler
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

import coach.credentials_manager as cm
import coach.settings_manager as sm
from coach.garmin_client import get_garmin_client, fetch_health_data, format_health_summary
from coach.claude_client import ClaudeCoach
from coach.nutrition_parser import load_nutrition
from coach.paths import bundle_dir, user_data_dir

# Log to the user data directory so it's writable in both dev and packaged modes
logging.basicConfig(
    filename=user_data_dir() / "digest.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


DIGEST_PROMPT = (
    "Based on my health data from yesterday, respond with ONLY a JSON object — no markdown, no extra text. "
    "The JSON must have exactly three string fields:\n\n"
    '  "recommendation": a personalised coaching note structured as 2–3 short paragraphs separated by a '
    "blank line (\\n\\n). Keep each paragraph to 2–3 sentences max.\n"
    "  Paragraph 1: highlight 1–2 key metrics from yesterday (HRV, readiness, sleep, steps, etc.) and what "
    "they imply — not just the numbers, but what they mean for how I should train or recover.\n"
    "  Paragraph 2: note any trend or pattern worth paying attention to (positive or concerning).\n"
    "  Paragraph 3: one concrete, actionable suggestion for today — specific and direct, no wellness clichés. "
    "If a workout is planned for today, name it and give a specific tip or encouragement for it "
    "(e.g. target zone, pacing strategy, what to focus on). "
    "If today is a rest or recovery day, say so clearly and explain why it matters given yesterday's data.\n\n"
    '  "quote": a short motivational quote (one or two sentences) relevant to sport, resilience, health, or '
    "performance. Choose something that fits my current situation — e.g. recovery or patience-themed if "
    "readiness is low or I'm strained; ambition or execution-themed if I'm in a productive or peaking phase. "
    "Vary the source: athletes, coaches, philosophers, writers — not always the same names.\n\n"
    '  "quote_author": the person the quote is attributed to (name only, no dates or titles).\n\n'
    'Example format: {"recommendation": "Paragraph one.\\n\\nParagraph two.\\n\\nParagraph three.", "quote": "...", "quote_author": "..."}'
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _hm(sec) -> str:
    """Seconds → '7h 22m', or '—' if missing."""
    if not sec:
        return "\u2014"
    return f"{int(sec) // 3600}h {(int(sec) % 3600) // 60}m"


def _val(v, suffix: str = "") -> str:
    """Value with optional suffix, or em-dash if None."""
    return f"{v}{suffix}" if v is not None else "\u2014"


# ---------------------------------------------------------------------------
# Template variable builder
# ---------------------------------------------------------------------------

def build_template_vars(raw: dict, coach_text: str, target_date: date, quote: str = "", quote_author: str = "") -> dict:
    """
    Extract display-ready values from the raw health_data dict.

    fetch_health_data() is called with days_back=2, so each list contains:
      index 0 — today
      index 1 — yesterday
    Fall back to index 0 if index 1 doesn't exist (edge case: first ever run).

    Daytime metrics (steps, calories, etc.) use yesterday (index 1) because
    today's accumulation is incomplete when the digest runs in the morning.

    Overnight metrics (sleep, HRV, readiness) use today (index 0) because
    Garmin records them under the wake-up date — e.g. Sunday-night sleep is
    stored under Monday's date, not Sunday's.
    """
    def _pick_yesterday(lst: list) -> dict:
        """Yesterday's data (index 1) — for daytime metrics like steps/calories."""
        if not lst:
            return {}
        return lst[1] if len(lst) > 1 else lst[0]

    def _pick_today(lst: list) -> dict:
        """Today's data (index 0) — for overnight metrics that end in the morning."""
        if not lst:
            return {}
        return lst[0]

    stats = _pick_yesterday(raw.get("daily_stats", []))
    sleep = _pick_today(raw.get("sleep", []))
    hrv   = _pick_today(raw.get("hrv", []))
    rdy   = _pick_today(raw.get("training_readiness", []))
    ts    = raw.get("training_status") or {}

    return {
        # %#d = day without leading zero on Windows (equivalent to Linux %-d)
        "date_label": target_date.strftime("%A, %B %#d"),
        "stat_metrics": [
            {
                "label": "Steps",
                "value": f"{stats['steps']:,}" if stats.get("steps") else "\u2014",
            },
            {
                "label": "Calories",
                "value": _val(stats.get("calories_total"), "\u202fkcal"),
            },
            {
                "label": "Body Battery",
                "value": _val(stats.get("body_battery"), "%"),
            },
            {
                "label": "Resting HR",
                "value": _val(stats.get("resting_hr"), "\u202fbpm"),
            },
        ],
        "hrv_avg":             _val(hrv.get("last_night_avg"), "\u202fms"),
        "hrv_status":          (hrv.get("status") or "\u2014").title(),
        "readiness_score":     _val(rdy.get("score")),
        "training_status":     ts.get("label", "\u2014"),
        "sleep_total":         _hm(sleep.get("total_seconds")),
        "sleep_deep":          _hm(sleep.get("deep_seconds")),
        "sleep_rem":           _hm(sleep.get("rem_seconds")),
        "sleep_score":         _val(sleep.get("score")),
        "coach_recommendation": coach_text,
        "quote":                quote,
        "quote_author":         quote_author,
    }


# ---------------------------------------------------------------------------
# Email rendering and sending
# ---------------------------------------------------------------------------

def _build_today_context(settings: dict, target_date: date) -> str:
    """
    Build a short addendum for the DIGEST_PROMPT that anchors Claude to today's
    date and any workout the user has planned.

    Returns an empty string if no athlete profile is configured, so the base
    prompt still works well for users who haven't filled in their profile.
    """
    profile = settings.get("athlete_profile") or {}
    today   = target_date + timedelta(days=1)    # digest covers yesterday; today = +1
    day_name = today.strftime("%A")              # e.g. "Tuesday"

    parts = [f"\n\nToday is {day_name}, {today.strftime('%B %#d')}."]

    training_plan = (profile.get("training_plan") or "").strip()
    if training_plan:
        parts.append(
            f" The athlete's training plan is: {training_plan}."
            f" Based on this plan, what is likely scheduled for {day_name}?"
            " If a specific session is planned, reference it explicitly in your recommendation"
            " — name the workout, give encouragement, and adjust your recovery/effort advice accordingly."
            " If no workout is scheduled (rest or easy day), acknowledge that and frame today's advice around recovery."
        )
    else:
        parts.append(
            " Tailor your Paragraph 3 suggestion to whether today looks like a good day to train hard,"
            " recover, or go easy — based on the readiness, HRV, and stress data."
        )

    upcoming = (profile.get("upcoming_events") or "").strip()
    if upcoming:
        parts.append(f" Upcoming events to keep in mind: {upcoming}.")

    return "".join(parts)


def render_email_html(template_vars: dict) -> str:
    """Render digest_email.html via Jinja2 (already installed as a FastAPI dep)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(bundle_dir() / "templates")))
    return env.get_template("digest_email.html").render(**template_vars)


def _html_to_plain(template_vars: dict) -> str:
    """
    Build a minimal plain-text fallback for email clients that prefer text/plain.
    Mirrors the structure of the HTML template without any markup.
    """
    v = template_vars
    lines = [
        f"YOUR HEALTH DIGEST — {v['date_label']}",
        "",
        "YESTERDAY'S STATS",
    ]
    for m in v.get("stat_metrics", []):
        lines.append(f"  {m['label']}: {m['value']}")
    lines += [
        "",
        "RECOVERY",
        f"  HRV: {v['hrv_avg']} ({v['hrv_status']})",
        f"  Readiness: {v['readiness_score']}/100",
        f"  Training Status: {v['training_status']}",
        "",
        "SLEEP",
        f"  Total: {v['sleep_total']}  |  Deep: {v['sleep_deep']}  |  REM: {v['sleep_rem']}  |  Score: {v['sleep_score']}/100",
        "",
        "COACH'S TAKE",
        "",
        v.get("coach_recommendation", ""),
    ]
    if v.get("quote"):
        lines += [
            "",
            f'"{v["quote"]}"',
        ]
        if v.get("quote_author"):
            lines.append(f'  — {v["quote_author"]}')
    lines += ["", "—", "Garmin Health Coach"]
    return "\n".join(lines)


def send_email(
    html: str,
    subject: str,
    sender: str,
    app_password: str,
    recipient: str,
    template_vars: dict | None = None,
) -> None:
    """Send an HTML email via Gmail SMTP SSL (port 465) with a plain-text fallback."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    # Plain-text part first — email clients pick the last compatible part (HTML preferred)
    if template_vars:
        msg.attach(MIMEText(_html_to_plain(template_vars), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, app_password)
        smtp.sendmail(sender, recipient, msg.as_string())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_digest(target_date: date | None = None) -> None:
    """
    Fetch data, generate recommendation, render and send the digest email.

    Args:
        target_date: The date to report on. When None (scheduled run), defaults
                     to yesterday and respects the digest_enabled setting.
                     When provided explicitly (test send from server.py), the
                     enabled check is skipped.
    """
    settings  = sm.load_settings()
    scheduled = target_date is None   # True when called from Task Scheduler

    if scheduled:
        if not settings.get("digest_enabled"):
            log.info("Digest is disabled — skipping.")
            return
        target_date = date.today() - timedelta(days=1)

    recipient = settings.get("digest_recipient", "").strip()
    if not recipient:
        raise ValueError("No recipient email configured in Settings.")

    cm.inject_into_env()
    sender      = cm.load_credential("digest_gmail_sender")
    app_password = cm.load_credential("digest_gmail_app_password")
    if not sender or not app_password:
        raise ValueError("Gmail credentials not set. Configure them in Settings → Daily Digest.")

    log.info("Building digest for %s", target_date)

    garmin = get_garmin_client(
        cm.load_credential("garmin_email"),
        cm.load_credential("garmin_password"),
    )

    # days_back=2: today (index 0) + yesterday (index 1)
    digest_settings = {**settings, "days_back": 2}
    raw     = fetch_health_data(garmin, digest_settings)
    summary = format_health_summary(raw, digest_settings, load_nutrition())

    # Build a "today" context block so Claude can tailor advice to the planned workout
    today_context = _build_today_context(settings, target_date)
    prompt = DIGEST_PROMPT + today_context

    raw_response = ClaudeCoach(health_summary=summary).chat(prompt)

    # Parse structured JSON response; fall back gracefully if Claude doesn't comply
    try:
        parsed         = _json.loads(raw_response)
        recommendation = parsed.get("recommendation", raw_response)
        quote          = parsed.get("quote", "")
        quote_author   = parsed.get("quote_author", "")
    except (_json.JSONDecodeError, AttributeError):
        recommendation = raw_response
        quote          = ""
        quote_author   = ""

    template_vars  = build_template_vars(raw, recommendation, target_date, quote, quote_author)
    html_body      = render_email_html(template_vars)

    subject = f"Your Health Digest \u2014 {target_date.strftime('%b %#d')}"
    send_email(html_body, subject, sender, app_password, recipient, template_vars)
    log.info("Digest sent successfully to %s", recipient)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the Garmin Health Digest email.")
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Date to report on (default: yesterday). Bypasses digest_enabled check.",
    )
    args = parser.parse_args()

    explicit_date = None
    if args.date:
        from datetime import datetime
        explicit_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    try:
        run_digest(target_date=explicit_date)
    except Exception as exc:
        log.exception("Digest failed: %s", exc)
        sys.exit(1)
