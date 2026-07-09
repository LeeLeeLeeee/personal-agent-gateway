# Gateway Screens (Jobs · Schedules · Artifacts · Settings) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four `… - PLANNED` placeholders in the gateway UI with real screens for Jobs, Artifacts, Settings, and a redefined Schedules (recurring instruction to the local agent), and remove the Capabilities menu.

**Architecture:** Each screen is a new React organism rendered by `GatewayApp` per `screen` value, fed by new `api` client methods over the already-existing FastAPI endpoints. Only Schedules needs backend work: a new `agent` runner + `agent.instruct` capability so a scheduled job runs one agent turn from a stored prompt. Reuse existing UI primitives (`.screen` padding, `StatusBadge`, `useConfirm`/`useToast`, the right detail-drawer pattern from `TeamRunDetail`, filter chips, clipboard-copy).

**Tech Stack:** Python/FastAPI/Pydantic backend, pytest; Vite + React 19, Vitest/Testing Library; vanilla CSS in `src/personal_agent_gateway/static/styles.css`.

## Global Constraints

- Remove `Capabilities` from the sidebar; do NOT add a Capabilities screen.
- Frontend served build lives in `src/personal_agent_gateway/frontend_dist/` (built by `cd frontend && npm run build`); the CSS file is imported by `main.jsx` from `../../src/personal_agent_gateway/static/styles.css`.
- Do not break existing suites: FE `cd frontend && npm test` (currently 51 passing), backend `python -m pytest -q` (currently 187 passing). Windows: run pytest from repo root (`pyproject` sets `pythonpath=src`).
- All four screens wrap content in `<div className="screen">` (24×28 padding) like the existing personas/teams screens.
- Status values come from the backend verbatim: JobStatus ∈ `draft|waiting_approval|queued|running|succeeded|failed|canceled`; JobSource ∈ `chat|manual|schedule|api`.
- Settings is strictly read-only: render only fields the backend returns; omit anything missing; no logout/edit actions.
- Destructive actions (schedule delete) use the existing `useConfirm()` modal; success/failure feedback uses `useToast()`.
- Match the existing brutalist style: `var(--bd)` (3px black) borders, `var(--font-mono)` labels, black header bars for drawers/panels.

## Backend reference (already exists — do not rebuild)

- Jobs `api/jobs.py`: `GET /api/jobs?status=&source=&capability_id=` (repeatable query params), `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events`. Job payload: `id, capability_id, source, title, status, input, command_preview, approval_id, created_at, started_at, finished_at, error_message`. Event payload: `id, kind, payload, created_at`.
- Schedules `api/schedules.py`: `GET /api/schedules`, `POST /api/schedules` (`{name, capability_id, cron_expression, timezone, input_template}`), `POST /api/schedules/{id}/pause`, `POST /api/schedules/{id}/resume`, `POST /api/schedules/{id}/run-now`, `DELETE /api/schedules/{id}`. Payload: `id, name, capability_id, cron_expression, timezone, input_template, enabled, last_run_job_id, last_run_at, next_run_at`.
- Artifacts `api/artifacts.py`: `GET /api/artifacts`, `GET /api/artifacts/{id}`, `GET /api/artifacts/{id}/content` (FileResponse — inline `<img>/<video>/<audio>` and `fetch().text()` all work; also usable as download link), `GET /api/artifacts/{id}/thumbnail` (images). Payload: `id, type, title, relative_path, mime_type, size_bytes, source_job_id, source_session_id, created_at, thumbnail_path, tags, metadata`.
- Settings `api/settings.py`: `GET /api/settings` → `{settings:{workspace_root, session_dir, artifact_root, temp_dir, provider, model, codex_binary, codex_sandbox, codex_approval_policy, codex_timeout_seconds, ffmpeg_binary, ffprobe_binary, capture_binary, job_worker_concurrency, cookie_secure, totp_configured}}`.
- Runner architecture: `runners/base.py` (`Runner` Protocol: `async def run(capability_id, input_json) -> RunResult`), concrete runners in `runners/shell.py|ffmpeg.py|capture.py`, dispatched in `job_worker.py` via `self._runners[self._jobs.runner_type_for(job)]`. Runners dict is assembled in `app.py`.

---

## File Structure

