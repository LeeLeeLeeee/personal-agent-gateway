# Frontend Redesign Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the real vanilla-JS frontend to the `Agent Gateway.dc.html` neo-brutalist design for Slice 1 — app shell (sidebar + status bar), OTP Login, and Chat (layout A) — with the other five screens present as PLANNED placeholders.

**Architecture:** No build step. Rewrite the three already-served static files (`index.html`, `app.js`, `styles.css`); `app.py` is not touched. `app.js` stays a single file with internally separable units (api / state / router / views). Existing API wiring (OTP auth, sessions, chat, approvals) is preserved and reskinned, not discarded.

**Tech Stack:** Plain HTML/CSS/ES2020 JS served by the existing FastAPI routes. No frameworks, no bundler, no external assets (self-contained).

## Global Constraints

- Ownership: only `src/personal_agent_gateway/static/**`. Do NOT modify `app.py` or any Python.
- Source of truth: `Agent Gateway.dc.html`. Follow its layout/visuals; do not invent new visual decisions beyond documented adaptations.
- No external fonts/CDN/images — self-contained. Approximate the mockup's font trio with system stacks.
- Neo-brutalist tokens (verbatim from mockup): black `#000`, white `#fff`, body bg `#E8E8E8`, panel `#F0F0F0`, grey `#808080`, dark grey `#333`, warning `#FFA500`, success `#008000`, danger `#FF0000`, link `#0000FF`. Square corners (no border-radius). Standard border `3px solid #000`; hero card `5px`; small `2px`; inner divider `1px`.
- Bind data ONLY to real endpoints. Anything unbacked renders as a greyed PLANNED state, never faked.
- Page load requires `?token=<AGENT_WEB_TOKEN>` (outer gate, sets cookie). OTP is the app-level login shown after the HTML loads.
- No JS test harness exists; verification is manual in-browser against the running app (`scripts/run_local.ps1`).

## File Structure

- `src/personal_agent_gateway/static/styles.css` — design tokens, reset, typography, component classes (button/chip/status-chip/input/panel/card), shell layout, screen layouts, responsive breakpoints.
- `src/personal_agent_gateway/static/index.html` — shell skeleton: `#app` root only; all regions rendered by JS.
- `src/personal_agent_gateway/static/app.js` — single module: `api` (fetch wrappers), `state` (in-memory), `router` (screen switch), `views` (shell / login / chat renderers), bootstrap.

## Running & Verifying

Start the app: `pwsh scripts/run_local.ps1` (uses `.env`: `AGENT_WEB_TOKEN`, `AGENT_WORKSPACE_ROOT`, `AGENT_SESSION_DIR`). Open `http://127.0.0.1:8787/?token=<AGENT_WEB_TOKEN>`. Each task lists exact observations. There are no automated JS tests; the "test" step is the browser checklist.

---

### Task 1: Neo-brutalist CSS + shell chrome

**Files:**
- Modify: `src/personal_agent_gateway/static/styles.css` (full rewrite)
- Modify: `src/personal_agent_gateway/static/index.html`
- Modify: `src/personal_agent_gateway/static/app.js` (shell render + routing; auth wired in Task 2 — for now bootstrap renders the shell directly)

**Interfaces:**
- Produces: CSS classes `btn btn-primary|btn-secondary|btn-destructive|btn-ghost` + `btn-sm|btn-lg`, `chip`, `chip-active`, `status-chip` + `sc-default|sc-warning|sc-active|sc-error`, `input-field`, `panel`, `card`, `card-hero`; shell classes `shell`, `sidebar`, `statusbar`, `main`, `drawer`.
- Produces (JS): `renderShell()`, `setScreen(name)`, `getStatus()` (calls `GET /api/status`), `state.screen`.

- [ ] **Step 1: Rewrite `styles.css` tokens + reset + typography**

