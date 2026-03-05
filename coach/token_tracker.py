"""token_tracker.py — Track and persist API token usage over time.

Records input/output/cache tokens from each API call to a JSON file.
Provides aggregated views (today, 7 days, 30 days, all time) for the
frontend usage monitor.
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path

from .paths import user_data_dir

USAGE_FILE = user_data_dir() / "token_usage.json"
MAX_RECORDS = 5000  # cap to prevent unbounded growth


def _load_records() -> list[dict]:
    if not USAGE_FILE.exists():
        return []
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_records(records: list[dict]) -> None:
    # Trim oldest if over cap
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]
    try:
        USAGE_FILE.write_text(
            json.dumps(records, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def record_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    model: str = "",
) -> None:
    """Append a usage record for a single API call."""
    records = _load_records()
    records.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "input": input_tokens,
        "output": output_tokens,
        "cache_create": cache_creation_tokens,
        "cache_read": cache_read_tokens,
        "model": model,
    })
    _save_records(records)


def get_usage_summary() -> dict:
    """Return aggregated token usage for display."""
    records = _load_records()
    today = date.today()

    def _sum_period(start_date: date) -> dict:
        totals = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "calls": 0}
        start_str = start_date.isoformat()
        for r in records:
            if r["ts"][:10] >= start_str:
                totals["input"] += r.get("input", 0)
                totals["output"] += r.get("output", 0)
                totals["cache_create"] += r.get("cache_create", 0)
                totals["cache_read"] += r.get("cache_read", 0)
                totals["calls"] += 1
        # Estimate cost savings: cache reads cost 90% less than regular input
        totals["cache_savings_tokens"] = int(totals["cache_read"] * 0.9)
        return totals

    return {
        "today": _sum_period(today),
        "week": _sum_period(today - timedelta(days=7)),
        "month": _sum_period(today - timedelta(days=30)),
        "all_time": _sum_period(date(2000, 1, 1)),
        "total_records": len(records),
    }
