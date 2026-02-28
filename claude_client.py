"""claude_client.py — Claude API interactions and conversation history.

Key design decisions:
- The Garmin data summary is embedded in the system prompt, not in the first
  user message. This keeps it invisible to the user's chat history and lets
  Claude reference it throughout the conversation without it being re-sent.
- Conversation history is stored as a plain list of {role, content} dicts —
  exactly what the Messages API expects — so follow-up questions work naturally.
- Model and token cap are module-level constants for easy tuning.
"""

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

# Max tokens per response — enough for detailed coaching advice without being verbose
MAX_TOKENS = 1024


class ClaudeCoach:
    """
    Manages a stateful coaching conversation with Claude.

    The health summary is baked into the system prompt once at construction;
    all subsequent chat() calls append to a growing message history.
    """

    def __init__(self, health_summary: str):
        self.client = Anthropic()
        self.history: list[dict] = []
        self.system_prompt = self._build_system_prompt(health_summary)

    def _build_system_prompt(self, health_summary: str) -> str:
        return f"""You are a personal health and fitness coach with access to the user's recent Garmin wearable data.

Your coaching style:
- Be concise and actionable — skip preambles, get to the insight
- Reference specific numbers from the data when relevant
- Spot trends and patterns across the week, not just single-day snapshots
- Be encouraging but honest; flag concerning patterns (e.g., chronic high stress, poor sleep)
- When the user asks general questions, ground your answer in their actual data

Here is the user's Garmin health data for the past 7 days:

{health_summary}

Use this data to answer questions and give personalized recommendations."""

    def chat(self, user_message: str) -> str:
        """
        Send a user message, get a response, and update history.

        The full conversation history is sent on every call so Claude has
        context for follow-up questions ("what about yesterday?" etc.).
        """
        self.history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=self.system_prompt,
            messages=self.history,
        )

        reply = response.content[0].text

        # Store the assistant's reply so the next turn has full context
        self.history.append({"role": "assistant", "content": reply})

        return reply

    def reset_history(self) -> None:
        """Clear conversation history while keeping the Garmin data context."""
        self.history = []
        print("Conversation history cleared.")
