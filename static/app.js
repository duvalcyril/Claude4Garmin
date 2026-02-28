/**
 * app.js — Chat interface with SSE streaming
 *
 * Flow:
 *   1. User types and hits Enter or Send
 *   2. User bubble appears immediately
 *   3. Empty coach bubble appears with streaming cursor
 *   4. POST /api/chat opens an SSE stream
 *   5. Chunks arrive and are appended as plain text
 *   6. On [DONE], accumulated text is rendered as markdown via marked.js
 *   7. Input is re-enabled
 */

const messagesEl = document.getElementById("messages");
const inputEl    = document.getElementById("msg-input");
const sendBtn    = document.getElementById("btn-send");
const resetBtn   = document.getElementById("btn-reset");
const refreshBtn = document.getElementById("btn-refresh");

let isStreaming = false;

// Configure marked.js: sanitize HTML, smart line breaks
marked.setOptions({ breaks: true, gfm: true });

// ── Helpers ─────────────────────────────────────────────────────────

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

/**
 * Append a message bubble to the chat.
 * Returns the inner .bubble element so callers can update it.
 */
function appendMessage(role, text = "") {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (role === "coach" && text) {
    bubble.innerHTML = marked.parse(text);
  } else {
    bubble.textContent = text;
  }

  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  scrollToBottom();
  return bubble;
}

function setInputDisabled(disabled) {
  inputEl.disabled = disabled;
  sendBtn.disabled = disabled;
}

// ── Send message ─────────────────────────────────────────────────────

async function sendMessage() {
  const message = inputEl.value.trim();
  if (!message || isStreaming) return;

  isStreaming = true;
  setInputDisabled(true);
  inputEl.value = "";
  autoResize();

  // Show user bubble immediately
  appendMessage("user", message);

  // Create an empty coach bubble with streaming cursor
  const coachBubble = appendMessage("coach", "");
  coachBubble.classList.add("streaming");
  let accumulated = "";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      coachBubble.classList.remove("streaming");
      coachBubble.textContent = `Error: ${err.detail || response.statusText}`;
      return;
    }

    // Read the SSE stream chunk by chunk
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep any incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();

        if (payload === "[DONE]") {
          // Render the full response as markdown now that it's complete
          coachBubble.classList.remove("streaming");
          coachBubble.innerHTML = marked.parse(accumulated);
          scrollToBottom();
          return;
        }

        const parsed = JSON.parse(payload);

        if (parsed.error) {
          coachBubble.classList.remove("streaming");
          coachBubble.textContent = `Error: ${parsed.error}`;
          return;
        }

        if (parsed.chunk) {
          accumulated += parsed.chunk;
          // During streaming: show as plain text so partial markdown doesn't flicker
          coachBubble.textContent = accumulated;
          scrollToBottom();
        }
      }
    }
  } catch (err) {
    coachBubble.classList.remove("streaming");
    coachBubble.textContent = `Network error: ${err.message}`;
  } finally {
    isStreaming = false;
    setInputDisabled(false);
    inputEl.focus();
    scrollToBottom();
  }
}

// ── Reset conversation ────────────────────────────────────────────────

async function resetConversation() {
  if (isStreaming) return;
  await fetch("/api/reset", { method: "POST" });

  // Clear all messages except the initial greeting
  messagesEl.innerHTML = "";
  appendMessage("coach", "Conversation reset. What would you like to explore?");
}

// ── Refresh Garmin data ───────────────────────────────────────────────

async function refreshData() {
  if (isStreaming) return;
  refreshBtn.textContent = "Refreshing…";
  refreshBtn.disabled = true;

  const res = await fetch("/api/refresh", { method: "POST" });
  const data = await res.json();

  if (data.ok) {
    // Reload the page so the sidebar shows fresh data
    window.location.reload();
  } else {
    refreshBtn.textContent = "↺ Refresh data";
    refreshBtn.disabled = false;
    appendMessage("coach", `⚠ Refresh failed: ${data.error || "Unknown error"}`);
  }
}

// ── Auto-resize textarea ──────────────────────────────────────────────

function autoResize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
}

// ── Event listeners ───────────────────────────────────────────────────

sendBtn.addEventListener("click", sendMessage);
resetBtn.addEventListener("click", resetConversation);
refreshBtn.addEventListener("click", refreshData);

inputEl.addEventListener("input", autoResize);

inputEl.addEventListener("keydown", (e) => {
  // Send on Enter; Shift+Enter inserts a newline
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Focus input on load
inputEl.focus();