```css
:root {
  --c-black:#000; --c-white:#fff; --c-bg:#E8E8E8; --c-panel:#F0F0F0;
  --c-grey:#808080; --c-dark:#333; --c-warn:#FFA500; --c-ok:#008000;
  --c-danger:#FF0000; --c-link:#0000FF;
  --bd:3px solid var(--c-black); --bd-hero:5px solid var(--c-black);
  --bd-sm:2px solid var(--c-black); --bd-in:1px solid var(--c-black);
  --font-headline:"Arial Black","Helvetica Neue",Arial,sans-serif;
  --font-body:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  --font-mono:ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%;background:var(--c-bg);color:var(--c-black);font-family:var(--font-body);-webkit-font-smoothing:antialiased}
button,input,textarea{font:inherit;color:inherit}
h1,h2,h3,p{margin:0}
[hidden]{display:none!important}
.mono{font-family:var(--font-mono)}
.headline{font-family:var(--font-headline);text-transform:uppercase;letter-spacing:.5px}
```

- [ ] **Step 2: Add component classes to `styles.css`**

```css
.btn{border:var(--bd);background:var(--c-white);color:var(--c-black);font-family:var(--font-mono);font-size:13px;padding:8px 14px;cursor:pointer;text-transform:uppercase;letter-spacing:1px}
.btn:hover:not(:disabled){background:var(--c-black);color:var(--c-white)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--c-black);color:var(--c-white)}
.btn-primary:hover:not(:disabled){background:var(--c-white);color:var(--c-black)}
.btn-destructive{border-color:var(--c-danger);color:var(--c-danger)}
.btn-destructive:hover:not(:disabled){background:var(--c-danger);color:var(--c-white)}
.btn-ghost{border-color:transparent;background:transparent}
.btn-sm{padding:5px 10px;font-size:11px}
.btn-lg{padding:14px 18px;font-size:14px}
.input-field{width:100%;border:var(--bd);background:var(--c-white);padding:12px 14px;font-family:var(--font-mono)}
.chip{border:var(--bd-sm);background:var(--c-white);font-family:var(--font-mono);font-size:11px;padding:3px 10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px}
.chip-active{background:var(--c-black);color:var(--c-white)}
.status-chip{font-family:var(--font-mono);font-size:11px;padding:1px 8px;border:var(--bd-sm);letter-spacing:1px}
.sc-default{color:var(--c-grey);border-color:var(--c-grey)}
.sc-warning{color:var(--c-warn);border-color:var(--c-warn)}
.sc-active{color:var(--c-ok);border-color:var(--c-ok)}
.sc-error{color:var(--c-danger);border-color:var(--c-danger)}
.panel{border:var(--bd);background:var(--c-white)}
.card-hero{border:var(--bd-hero);background:var(--c-white)}
.planned{color:var(--c-grey);font-family:var(--font-mono);font-size:12px;border:var(--bd) ;border-style:dashed;padding:24px;text-align:center;letter-spacing:1px}
```

- [ ] **Step 3: Add shell layout to `styles.css`**

```css
.shell{display:flex;height:100%}
.sidebar{width:224px;flex:none;border-right:var(--bd);display:flex;flex-direction:column;background:var(--c-white)}
.sidebar-brand{padding:18px;border-bottom:var(--bd)}
.sidebar-nav{flex:1;overflow-y:auto}
.nav-item{display:flex;align-items:center;justify-content:space-between;width:100%;padding:13px 18px;border:none;border-bottom:var(--bd-in);background:var(--c-white);color:var(--c-black);font-family:var(--font-body);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:1px;cursor:pointer;text-align:left}
.nav-item:hover{background:var(--c-black);color:var(--c-white)}
.nav-item-active{background:var(--c-black);color:var(--c-white)}
.nav-badge{font-family:var(--font-mono);font-size:11px;min-width:22px;height:20px;display:inline-flex;align-items:center;justify-content:center;border:var(--bd-sm)}
.sidebar-foot{border-top:var(--bd);padding:14px 16px}
.main-col{flex:1;min-width:0;display:flex;flex-direction:column}
.statusbar{height:48px;flex:none;border-bottom:var(--bd);display:flex;align-items:stretch;background:var(--c-black);color:var(--c-white)}
.status-item{display:flex;flex-direction:column;justify-content:center;padding:0 16px;border-right:1px solid #333}
.status-k{font-family:var(--font-mono);font-size:9px;letter-spacing:1px;color:var(--c-grey)}
.status-v{font-family:var(--font-mono);font-size:12px}
.content-row{flex:1;min-height:0;display:flex}
.main{flex:1;min-width:0;overflow-y:auto;background:var(--c-white)}
.drawer{width:300px;flex:none;border-left:var(--bd);display:flex;flex-direction:column;overflow-y:auto}
```

