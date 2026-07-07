const NAV = [
  { key: "chat", label: "Chat" }, { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" }, { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" }, { key: "settings", label: "Settings" },
];
const PLANNED = new Set(["jobs", "schedules", "capabilities", "artifacts", "settings"]);
const state = {
  screen: "chat", status: null,
  authStage: "login", otpInput: "", authError: "", setup: null, recoveryCodes: [],
  messages: [], sessions: [], sessionQuery: "", pendingApproval: null, busy: false,
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
};

function normalizeApproval(v) {
  if (!v || typeof v !== "object") return null;
  if (typeof v.id !== "string" || typeof v.command !== "string") return null;
  return { id: v.id, command: v.command };
}

function messagesFromEvents(events) {
  const out = [];
  for (const e of events) {
    const p = e.payload || {};
    if ((e.kind === "user" || e.kind === "assistant") && typeof p.content === "string") out.push({ role: e.kind, content: p.content });
    else if (e.kind === "runtime_error" && typeof p.message === "string") out.push({ role: "system", content: `Error: ${p.message}` });
    else if (e.kind === "tool_denial" && typeof p.command === "string") out.push({ role: "system", content: `Denied: ${p.command}` });
    else if (e.kind === "tool_result" && typeof p.command === "string") out.push({ role: "tool", content: `$ ${p.command}\nexit ${p.exit_code ?? ""}\n${p.stdout || ""}${p.stderr || ""}` });
  }
  return out;
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

function setScreen(name) { state.screen = name; renderShell(); }

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
          onclick: () => { state.authStage = "login"; state.authError = ""; state.otpInput = ""; renderLogin(); } }, "← Back to sign in")),
    ];
  } else {
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Recovery codes"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:16px" }, "Store these now. They are shown only once and let you sign in if you lose your device."),
      el("div", { class: "mono", style: "border:3px solid var(--c-black);padding:14px;display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px" },
        (state.recoveryCodes || []).map(c => el("span", {}, c))),
      el("div", { class: "mono", style: "margin-top:16px;border:3px solid var(--c-warn);padding:10px 12px;font-size:12px" }, "These codes will not be shown again."),
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%", onclick: () => afterAuth() }, "I have saved these — continue")),
    ];
  }
  app.replaceChildren(el("div", { style: "max-width:520px;margin:64px auto;padding:0 24px" },
    el("div", { class: "card-hero", style: "padding:32px" }, body)));
}

// ---- chat ----
function loaderEl(label) {
  const cube = el("div", { class: "cube" }, ["f1", "f2", "f3", "f4", "f5", "f6"].map(f => el("div", { class: `face ${f}` })));
  return el("div", { class: "loader" }, [
    el("div", { class: "cube-wrap" }, cube),
    el("span", { class: "loader-label" }, `${label || "WORKING"}…`),
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
    card.onclick = async () => { const d = await api.activate(se.id); if (d) { state.messages = messagesFromEvents(d.events); state.pendingApproval = null; state.status = await api.getStatus(); state.sessions = await api.sessions(); renderShell(); } };
    return card;
  });
  return el("div", { class: "sess-rail" }, [
    el("div", { class: "sess-head" }, [
      el("span", { class: "headline", style: "font-size:12px" }, "Sessions"),
      el("button", { class: "btn btn-sm",
        onclick: async () => { await api.reset(); state.messages = []; state.pendingApproval = null; state.status = await api.getStatus(); state.sessions = await api.sessions(); renderShell(); } }, "+"),
    ]),
    el("div", { style: "padding:10px 12px" }, search),
    el("div", { style: "flex:1;overflow-y:auto" }, items),
  ]);
}

