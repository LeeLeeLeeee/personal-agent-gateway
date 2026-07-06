const state = {
  messages: [],
  pendingApproval: null,
  sessions: [],
  sessionQuery: "",
  status: null,
  authStatus: null,
  otpSetup: null,
  recoveryCodes: [],
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
  otpCodeInput: document.querySelector("#otp-code-input"),
  otpLoginForm: document.querySelector("#otp-login-form"),
  otpLoginInput: document.querySelector("#otp-login-input"),
  otpLoginPanel: document.querySelector("#otp-login-panel"),
  otpQr: document.querySelector("#otp-qr"),
  otpRecoveryCodes: document.querySelector("#otp-recovery-codes"),
  otpRecoveryPanel: document.querySelector("#otp-recovery-panel"),
  otpSecret: document.querySelector("#otp-secret"),
  otpSetupBody: document.querySelector("#otp-setup-body"),
  otpSetupPanel: document.querySelector("#otp-setup-panel"),
  otpSetupState: document.querySelector("#otp-setup-state"),
  otpStartButton: document.querySelector("#otp-start-button"),
  otpVerifyForm: document.querySelector("#otp-verify-form"),
  resetButton: document.querySelector("#reset-button"),
  sendButton: document.querySelector("#send-button"),
  sessionList: document.querySelector("#session-list"),
  sessionMeta: document.querySelector("#session-meta"),
  sessionSearch: document.querySelector("#session-search"),
  sessionSearchForm: document.querySelector("#session-search-form"),
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
  if (!content || state.busy || !canSendMessage()) {
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
    await refreshRuntimeMetadata();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

elements.otpLoginForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const otp = elements.otpLoginInput?.value.trim() ?? "";
  if (!otp || state.busy) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    await apiFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp }),
    });
    if (elements.otpLoginInput) {
      elements.otpLoginInput.value = "";
    }
    await loadAuthStatus();
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

elements.otpStartButton?.addEventListener("click", async () => {
  if (state.busy) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    const data = await apiFetch("/api/auth/setup/start", { method: "POST" });
    state.otpSetup = normalizeOtpSetup(data);
    state.recoveryCodes = [];
    renderOtpSetup();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

elements.otpVerifyForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const otp = elements.otpCodeInput?.value.trim() ?? "";
  if (!otp || state.busy) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    const data = await apiFetch("/api/auth/setup/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp }),
    });
    state.recoveryCodes = Array.isArray(data.recovery_codes)
      ? data.recovery_codes.map(String)
      : [];
    state.otpSetup = null;
    if (elements.otpCodeInput) {
      elements.otpCodeInput.value = "";
    }
    await loadAuthStatus();
    render();
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
  if (!window.confirm("Start a new session? Current transcript stays on disk.")) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    await apiFetch("/api/reset", { method: "POST" });
    state.messages = [];
    state.pendingApproval = null;
    await refreshRuntimeMetadata();
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

elements.sessionSearchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.sessionQuery = elements.sessionSearch?.value.trim() ?? "";
  await loadSessions();
  renderSessions();
});

elements.sessionSearch?.addEventListener("input", async () => {
  const query = elements.sessionSearch?.value.trim() ?? "";
  if (query) {
    return;
  }

  state.sessionQuery = "";
  await loadSessions();
  renderSessions();
});