- [ ] **Step 4: Replace `index.html` body with the shell skeleton**

```html
<body>
  <main id="app"></main>
  <script src="/static/app.js"></script>
</body>
```
Keep the existing `<head>` (title, viewport, `/static/styles.css` link).

- [ ] **Step 5: Rewrite `app.js` with state, api.getStatus, router, and shell render**

```js
const NAV = [
  { key: "chat", label: "Chat" }, { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" }, { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" }, { key: "settings", label: "Settings" },
];
const PLANNED = new Set(["jobs", "schedules", "capabilities", "artifacts", "settings"]);
const state = { screen: "chat", status: null, authed: true /* Task 2 */ };

const api = {
  async getStatus() { const r = await fetch("/api/status"); return r.ok ? r.json() : null; },
};

function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k === "onclick") n.onclick = v;
    else if (k === "html") n.innerHTML = v; else n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) n.append(c instanceof Node ? c : document.createTextNode(c));
  return n;
}

function setScreen(name) { state.screen = name; renderShell(); }

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
    el("div", { class: "sidebar-foot" }, el("button", { class: "btn btn-sm", onclick: () => {} }, "Log out")),
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

async function bootstrap() {
  state.status = await api.getStatus();
  renderShell();
}
bootstrap();
```

- [ ] **Step 6: Verify in browser**

Run `pwsh scripts/run_local.ps1`; open `http://127.0.0.1:8787/?token=<AGENT_WEB_TOKEN>`. Observe:
- Sidebar (Agent Gateway brand, 6 nav items, Log out), black status bar with WORKSPACE/MODEL/SESSION/PENDING (real) + RUNNING/TUNNEL = PLANNED.
- Clicking a nav item highlights it; Jobs/Schedules/Capabilities/Artifacts/Settings show "PLANNED"; Chat shows the Task-3 placeholder.
- Square corners, heavy black borders, mono labels — matches mockup chrome.

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/static/styles.css src/personal_agent_gateway/static/index.html src/personal_agent_gateway/static/app.js
git commit -m "feat(ui): neo-brutalist shell chrome + screen routing"
```

---

### Task 2: OTP Login + auth gating

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js` (auth api + login views + bootstrap gate)
- Modify: `src/personal_agent_gateway/static/styles.css` (login/hero classes)

**Interfaces:**
- Consumes: `renderShell()`, `el()`, `state` from Task 1.
- Produces: `api.authStatus/login/setupStart/setupVerify/logout`; `state.authStage` (`"login"|"setup"|"recovery"`); `renderLogin()`; bootstrap chooses login vs shell.

- [ ] **Step 1: Add auth API wrappers to `app.js`**

```js
Object.assign(api, {
  async authStatus() { const r = await fetch("/api/auth/status"); return r.ok ? r.json() : { authenticated:false, totp_configured:false }; },
  async login(otp) { const r = await fetch("/api/auth/login", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ otp }) }); return r.ok; },
  async setupStart() { const r = await fetch("/api/auth/setup/start", { method:"POST" }); return r.ok ? r.json() : null; },
  async setupVerify(otp) { const r = await fetch("/api/auth/setup/verify", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ otp }) }); return r.ok ? r.json() : null; },
  async logout() { await fetch("/api/auth/logout", { method:"POST" }); },
});
```

- [ ] **Step 2: Add login state + render (3 stages) to `app.js`**

