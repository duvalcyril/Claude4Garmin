"""memory_manager.py — Persistent coach memory across conversations.

Extracts key facts (PRs, injuries, goals, training decisions) from conversation
history using Claude Haiku and persists them to coach_memory.json. Notes are
injected into the system prompt on every session so memory accumulates over time.

Extraction is incremental: only turns since the last extraction are processed.
The last processed turn is remembered by a content fingerprint rather than a
list index, because ClaudeCoach archives old turns (and /api/reset clears them),
which shifts every index and would otherwise stall extraction forever.
Extraction is capped at EXTRACTION_MAX_NEW_TURNS to bound API cost.
The extraction model is claude-haiku for speed and cost efficiency.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic

from .paths import user_data_dir

MEMORY_FILE = user_data_dir() / "coach_memory.json"
EXTRACTION_MODEL = "claude-haiku-5-5"
EXTRACTION_MAX_TOKENS = 1024
EXTRACTION_INTERVAL = 10       # extract when ≥10 new turns since last extraction
EXTRACTION_MAX_NEW_TURNS = 40  # cap to bound token cost per extraction call

EXTRACTION_PROMPT = (
    "You are a memory assistant for a personal health and fitness coaching app.\n\n"
    "Below is a segment of recent conversation between a user and their AI health coach. "
    "Extract ONLY durable, factual information the coach should remember permanently across future sessions. "
    "Focus on:\n"
    "- Personal records / PRs (with dates and values if mentioned)\n"
    "- Injuries or physical limitations (resolved or ongoing)\n"
    "- Explicit training decisions or significant plan changes\n"
    "- Goals set or changed (with target dates/values if given)\n"
    "- Health events or milestones worth noting\n"
    "- Upcoming events or races mentioned\n\n"
    "Rules:\n"
    "- One sentence per note, start each line with \"- \"\n"
    "- Use third person (\"User ran...\", \"User has...\")\n"
    "- Skip questions, generic advice, or temporary/daily state\n"
    "- Do NOT repeat facts already in the existing notes\n"
    "- If nothing noteworthy: return exactly NO_NEW_FACTS\n\n"
    "Existing notes (do NOT repeat these):\n"
    "{existing_notes}\n\n"
    "Conversation segment to process:\n"
    "{conversation_segment}\n\n"
    "Return ONLY the new facts, one per line starting with \"- \". "
    "If no new facts found, return: NO_NEW_FACTS"
)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _default_memory() -> dict:
    return {
        "notes": "",
        "last_extracted_from_turn": 0,   # legacy index; superseded by the marker
        "last_extracted_marker": None,   # fingerprint of the last turn processed
        "last_updated": None,
    }


def load_memory() -> dict:
    """Load coach memory from disk, or return empty defaults."""
    if not MEMORY_FILE.exists():
        return _default_memory()
    try:
        raw = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        return {**_default_memory(), **raw}
    except Exception:
        return _default_memory()


def save_memory(memory: dict) -> None:
    """Persist coach memory to disk. Non-fatal on failure."""
    try:
        MEMORY_FILE.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_memory_for_prompt(memory: dict) -> str:
    """
    Return a formatted block for injection into the system prompt.
    Returns empty string if no notes exist.
    """
    notes = (memory.get("notes") or "").strip()
    if not notes:
        return ""
    last_updated = memory.get("last_updated") or "unknown"
    return (
        f"=== COACH MEMORY (accumulated from past conversations) ===\n"
        f"Last updated: {last_updated}\n\n"
        f"{notes}\n"
    )


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------

def _turn_fingerprint(turn: dict) -> str:
    """Stable short hash identifying a history turn by role + content."""
    raw = f"{turn.get('role', '')}|{turn.get('content') or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _unprocessed_turns(history: list, memory: dict) -> list:
    """
    Return the turns added since the last extraction pass.

    Locates the last processed turn by fingerprint (searching from the end).
    If it is no longer in the active window — archived away or history reset —
    every turn currently in the window is new. Memories written before the
    marker existed fall back to the legacy index, treating an out-of-range
    index (the archiving bug) as "everything is new".
    """
    marker = memory.get("last_extracted_marker")
    if marker:
        for i in range(len(history) - 1, -1, -1):
            if _turn_fingerprint(history[i]) == marker:
                return history[i + 1:]
        return list(history)

    last_turn = memory.get("last_extracted_from_turn", 0) or 0
    if 0 <= last_turn <= len(history):
        return history[last_turn:]
    return list(history)


def should_extract(history: list, memory: dict) -> bool:
    """
    Return True if enough new conversation turns have accumulated
    to warrant running an extraction pass.
    """
    return len(_unprocessed_turns(history, memory)) >= EXTRACTION_INTERVAL


def extract_memory(history: list, memory: dict) -> dict:
    """
    Call Claude Haiku to extract new facts from conversation history since
    the last extraction pass.

    Args:
        history: Full conversation history (list of {role, content} dicts).
        memory:  Current memory dict (loaded from disk).

    Returns:
        Updated memory dict (not yet saved — caller must call save_memory()).
    """
    new_turns = _unprocessed_turns(history, memory)

    # Cap to prevent runaway cost on very long catch-up extractions
    if len(new_turns) > EXTRACTION_MAX_NEW_TURNS:
        new_turns = new_turns[-EXTRACTION_MAX_NEW_TURNS:]

    if not new_turns:
        return dict(memory)

    # Format conversation segment as readable plain text
    segment_lines = []
    for turn in new_turns:
        role = "User" if turn.get("role") == "user" else "Coach"
        content = turn.get("content") or ""
        # Truncate very long coach responses to keep tokens reasonable
        if len(content) > 800:
            content = content[:800] + "...[truncated]"
        segment_lines.append(f"{role}: {content}")
    segment_text = "\n\n".join(segment_lines)

    existing_notes = (memory.get("notes") or "").strip() or "(none yet)"

    prompt = EXTRACTION_PROMPT.format(
        existing_notes=existing_notes,
        conversation_segment=segment_text,
    )

    client = Anthropic()
    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=EXTRACTION_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    result_text = (response.content[0].text or "").strip()

    updated_memory = dict(memory)
    updated_memory["last_extracted_from_turn"] = len(history)
    updated_memory["last_extracted_marker"]    = _turn_fingerprint(history[-1])
    updated_memory["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    if result_text and result_text != "NO_NEW_FACTS":
        new_notes = "\n".join(
            line for line in result_text.splitlines()
            if line.strip().startswith("- ")
        )
        if new_notes:
            existing = (memory.get("notes") or "").strip()
            updated_memory["notes"] = (existing + "\n" + new_notes).strip() if existing else new_notes

    return updated_memory
