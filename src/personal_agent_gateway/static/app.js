const NAV = [
  { key: "chat", label: "Chat" }, { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" }, { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" }, { key: "settings", label: "Settings" },
];
const PLANNED = new Set(["jobs", "schedules", "capabilities", "artifacts", "settings"]);
const state = {
  screen: "chat", status: null,
  authStage: "login", otpInput: "", authError: "", setup: null, recoveryCodes: [],
};

const api = {
  async getStatus() { const r = await fetch("/api/status"); return r.ok ? r.json() : null; },
  async authStatus() { const r = await fetch("/api/auth/status"); return r.ok ? r.json() : { authenticated: false, totp_configured: false }; },
  async login(otp) { const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ otp }) }); return r.ok; },
  async setupStart() { const r = await fetch("/api/auth/setup/start", { method: "POST" }); return r.ok ? r.json() : null; },
  async setupVerify(otp) { const r = await fetch("/api/auth/setup/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ otp }) }); return r.ok ? r.json() : null; },
  async logout() { await fetch("/api/auth/logout", { method: "POST" }); },
};

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
  if (state.screen === "chat") return el("div", { class: "planned" }, "CHAT — rendered in Task 3");
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