- `frontend/src/api/client.js` — add: `jobs(filters)`, `job(id)`, `jobEvents(id)`, `schedules()`, `createSchedule(payload)`, `pauseSchedule(id)`, `resumeSchedule(id)`, `deleteSchedule(id)`, `runScheduleNow(id)`, `settings()`, and artifact URL helpers `artifactContentUrl(id)` / `artifactThumbnailUrl(id)` / `artifactText(id)`.
- `frontend/src/components/organisms/Sidebar/index.jsx` — remove Capabilities from `NAV`.
- `frontend/src/components/organisms/SettingsView/index.jsx` (+ test) — read-only grouped key/values.
- `frontend/src/components/organisms/ArtifactsView/index.jsx` (+ test) — grid + type filter + viewer drawer.
- `frontend/src/components/organisms/JobsView/index.jsx` (+ test) — table + 2-axis filter + detail drawer.
- `frontend/src/components/organisms/SchedulesView/index.jsx` (+ test) — list + create form (frequency→cron + instruction).
- `frontend/src/lib/cron.js` (+ test) — build a cron string from `{mode, ...}`.
- `frontend/src/components/containers/GatewayApp/index.jsx` — fetch-on-screen + render the four views (replace the `.planned` fallbacks).
- `src/personal_agent_gateway/static/styles.css` — CSS for jobs table, artifact grid/cards, drawers, settings groups, schedule list/form.
- `src/personal_agent_gateway/runners/agent.py` (+ `tests/test_runners.py` case) — new `AgentRunner`.
- `src/personal_agent_gateway/capabilities.py` — add `agent.instruct` capability.
- `src/personal_agent_gateway/app.py` — register the `agent` runner in the runners dict.

---

### Task A: Remove Capabilities from the sidebar

**Files:**
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx`
- Test: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx` (existing "preserves planned tabs" test still passes with Jobs)

- [ ] **Step 1: Edit NAV** — remove the `{ key: "capabilities", label: "Capabilities" }` entry from the exported `NAV` array. Leave `chat, jobs, schedules, artifacts, settings`.
- [ ] **Step 2: Verify** — `cd frontend && npm test`. Expected: PASS (no test asserts a Capabilities nav item; the planned-tab test uses "Jobs").
- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/organisms/Sidebar/index.jsx
git commit -m "feat(ui): remove Capabilities from sidebar nav"
```

---

### Task B: Settings screen (read-only)

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/organisms/SettingsView/index.jsx`
- Create: `frontend/src/components/organisms/SettingsView/SettingsView.test.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Produces: `api.settings(): Promise<object|null>` (returns the `settings` object).
- Produces: `<SettingsView settings={settingsObject} />`.

- [ ] **Step 1: Add client method** to `client.js`:

```js
  async settings() {
    const body = await jsonOrNull(await fetch("/api/settings"));
    return body?.settings || null;
  },
```

- [ ] **Step 2: Write failing SettingsView test** (`SettingsView.test.jsx`):

```jsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingsView } from "./index.jsx";

const settings = {
  workspace_root: "C:/ws", artifact_root: "./data/artifacts", session_dir: "./data/sessions", temp_dir: "./data/temp",
  provider: "codex", model: "default", totp_configured: true,
  ffmpeg_binary: "ffmpeg", ffprobe_binary: "ffprobe", capture_binary: "", job_worker_concurrency: 2,
  codex_sandbox: "workspace-write", codex_approval_policy: "never", codex_timeout_seconds: 120, cookie_secure: false
};