```js
Object.assign(state, { authStage: "login", authError: "", setup: null, recoveryCodes: [] });

function renderLogin() {
  const app = document.getElementById("app");
  const otp = el("input", { class: "input-field", type: "text", inputmode: "numeric", maxlength: "6", placeholder: "000000" });
  const err = state.authError ? el("div", { class: "mono", html: state.authError, style: "border:3px solid var(--c-danger);color:var(--c-danger);padding:12px 14px;margin-top:16px;font-size:12px" }) : "";
  let body;
  if (state.authStage === "login") {
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Sign in"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:24px" }, "Enter the 6-digit code from your authenticator app."),
      otp, err,
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%",
        onclick: async () => { const ok = await api.login(otp.value.trim()); if (ok) return afterAuth(); state.authError = "Invalid code. Session refused."; renderLogin(); } }, "Continue")),
      el("button", { class: "btn-ghost mono", style: "margin-top:20px;border:none;color:var(--c-link);cursor:pointer;background:none",
        onclick: async () => { state.setup = await api.setupStart(); state.authStage = "setup"; state.authError = ""; renderLogin(); } }, "First time on this device? Set up authenticator"),
    ];
  } else if (state.authStage === "setup") {
    const s = state.setup || {};
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Set up authenticator"),
      el("div", { html: s.qr_svg || "", style: "width:140px;height:140px;border:var(--bd)" }),
      el("div", { class: "mono", style: "margin-top:8px;font-size:12px" }, `KEY ${s.secret || ""}`),
      otp, err,
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%",
        onclick: async () => { const r = await api.setupVerify(otp.value.trim()); if (r && r.enabled) { state.recoveryCodes = r.recovery_codes || []; state.authStage = "recovery"; renderLogin(); } else { state.authError = "Code did not match."; renderLogin(); } } }, "Verify & enable")),
    ];
  } else {
    body = [
      el("div", { class: "headline", style: "font-size:22px;margin-bottom:6px" }, "Recovery codes"),
      el("div", { style: "font-size:13px;color:var(--c-dark);margin-bottom:16px" }, "Store these now. Shown only once."),
      el("div", { class: "mono", style: "border:var(--bd);padding:14px;display:grid;grid-template-columns:1fr 1fr;gap:8px" }, state.recoveryCodes.map(c => el("span", {}, c))),
      el("div", { style: "margin-top:24px" }, el("button", { class: "btn btn-primary btn-lg", style: "width:100%", onclick: () => afterAuth() }, "I have saved these — continue")),
    ];
  }
  app.replaceChildren(el("div", { style: "max-width:520px;margin:64px auto;padding:0 24px" },
    el("div", { class: "card-hero", style: "padding:32px" }, body)));
}
```

- [ ] **Step 3: Gate bootstrap on auth status**

```js
async function afterAuth() { state.authed = true; state.status = await api.getStatus(); renderShell(); }

async function bootstrap() {
  const auth = await api.authStatus();
  if (auth.authenticated) return afterAuth();
  state.authStage = auth.totp_configured ? "login" : "setup";
  if (state.authStage === "setup") state.setup = await api.setupStart();
  renderLogin();
}
```
Wire Log out: in `renderSidebar`, set the button `onclick` to `async () => { await api.logout(); location.reload(); }`.

- [ ] **Step 4: Verify in browser**

Restart app; open `/?token=<token>` with **no** `agent_session` cookie (use a fresh/incognito window).
- If TOTP already configured → OTP login screen; wrong code → red error; correct code → shell appears.
- If not configured → setup screen with QR (inline SVG) + key; verify → recovery codes shown once → continue → shell.
- Log out → returns to login.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/static/app.js src/personal_agent_gateway/static/styles.css
git commit -m "feat(ui): OTP login/setup/recovery screens + auth gating"
```

---

### Task 3: Chat screen (layout A) — sessions, transcript, composer

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js` (chat api + chat view + right drawer)
- Modify: `src/personal_agent_gateway/static/styles.css` (chat layout classes)

**Interfaces:**
- Consumes: `el()`, `state`, `renderShell()`, `renderMain()` from Task 1.
- Produces: `api.history/sendChat/sessions/searchSessions/activate/deleteSession/reset`; `state.messages/sessions/sessionQuery`; `renderChat()` returning the 3-column layout A; `renderMain()` returns `renderChat()` when `state.screen==="chat"`.

- [ ] **Step 1: Add chat API wrappers**

