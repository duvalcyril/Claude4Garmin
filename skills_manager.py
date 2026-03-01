"""skills_manager.py — Load coaching skill prompts and personas.

Two skill types are supported:

  Prompt skills  — JSON files in skills/
    trigger, description, prompt
    Expand into the chat input textarea when selected.

  Persona skills — .skill files in .claude/
    ZIP archives containing a SKILL.md with YAML frontmatter.
    Activate a coaching overlay that modifies the system prompt.

Type / in the chat input to browse and invoke either type.
"""

import json
import zipfile
from pathlib import Path

SKILLS_DIR = Path("skills")
CLAUDE_DIR  = Path(".claude")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-style --- frontmatter from markdown. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end == -1:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


def _load_skill_file(path: Path) -> dict | None:
    """Load a .skill file (ZIP containing SKILL.md). Returns a persona skill dict or None."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            entry = next((n for n in z.namelist() if n.endswith("SKILL.md")), None)
            if not entry:
                return None
            raw = z.read(entry).decode("utf-8")
        meta, body = _parse_frontmatter(raw)
        trigger = meta.get("name") or path.stem
        description = meta.get("description", "")
        # Keep description short enough for the picker UI
        if len(description) > 120:
            description = description[:117] + "..."
        return {
            "trigger": trigger,
            "description": description,
            "content": raw,   # full SKILL.md text used as system-prompt overlay
            "type": "persona",
        }
    except Exception:
        return None


def load_skills() -> list[dict]:
    """Return all valid skill definitions, prompt skills first then persona skills."""
    skills = []

    # Prompt skills from skills/ directory
    if SKILLS_DIR.exists():
        for path in sorted(SKILLS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "trigger" in data and "prompt" in data:
                    skills.append({
                        "trigger": data["trigger"],
                        "description": data.get("description", ""),
                        "prompt": data["prompt"].strip(),
                        "type": "prompt",
                    })
            except Exception:
                pass

    # Persona skills from .claude/ directory
    if CLAUDE_DIR.exists():
        for path in sorted(CLAUDE_DIR.glob("*.skill")):
            skill = _load_skill_file(path)
            if skill:
                skills.append(skill)

    return skills


def get_skill_by_trigger(trigger: str) -> dict | None:
    """Return a single skill dict by trigger name, or None if not found."""
    for skill in load_skills():
        if skill["trigger"] == trigger:
            return skill
    return None
