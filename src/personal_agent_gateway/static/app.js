const NAV = [
  { key: "chat", label: "Chat" }, { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" }, { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" }, { key: "settings", label: "Settings" },
];
const PLANNED = new Set(["jobs", "schedules", "capabilities", "artifacts", "settings"]);
const state = {
  screen: "chat", status: null,
  authStage: "login", otpInput: "", authError: "", setup: null, recoveryCodes: [],
  timeline: [], live: [], sessions: [], sessionQuery: "", pendingApproval: null, busy: false, navOpen: false,
  eventSource: null, sseState: "idle", turnStart: null, turnEnd: null, openCmd: {}, elapsedTimer: null, autoScroll: true,
};

const api = {
  async getStatus() { const r = await fetch("/api/status"); return r.ok ? r.json() : null; },
  async authStatus() { const r = await fetch("/api/auth/status"); return r.ok ? r.json() : { authenticated: false, totp_configured: false }; },
  async login(otp) { const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ otp }) }); return r.ok; },
  async setupStart() { const r = await fetch("/api/auth/setup/start", { method: "POST" }); return r.ok ? r.json() : null; },
  async setupVerify(otp) { const r = await fetch("/api/auth/setup/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ otp }) }); return r.ok ? r.json() : null; },
  async logout() { await fetch("/api/auth/logout", { method: "POST" }); },
  async history() { const r = await fetch("/api/history"); return r.ok ? (await r.json()).events : []; },
  async sendChat(message) { const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) }); return r.ok ? r.json() : null; },
  async sessions() { const r = await fetch("/api/sessions"); return r.ok ? (await r.json()).sessions : []; },
  async searchSessions(q) { const r = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`); return r.ok ? (await r.json()).sessions : []; },
  async activate(id) { const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/activate`, { method: "POST" }); return r.ok ? r.json() : null; },
  async deleteSession(id) { const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }); return r.ok ? r.json() : null; },
  async reset() { const r = await fetch("/api/reset", { method: "POST" }); return r.ok ? r.json() : null; },
  async approve(id) { const r = await fetch(`/api/approvals/${encodeURIComponent(id)}/approve`, { method: "POST" }); return r.ok ? r.json() : null; },
  async deny(id) { const r = await fetch(`/api/approvals/${encodeURIComponent(id)}/deny`, { method: "POST" }); return r.ok ? r.json() : null; },
  async artifacts() { const r = await fetch("/api/artifacts"); return r.ok ? (await r.json()).artifacts : []; },
};

function normalizeApproval(v) {
  if (!v || typeof v !== "object") return null;
  if (typeof v.id !== "string" || typeof v.command !== "string") return null;
  return { id: v.id, command: v.command };
}

