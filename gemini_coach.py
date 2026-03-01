"""gemini_coach.py — Google Gemini AI coaching backend (google-genai SDK).

Drop-in replacement for ClaudeCoach. Implements the same public interface:
  chat(), chat_stream(), chat_stream_async(), set_persona(), clear_persona(),
  reset_history(), active_persona property.

Uses the current google-genai package (replaces the deprecated
google-generativeai). Async streaming is natively supported, so
chat_stream_async() yields tokens as they arrive.

History stored separately as chat_history_gemini.json so switching
providers does not corrupt Claude's conversation history.
"""

import json
from pathlib import Path

from google import genai
from google.genai import types

MAX_TOKENS    = 1024
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiCoach:
    """
    Manages a stateful coaching conversation with Google Gemini.

    The health summary is injected via system_instruction on every request;
    conversation history is stored as plain dicts compatible with the
    google-genai contents format.

    If history_file is provided, the conversation is persisted to disk
    and reloaded on the next startup so Gemini remembers previous sessions.
    """

    def __init__(
        self,
        health_summary: str,
        history_file: Path | None = None,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
    ):
        self._client       = genai.Client(api_key=api_key)
        self._model_name   = model
        self._history_file = history_file
        self._health_summary  = health_summary
        self._persona_content: str | None = None
        self.history: list[dict] = self._load_history()
        self._system_prompt = self._build_system_prompt()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        base = (
            "You are a personal health and fitness coach with access to the user's "
            "recent Garmin wearable data.\n\n"
            "Your coaching style:\n"
            "- Be concise and actionable — skip preambles, get to the insight\n"
            "- Reference specific numbers from the data when relevant\n"
            "- Spot trends and patterns across days, not just single-day snapshots\n"
            "- Be encouraging but honest; flag concerning patterns "
            "(e.g., chronic high stress, poor sleep)\n"
            "- When the user asks general questions, ground your answer in their actual data\n\n"
            f"Here is the user's recent Garmin health data:\n\n{self._health_summary}\n\n"
            "Use this data to answer questions and give personalized recommendations."
        )
        if self._persona_content:
            base += "\n\n---\n\n" + self._persona_content
        return base

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            max_output_tokens=MAX_TOKENS,
        )

    def _contents(self, user_message: str) -> list[dict]:
        """Return history + the new user turn as a contents list."""
        return self.history + [{"role": "user", "parts": [{"text": user_message}]}]

    def _load_history(self) -> list[dict]:
        """Load persisted Gemini history from disk, or return an empty list."""
        if self._history_file and self._history_file.exists():
            try:
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
                # Validate Gemini format: entries must have "parts", not Claude's "content"
                if data and isinstance(data[0], dict) and "parts" in data[0]:
                    return data
            except Exception:
                pass
        return []

    def _save_history(self) -> None:
        """Persist current conversation history to disk."""
        if self._history_file:
            try:
                self._history_file.write_text(
                    json.dumps(self.history, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass

    # ── Persona management ────────────────────────────────────────────────

    def set_persona(self, persona_content: str) -> None:
        """Overlay a coaching persona on the base system prompt."""
        self._persona_content = persona_content
        self._system_prompt   = self._build_system_prompt()

    def clear_persona(self) -> None:
        """Remove the active persona and restore the base system prompt."""
        self._persona_content = None
        self._system_prompt   = self._build_system_prompt()

    @property
    def active_persona(self) -> bool:
        return self._persona_content is not None

    # ── Chat methods ──────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """Send a message and return the full reply. Updates history."""
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=self._contents(user_message),
            config=self._config(),
        )
        reply = response.text
        self.history.append({"role": "user",  "parts": [{"text": user_message}]})
        self.history.append({"role": "model", "parts": [{"text": reply}]})
        self._save_history()
        return reply

    def chat_stream(self, user_message: str):
        """
        Sync generator that yields text chunks as Gemini produces them.
        Used by the CLI (main.py). Updates history when complete.
        """
        full_reply = ""
        for chunk in self._client.models.generate_content_stream(
            model=self._model_name,
            contents=self._contents(user_message),
            config=self._config(),
        ):
            if chunk.text:
                full_reply += chunk.text
                yield chunk.text
        self.history.append({"role": "user",  "parts": [{"text": user_message}]})
        self.history.append({"role": "model", "parts": [{"text": full_reply}]})
        self._save_history()

    async def chat_stream_async(self, user_message: str):
        """
        Async generator used by the web server (server.py) via SSE.
        Yields tokens as they arrive — true streaming via the google-genai SDK.
        """
        full_reply = ""
        stream = await self._client.aio.models.generate_content_stream(
            model=self._model_name,
            contents=self._contents(user_message),
            config=self._config(),
        )
        async for chunk in stream:
            if chunk.text:
                full_reply += chunk.text
                yield chunk.text
        self.history.append({"role": "user",  "parts": [{"text": user_message}]})
        self.history.append({"role": "model", "parts": [{"text": full_reply}]})
        self._save_history()

    def reset_history(self) -> None:
        """Clear conversation history and remove the persisted history file."""
        self.history = []
        if self._history_file and self._history_file.exists():
            self._history_file.unlink(missing_ok=True)