elements.sessionList?.addEventListener("click", async (event) => {
  const target = event.target instanceof HTMLElement ? event.target : null;
  const button = target?.closest("button[data-session-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  const sessionId = button.dataset.sessionId ?? "";
  if (!sessionId || state.busy) {
    return;
  }

  const action = button.dataset.sessionAction;
  if (action === "activate") {
    await activateSession(sessionId);
  }
  if (action === "delete") {
    await deleteSession(sessionId);
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
    await refreshRuntimeMetadata();
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

async function loadStatus() {
  const data = await apiFetch("/api/status");
  state.status = {
    cookieSecure: Boolean(data.cookie_secure),
    messageCount: Number(data.message_count ?? 0),
    model: String(data.model ?? "default"),
    pendingApproval: Boolean(data.pending_approval),
    provider: String(data.provider ?? "unknown"),
    sessionId: typeof data.session_id === "string" ? data.session_id : "",
    sessionStatus: String(data.session_status ?? "idle"),
    workspaceRoot: String(data.workspace_root ?? ""),
  };
}

async function loadAuthStatus() {
  const data = await apiFetch("/api/auth/status");
  state.authStatus = {
    authenticated: Boolean(data.authenticated),
    totpConfigured: Boolean(data.totp_configured),
  };
}

async function loadSessions() {
  const query = state.sessionQuery.trim();
  const url = query
    ? `/api/sessions/search?q=${encodeURIComponent(query)}`
    : "/api/sessions";
  const data = await apiFetch(url);
  state.sessions = Array.isArray(data.sessions) ? data.sessions.map(normalizeSession).filter(Boolean) : [];
}

async function refreshRuntimeMetadata() {
  await Promise.all([loadStatus(), loadSessions(), loadAuthStatus()]);
}

async function activateSession(sessionId) {
  setBusy(true);
  hideError();

  try {
    const data = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/activate`, {
      method: "POST",
    });
    const events = Array.isArray(data.events) ? data.events : [];
    state.messages = messagesFromEvents(events);
    state.pendingApproval = pendingApprovalFromEvents(events);
    await refreshRuntimeMetadata();
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function deleteSession(sessionId) {
  if (!window.confirm("Delete this session transcript?")) {
    return;
  }

  setBusy(true);
  hideError();

  try {
    const data = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (data.active_session_id === null) {
      state.messages = [];
      state.pendingApproval = null;
    }
    await refreshRuntimeMetadata();
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
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
    await refreshRuntimeMetadata();
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

function normalizeOtpSetup(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (
    typeof value.secret !== "string" ||
    typeof value.otpauth_uri !== "string" ||
    typeof value.qr_svg !== "string"
  ) {
    return null;
  }
  return {
    otpauthUri: value.otpauth_uri,
    qrSvg: value.qr_svg,
    secret: value.secret,
  };
}

function normalizeSession(value) {
  if (!value || typeof value !== "object" || typeof value.id !== "string") {
    return null;
  }
  return {
    createdAt: String(value.created_at ?? ""),
    id: value.id,
    isActive: Boolean(value.is_active),
    messageCount: Number(value.message_count ?? 0),
    status: String(value.status ?? "idle"),
    title: String(value.title ?? "Untitled session"),
    updatedAt: String(value.updated_at ?? ""),
  };
}

function render() {
  renderMessages();
  renderApproval();
  renderOtpLogin();
  renderOtpSetup();
  renderSessions();
  if (elements.connectionState) {
    elements.connectionState.textContent = connectionStateText();
  }
  renderStatus();
  renderComposer();
}

function renderOtpLogin() {
  if (!elements.otpLoginPanel) {
    return;
  }

  const shouldShow = Boolean(
    elements.tokenPanel?.hidden &&
      state.authStatus?.totpConfigured &&
      !state.authStatus?.authenticated
  );
  elements.otpLoginPanel.hidden = !shouldShow;
}

function renderOtpSetup() {
  if (!elements.otpSetupPanel || !elements.otpSetupState) {
    return;
  }

  const configured = Boolean(state.authStatus?.totpConfigured);
  const shouldShow = elements.tokenPanel?.hidden && (!configured || state.recoveryCodes.length > 0);
  elements.otpSetupPanel.hidden = !shouldShow;
  if (!shouldShow) {
    return;
  }

  elements.otpSetupState.textContent = configured ? "Configured" : "Not configured";
  if (elements.otpStartButton) {
    elements.otpStartButton.hidden = configured || Boolean(state.otpSetup);
  }
  if (elements.otpSetupBody) {
    elements.otpSetupBody.hidden = !state.otpSetup;
  }
  if (elements.otpQr) {
    elements.otpQr.replaceChildren();
    if (state.otpSetup) {
      elements.otpQr.innerHTML = state.otpSetup.qrSvg;
    }
  }
  if (elements.otpSecret) {
    elements.otpSecret.textContent = state.otpSetup?.secret ?? "";
  }
  if (elements.otpRecoveryPanel) {
    elements.otpRecoveryPanel.hidden = state.recoveryCodes.length === 0;
  }
  if (elements.otpRecoveryCodes) {
    elements.otpRecoveryCodes.replaceChildren();
    for (const code of state.recoveryCodes) {
      const item = document.createElement("li");
      item.textContent = code;
      elements.otpRecoveryCodes.append(item);
    }
  }
}

function renderComposer() {
  if (elements.sendButton) {
    elements.sendButton.disabled = state.busy || !canSendMessage();
  }
  if (elements.messageInput) {
    elements.messageInput.disabled = state.busy || !canSendMessage();
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

function renderStatus() {
  if (!elements.sessionMeta) {
    return;
  }
  if (!state.status) {
    elements.sessionMeta.textContent = "Session pending";
    return;
  }

  const sessionLabel = state.status.sessionId
    ? state.status.sessionId.slice(0, 8)
    : "none";
  elements.sessionMeta.textContent = [
    `Session ${sessionLabel}`,
    state.status.sessionStatus,
    `${state.status.provider}/${state.status.model}`,
    `${state.status.messageCount} messages`,
  ].join(" · ");
}

function renderSessions() {
  if (!elements.sessionList) {
    return;
  }

  elements.sessionList.replaceChildren();
  if (state.sessions.length === 0) {
    const empty = document.createElement("li");
    empty.className = "session-empty";
    empty.textContent = state.sessionQuery ? "No matching sessions" : "No sessions yet";
    elements.sessionList.append(empty);
    return;
  }

  for (const session of state.sessions) {
    const item = document.createElement("li");
    item.className = `session-item${session.isActive ? " session-item-active" : ""}`;

    const activateButton = document.createElement("button");
    activateButton.className = "session-main";
    activateButton.dataset.sessionAction = "activate";
    activateButton.dataset.sessionId = session.id;
    activateButton.type = "button";

    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title;

    const meta = document.createElement("span");
    meta.className = "session-row-meta";
    meta.textContent = `${session.status} · ${session.messageCount} messages`;

    activateButton.append(title, meta);

    const deleteButton = document.createElement("button");
    deleteButton.className = "session-delete secondary";
    deleteButton.dataset.sessionAction = "delete";
    deleteButton.dataset.sessionId = session.id;
    deleteButton.type = "button";
    deleteButton.textContent = "Delete";

    item.append(activateButton, deleteButton);
    elements.sessionList.append(item);
  }
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
  if (elements.otpLoginPanel) {
    elements.otpLoginPanel.hidden = true;
  }
  if (elements.otpSetupPanel) {
    elements.otpSetupPanel.hidden = true;
  }
  state.status = null;
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
    elements.otpStartButton,
  ]) {
    if (button) {
      button.disabled = isBusy;
    }
  }
  renderComposer();
}

function canSendMessage() {
  return Boolean(elements.tokenPanel?.hidden && state.authStatus?.authenticated);
}

function connectionStateText() {
  if (!elements.tokenPanel?.hidden) {
    return "Token required";
  }
  if (!state.authStatus?.totpConfigured) {
    return "OTP setup required";
  }
  if (!state.authStatus?.authenticated) {
    return "OTP login required";
  }
  return "Connected";
}
