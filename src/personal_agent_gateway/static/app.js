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
    if (k === "class") n.className = v;
    else if (k === "onclick") n.onclick = v;
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v);
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