// ---- time / text helpers ----
function fmtTime(iso, withSeconds) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  return withSeconds ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` : `${p(d.getHours())}:${p(d.getMinutes())}`;
}
function nowHMS() { return fmtTime(new Date().toISOString(), true); }
function nowHM() { return fmtTime(new Date().toISOString(), false); }
function fmtElapsed(sec) { const s = Math.floor(sec); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }
function linesFrom(text) { if (!text) return []; return String(text).replace(/\s+$/, "").split("\n").map((t) => ({ text: t, color: "#E6E6E6" })); }

// ---- timeline model ----
function timelineFromHistory(events) {
  const out = [];
  events.forEach((e, i) => {
    const p = e.payload || {};
    const t = fmtTime(e.created_at, false);
    if (e.kind === "user" && typeof p.content === "string") out.push({ type: "user", text: p.content, time: t });
    else if (e.kind === "assistant" && typeof p.content === "string") out.push({ type: "agent", text: p.content, time: t });
    else if (e.kind === "tool_result" && typeof p.command === "string") {
      out.push({
        type: "command", key: "h" + i, command: p.command,
        status: p.exit_code === 0 ? "completed" : "failed", exit: p.exit_code,
        lines: linesFrom(`${p.stdout || ""}${p.stderr || ""}`), time: fmtTime(e.created_at, true), duration: "",
      });
    } else if (e.kind === "tool_denial" && typeof p.command === "string") {
      out.push({ type: "event_row", label: "tool_denial", detail: p.command, dotColor: "#FF0000", time: fmtTime(e.created_at, true) });
    } else if (e.kind === "runtime_error" && typeof p.message === "string") {
      out.push({ type: "runtime_error", message: p.message, time: fmtTime(e.created_at, true) });
    }
  });
  return out;
}

function entryFromSse(ev) {
  if (ev.type === "codex.event") {
    const it = ev.item || {};
    if (it.type === "command_execution") {
      const status = it.status || "running";
      const done = status === "completed" || status === "failed";
      return {
        type: "command", key: "l" + (it.id || it.command || ""), command: it.command || "command",
        status, exit: it.exit_code, lines: linesFrom(it.aggregated_output || ""), time: nowHMS(),
        duration: done ? "" : "live", live: true,
      };
    }
    if (it.type === "agent_message") return { type: "agent", text: it.text || "", time: nowHM(), streaming: false };
    return null;
  }
  if (ev.type === "runtime.user_message.started") return { type: "event_row", label: "runtime.user_message.started", detail: "message accepted", dotColor: "#000", time: nowHMS() };
  if (ev.type === "runtime.completed") return { type: "event_row", label: "runtime.completed", detail: "session finished", dotColor: "#008000", time: nowHMS() };
  if (ev.type === "runtime.error") return { type: "runtime_error", message: typeof ev.message === "string" ? ev.message : "runtime error", time: nowHMS() };
  return null;
}

function connectEvents() {
  if (state.eventSource) state.eventSource.close();
  if (typeof EventSource === "undefined") return;
  const source = new EventSource("/api/events");
  state.eventSource = source;
  source.onopen = () => { if (!state.busy) state.sseState = "connected"; renderShell(); };
  source.onerror = () => { state.sseState = "error"; renderShell(); };
  source.onmessage = (event) => { try { applySse(JSON.parse(event.data)); } catch (_err) { /* ignore malformed */ } };
}

function applySse(event) {
  if (event.type === "runtime.user_message.started") { state.turnStart = Date.now(); state.turnEnd = null; }
  const entry = entryFromSse(event);
  if (entry) {
    if (entry.type === "command") {
      const idx = state.live.findIndex((e) => e.type === "command" && e.key === entry.key);
      if (idx >= 0) state.live[idx] = entry; else state.live.push(entry);
    } else {
      state.live.push(entry);
    }
  }
  renderShell(); scrollBottom();
}

function deriveLive() {
  const entries = state.timeline.concat(state.live);
  const running = state.live.filter((e) => e.type === "command" && e.status !== "completed" && e.status !== "failed").length;
  let phase, color;
  if (running > 0) { phase = "COMMAND RUNNING"; color = "var(--c-warn)"; }
  else if (state.busy) { phase = "WORKING"; color = "var(--c-warn)"; }
  else if (!entries.length) { phase = "IDLE"; color = "var(--c-grey)"; }
  else {
    // Find the last significant execution in the current turn (ignore trailing plain messages, stop at the turn's user message).
    let sig = null;
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (e.type === "runtime_error" || e.type === "command") { sig = e; break; }
      if (e.type === "user") break;
    }
    if (sig && sig.type === "runtime_error") { phase = "ERROR"; color = "var(--c-danger)"; }
    else if (sig && sig.type === "command" && sig.status === "failed") { phase = "FAILED"; color = "var(--c-danger)"; }
    else { phase = "DONE"; color = "var(--c-ok)"; }
  }
  const kindMap = { "COMMAND RUNNING": "running", WORKING: "working", DONE: "completed", FAILED: "failed", ERROR: "error", IDLE: "idle" };
  let elapsed = "—";
  if (state.turnStart) { const end = state.busy ? Date.now() : (state.turnEnd || Date.now()); elapsed = fmtElapsed(Math.max(0, (end - state.turnStart) / 1000)); }
  return { phase, color, running, lastKind: kindMap[phase], elapsed, events: entries.length };
}

function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.onclick = v;
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) n.append(c instanceof Node ? c : document.createTextNode(c));
  return n;
}

function setScreen(name) { state.screen = name; state.navOpen = false; renderShell(); }

function scrollBottom() {
  if (!state.autoScroll) return;
  requestAnimationFrame(() => { const t = document.querySelector(".transcript"); if (t) t.scrollTop = t.scrollHeight; });
}
function startElapsedTimer() { if (state.elapsedTimer) return; state.elapsedTimer = setInterval(() => { if (state.screen === "chat" && state.busy) renderShell(); }, 1000); }
function stopElapsedTimer() { if (state.elapsedTimer) { clearInterval(state.elapsedTimer); state.elapsedTimer = null; } }

// ---- login ----
function renderLogin() {
  const app = document.getElementById("app");
  const otp = el("input", { class: "input-field", type: "text", inputmode: "numeric", maxlength: "6", placeholder: "000000" });
  otp.value = state.otpInput || "";
  otp.oninput = () => { state.otpInput = otp.value; };
  const err = state.authError
    ? el("div", { class: "mono", style: "border:3px solid var(--c-danger);color:var(--c-danger);padding:12px 14px;margin-top:16px;font-size:12px" }, state.authError)
    : "";
  let body;
  if (state.authStage === "login") {
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Sign in"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:24px" }, "Enter the 6-digit code from your authenticator app."),
      otp, err,
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%",
        onclick: async () => { const ok = await api.login((state.otpInput || "").trim()); if (ok) return afterAuth(); state.authError = "Invalid code. Session refused."; renderLogin(); } }, "Continue")),
      el("div", { style: "margin-top:20px;border-top:1px solid #CCC;padding-top:16px" },
        el("button", { class: "mono", style: "background:none;border:none;padding:0;color:var(--c-link);cursor:pointer;text-decoration:underline;font-size:12px",
          onclick: async () => { state.authError = ""; state.otpInput = ""; state.setup = await api.setupStart(); state.authStage = "setup"; renderLogin(); } }, "First time on this device? Set up authenticator")),
    ];
  } else if (state.authStage === "setup") {
    const s = state.setup || {};
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Set up authenticator"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:16px" }, "Scan the QR in Google Authenticator, then enter the 6-digit code."),
      el("div", { style: "display:flex;gap:16px;align-items:flex-start" }, [
        el("div", { class: "qr", html: s.qr_svg || "" }),
        el("div", { style: "flex:1;min-width:0" }, [
          el("div", { class: "mono", style: "font-size:10px;letter-spacing:1px;color:var(--c-grey);margin-bottom:4px" }, "MANUAL SETUP KEY"),
          el("div", { class: "mono", style: "font-size:13px;word-break:break-all;border:2px solid var(--c-black);padding:8px 10px" }, s.secret || ""),
        ]),
      ]),
      el("div", { style: "margin-top:16px" }, otp), err,
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%",
        onclick: async () => { const r = await api.setupVerify((state.otpInput || "").trim()); if (r && r.enabled) { state.recoveryCodes = r.recovery_codes || []; state.otpInput = ""; state.authStage = "recovery"; renderLogin(); } else { state.authError = "Code did not match. Try the current code."; renderLogin(); } } }, "Verify & enable")),
      el("div", { style: "margin-top:16px" },
        el("button", { class: "mono", style: "background:none;border:none;padding:0;color:var(--c-link);cursor:pointer;text-decoration:underline;font-size:12px",
          onclick: () => { state.authStage = "login"; state.authError = ""; state.otpInput = ""; renderLogin(); } }, "Back to sign in")),
    ];
  } else {
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Recovery codes"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:16px" }, "Store these now. They are shown only once and let you sign in if you lose your device."),
      el("div", { class: "mono", style: "border:3px solid var(--c-black);padding:14px;display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px" },
        (state.recoveryCodes || []).map(c => el("span", {}, c))),
      el("div", { class: "mono", style: "margin-top:16px;border:3px solid var(--c-warn);padding:10px 12px;font-size:12px" }, "These codes will not be shown again."),
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%", onclick: () => afterAuth() }, "I have saved these - continue")),
    ];
  }
  app.replaceChildren(el("div", { style: "height:100%;overflow-y:auto;display:flex;align-items:flex-start;justify-content:center;padding:48px 24px" },
    el("div", { class: "card-hero", style: "width:100%;max-width:520px;padding:32px" }, body)));
}

// ---- chat ----
function loaderEl(label) {
  const cube = el("div", { class: "cube" }, ["f1", "f2", "f3", "f4", "f5", "f6"].map(f => el("div", { class: `face ${f}` })));
  return el("div", { class: "loader" }, [
    el("div", { class: "cube-wrap" }, cube),
    el("span", { class: "loader-label" }, `${label || "WORKING"}...`),
  ]);
}

function renderSessionRail() {
  const search = el("input", { class: "input-field", type: "search", placeholder: "Search" });
  search.value = state.sessionQuery || "";
  search.oninput = async () => { state.sessionQuery = search.value.trim(); state.sessions = state.sessionQuery ? await api.searchSessions(state.sessionQuery) : await api.sessions(); renderShell(); };
  const items = (state.sessions || []).map(se => {
    const card = el("div", { class: `sess-item${se.is_active ? " sess-item-active" : ""}`, style: "cursor:pointer" }, [
      el("div", { style: "font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, se.title || "Untitled"),
      el("div", { class: "mono", style: "font-size:10px;color:var(--c-grey);margin-top:3px" }, `${se.status} · ${se.message_count} msg`),
      el("button", { class: "btn btn-sm", style: "margin-top:6px",
        onclick: async (e) => { e.stopPropagation(); if (window.confirm("Delete session?")) { await api.deleteSession(se.id); state.sessions = await api.sessions(); renderShell(); } } }, "Delete"),
    ]);
    card.onclick = async () => {
      const d = await api.activate(se.id);
      if (d) { state.timeline = timelineFromHistory(d.events); state.live = []; state.pendingApproval = null; state.turnStart = null; state.turnEnd = null; state.autoScroll = true; state.status = await api.getStatus(); state.sessions = await api.sessions(); renderShell(); scrollBottom(); }
    };
    return card;
  });
  return el("div", { class: "sess-rail" }, [
    el("div", { class: "sess-head" }, [
      el("span", { class: "headline", style: "font-size:12px" }, "Sessions"),
      el("button", { class: "btn btn-sm",
        onclick: async () => { await api.reset(); state.timeline = []; state.live = []; state.pendingApproval = null; state.turnStart = null; state.turnEnd = null; state.status = await api.getStatus(); state.sessions = await api.sessions(); renderShell(); } }, "+"),
    ]),
    el("div", { style: "padding:10px 12px" }, search),
    el("div", { style: "flex:1;overflow-y:auto" }, items),
  ]);
}

function renderComposer() {
  const ta = el("textarea", { class: "input-field", rows: "2", placeholder: "Message the agent, or describe a local action..." });
  const send = async () => {
    const msg = ta.value.trim(); if (!msg || state.busy) return;
    state.timeline.push({ type: "user", text: msg, time: nowHM() });
    ta.value = ""; state.busy = true; state.live = []; state.turnStart = Date.now(); state.turnEnd = null; state.autoScroll = true;
    startElapsedTimer(); renderShell(); scrollBottom();
    try {
      const data = await api.sendChat(msg);
      if (data) state.pendingApproval = normalizeApproval(data.pending_approval);
      await afterTurn();
    } finally {
      state.busy = false; state.turnEnd = Date.now(); stopElapsedTimer(); renderShell(); scrollBottom();
    }
  };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  if (state.busy) ta.disabled = true;
  const btn = el("button", { class: "btn btn-primary", onclick: send }, "Send");
  if (state.busy) btn.disabled = true;
  return el("div", { class: "composer" }, [ta, btn]);
}

async function afterTurn() {
  state.timeline = timelineFromHistory(await api.history());
  state.live = [];
  state.status = await api.getStatus();
  await maybeArtifact();
}

async function maybeArtifact() {
  if (!state.turnStart) return;
  let arts = [];
  try { arts = await api.artifacts(); } catch (_e) { return; }
  const sid = state.status && state.status.session_id;
  const fresh = arts.filter((a) => a.source_session_id === sid && Date.parse(a.created_at) >= state.turnStart - 2000);
  for (const a of fresh) state.timeline.push({ type: "artifact", artifact: a });
}

async function resolveApproval(action) {
  if (!state.pendingApproval || state.busy) return;
  const id = state.pendingApproval.id;
  state.busy = true; state.turnStart = state.turnStart || Date.now(); state.turnEnd = null; startElapsedTimer(); renderShell();
  try {
    const data = action === "approve" ? await api.approve(id) : await api.deny(id);
    if (data) state.pendingApproval = normalizeApproval(data.pending_approval);
    await afterTurn();
  } finally {
    state.busy = false; state.turnEnd = Date.now(); stopElapsedTimer(); renderShell(); scrollBottom();
  }
}

function approvalActions(size) {
  return el("div", { style: "margin-top:14px;display:flex;gap:8px" }, [
    el("button", { class: `btn btn-primary ${size}`, onclick: () => resolveApproval("approve") }, "Approve"),
    el("button", { class: `btn btn-destructive ${size}`, onclick: () => resolveApproval("deny") }, "Deny"),
  ]);
}

function renderProposal(a) {
  return el("div", { class: "proposal" }, [
    el("div", { class: "proposal-head" }, [
      el("span", {}, "JOB PROPOSAL"),
      el("span", { style: "color:var(--c-warn)" }, "WAITING APPROVAL"),
    ]),
    el("div", { style: "padding:16px" }, [
      el("div", { class: "kv" }, [
        el("div", { class: "k" }, "CAPABILITY"), el("div", {}, "shell.run"),
        el("div", { class: "k" }, "RISK"), el("div", { style: "color:var(--c-danger)" }, "HIGH · runs a local command"),
      ]),
      el("div", { style: "margin-top:12px" }, [
        el("div", { class: "mono", style: "font-size:10px;letter-spacing:1px;color:var(--c-grey);margin-bottom:4px" }, "COMMAND PREVIEW"),
        el("div", { class: "console" }, a.command),
      ]),
      approvalActions("btn-sm"),
    ]),
  ]);
}

// ---- timeline renderers ----
function statusBadge(kind) {
  const L = { running: "RUNNING", working: "WORKING", completed: "COMPLETED", failed: "FAILED", error: "ERROR", idle: "IDLE" };
  const dot = kind === "running" || kind === "working";
  return el("span", { class: `badge badge-${kind}` }, [dot ? el("span", { class: "dot" }) : "", L[kind] || "IDLE"]);
}

function toggleCmd(key, def) { const cur = key in state.openCmd ? state.openCmd[key] : def; state.openCmd[key] = !cur; renderShell(); }

function renderCommandBlock(e) {
  const def = e.status !== "completed";
  const open = e.key in state.openCmd ? state.openCmd[e.key] : def;
  const badgeKind = e.status === "completed" ? "completed" : (e.status === "failed" ? "failed" : "running");
  const dotColor = badgeKind === "completed" ? "#008000" : (badgeKind === "failed" ? "#FF0000" : "#FFA500");
  const dotBlink = badgeKind === "running" ? "animation:blink-hard 1s step-end infinite;" : "";
  const exit = (e.exit === undefined || e.exit === null) ? "—" : String(e.exit);
  const exitColor = e.status === "completed" ? "var(--c-ok)" : (e.status === "failed" ? "var(--c-danger)" : "var(--c-grey)");
  const head = el("button", { class: "cmd-head", onclick: () => toggleCmd(e.key, def) }, [
    e.time ? el("span", { class: "tl-time" }, e.time) : "",
    el("span", { class: "cmd-name" }, `$ ${e.command}`),
    statusBadge(badgeKind),
  ]);
  const out = open
    ? el("div", { class: "cmd-out" }, [
        el("div", { class: "cmd-out-label" }, "AGGREGATED OUTPUT"),
        ...(e.lines && e.lines.length ? e.lines : [{ text: "(no output)", color: "#606060" }]).map((ln) => el("div", { class: "cmd-line", style: `color:${ln.color}` }, ln.text)),
      ])
    : "";
  const foot = el("div", { class: "cmd-foot" }, [
    el("span", {}, ["EXIT ", el("span", { style: `color:${exitColor}` }, exit)]),
    e.duration ? el("span", {}, e.duration) : "",
    el("span", { class: "toggle" }, open ? "▾ HIDE OUTPUT" : `▸ SHOW OUTPUT · ${(e.lines || []).length} LINES`),
  ]);
  return el("div", { class: "tl-cmd" }, [
    el("span", { class: "tl-dot", style: `background:${dotColor};${dotBlink}` }),
    el("div", { class: "cmd-block" }, [head, out, foot]),
  ]);
}

function renderEventRow(e) {
  const blink = e.dotColor === "#FFA500" ? "animation:blink-hard 1s step-end infinite;" : "";
  return el("div", { class: "tl-row" }, [
    el("span", { class: "tl-dot", style: `background:${e.dotColor || "#000"};${blink}` }),
    e.time ? el("span", { class: "tl-time" }, e.time) : "",
    el("span", { class: "tl-label" }, e.label || ""),
    e.detail ? el("span", { class: "tl-detail" }, e.detail) : "",
  ]);
}

function renderUser(e) {
  return el("div", { class: "msg-user" }, [
    el("div", { class: "msg-meta" }, `YOU · ${e.time || ""}`),
    el("div", { class: "bubble" }, e.text),
  ]);
}

function renderAgent(e) {
  const bubble = el("div", { class: "bubble" }, e.text);
  if (e.streaming) bubble.append(el("span", { class: "agent-cursor" }));
  return el("div", { class: "msg-agent" }, [
    el("div", { class: "msg-meta" }, `AGENT · ${e.time || ""}`),
    bubble,
  ]);
}

function renderArtifactCard(e) {
  const a = e.artifact || {};
  const type = String(a.type || "");
  const glyph = type.startsWith("audio") ? "♪" : type.startsWith("image") ? "▣" : type.startsWith("video") ? "►" : "▤";
  const size = a.size_bytes ? `${(a.size_bytes / 1024 / 1024).toFixed(1)} MB` : "";
  return el("div", { class: "artifact-card" }, [
    el("div", { class: "head" }, "OUTPUT GENERATED"),
    el("div", { class: "body" }, [
      el("span", { class: "glyph" }, glyph),
      el("span", { style: "flex:1;min-width:0" }, [
        el("span", { style: "display:block;font-family:var(--font-mono);font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, a.title || "artifact"),
        el("span", { style: "display:block;font-family:var(--font-mono);font-size:10px;color:var(--c-grey);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, `${(a.type || "FILE").toUpperCase()}${size ? ` · ${size}` : ""}${a.relative_path ? ` · ${a.relative_path}` : ""}`),
      ]),
      el("span", { style: "font-family:var(--font-mono);font-size:9px;letter-spacing:1px;color:var(--c-grey);border:1px solid var(--c-grey);padding:2px 6px;flex:none;white-space:nowrap" }, "ARTIFACT READY"),
    ]),
  ]);
}

function renderRuntimeError(e) {
  return el("div", { class: "rt-error" }, [
    el("div", { class: "head" }, `RUNTIME ERROR${e.time ? ` · ${e.time}` : ""}`),
    el("div", { class: "body" }, e.message),
  ]);
}

function idleEmpty() {
  return el("div", { class: "idle-empty" }, [
    el("div", { class: "k" }, "AGENT IDLE"),
    el("div", { class: "d" }, "No active runtime. Send a message and the live activity stream appears here in real time."),
  ]);
}

function renderTimeline() {
  const entries = state.timeline.concat(state.live);
  if (!entries.length && !state.busy) return el("div", { class: "stream" }, idleEmpty());
  const nodes = [];
  let cluster = [];
  const flush = () => {
    if (!cluster.length) return;
    const rows = cluster.map((e) => (e.type === "command" ? renderCommandBlock(e) : renderEventRow(e)));
    nodes.push(el("div", { class: "tl-wrap" }, [el("div", { class: "tl-label-head" }, "AGENT ACTIVITY"), el("div", { class: "timeline" }, rows)]));
    cluster = [];
  };
  for (const e of entries) {
    if (e.type === "event_row" || e.type === "command") { cluster.push(e); continue; }
    flush();
    if (e.type === "user") nodes.push(renderUser(e));
    else if (e.type === "agent") nodes.push(renderAgent(e));
    else if (e.type === "artifact") nodes.push(renderArtifactCard(e));
    else if (e.type === "runtime_error") nodes.push(renderRuntimeError(e));
  }
  flush();
  return el("div", { class: "stream" }, nodes);
}

function renderChatHeader() {
  const active = (state.sessions || []).find((s) => s.is_active);
  const title = (active && active.title) || "New session";
  const started = active && active.created_at ? fmtTime(active.created_at, false) : "";
  return el("div", { class: "chat-header" }, [
    el("span", { class: "title" }, title),
    el("span", { class: "meta" }, `SESSION · ${title.slice(0, 28)}${started ? ` · started ${started}` : ""}`),
  ]);
}

function renderLiveStatusSummary() {
  const d = deriveLive();
  const cell = (k, node) => el("div", { class: "summary-cell" }, [el("span", { class: "summary-k" }, k), node]);
  return el("div", { class: "summary-bar" }, [
    cell("CURRENT PHASE", el("span", { class: "summary-v", style: `color:${d.color}` }, d.phase)),
    cell("RUNNING", el("span", { class: "summary-v", style: `color:${d.running > 0 ? "var(--c-warn)" : "var(--c-grey)"}` }, String(d.running))),
    cell("LAST EVENT", el("div", { style: "margin-top:4px" }, statusBadge(d.lastKind))),
    cell("ELAPSED", el("span", { class: "summary-v" }, d.elapsed)),
  ]);
}

function renderChat() {
  const transcript = el("div", { class: "transcript" }, [
    renderTimeline(),
    (state.busy && !state.live.length) ? loaderEl("AGENT WORKING") : "",
    state.pendingApproval ? renderProposal(state.pendingApproval) : "",
  ]);
  transcript.onscroll = () => { state.autoScroll = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80; };
  return el("div", { class: "chat" }, [
    renderSessionRail(),
    el("div", { class: "chat-col" }, [renderChatHeader(), renderLiveStatusSummary(), transcript, renderComposer()]),
  ]);
}

// ---- shell ----
function sseIndicator() {
  let color, label, blink = false;
  if (state.busy) { color = "var(--c-warn)"; label = "STREAMING /api/events"; blink = true; }
  else if (state.sseState === "error") { color = "var(--c-danger)"; label = "DISCONNECTED"; }
  else { color = "var(--c-ok)"; label = state.timeline.length ? "CONNECTED" : "IDLE · CONNECTED"; }
  return el("div", { class: "sse-wrap" }, [
    el("span", { class: "sse-dot", style: `background:${color};${blink ? "animation:blink-hard 1s step-end infinite;" : ""}` }),
    el("span", { class: "sse-label" }, label),
  ]);
}

function renderStatusbar() {
  const s = state.status || {};
  const d = deriveLive();
  const items = [
    ["WORKSPACE", s.workspace_root || "--"],
    ["MODEL", `${s.provider || "codex"}/${s.model || "default"}`],
    ["SESSION", `${s.session_status || "idle"} ${(s.session_id || "").slice(0, 8)}`],
    ["PHASE", d.phase],
    ["RUNNING", String(d.running)],
    ["PENDING", s.pending_approval ? "1" : "0"],
    ["EVENTS", String(d.events)],
  ];
  const toggle = el("button", { class: "nav-toggle", onclick: () => { state.navOpen = !state.navOpen; renderShell(); } }, "Menu");
  const cells = items.map(([k, v]) => el("div", { class: "status-item" },
    [el("span", { class: "status-k" }, k), el("span", { class: "status-v" }, String(v))]));
  return el("header", { class: "statusbar" }, [toggle, ...cells, sseIndicator()]);
}

function renderSidebar() {
  const nav = NAV.map(n => el("button", {
    class: `nav-item${state.screen === n.key ? " nav-item-active" : ""}`,
    onclick: () => setScreen(n.key),
  }, n.label));
  return el("aside", { class: "sidebar" }, [
    el("div", { class: "sidebar-brand headline", html: "Agent<br>Gateway" }),
    el("nav", { class: "sidebar-nav" }, nav),
    el("div", { class: "sidebar-foot" }, el("button", { class: "btn btn-sm",
      onclick: async () => { await api.logout(); location.reload(); } }, "Log out")),
  ]);
}

function renderMain() {
  if (state.screen === "chat") return renderChat();
  const label = NAV.find(n => n.key === state.screen).label.toUpperCase();
  return el("div", { class: "planned" }, `${label} - PLANNED`);
}

function renderShell() {
  const app = document.getElementById("app");
  app.replaceChildren(el("div", { class: `shell${state.navOpen ? " nav-open" : ""}` }, [
    renderSidebar(),
    el("div", { class: "main-col" }, [
      renderStatusbar(),
      el("div", { class: "content-row" }, el("main", { class: "main" }, renderMain())),
    ]),
    state.navOpen ? el("div", { class: "nav-backdrop", onclick: () => { state.navOpen = false; renderShell(); } }) : "",
  ]));
}

// ---- bootstrap ----
async function afterAuth() {
  state.status = await api.getStatus();
  state.sessions = await api.sessions();
  state.timeline = timelineFromHistory(await api.history());
  state.live = [];
  connectEvents();
  renderShell();
  scrollBottom();
}

async function bootstrap() {
  const auth = await api.authStatus();
  if (auth.authenticated) return afterAuth();
  state.authStage = auth.totp_configured ? "login" : "setup";
  if (state.authStage === "setup") state.setup = await api.setupStart();
  renderLogin();
}
bootstrap();
