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

const messagesEl   = document.getElementById("messages");
const inputEl      = document.getElementById("msg-input");
const sendBtn      = document.getElementById("btn-send");
const resetBtn     = document.getElementById("btn-reset");
const refreshBtn   = document.getElementById("btn-refresh");
const pickerEl     = document.getElementById("skill-picker");
const pickerListEl = document.getElementById("skill-picker-list");
const personaBadgeEl   = document.getElementById("persona-badge");
const personaBadgeName = document.getElementById("persona-badge-name");
const personaClearBtn  = document.getElementById("persona-badge-clear");

let isStreaming = false;

// ── Skill picker ─────────────────────────────────────────────────────

let allSkills     = [];   // loaded once from /api/skills
let filteredSkills = [];  // subset matching current query
let pickerIndex   = 0;    // keyboard-selected row

// Fetch skills on load so the first "/" is instant
fetch("/api/skills")
  .then(r => r.json())
  .then(data => { allSkills = data; })
  .catch(() => {});

function showPicker(query) {
  const q = query.toLowerCase();
  filteredSkills = allSkills.filter(
    s => s.trigger.includes(q) || s.description.toLowerCase().includes(q)
  );
  if (!filteredSkills.length) { hidePicker(); return; }
  pickerIndex = 0;
  renderPicker();
  pickerEl.hidden = false;
}

function hidePicker() {
  pickerEl.hidden = true;
  filteredSkills = [];
  pickerIndex = 0;
}

function renderPicker() {
  pickerListEl.innerHTML = "";
  filteredSkills.forEach((skill, i) => {
    const item = document.createElement("div");
    const isPersona = skill.type === "persona";
    item.className = "skill-item"
      + (isPersona ? " persona" : "")
      + (i === pickerIndex ? " active" : "");
    item.innerHTML =
      `<span class="skill-trigger">/${skill.trigger}</span>` +
      `<span class="skill-desc">${skill.description}</span>`;
    item.addEventListener("mousedown", (e) => {
      e.preventDefault(); // don't blur the input
      applySkill(i);
    });
    pickerListEl.appendChild(item);
  });
}

function updatePickerSelection(delta) {
  pickerIndex = (pickerIndex + delta + filteredSkills.length) % filteredSkills.length;
  renderPicker();
}

function applySkill(index) {
  const skill = filteredSkills[index];
  if (!skill) return;
  hidePicker();
  if (skill.type === "persona") {
    activatePersona(skill);
  } else {
    inputEl.value = skill.prompt;
    autoResize();
    inputEl.focus();
  }
}

// ── Persona activation ────────────────────────────────────────────────

async function activatePersona(skill) {
  try {
    const res = await fetch("/api/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger: skill.trigger }),
    });
    if (res.ok) {
      personaBadgeName.textContent = skill.trigger;
      personaBadgeEl.hidden = false;
      appendMessage("coach", `**${skill.trigger}** persona activated. I'll follow that coaching style for this conversation.`);
    } else {
      appendMessage("coach", "Could not activate persona — persona skill not found.");
    }
  } catch {
    appendMessage("coach", "Network error activating persona.");
  }
  inputEl.value = "";
  inputEl.focus();
}

async function clearPersona() {
  await fetch("/api/persona/clear", { method: "POST" });
  personaBadgeEl.hidden = true;
  personaBadgeName.textContent = "";
  appendMessage("coach", "Persona deactivated. Back to default coaching mode.");
}

personaClearBtn.addEventListener("click", clearPersona);

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
  hidePicker();

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

  refreshBtn.textContent = "↺ Refresh data";
  refreshBtn.disabled = false;

  if (data.ok) {
    // Fetch fresh sidebar HTML and swap it in — no page reload, conversation preserved.
    // Tab switching uses event delegation on the sidebar element so no re-binding needed.
    try {
      const html = await fetch("/api/sidebar-html").then(r => r.text());
      document.getElementById("sidebar").innerHTML = html;
    } catch {
      // Sidebar update failed — data is still fresh on the server side
    }
    appendMessage("coach", "✓ Garmin data refreshed.");
  } else {
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

inputEl.addEventListener("input", () => {
  autoResize();
  const val = inputEl.value;
  if (val.startsWith("/") && val.length >= 1) {
    showPicker(val.slice(1)); // pass the part after "/"
  } else {
    hidePicker();
  }
});

inputEl.addEventListener("keydown", (e) => {
  // When picker is open, intercept navigation keys
  if (!pickerEl.hidden) {
    if (e.key === "ArrowDown")  { e.preventDefault(); updatePickerSelection(+1); return; }
    if (e.key === "ArrowUp")    { e.preventDefault(); updatePickerSelection(-1); return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
      e.preventDefault();
      applySkill(pickerIndex);
      return;
    }
    if (e.key === "Escape")     { e.preventDefault(); hidePicker(); return; }
  }

  // Send on Enter; Shift+Enter inserts a newline
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Focus input on load
inputEl.focus();

// ── Sidebar tab switching (event delegation — survives sidebar HTML swaps) ──

document.getElementById("sidebar").addEventListener("click", function(e) {
  const tab = e.target.closest(".sidebar-tab");
  if (!tab) return;
  document.querySelectorAll(".sidebar-tab").forEach(function(t) { t.classList.remove("active"); });
  document.querySelectorAll(".tab-panel").forEach(function(p) { p.classList.remove("active"); });
  tab.classList.add("active");
  var panel = document.getElementById(tab.dataset.tab);
  if (panel) panel.classList.add("active");
});