```js
Object.assign(api, {
  async history() { const r = await fetch("/api/history"); return r.ok ? (await r.json()).events : []; },
  async sendChat(message) { const r = await fetch("/api/chat", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ message }) }); return r.ok ? r.json() : null; },
  async sessions() { const r = await fetch("/api/sessions"); return r.ok ? (await r.json()).sessions : []; },
  async searchSessions(q) { const r = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`); return r.ok ? (await r.json()).sessions : []; },
  async activate(id) { const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/activate`, { method:"POST" }); return r.ok ? r.json() : null; },
  async deleteSession(id) { const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method:"DELETE" }); return r.ok ? r.json() : null; },
  async reset() { const r = await fetch("/api/reset", { method:"POST" }); return r.ok ? r.json() : null; },
});
```

- [ ] **Step 2: Derive messages from transcript events (reuse existing mapping semantics)**

```js
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
```

- [ ] **Step 3: Add chat layout CSS**

```css
.chat{display:flex;height:100%}
.sess-rail{width:200px;flex:none;border-right:var(--bd);display:flex;flex-direction:column}
.sess-head{padding:14px 16px;border-bottom:var(--bd);display:flex;justify-content:space-between;align-items:center}
.sess-item{padding:12px 16px;border-bottom:var(--bd-in);cursor:pointer}
.sess-item-active{background:var(--c-black);color:var(--c-white)}
.chat-col{flex:1;min-width:0;display:flex;flex-direction:column}
.transcript{flex:1;overflow-y:auto;padding:24px 28px}
.msg{border:var(--bd);margin:0 auto 18px;max-width:720px}
.msg-head{border-bottom:var(--bd-in);padding:6px 12px;display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:10px;letter-spacing:1px}
.msg-body{padding:12px 14px;font-size:14px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.composer{flex:none;border-top:var(--bd);padding:14px 20px;display:flex;gap:10px;align-items:flex-end}
.composer .input-field{flex:1}
```

- [ ] **Step 4: Render chat (layout A) + wire into `renderMain`**

```js
function renderSessionRail() {
  const search = el("input", { class: "input-field", type: "search", placeholder: "Search" });
  search.value = state.sessionQuery || "";
  search.oninput = async () => { state.sessionQuery = search.value.trim(); state.sessions = state.sessionQuery ? await api.searchSessions(state.sessionQuery) : await api.sessions(); renderShell(); };
  const items = (state.sessions || []).map(se => el("div", { class: `sess-item${se.is_active ? " sess-item-active" : ""}` }, [
    el("div", { style: "font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", onclick: async () => { const d = await api.activate(se.id); if (d) { state.messages = messagesFromEvents(d.events); renderShell(); } } }, se.title || "Untitled"),
    el("div", { class: "mono", style: "font-size:10px;color:var(--c-grey);margin-top:3px" }, `${se.status} · ${se.message_count} msg`),
    el("button", { class: "btn btn-sm", style: "margin-top:6px", onclick: async () => { if (confirm("Delete session?")) { await api.deleteSession(se.id); state.sessions = await api.sessions(); renderShell(); } } }, "Delete"),
  ]));
  return el("div", { class: "sess-rail" }, [
    el("div", { class: "sess-head" }, [el("span", { class: "headline", style: "font-size:12px" }, "Sessions"),
      el("button", { class: "btn btn-sm", onclick: async () => { await api.reset(); state.messages = []; state.sessions = await api.sessions(); renderShell(); } }, "+")]),
    search, el("div", { style: "flex:1;overflow-y:auto" }, items),
  ]);
}

function renderComposer() {
  const ta = el("textarea", { class: "input-field", rows: "2", placeholder: "Ask the agent, or describe a local action…" });
  const send = async () => {
    const msg = ta.value.trim(); if (!msg) return;
    state.messages.push({ role: "user", content: msg }); ta.value = ""; renderShell();
    const data = await api.sendChat(msg);
    if (data) { for (const m of (data.messages || [])) if (typeof m.content === "string") state.messages.push({ role: m.role || "assistant", content: m.content }); state.pendingApproval = data.pending_approval || null; }
    state.status = await api.getStatus(); renderShell();
  };
  return el("div", { class: "composer" }, [ta, el("button", { class: "btn btn-primary", onclick: send }, "Send")]);
}

function renderChat() {
  const msgs = (state.messages || []).map(m => el("div", { class: "msg" }, [
    el("div", { class: "msg-head" }, [el("span", {}, m.role.toUpperCase()), el("span", { style: "color:var(--c-grey)" }, "")]),
    el("div", { class: `msg-body${m.role === "tool" || m.role === "system" ? " mono" : ""}` }, m.content),
  ]));
  return el("div", { class: "chat" }, [
    renderSessionRail(),
    el("div", { class: "chat-col" }, [el("div", { class: "transcript" }, msgs), renderComposer()]),
    renderChatDrawer(),
  ]);
}

function renderChatDrawer() {
  return el("aside", { class: "drawer" }, [
    el("div", { style: "background:var(--c-warn);padding:8px 14px;font-family:var(--font-mono);font-size:11px;letter-spacing:1px" }, "PENDING APPROVAL"),
    el("div", { id: "approval-slot", style: "padding:14px" }, state.pendingApproval ? "" : el("span", { class: "mono", style: "font-size:11px;color:var(--c-grey)" }, "No approvals waiting.")),
    el("div", { class: "planned", style: "margin:14px" }, "SESSION ARTIFACTS — PLANNED"),
    el("div", { class: "planned", style: "margin:14px" }, "ACTIVITY — PLANNED"),
  ]);
}
```
Change `renderMain()` (from Task 1) so `state.screen === "chat"` returns `renderChat()`. In `afterAuth`, also load `state.messages = messagesFromEvents(await api.history()); state.sessions = await api.sessions();` before `renderShell()`.

- [ ] **Step 5: Verify in browser**

After login: Chat shows session rail (search, "+", Delete), transcript, composer, right drawer (No approvals waiting + PLANNED artifacts/activity). Type a message → user bubble appears, assistant reply appears, status bar refreshes. "+" starts a new session; search filters; Delete removes.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/static/app.js src/personal_agent_gateway/static/styles.css
git commit -m "feat(ui): chat screen layout A — sessions, transcript, composer"
```

---

### Task 4: Job Proposal (shell approval) in Chat

**Files:**
- Modify: `src/personal_agent_gateway/static/app.js` (approval api + proposal card)
- Modify: `src/personal_agent_gateway/static/styles.css` (proposal card classes)

**Interfaces:**
- Consumes: `el()`, `state.pendingApproval` (`{id, command}`), `renderShell()`.
- Produces: `api.approve(id)/deny(id)`; `renderProposal(approval)` used in the chat transcript and drawer.

- [ ] **Step 1: Add approval API wrappers**

```js
Object.assign(api, {
  async approve(id) { const r = await fetch(`/api/approvals/${encodeURIComponent(id)}/approve`, { method:"POST" }); return r.ok ? r.json() : null; },
  async deny(id) { const r = await fetch(`/api/approvals/${encodeURIComponent(id)}/deny`, { method:"POST" }); return r.ok ? r.json() : null; },
});
```

- [ ] **Step 2: Add proposal card CSS**

```css
.proposal{border:var(--bd-hero);margin:0 auto;max-width:720px}
.proposal-head{background:var(--c-black);color:var(--c-white);padding:8px 14px;display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11px;letter-spacing:1px}
.kv{display:grid;grid-template-columns:96px 1fr;border:var(--bd-in)}
.kv>div{padding:8px 10px;font-family:var(--font-mono);font-size:12px;border-bottom:var(--bd-in)}
.kv .k{border-right:var(--bd-in);color:var(--c-grey);font-size:10px;letter-spacing:1px}
.console{background:var(--c-black);color:var(--c-white);padding:12px 14px;font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;overflow-x:auto}
```

- [ ] **Step 3: Render the Job Proposal from a pending shell approval**

```js
function renderProposal(a) {
  return el("div", { class: "proposal" }, [
    el("div", { class: "proposal-head" }, [el("span", {}, "JOB PROPOSAL"), el("span", { style: "color:var(--c-warn)" }, "● WAITING APPROVAL")]),
    el("div", { style: "padding:16px" }, [
      el("div", { class: "kv" }, [
        el("div", { class: "k" }, "CAPABILITY"), el("div", {}, "shell.run"),
        el("div", { class: "k" }, "RISK"), el("div", { style: "color:var(--c-danger)" }, "HIGH · runs a local command"),
      ]),
      el("div", { style: "margin-top:12px" }, [el("div", { class: "mono", style: "font-size:10px;color:var(--c-grey);margin-bottom:4px" }, "COMMAND PREVIEW"), el("div", { class: "console" }, a.command)]),
      el("div", { style: "margin-top:16px;display:flex;gap:10px" }, [
        el("button", { class: "btn btn-primary btn-sm", onclick: async () => { const d = await api.approve(a.id); applyRuntime(d); } }, "Approve"),
        el("button", { class: "btn btn-destructive btn-sm", onclick: async () => { const d = await api.deny(a.id); applyRuntime(d); } }, "Deny"),
      ]),
    ]),
  ]);
}

async function applyRuntime(data) {
  if (data) { for (const m of (data.messages || [])) if (typeof m.content === "string") state.messages.push({ role: m.role || "assistant", content: m.content }); state.pendingApproval = data.pending_approval || null; }
  state.messages = messagesFromEvents(await api.history()); state.status = await api.getStatus(); renderShell();
}
```

- [ ] **Step 4: Show the proposal in transcript + drawer when pending**

In `renderChat`, after the messages list, append `state.pendingApproval ? renderProposal(state.pendingApproval) : ""`. In `renderChatDrawer`, when `state.pendingApproval`, replace the "No approvals waiting" slot with a compact `shell.run` + Approve/Deny (reuse `api.approve/deny` + `applyRuntime`). On denial, the transcript's `tool_denial` mapping already renders "Denied: <command>".

- [ ] **Step 5: Verify in browser**

Send a message that makes the agent propose a shell command (e.g., ask it to run a listing). Observe the Job Proposal card (capability shell.run, HIGH, command preview) in the transcript and a compact panel in the drawer. Approve → console result (`$ cmd / exit N / stdout`) appears and proposal clears. Repeat and Deny → "Denied: …" system line; nothing executed.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/static/app.js src/personal_agent_gateway/static/styles.css
git commit -m "feat(ui): shell.run approval rendered as job proposal card"
```

---

### Task 5: Responsive adaptation

**Files:**
- Modify: `src/personal_agent_gateway/static/styles.css` (breakpoints)
- Modify: `src/personal_agent_gateway/static/app.js` (sidebar toggle button)

**Interfaces:**
- Consumes: shell/chat classes from Tasks 1 & 3.
- Produces: `state.navOpen`; `.shell-narrow` behavior via media query + a toggle in the status bar.

- [ ] **Step 1: Add responsive CSS**

```css
@media (max-width:900px){
  .sidebar{position:absolute;z-index:20;height:100%;transform:translateX(-100%);transition:transform .15s}
  .shell.nav-open .sidebar{transform:translateX(0)}
  .drawer{position:absolute;right:0;z-index:10;height:100%;background:var(--c-white)}
  .sess-rail{display:none}
}
.nav-toggle{display:none}
@media (max-width:900px){ .nav-toggle{display:inline-flex;align-items:center;padding:0 14px;background:none;border:none;color:var(--c-white);font-family:var(--font-mono);cursor:pointer} }
```

- [ ] **Step 2: Add a nav toggle to the status bar**

In `renderStatusbar`, prepend `el("button", { class: "nav-toggle", onclick: () => { state.navOpen = !state.navOpen; renderShell(); } }, "☰")`. Add `nav-open` to the `.shell` div class when `state.navOpen`.

- [ ] **Step 3: Verify in browser**

Narrow the window below 900px: sidebar hides behind a ☰ toggle; opening it slides the sidebar in; the right drawer overlays instead of taking a fixed column; the session rail collapses; composer and transcript stay usable. Widen again → full three-region layout returns.

- [ ] **Step 4: Commit**

```bash
git add src/personal_agent_gateway/static/styles.css src/personal_agent_gateway/static/app.js
git commit -m "feat(ui): responsive sidebar + drawer for narrow viewports"
```

---

## Self-Review

- **Spec coverage:** shell+sidebar+statusbar (T1), OTP login/setup/recovery + gating (T2), Chat layout A sessions/transcript/composer + PLANNED drawer sections (T3), shell.run job proposal + console result + denied copy (T4), responsive adaptation (T5), PLANNED policy for 5 screens + RUNNING/TUNNEL + drawer sections (T1/T3), status bar bound to real `/api/status` (T1). All spec sections map to a task.
- **Placeholder scan:** no TBD/TODO; every code step shows real code and real endpoints. "PLANNED" strings are intended product states, not plan placeholders.
- **Type consistency:** `el()`, `state`, `renderShell()`, `renderMain()`, `api.*`, `applyRuntime()`, `messagesFromEvents()` are defined once and reused with consistent signatures across tasks; endpoint paths/shapes match the merged backend (`/api/auth/*`, `/api/status`, `/api/history`, `/api/chat`, `/api/sessions*`, `/api/reset`, `/api/approvals/{id}/approve|deny`).
- **Boundary:** only `static/` files touched; `app.py` untouched (the three files are already served).