function renderComposer() {
  const ta = el("textarea", { class: "input-field", rows: "2", placeholder: "Ask the agent, or describe a local action…" });
  const send = async () => {
    const msg = ta.value.trim(); if (!msg || state.busy) return;
    state.messages.push({ role: "user", content: msg }); ta.value = ""; state.busy = true; renderShell();
    try {
      const data = await api.sendChat(msg);
      if (data) {
        for (const m of (data.messages || [])) if (typeof m.content === "string") state.messages.push({ role: m.role || "assistant", content: m.content });
        state.pendingApproval = normalizeApproval(data.pending_approval);
      }
      state.messages = messagesFromEvents(await api.history());
      state.status = await api.getStatus();
    } finally {
      state.busy = false; renderShell();
    }
  };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  if (state.busy) ta.disabled = true;
  const btn = el("button", { class: "btn btn-primary", onclick: send }, "Send");
  if (state.busy) btn.disabled = true;
  return el("div", { class: "composer" }, [ta, btn]);
}

function renderChatDrawer() {
  const slot = state.pendingApproval
    ? el("div", { class: "mono", style: "padding:14px;font-size:11px;word-break:break-all" }, state.pendingApproval.command)
    : el("div", { class: "mono", style: "padding:14px;font-size:11px;color:var(--c-grey)" }, "No approvals waiting.");
  return el("aside", { class: "drawer" }, [
    el("div", { class: "mono", style: "background:var(--c-warn);padding:8px 14px;font-size:11px;letter-spacing:1px" }, "PENDING APPROVAL"),
    slot,
    el("div", { class: "planned", style: "margin:14px" }, "SESSION ARTIFACTS — PLANNED"),
    el("div", { class: "planned", style: "margin:14px" }, "ACTIVITY — PLANNED"),
  ]);
}

function renderChat() {
  const msgs = (state.messages || []).map(m => el("div", { class: "msg" }, [
    el("div", { class: "msg-head" }, [el("span", {}, m.role.toUpperCase()), el("span", { style: "color:var(--c-grey)" }, "")]),
    el("div", { class: `msg-body${m.role === "tool" || m.role === "system" ? " mono" : ""}` }, m.content),
  ]));
  return el("div", { class: "chat" }, [
    renderSessionRail(),
    el("div", { class: "chat-col" }, [el("div", { class: "transcript" }, [...msgs, state.busy ? loaderEl("AGENT WORKING") : ""]), renderComposer()]),
    renderChatDrawer(),
  ]);
}

// ---- shell ----
function renderStatusbar() {
  const s = state.status || {};
  const items = [
    ["WORKSPACE", s.workspace_root || "—"],
    ["MODEL", `${s.provider || "codex"}/${s.model || "default"}`],
    ["SESSION", `${s.session_status || "idle"} ${(s.session_id || "").slice(0, 8)}`],
    ["PENDING", s.pending_approval ? "1" : "0"],
    ["RUNNING", "PLANNED"], ["TUNNEL", "PLANNED"],
  ];
  return el("header", { class: "statusbar" },
    items.map(([k, v]) => el("div", { class: "status-item" },
      [el("span", { class: "status-k" }, k), el("span", { class: "status-v" }, String(v))])));
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
  return el("div", { class: "planned" }, `${label} — PLANNED`);
}

function renderShell() {
  const app = document.getElementById("app");
  app.replaceChildren(el("div", { class: "shell" }, [
    renderSidebar(),
    el("div", { class: "main-col" }, [
      renderStatusbar(),
      el("div", { class: "content-row" }, el("main", { class: "main" }, renderMain())),
    ]),
  ]));
}

// ---- bootstrap ----
async function afterAuth() {
  state.status = await api.getStatus();
  state.messages = messagesFromEvents(await api.history());
  state.sessions = await api.sessions();
  renderShell();
}

async function bootstrap() {
  const auth = await api.authStatus();
  if (auth.authenticated) return afterAuth();
  state.authStage = auth.totp_configured ? "login" : "setup";
  if (state.authStage === "setup") state.setup = await api.setupStart();
  renderLogin();
}
bootstrap();
