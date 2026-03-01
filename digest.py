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

import credentials_manager as cm
import settings_manager as sm
from garmin_client import get_garmin_client, fetch_health_data, format_health_summary
from claude_client import ClaudeCoach
from paths import bundle_dir, user_data_dir

# Log to the user data directory so it's writable in both dev and packaged modes
logging.basicConfig(
    filename=user_data_dir() / "digest.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


DIGEST_PROMPT = (
    "Based on my health data from yesterday, write a single encouraging paragraph "
    "(3-5 sentences) summarising how I did and what I should focus on today. "
    "Be specific about the numbers. Keep it motivating but honest."
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

def build_template_vars(raw: dict, coach_text: str, target_date: date) -> dict:
    """
    Extract display-ready values from the raw health_data dict.

    fetch_health_data() is called with days_back=2, so each list contains:
      index 0 — today (partial data, probably empty for most metrics)
      index 1 — yesterday (the data we want)
    Fall back to index 0 if index 1 doesn't exist (edge case: first ever run).
    """
    def _pick(lst: list) -> dict:
        if not lst:
            return {}
        return lst[1] if len(lst) > 1 else lst[0]

    stats = _pick(raw.get("daily_stats", []))
    sleep = _pick(raw.get("sleep", []))
    hrv   = _pick(raw.get("hrv", []))
    rdy   = _pick(raw.get("training_readiness", []))
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
    }


# ---------------------------------------------------------------------------
# Email rendering and sending
# ---------------------------------------------------------------------------

def render_email_html(template_vars: dict) -> str:
    """Render digest_email.html via Jinja2 (already installed as a FastAPI dep)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(bundle_dir() / "templates")))
    return env.get_template("digest_email.html").render(**template_vars)


def send_email(
    html: str,
    subject: str,
    sender: str,
    app_password: str,
    recipient: str,
) -> None:
    """Send an HTML email via Gmail SMTP SSL (port 465)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
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
    summary = format_health_summary(raw, digest_settings)

    recommendation = ClaudeCoach(health_summary=summary).chat(DIGEST_PROMPT)
    template_vars  = build_template_vars(raw, recommendation, target_date)
    html_body      = render_email_html(template_vars)

    subject = f"Your Health Digest \u2014 {target_date.strftime('%b %#d')}"
    send_email(html_body, subject, sender, app_password, recipient)
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
