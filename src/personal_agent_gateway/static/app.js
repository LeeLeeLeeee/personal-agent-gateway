const state = {
  messages: [],
  pendingApproval: null,
  busy: false,
};

const elements = {
  approvalCommand: document.querySelector("#approval-command"),
  approvalPanel: document.querySelector("#approval-panel"),
  approveButton: document.querySelector("#approve-button"),
  chatForm: document.querySelector("#chat-form"),
  connectionState: document.querySelector("#connection-state"),
  denyButton: document.querySelector("#deny-button"),
  errorBanner: document.querySelector("#error-banner"),
  messageInput: document.querySelector("#message-input"),
  messageList: document.querySelector("#message-list"),
  resetButton: document.querySelector("#reset-button"),
  sendButton: document.querySelector("#send-button"),
  tokenForm: document.querySelector("#token-form"),
  tokenInput: document.querySelector("#token-input"),
  tokenPanel: document.querySelector("#token-panel"),
};

elements.tokenForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = elements.tokenInput?.value.trim() ?? "";
  if (!token) {
    showError("Enter a token.");
    return;
  }

  await authenticateWithToken(token);
});

elements.chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = elements.messageInput?.value.trim() ?? "";
  if (!content || state.busy) {
    return;
  }

  setBusy(true);
  hideError();
  appendMessage("user", content);
  if (elements.messageInput) {
    elements.messageInput.value = "";
  }

  try {
    const data = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: content }),
    });
    applyRuntimeResponse(data);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

elements.approveButton?.addEventListener("click", async () => {
  await resolveApproval("approve");
});

elements.denyButton?.addEventListener("click", async () => {
  await resolveApproval("deny");
});