describe("SettingsView", () => {
  it("renders grouped read-only values from the backend", () => {
    render(<SettingsView settings={settings} />);
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("C:/ws")).toBeInTheDocument();
    expect(screen.getByText("AUTHENTICATED")).toBeInTheDocument(); // derived from totp_configured
    expect(screen.getByText("LOCAL ONLY")).toBeInTheDocument();    // static Security row
  });

  it("omits rows whose value is missing", () => {
    render(<SettingsView settings={{ ...settings, capture_binary: "" }} />);
    expect(screen.queryByText(/CAPTURE BINARY/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails** — `cd frontend && npm test -- SettingsView`. Expected: FAIL (module missing).

- [ ] **Step 4: Implement `SettingsView/index.jsx`.** Build `groups` from `settings` (skip rows where the value is falsy/empty). Groups and rows:
  - **Workspace**: `WORKSPACE ROOT`=workspace_root, `ARTIFACT ROOT`=artifact_root, `SESSION DIR`=session_dir, `TEMP DIR`=temp_dir.
  - **Agent**: `PROVIDER / MODEL`=`${provider} · ${model}`, `AUTH`=`totp_configured ? "AUTHENTICATED" : "NOT CONFIGURED"` (color `var(--c-ok)` when authenticated).
  - **Tools**: `FFMPEG`=ffmpeg_binary, `FFPROBE`=ffprobe_binary, `CAPTURE`=capture_binary, `JOB CONCURRENCY`=String(job_worker_concurrency).
  - **Security**: `TUNNEL`=`"LOCAL ONLY"` (static, color `var(--c-ok)`), `COOKIE SECURE`=`cookie_secure ? "ON" : "OFF"`, `SANDBOX`=codex_sandbox, `APPROVAL POLICY`=codex_approval_policy.

  Structure: `<section className="screen settings-view">` → `<h1 className="headline">Settings</h1>` + sub caption → for each group: `<div className="settings-group">` with a `<div className="settings-group-head">{name}</div>` and a `<div className="settings-block">` containing rows `<div className="settings-row"><span className="settings-k mono">{k}</span><span className="settings-v mono" style={{color}}>{v}</span></div>`. Filter out rows with empty `v`.

- [ ] **Step 5: Add CSS** to `styles.css`:

```css
/* ---- settings ---- */
.settings-view{max-width:680px}
.settings-group{margin-bottom:18px}
.settings-group-head{font-family:var(--font-headline);font-size:13px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.settings-block{border:var(--bd)}
.settings-row{display:grid;grid-template-columns:200px 1fr;border-bottom:var(--bd-in)}
.settings-row:last-child{border-bottom:none}
.settings-k{padding:9px 13px;border-right:var(--bd-in);font-size:10px;letter-spacing:1px;color:var(--c-grey)}
.settings-v{padding:9px 13px;font-size:12px}
@media (max-width:900px){.settings-row{grid-template-columns:1fr}.settings-k{border-right:none;border-bottom:var(--bd-in)}}
```

- [ ] **Step 6: Wire in GatewayApp.** Add state `const [settings, setSettings] = useState(null);`. In the `useEffect([screen, authenticated])` block, add `else if (screen === "settings") api.settings().then(setSettings);`. Replace the `.planned` fallback for `screen === "settings"` with `<SettingsView settings={settings} />` (render nothing until loaded, or a small "Loading" note).
- [ ] **Step 7: Run tests** — `cd frontend && npm test`. Expected: PASS.
- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/SettingsView src/personal_agent_gateway/static/styles.css frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat(ui): read-only Settings screen"
```

---

### Task C: Artifacts screen

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/organisms/ArtifactsView/index.jsx` (+ `ArtifactsView.test.jsx`)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`, `styles.css`

**Interfaces:**
- Uses existing `api.artifacts(): Promise<Artifact[]>`.
- Produces URL helpers on `api`: `artifactContentUrl(id) => "/api/artifacts/{id}/content"`, `artifactThumbnailUrl(id) => "/api/artifacts/{id}/thumbnail"`, `artifactText(id) => Promise<string>`.
- Produces `<ArtifactsView artifacts={Artifact[]} />` (self-contained: selection + type filter as local state).

- [ ] **Step 1: Add client helpers** to `client.js`:

```js
  artifactContentUrl(id) {
    return `/api/artifacts/${encodeURIComponent(id)}/content`;
  },
  artifactThumbnailUrl(id) {
    return `/api/artifacts/${encodeURIComponent(id)}/thumbnail`;
  },
  async artifactText(id) {
    const response = await fetch(this.artifactContentUrl(id));
    return response.ok ? response.text() : "";
  },
```

- [ ] **Step 2: Write failing ArtifactsView test:**

```jsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactsView } from "./index.jsx";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

const artifacts = [
  { id: "a1", type: "image", title: "snap.png", relative_path: "captures/snap.png", mime_type: "image/png", size_bytes: 1400000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j1", source_session_id: "s1" },
  { id: "a2", type: "log", title: "run.log", relative_path: "logs/run.log", mime_type: "text/plain", size_bytes: 3000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j2", source_session_id: "s2" }
];

function renderView() {
  return render(<UiProvider><ArtifactsView artifacts={artifacts} /></UiProvider>);
}

describe("ArtifactsView", () => {
  it("shows a card grid and filters by type", async () => {
    renderView();
    expect(screen.getByText("snap.png")).toBeInTheDocument();
    expect(screen.getByText("run.log")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Images$/i }));
    expect(screen.getByText("snap.png")).toBeInTheDocument();
    expect(screen.queryByText("run.log")).not.toBeInTheDocument();
  });

  it("opens a viewer drawer with provenance and copy path", async () => {
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    renderView();
    await userEvent.click(screen.getByRole("button", { name: /open snap.png/i }));
    expect(screen.getByText("captures/snap.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy path/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("captures/snap.png");
  });
});
```

(Note: `navigator.clipboard` may need defining in the test as in `Statusbar.test.jsx` — `Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn() }, configurable: true })`.)

- [ ] **Step 3: Run to verify fail** — `npm test -- ArtifactsView`. Expected: FAIL.
- [ ] **Step 4: Implement `ArtifactsView`.** Local state: `type` (filter, default `"all"`), `selectedId`. Constants: `TYPE_FILTERS = [["all","All"],["image","Images"],["video","Videos"],["audio","Audio"],["log","Logs"],["report","Reports"],["archive","Archives"]]`; `GLYPH = {image:"▦",video:"▶",audio:"♪",log:"≣",report:"¶",archive:"◫"}`. Helpers: `fmtSize(bytes)` (KB/MB), `fmtWhen(iso)` (relative or short date via existing `lib/time.js` if suitable).
  - Header: `<h1 className="headline">Artifacts</h1>` + caption `${grid.length} shown · ./data/artifacts`.
  - Filter chips row: buttons with `aria-pressed`, class `chip`/`chip-active`, filtering `artifacts` by `type`.
  - Grid: `<div className="artifact-grid">` of cards. Each card is a `<button className="artifact-card" aria-label={`Open ${a.title}`} onClick={()=>setSelectedId(a.id)}>`: top glyph area (`GLYPH[a.type]` + type label), body (`title`, `${fmtSize}·${fmtWhen}`).
  - Empty state when filtered list is empty: bordered box "NO ARTIFACTS".
  - Viewer drawer (when `selectedId`): right panel, black header `ARTIFACT · {type}` + `✕` (clears selection). Preview by type:
    - image → `<img src={api.artifactContentUrl(id)} />` (fallback thumbnail).
    - log/report → `useEffect` fetch `api.artifactText(id)` into state, render in a `<pre className="mono">`.
    - video → `<video controls src={contentUrl} />`; audio → `<audio controls src={contentUrl} />`.
    - archive → icon + size only.
  - Provenance table (`PATH`=relative_path, `SIZE`=`${fmtSize} · ${mime_type}`, `JOB`=source_job_id, `SESSION`=source_session_id).
  - Actions: `Download` = `<a className="btn btn-primary btn-sm" href={contentUrl} download>Download</a>`; `Copy path` = button → `navigator.clipboard.writeText(relative_path)` then `useToast()("경로가 복사되었습니다","success")`.
- [ ] **Step 5: CSS** (`styles.css`): `.artifact-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}` plus `.artifact-card` (border, button reset, hover `background:var(--c-panel)`), `.artifact-card-thumb` (height 84px, border-bottom, centered glyph, relative; type label absolute top-right), `.artifact-card-body`, and a `.artifact-drawer` reusing the drawer look (border-left 3px, black header). Add responsive `@media (max-width:1100px)` to stack the drawer under the grid.
- [ ] **Step 6: Wire in GatewayApp.** Add `const [artifacts, setArtifacts] = useState([]);`; in the screen effect add `else if (screen === "artifacts") api.artifacts().then(setArtifacts);`. Replace `screen === "artifacts"` planned fallback with `<div className="screen"><ArtifactsView artifacts={artifacts} /></div>`.
- [ ] **Step 7: Run tests** — `npm test`. Expected: PASS.
- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/ArtifactsView src/personal_agent_gateway/static/styles.css frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat(ui): Artifacts grid + type-aware viewer"
```

---

### Task D: Jobs screen

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/organisms/JobsView/index.jsx` (+ `JobsView.test.jsx`)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`, `styles.css`

**Interfaces:**
- Produces `api.jobs(): Promise<Job[]>`, `api.jobEvents(id): Promise<Event[]>`.
- Produces `<JobsView jobs={Job[]} onLoadEvents={(id)=>Promise<Event[]>} />` — filters + selection are local state; the parent supplies the events loader.

- [ ] **Step 1: Client methods:**

```js
  async jobs() {
    return jsonList(await fetch("/api/jobs"), "jobs");
  },
  async jobEvents(id) {
    return jsonList(await fetch(`/api/jobs/${encodeURIComponent(id)}/events`), "events");
  },
```

(Filtering is done client-side over the full list so both filter rows stay visible; the backend query params are available if server-side filtering is preferred later.)

- [ ] **Step 2: Write failing JobsView test:**

```jsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { JobsView } from "./index.jsx";

const jobs = [
  { id: "j1", title: "Extract audio", capability_id: "ffmpeg.extract-audio", source: "chat", status: "succeeded", input: { source_file: "a.mov" }, command_preview: "ffmpeg -i a.mov", created_at: "2026-07-08T00:00:00Z" },
  { id: "j2", title: "Nightly compress", capability_id: "ffmpeg.thumbnail", source: "schedule", status: "failed", input: {}, command_preview: "ffmpeg ...", created_at: "2026-07-08T00:00:00Z" }
];

describe("JobsView", () => {
  it("filters by status independently of source", async () => {
    render(<JobsView jobs={jobs} onLoadEvents={vi.fn().mockResolvedValue([])} />);
    expect(screen.getByText("Extract audio")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Failed$/i }));
    expect(screen.queryByText("Extract audio")).not.toBeInTheDocument();
    expect(screen.getByText("Nightly compress")).toBeInTheDocument();
  });

  it("opens a detail drawer with command and loads logs", async () => {
    const onLoadEvents = vi.fn().mockResolvedValue([{ id: "e1", kind: "log", payload: { line: "started" }, created_at: "2026-07-08T00:00:00Z" }]);
    render(<JobsView jobs={jobs} onLoadEvents={onLoadEvents} />);
    await userEvent.click(screen.getByRole("button", { name: /open Extract audio/i }));
    expect(screen.getByText("ffmpeg -i a.mov")).toBeInTheDocument();
    expect(onLoadEvents).toHaveBeenCalledWith("j1");
    expect(await screen.findByText(/started/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify fail** — `npm test -- JobsView`. Expected: FAIL.
- [ ] **Step 4: Implement `JobsView`.** Local state: `statusFilter` ("all"), `sourceFilter` ("all"), `selectedId`, `events` ([]). Constants: `STATUS = [["all","All"],["waiting_approval","Waiting"],["running","Running"],["succeeded","Succeeded"],["failed","Failed"],["canceled","Canceled"],["draft","Draft"]]`, `SOURCE = [["all","All"],["chat","Chat"],["manual","Manual"],["schedule","Schedule"]]`.
  - Header: `Jobs` + `${rows.length} shown`.
  - Two chip rows (STATUS, SOURCE) — buttons with `aria-pressed`, class `chip`/`chip-active`.
  - `rows = jobs.filter(j => (statusFilter==="all"||j.status===statusFilter) && (sourceFilter==="all"||j.source===sourceFilter))`.
  - Table: header row (TITLE·CAPABILITY·SOURCE·STATUS·TIME) then a row per job as `<button className="jobs-row" aria-label={`Open ${j.title}`} onClick={()=>selectJob(j.id)}>` with cells; STATUS cell uses `<StatusBadge kind={j.status} />`; TIME = `fmtWhen(j.finished_at||j.started_at||j.created_at)`.
  - Empty state when `rows` empty (keep both filter rows visible): bordered "NO JOBS MATCH".
  - `selectJob(id)` sets `selectedId` and calls `onLoadEvents(id).then(setEvents)`.
  - Detail drawer (read-only): black header `JOB DETAIL` + `✕`; title; `<StatusBadge>`; a key/value table (CAPABILITY, SOURCE, INPUT=`JSON.stringify(input)`); `COMMAND` block (`command_preview` on black); `LOGS` block rendering `events` (each `payload.line || JSON.stringify(payload)`), with a blinking `● LIVE` marker when `status==="running"`; `error_message` if present. Footer action: `Copy command` → clipboard + toast. **No** Run again / approve / deny.
- [ ] **Step 5: CSS** (`styles.css`): `.jobs-table{border:var(--bd)}`, header + `.jobs-row` as `display:grid;grid-template-columns:1fr 150px 90px 120px 70px;border-bottom:var(--bd-in);` (button reset, hover `var(--c-panel)`, selected `var(--c-panel)`), `.jobs-drawer` (border-left drawer), reuse `.console`/`.cmd-*` styling for command/logs where convenient. Responsive: hide CAPABILITY/SOURCE columns under 900px (stacked card look) or allow horizontal scroll.
- [ ] **Step 6: Wire in GatewayApp.** Add `const [jobs, setJobs] = useState([]);`; screen effect: `else if (screen === "jobs") api.jobs().then(setJobs);`. Render `<div className="screen"><JobsView jobs={jobs} onLoadEvents={api.jobEvents} /></div>` for `screen==="jobs"`.
- [ ] **Step 7: Run tests** — `npm test`. Expected: PASS.
- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/JobsView src/personal_agent_gateway/static/styles.css frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat(ui): read-only Jobs table + detail drawer"
```

---

### Task E: Backend `agent.instruct` capability + agent runner

**Files:**
- Create: `src/personal_agent_gateway/runners/agent.py`
- Modify: `src/personal_agent_gateway/capabilities.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_runners.py`, `tests/test_capabilities.py` (or wherever the default catalog is asserted)

**Interfaces:**
- Produces capability `agent.instruct` (runner_type `agent`, required input `prompt`, output `log`, `requires_approval=False`, `risk_level="medium"`).
- Produces `AgentRunner(runtime_factory)` implementing `runners/base.py`'s `Runner`: `async def run(capability_id, input_json) -> RunResult`.

- [ ] **Step 1: Read `runners/base.py`** to learn the exact `RunResult` fields and how `runners/shell.py` builds one (log lines / artifact paths / exit info). Mirror `ShellRunner`'s return shape.
- [ ] **Step 2: Write failing runner test** in `tests/test_runners.py` (mirror the existing runner tests' fixtures). The test constructs `AgentRunner` with a fake runtime factory whose runtime returns a known text for a turn, calls `await runner.run("agent.instruct", {"prompt": "hi"})`, and asserts the `RunResult` contains that text (as a log line) and reports success. Match `RunResult`'s real field names discovered in Step 1.
- [ ] **Step 3: Run to verify fail** — `python -m pytest tests/test_runners.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement `AgentRunner`.** It takes an `AgentRuntimeFactory` (or a callable producing an `AgentRuntime`). `run()` reads `input_json["prompt"]` (raise a clear error if absent), builds a runtime (`create_default_runtime()`), sends the prompt as a single user turn, captures the agent's response text, and returns a `RunResult` with the response as a log line and success status (no artifact). Keep it small and mirror `ShellRunner` structure/return type exactly.
- [ ] **Step 5: Add the capability** to the `CapabilityRegistry.default()` list in `capabilities.py`:

```python
                Capability(
                    id="agent.instruct",
                    title="Instruct Agent",
                    description="Send a saved instruction to the local agent on a schedule.",
                    category="Agent",
                    risk_level="medium",
                    required_inputs=("prompt",),
                    output_types=("log",),
                    requires_approval=False,
                    runner_type="agent",
                ),
```

- [ ] **Step 6: Register the runner** in `app.py` where the runners dict is built: add `"agent": AgentRunner(<runtime factory already constructed in app.py>)`. Import `AgentRunner`. (Read the surrounding `app.py` to pass the existing runtime factory / config it already has.)
- [ ] **Step 7: Update catalog assertions** if any test enumerates the exact capability count/ids (`tests/test_capabilities.py` and/or `tests/test_api` capability tests) — add `agent.instruct` to expected sets.
- [ ] **Step 8: Run tests** — `python -m pytest tests/test_runners.py tests/test_capabilities.py -q`, then `python -m pytest -q`. Expected: PASS.
- [ ] **Step 9: Commit**

```bash
git add src/personal_agent_gateway/runners/agent.py src/personal_agent_gateway/capabilities.py src/personal_agent_gateway/app.py tests/test_runners.py tests/test_capabilities.py
git commit -m "feat(agent): agent.instruct capability + agent runner for schedules"
```

---

### Task F: Schedules screen (recurring instruction) — depends on Task E

**Files:**
- Create: `frontend/src/lib/cron.js` (+ `cron.test.js`)
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/organisms/SchedulesView/index.jsx` (+ `SchedulesView.test.jsx`)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`, `styles.css`

**Interfaces:**
- Produces `buildCron({ mode, time, weekday, everyMinutes }) => string` and `describeCron(spec) => string` in `lib/cron.js`.
- Produces `api.schedules()`, `api.createSchedule({name, capability_id, cron_expression, timezone, input_template})`, `api.pauseSchedule(id)`, `api.resumeSchedule(id)`, `api.deleteSchedule(id)`, `api.runScheduleNow(id)`.
- Produces `<SchedulesView schedules={Schedule[]} onCreate onPause onResume onDelete onRunNow />`.

- [ ] **Step 1: Write failing cron test** (`lib/cron.test.js`):

```js
import { describe, expect, it } from "vitest";
import { buildCron } from "./cron.js";

describe("buildCron", () => {
  it("daily", () => expect(buildCron({ mode: "daily", time: "09:00" })).toBe("0 9 * * *"));
  it("weekly", () => expect(buildCron({ mode: "weekly", time: "18:00", weekday: 5 })).toBe("0 18 * * 5"));
  it("interval", () => expect(buildCron({ mode: "interval", everyMinutes: 30 })).toBe("*/30 * * * *"));
});
```

- [ ] **Step 2: Run to verify fail** — `npm test -- cron`. Expected: FAIL.
- [ ] **Step 3: Implement `lib/cron.js`** — `buildCron` parses `time` `"HH:MM"` → `"MM HH * * *"` (daily), `"MM HH * * <weekday>"` (weekly), `"*/<n> * * * *"` (interval). Add `describeCron` returning a human string ("Runs daily at 09:00", etc.).
- [ ] **Step 4: Run to verify pass** — `npm test -- cron`. Expected: PASS.
- [ ] **Step 5: Client methods:**

```js
  async schedules() {
    return jsonList(await fetch("/api/schedules"), "schedules");
  },
  async createSchedule(payload) {
    const body = await jsonOrNull(await fetch("/api/schedules", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.schedule || null;
  },
  async pauseSchedule(id) {
    const body = await jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/pause`, { method: "POST" }));
    return body?.schedule || null;
  },
  async resumeSchedule(id) {
    const body = await jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/resume`, { method: "POST" }));
    return body?.schedule || null;
  },
  async runScheduleNow(id) {
    return jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/run-now`, { method: "POST" }));
  },
  async deleteSchedule(id) {
    const response = await fetch(`/api/schedules/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
```

- [ ] **Step 6: Write failing SchedulesView test:**

```jsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SchedulesView } from "./index.jsx";

const schedules = [
  { id: "s1", name: "Nightly digest", cron_expression: "0 9 * * *", enabled: true, next_run_at: "2026-07-10T09:00:00Z", last_run_at: null, input_template: { prompt: "Summarize my workspace" } }
];

describe("SchedulesView", () => {
  it("lists schedules with cron and enabled state", () => {
    render(<SchedulesView schedules={schedules} onCreate={vi.fn()} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
    expect(screen.getByText("Nightly digest")).toBeInTheDocument();
    expect(screen.getByText("0 9 * * *")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
  });

  it("builds an agent-instruction schedule from the form", async () => {
    const onCreate = vi.fn();
    render(<SchedulesView schedules={[]} onCreate={onCreate} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Name"), "Morning brief");
    await userEvent.type(screen.getByLabelText("Instruction"), "Give me a status brief");
    await userEvent.click(screen.getByRole("button", { name: /create schedule/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Morning brief",
      capability_id: "agent.instruct",
      cron_expression: "0 9 * * *",
      input_template: { prompt: "Give me a status brief" }
    }));
  });
});
```

- [ ] **Step 7: Run to verify fail** — `npm test -- SchedulesView`. Expected: FAIL.
- [ ] **Step 8: Implement `SchedulesView`.** Two-column: left list, right create form.
  - **Form** state: `name`, `instruction`, `mode` ("daily"), `time` ("09:00"), `weekday` (5), `everyMinutes` (30). Frequency tabs (Daily/Weekly/Interval) switch `mode`; render the matching control (time input; weekday select + time; minutes number). Show generated cron via `buildCron(...)` + `describeCron(...)` in a black block. Fields: `Name` input (aria-label "Name"), `Instruction` textarea (aria-label "Instruction"). Submit builds `{ name, capability_id: "agent.instruct", cron_expression: buildCron(state), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, input_template: { prompt: instruction.trim() } }` → `onCreate(payload)`. Static policy note ("Auto-approve · runs the local agent").
  - **List**: each row shows `name`, `instruction` (from `input_template.prompt`), cron chip, `ENABLED`/`PAUSED` via `<StatusBadge kind={enabled?"active":"default"}>`, `NEXT · {fmtWhen(next_run_at)}` / `LAST · {fmtWhen(last_run_at) || "never"}`, and actions: Pause/Resume toggle (`onPause`/`onResume`), `Run now` (`onRunNow`), `Delete` (destructive; confirm via `useConfirm()` then `onDelete`).
  - Empty list → "NO SCHEDULES" box.
- [ ] **Step 9: CSS** (`styles.css`): `.schedules-view` two-column grid `grid-template-columns:1fr 356px` (reuse the team-run-new pattern), `.schedule-row` (border-bottom 3px, padding), `.schedule-cron` (mono chip on `var(--c-panel)` with border), `.schedule-form` (border 5px, black header), frequency tabs like `team-run-mode`. Responsive: single column under 1100px.
- [ ] **Step 10: Wire in GatewayApp.** Add `const [schedules, setSchedules] = useState([]);`; screen effect `else if (screen === "schedules") api.schedules().then(setSchedules);`. Handlers: `handleCreateSchedule` (`await api.createSchedule(payload)`, refresh, toast success/error), `handlePauseSchedule`/`handleResumeSchedule` (call api, refresh), `handleDeleteSchedule` (api + refresh + toast), `handleRunScheduleNow` (api + toast "실행을 시작했습니다"). Render `<div className="screen"><SchedulesView schedules={schedules} onCreate={handleCreateSchedule} onPause={handlePauseSchedule} onResume={handleResumeSchedule} onDelete={handleDeleteSchedule} onRunNow={handleRunScheduleNow} /></div>`.
- [ ] **Step 11: Run tests** — `cd frontend && npm test`; then `python -m pytest -q`. Expected: PASS.
- [ ] **Step 12: Commit**

```bash
git add frontend/src/lib/cron.js frontend/src/api/client.js frontend/src/components/organisms/SchedulesView src/personal_agent_gateway/static/styles.css frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat(ui): Schedules — recurring local-agent instruction"
```

---

### Task G: Integration verification

- [ ] **Step 1:** `cd frontend && npm run build` — build succeeds.
- [ ] **Step 2:** `python -m pytest -q` — all pass.
- [ ] **Step 3:** Restart the server (`scripts/run_local.ps1`) since backend changed (Task E), and smoke-test: sidebar has no Capabilities; each of Jobs/Schedules/Artifacts/Settings renders real data; creating a schedule posts `capability_id: "agent.instruct"`; deleting a schedule shows the confirm modal + success toast.

---

## Self-Review

- **Spec coverage:** Jobs (table + 2-axis filter + read-only drawer + logs + copy command) → Task D; dropped Run-again/approve/live-stream per decision. Schedules redefined as recurring agent instruction → Tasks E+F; capability picker removed, `agent.instruct` added. Artifacts (grid + filter + type-aware viewer + provenance + download/copy) → Task C; dropped precise zoom/scrubber/archive-listing. Settings (read-only grouped) → Task B; no logout/edit. Capabilities menu removed → Task A.
- **Placeholder scan:** UI view components specify structure + exact class names + data mapping rather than full JSX; every client method, cron util, backend capability, and all test code is complete. The two backend spots requiring a file read first (RunResult shape in Step E1, runner-dict location in E6) are explicit inspection steps, not vague TODOs.
- **Type consistency:** client method names match their usages in GatewayApp and view props; `capability_id: "agent.instruct"` and `input_template: { prompt }` are consistent between Task E (capability `required_inputs=("prompt",)`) and Task F (form payload + test).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-gateway-screens-jobs-schedules-artifacts-settings.md`.