elements.resetButton?.addEventListener("click", async () => {
  if (state.busy) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    await apiFetch("/api/reset", { method: "POST" });
    state.messages = [];
    state.pendingApproval = null;
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

bootstrap();

async function bootstrap() {
  const token = new URLSearchParams(window.location.search).get("token");
  if (token) {
    await authenticateWithToken(token);
    return;
  }

  await loadHistory();
}

async function authenticateWithToken(token) {
  setBusy(true);
  hideError();

  try {
    const response = await fetch(`/?token=${encodeURIComponent(token)}`);
    if (!response.ok) {
      throw new Error(`Authentication failed (${response.status}).`);
    }
    window.history.replaceState({}, "", window.location.pathname);
    await loadHistory();
  } catch (error) {
    showTokenPanel();
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function loadHistory() {
  hideError();

  try {
    const data = await apiFetch("/api/history");
    const events = Array.isArray(data.events) ? data.events : [];
    state.messages = messagesFromEvents(events);
    state.pendingApproval = pendingApprovalFromEvents(events);
    hideTokenPanel();
    render();
  } catch (error) {
    if (error.status === 401) {
      state.messages = [];
      state.pendingApproval = null;
      showTokenPanel();
      render();
      return;
    }
    showError(error.message);
  }
}

async function resolveApproval(action) {
  if (!state.pendingApproval || state.busy) {
    return;
  }

  const approvalId = state.pendingApproval.id;
  setBusy(true);
  hideError();

  try {
    const data = await apiFetch(`/api/approvals/${encodeURIComponent(approvalId)}/${action}`, {
      method: "POST",
    });
    applyRuntimeResponse(data);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  if (response.ok) {
    return response.json();
  }

  let message = `API request failed (${response.status}).`;
  try {
    const data = await response.json();
    if (typeof data.detail === "string") {
      message = data.detail;
    }
  } catch (_error) {
    message = response.statusText || message;
  }

  const error = new Error(message);
  error.status = response.status;
  throw error;
}

function applyRuntimeResponse(data) {
  const messages = Array.isArray(data.messages) ? data.messages : [];
  for (const message of messages) {
    if (typeof message.content === "string") {
      appendMessage(String(message.role ?? "assistant"), message.content);
    }
  }

  state.pendingApproval = normalizeApproval(data.pending_approval);
  hideTokenPanel();
  render();
}

function appendMessage(role, content) {
  state.messages.push({ role, content });
  renderMessages();
}

function messagesFromEvents(events) {
  const messages = [];
  for (const event of events) {
    const payload = event.payload ?? {};
    if ((event.kind === "user" || event.kind === "assistant") && typeof payload.content === "string") {
      messages.push({ role: event.kind, content: payload.content });
    }
    if (event.kind === "runtime_error" && typeof payload.message === "string") {
      messages.push({ role: "system", content: `Error: ${payload.message}` });
    }
    if (event.kind === "tool_denial" && typeof payload.command === "string") {
      messages.push({ role: "system", content: `Denied: ${payload.command}` });
    }
    if (event.kind === "tool_result" && typeof payload.command === "string") {
      messages.push({ role: "tool", content: shellResultText(payload) });
    }
  }
  return messages;
}

function pendingApprovalFromEvents(events) {
  const pendingByToolId = new Map();
  for (const event of events) {
    const payload = event.payload ?? {};
    if (event.kind === "tool_request" && payload.name === "shell.run") {
      const approval = approvalFromToolRequest(payload);
      if (approval) {
        pendingByToolId.set(approval.toolCallId, approval);
      }
    }
    if (event.kind === "tool_result" || event.kind === "tool_denial") {
      pendingByToolId.delete(String(payload.id ?? ""));
    }
  }

  const pending = Array.from(pendingByToolId.values()).pop();
  if (!pending) {
    return null;
  }
  return { id: pending.id, command: pending.command };
}

function approvalFromToolRequest(payload) {
  const args = payload.arguments ?? {};
  if (
    typeof payload.id !== "string" ||
    typeof payload.approval_id !== "string" ||
    typeof args.command !== "string"
  ) {
    return null;
  }

  return {
    command: args.command,
    id: payload.approval_id,
    toolCallId: payload.id,
  };
}

function shellResultText(payload) {
  const lines = [`$ ${payload.command}`, `exit ${payload.exit_code ?? ""}`];
  if (payload.stdout) {
    lines.push(String(payload.stdout));
  }
  if (payload.stderr) {
    lines.push(String(payload.stderr));
  }
  return lines.join("\n");
}

function normalizeApproval(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (typeof value.id !== "string" || typeof value.command !== "string") {
    return null;
  }
  return { id: value.id, command: value.command };
}

function render() {
  renderMessages();
  renderApproval();
  if (elements.connectionState) {
    elements.connectionState.textContent = elements.tokenPanel?.hidden ? "Connected" : "Token required";
  }
}

function renderMessages() {
  if (!elements.messageList) {
    return;
  }

  elements.messageList.replaceChildren();
  for (const message of state.messages) {
    const item = document.createElement("li");
    item.className = `message message-${message.role}`;

    const role = document.createElement("span");
    role.className = "message-role";
    role.textContent = message.role;

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content;

    item.append(role, content);
    elements.messageList.append(item);
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function renderApproval() {
  const approval = state.pendingApproval;
  if (!elements.approvalPanel || !elements.approvalCommand) {
    return;
  }

  elements.approvalPanel.hidden = !approval;
  elements.approvalCommand.textContent = approval?.command ?? "";
}

function showTokenPanel() {
  if (elements.tokenPanel) {
    elements.tokenPanel.hidden = false;
  }
  if (elements.connectionState) {
    elements.connectionState.textContent = "Token required";
  }
}

function hideTokenPanel() {
  if (elements.tokenPanel) {
    elements.tokenPanel.hidden = true;
  }
}

function showError(message) {
  if (!elements.errorBanner) {
    return;
  }
  elements.errorBanner.textContent = message;
  elements.errorBanner.hidden = false;
}

function hideError() {
  if (elements.errorBanner) {
    elements.errorBanner.hidden = true;
    elements.errorBanner.textContent = "";
  }
}

function setBusy(isBusy) {
  state.busy = isBusy;
  for (const button of [
    elements.approveButton,
    elements.denyButton,
    elements.resetButton,
    elements.sendButton,
  ]) {
    if (button) {
      button.disabled = isBusy;
    }
  }
}
