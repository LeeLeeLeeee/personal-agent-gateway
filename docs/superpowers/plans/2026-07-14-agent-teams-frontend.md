# Agent Teams — 프런트엔드 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team Runs 목록, Team Run 상세(디자인 D + 아바타 + DOCUMENTS 프리뷰), Rules, 신규 Teams 관리 화면을 구현하고 팀 기반 실행 시작 흐름으로 전환한다.

**Architecture:** React 19 + Vite. atoms/molecules/organisms/containers 폴더 구조. 상태는 `containers/GatewayApp`가 소유하고 organisms에 props로 내려준다. API는 `api/client.js`. 전역 CSS는 `src/personal_agent_gateway/static/styles.css`(프런트가 `main.jsx`에서 import). 테스트는 vitest + @testing-library/react, 컴포넌트 옆 `*.test.jsx`.

**Tech Stack:** React 19, Vite 6, Vitest 4, @testing-library/react 16.

## Global Constraints

- **선행 조건:** 백엔드 계획 `2026-07-14-agent-teams-backend.md`가 먼저 완료돼 있어야 한다(팀/규칙/문서 API, 팀 기반 `POST /api/team-runs`, enrich 목록).
- 새 컴포넌트는 `frontend/src/components/<layer>/<Name>/index.jsx`. 테스트는 같은 폴더 `<Name>.test.jsx`.
- CSS 클래스는 `src/personal_agent_gateway/static/styles.css`에 추가. 시각 값(3px 테두리, `var(--font-mono)`/`var(--font-headline)`, 색 `#000/#fff/#FFA500/#FF0000/#008000/#808080/#0000FF`)은 디자인 파일 `Agent Teams.dc.html` 기준.
- 아바타 이미지: `/static/avatars/<code>.png`, 없으면 이니셜 폴백(기존 패턴 재사용).
- 테스트 실행: `frontend/`에서 `npm test`(vitest run). 빌드: `frontend/`에서 `npm run build`(→ `frontend_dist`).
- 커밋 메시지는 한국어 Conventional Commits + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- 브랜치 `agent-teams-rules`.

---

## 파일 구조

- Modify: `frontend/src/api/client.js` — teams/rules/documents 호출, 팀 기반 createTeamRun.
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx` — TEAM_NAV에 Teams·Rules 추가.
- Create: `frontend/src/components/molecules/TeamRunCard/index.jsx` (+ test) — 목록 카드.
- Create: `frontend/src/components/organisms/TeamPicker/index.jsx` (+ test) — 새 실행용 팀 선택.
- Create: `frontend/src/components/organisms/TeamsView/index.jsx` (+ test) — 팀 관리(CRUD/로스터).
- Create: `frontend/src/components/organisms/RulesView/index.jsx` (+ test) — 규칙 편집.
- Create: `frontend/src/components/organisms/DocumentPreview/index.jsx` (+ test) — 문서 모달.
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` (+ test) — 디자인 D 정렬 + DOCUMENTS 탭.
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` — 화면/상태/핸들러.
- Modify: `src/personal_agent_gateway/static/styles.css` — 신규 클래스.

---

## Task 1: API 클라이언트 확장

**Files:**
- Modify: `frontend/src/api/client.js`
- Test: `frontend/src/api/client.test.js` (append)

**Interfaces:**
- Produces on `api`:
  - `teams()`, `createTeam(payload)`, `updateTeam(id, payload)`, `deleteTeam(id)`
  - `rules()`, `updateGlobalRules(payload)`, `updatePersonaBaselineRules(payload)`, `updateTeamRules(teamId, payload)`
  - `teamDocuments(runId)`, `teamDocumentContent(runId, path)`
  - `createTeamRun(payload)` now posts `{team_id, goal, run_mode, max_workers}` (unchanged signature — payload shape changes at call site).

- [ ] **Step 1: Write failing test**

Append to `frontend/src/api/client.test.js` (follow the file's existing `vi.stubGlobal("fetch", ...)` / mock pattern — read the top of the file first). Example:

```javascript
it("teams() returns the teams array", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ teams: [{ id: "t1", name: "Release Crew" }] }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  )));
  const { api } = await import("./client.js");
  const teams = await api.teams();
  expect(teams).toEqual([{ id: "t1", name: "Release Crew" }]);
});

it("teamDocuments() returns the documents array", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ documents: [{ path: "notes.md", kind: "md", previewable: true }] }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  )));
  const { api } = await import("./client.js");
  const docs = await api.teamDocuments("run-1");
  expect(docs[0].path).toBe("notes.md");
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- client`
Expected: FAIL (`api.teams is not a function`).

- [ ] **Step 3: Add methods to `client.js`**

Insert before the closing `};` of the `api` object:

```javascript
  async teams() {
    return jsonList(await fetch("/api/teams"), "teams");
  },
  async createTeam(payload) {
    const body = await jsonOrNull(await fetch("/api/teams", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.team || null;
  },
  async updateTeam(id, payload) {
    const body = await jsonOrNull(await fetch(`/api/teams/${encodeURIComponent(id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.team || null;
  },
  async deleteTeam(id) {
    const response = await fetch(`/api/teams/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async rules() {
    return jsonOrNull(await fetch("/api/rules"));
  },
  async updateGlobalRules(payload) {
    const body = await jsonOrNull(await fetch("/api/rules/global", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async updatePersonaBaselineRules(payload) {
    const body = await jsonOrNull(await fetch("/api/rules/persona-baseline", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async updateTeamRules(teamId, payload) {
    const body = await jsonOrNull(await fetch(`/api/teams/${encodeURIComponent(teamId)}/rules`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async teamDocuments(runId) {
    return jsonList(await fetch(`/api/team-runs/${encodeURIComponent(runId)}/documents`), "documents");
  },
  async teamDocumentContent(runId, path) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(runId)}/documents/content?path=${encodeURIComponent(path)}`
    ));
  },
```

(The existing `createTeamRun(payload)` stays as-is; only its caller changes in Task 4.)

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- client`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js
git commit -m "feat: 프런트 API 클라이언트에 팀·규칙·문서 호출 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 사이드바 내비게이션 (Teams · Rules)

**Files:**
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx`
- Test: `frontend/src/components/organisms/Sidebar/Sidebar.test.jsx` (Create)

**Interfaces:**
- Produces: `TEAM_NAV` = `[{teams, "Team Runs"}, {team-admin, "Teams"}, {personas, "Personas"}, {rules, "Rules"}]`.

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/organisms/Sidebar/Sidebar.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./index.jsx";

describe("Sidebar", () => {
  it("renders Team Runs, Teams, Personas, Rules nav items", () => {
    render(<Sidebar screen="chat" onScreenChange={vi.fn()} />);
    expect(screen.getByText("Team Runs")).toBeInTheDocument();
    expect(screen.getByText("Teams")).toBeInTheDocument();
    expect(screen.getByText("Personas")).toBeInTheDocument();
    expect(screen.getByText("Rules")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- Sidebar`
Expected: FAIL (`Teams`, `Rules` not found).

- [ ] **Step 3: Update `TEAM_NAV`**

In `Sidebar/index.jsx`:

```javascript
export const TEAM_NAV = [
  { key: "teams", label: "Team Runs" },
  { key: "team-admin", label: "Teams" },
  { key: "personas", label: "Personas" },
  { key: "rules", label: "Rules" }
];
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- Sidebar`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/organisms/Sidebar/index.jsx frontend/src/components/organisms/Sidebar/Sidebar.test.jsx
git commit -m "feat: 사이드바에 Teams·Rules 내비게이션 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Team Runs 목록 (TeamRunCard + 상태 필터)

**Files:**
- Create: `frontend/src/components/molecules/TeamRunCard/index.jsx` (+ test)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` (목록 렌더 교체)
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: enriched run 객체(`leader_name`, `members[{name,avatar,initials}]`, `task_counts`, `task_done`, `task_total`, `elapsed_seconds`, `team_id`).
- Produces: `TeamRunCard({ run, onOpen })`.

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/molecules/TeamRunCard/TeamRunCard.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamRunCard } from "./index.jsx";

const run = {
  id: "TR-204", goal: "Ship export-to-PDF", status: "running", run_mode: "plan_and_execute",
  leader_name: "Tech Lead",
  members: [{ name: "Frontend Dev", avatar: "a05", initials: "FD" }],
  task_counts: { completed: 2, in_progress: 1, pending: 3 },
  task_done: 2, task_total: 6, elapsed_seconds: 251, team_id: "t1"
};

describe("TeamRunCard", () => {
  it("shows id, goal, leader, members and task progress", () => {
    render(<TeamRunCard run={run} onOpen={vi.fn()} />);
    expect(screen.getByText("TR-204")).toBeInTheDocument();
    expect(screen.getByText(/Ship export-to-PDF/i)).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(screen.getByText("2 / 6 DONE")).toBeInTheDocument();
  });

  it("calls onOpen when clicked", async () => {
    const onOpen = vi.fn();
    render(<TeamRunCard run={run} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button", { name: /open team run/i }));
    expect(onOpen).toHaveBeenCalledWith("TR-204");
  });

  it("marks legacy runs without a team", () => {
    render(<TeamRunCard run={{ ...run, team_id: null }} onOpen={vi.fn()} />);
    expect(screen.getByText("LEGACY")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- TeamRunCard`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `TeamRunCard/index.jsx`**

```javascript
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";

const SEG_COLORS = {
  completed: "#008000", in_progress: "#FFA500", blocked: "#FF0000",
  failed: "#FF0000", pending: "#E8E8E8", canceled: "#808080"
};
const SEG_ORDER = ["completed", "in_progress", "blocked", "failed", "pending"];
const ACTIVE = new Set(["running", "planning", "summarizing"]);

function fmtElapsed(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function Avatar({ member }) {
  if (member.avatar) {
    return <img className="trc-member-avatar" src={`/static/avatars/${member.avatar}.png`} alt="" />;
  }
  return <span className="trc-member-avatar trc-member-initials mono">{member.initials || "?"}</span>;
}

export function TeamRunCard({ run, onOpen }) {
  const counts = run.task_counts || {};
  const segments = SEG_ORDER
    .filter((key) => counts[key] > 0)
    .map((key) => ({ key, flex: counts[key], color: SEG_COLORS[key] }));
  const active = ACTIVE.has(run.status);

  return (
    <button
      type="button"
      className="trc"
      aria-label={`Open team run ${run.goal}`}
      onClick={() => onOpen(run.id)}
    >
      <div className="trc-main">
        <div className="trc-top">
          <span className="mono trc-id">{run.id}</span>
          <StatusBadge kind={run.status} />
          <span className="mono trc-mode">{run.run_mode}</span>
          {run.team_id ? null : <span className="mono trc-legacy">LEGACY</span>}
        </div>
        <div className="headline trc-goal">{run.goal}</div>
        <div className="trc-roster">
          <span className="mono trc-roster-k">LEADER</span>
          <span className="trc-leader">{run.leader_name || "—"}</span>
          <span className="mono trc-roster-k">MEMBERS</span>
          <span className="trc-members">
            {(run.members || []).map((member, index) => <Avatar key={index} member={member} />)}
          </span>
        </div>
      </div>
      <div className="trc-progress">
        <div className="trc-progress-head">
          <span className="mono trc-progress-k">TASKS</span>
          <span className="mono trc-progress-v">{run.task_done} / {run.task_total} DONE</span>
        </div>
        <div className="trc-bar">
          {segments.map((seg) => (
            <span key={seg.key} style={{ flex: seg.flex, background: seg.color }} />
          ))}
        </div>
        <div className="trc-progress-foot">
          <span className="mono trc-elapsed">{active ? "ELAPSED" : "TOOK"} · {fmtElapsed(run.elapsed_seconds)}</span>
          <span className="mono trc-open">OPEN →</span>
        </div>
      </div>
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd frontend && npm test -- TeamRunCard`
Expected: PASS.

- [ ] **Step 5: Add CSS**

Append to `src/personal_agent_gateway/static/styles.css` (values from design A card):

```css
.trc { display: flex; width: 100%; border: 3px solid #000; background: #fff; padding: 0; cursor: pointer; align-items: stretch; text-align: left; }
.trc:hover { background: #F0F0F0; }
.trc-main { flex: 1; min-width: 0; padding: 16px 18px; border-right: 1px solid #000; }
.trc-top { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.trc-id, .trc-mode { font-size: 10px; color: #808080; letter-spacing: 1px; }
.trc-legacy { font-size: 9px; letter-spacing: 1px; border: 1px solid #808080; color: #808080; padding: 1px 5px; }
.trc-goal { font-size: 19px; line-height: 1.15; text-transform: uppercase; }
.trc-roster { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.trc-roster-k { font-size: 10px; color: #808080; letter-spacing: 1px; }
.trc-leader { border: 2px solid #000; padding: 2px 7px; font-family: var(--font-mono); font-size: 11px; }
.trc-members { display: flex; gap: 5px; }
.trc-member-avatar { width: 24px; height: 24px; border: 2px solid #000; object-fit: cover; display: block; }
.trc-member-initials { display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; background: #E8E8E8; }
.trc-progress { width: 280px; flex: none; padding: 16px 18px; display: flex; flex-direction: column; justify-content: center; }
.trc-progress-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.trc-progress-k { font-size: 10px; letter-spacing: 1px; color: #808080; }
.trc-progress-v { font-size: 13px; }
.trc-bar { display: flex; height: 16px; border: 2px solid #000; }
.trc-bar > span { border-right: 1px solid #000; }
.trc-bar > span:last-child { border-right: none; }
.trc-progress-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; }
.trc-elapsed { font-size: 10px; color: #808080; }
.trc-open { font-size: 10px; color: #0000FF; }
```

- [ ] **Step 6: Wire into GatewayApp list + status filter**

In `containers/GatewayApp/index.jsx`:

Add import: `import { TeamRunCard } from "../../molecules/TeamRunCard/index.jsx";`

Add filter state near other useState hooks:

```javascript
  const [runFilter, setRunFilter] = useState("all");
```

Replace the `team-run-list` block (the `.map` rendering `team-run-list-item`) with:

```jsx
            <div className="team-runs-filter">
              <span className="mono team-runs-filter-k">STATUS</span>
              {[["all", "All"], ["running", "Running"], ["completed", "Completed"], ["failed", "Failed"]].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`chip${runFilter === key ? " chip-active" : ""}`}
                  aria-pressed={runFilter === key}
                  onClick={() => setRunFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="team-run-list">
              {teamRuns
                .filter((run) => {
                  if (runFilter === "all") return true;
                  if (runFilter === "running") return run.status === "running" || run.status === "planning";
                  if (runFilter === "completed") return run.status === "completed" || run.status === "completed_with_failures";
                  if (runFilter === "failed") return run.status === "failed";
                  return true;
                })
                .map((run) => (
                  <TeamRunCard key={run.id} run={run} onOpen={handleSelectTeamRun} />
                ))}
            </div>
```

(Delete the old per-item Delete button UI. Deletion moves to the detail view or stays out of the list per design; if delete must remain, keep a small control — but the design list has no delete, so remove it here.)

Add CSS for filter chips:

```css
.team-runs-filter { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 18px 0 16px; }
.team-runs-filter-k { font-size: 10px; letter-spacing: 1px; color: #808080; width: 52px; }
.chip { border: 2px solid #000; background: #fff; color: #000; font-family: var(--font-mono); font-size: 11px; padding: 3px 10px; cursor: pointer; }
.chip-active { background: #000; color: #fff; }
.team-run-list { display: flex; flex-direction: column; gap: 14px; }
```

- [ ] **Step 7: Run frontend tests + build**

Run: `cd frontend && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/molecules/TeamRunCard frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: Team Runs 목록 리치 카드와 상태 필터

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 팀 기반 New Team Run 흐름

**Files:**
- Create: `frontend/src/components/organisms/TeamPicker/index.jsx` (+ test)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: `teams` (list with `leader`, `members` 요약).
- Produces: `TeamPicker({ teams, onStart })` — 팀 선택 + 로스터 읽기전용 표시 + goal/run_mode/max_workers → `onStart({team_id, goal, run_mode, max_workers})`.

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/organisms/TeamPicker/TeamPicker.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamPicker } from "./index.jsx";

const teams = [{
  id: "t1", name: "Release Crew",
  leader: { name: "Tech Lead", avatar: "a01" },
  members: [{ name: "QA", avatar: "a08" }]
}];

describe("TeamPicker", () => {
  it("shows the selected team roster read-only and starts a run", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/goal/i), "ship it");
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({
      team_id: "t1", goal: "ship it", run_mode: "planning_only"
    }));
  });

  it("prompts to create a team when none exist", () => {
    render(<TeamPicker teams={[]} onStart={vi.fn()} />);
    expect(screen.getByText(/먼저 팀을 만드세요/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- TeamPicker`
Expected: FAIL.

- [ ] **Step 3: Implement `TeamPicker/index.jsx`**

```javascript
import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const RUN_MODES = [
  { value: "planning_only", label: "PLANNING ONLY", desc: "Leader decomposes the goal and drafts tasks. Nothing executes." },
  { value: "plan_and_execute", label: "PLAN + EXECUTE", desc: "Leader plans, then members execute their tasks and report back." },
  { value: "review_only", label: "REVIEW ONLY", desc: "Members review existing work against their persona and report findings." }
];

function Avatar({ person }) {
  if (person?.avatar) return <img className="tp-avatar" src={`/static/avatars/${person.avatar}.png`} alt="" />;
  return <span className="tp-avatar tp-avatar-initials mono">{(person?.name || "?").slice(0, 2).toUpperCase()}</span>;
}

export function TeamPicker({ teams = [], onStart }) {
  const [teamId, setTeamId] = useState("");
  const [goal, setGoal] = useState("");
  const [runMode, setRunMode] = useState("planning_only");
  const [maxWorkers, setMaxWorkers] = useState(3);

  useEffect(() => {
    if (!teamId && teams.length) setTeamId(teams[0].id);
  }, [teams, teamId]);

  if (!teams.length) {
    return <div className="tp-empty mono">먼저 팀을 만드세요 — Teams 화면에서 팀과 로스터를 구성할 수 있습니다.</div>;
  }

  const team = teams.find((t) => t.id === teamId) || teams[0];
  const activeMode = RUN_MODES.find((m) => m.value === runMode) || RUN_MODES[0];

  return (
    <form className="tp" aria-label="New team run" onSubmit={(event) => {
      event.preventDefault();
      onStart({ team_id: team.id, goal: goal.trim(), run_mode: runMode, max_workers: Number(maxWorkers) || 1 });
    }}>
      <div className="tp-form">
        <div className="tp-field">
          <span className="tp-label">Team</span>
          <div className="tp-teams">
            {teams.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-pressed={t.id === team.id}
                className={`tp-team${t.id === team.id ? " active" : ""}`}
                onClick={() => setTeamId(t.id)}
              >
                {t.name}
              </button>
            ))}
          </div>
        </div>

        <div className="tp-field">
          <span className="tp-label">Roster (locked)</span>
          <div className="tp-roster">
            <div className="tp-roster-row">
              <Avatar person={team.leader} />
              <span className="mono tp-roster-name">{team.leader?.name || "—"}</span>
              <span className="mono tp-roster-role">LEADER</span>
            </div>
            {(team.members || []).map((member, index) => (
              <div className="tp-roster-row" key={index}>
                <Avatar person={member} />
                <span className="mono tp-roster-name">{member.name}</span>
                <span className="mono tp-roster-role">MEMBER</span>
              </div>
            ))}
          </div>
        </div>

        <div className="tp-field">
          <span className="tp-label" id="tp-goal-label">Goal</span>
          <textarea
            className="tp-goal"
            aria-labelledby="tp-goal-label"
            aria-label="Goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="What should the team accomplish, end to end?"
          />
        </div>

        <div className="tp-settings">
          <div className="tp-field">
            <span className="tp-label">Run mode</span>
            <div className="tp-mode" role="group" aria-label="Run mode">
              {RUN_MODES.map((mode) => (
                <button key={mode.value} type="button" aria-pressed={runMode === mode.value}
                  className={`tp-mode-btn${runMode === mode.value ? " active" : ""}`}
                  onClick={() => setRunMode(mode.value)}>{mode.label}</button>
              ))}
            </div>
            <div className="tp-mode-desc">{activeMode.desc}</div>
          </div>
          <div className="tp-field">
            <span className="tp-label">Max workers</span>
            <div className="tp-workers">
              <button type="button" aria-label="Decrease workers" onClick={() => setMaxWorkers((v) => Math.max(1, v - 1))}>−</button>
              <div className="tp-workers-val" aria-label="Max workers">{maxWorkers}</div>
              <button type="button" aria-label="Increase workers" onClick={() => setMaxWorkers((v) => Math.min(8, v + 1))}>+</button>
            </div>
          </div>
        </div>
      </div>

      <aside className="tp-preview">
        <div className="tp-preview-head">RUN PREVIEW</div>
        <div className="tp-preview-body">
          <div className="tp-preview-kv">
            <div className="k">TEAM</div><div>{team.name}</div>
            <div className="k">MEMBERS</div><div>{(team.members || []).length} agents</div>
            <div className="k">MODE</div><div>{activeMode.label}</div>
            <div className="k">WORKERS</div><div>max {maxWorkers} concurrent</div>
          </div>
          <div className="tp-preview-action">
            <Button type="submit" variant="primary" size="btn-lg">Start team run</Button>
          </div>
        </div>
      </aside>
    </form>
  );
}
```

- [ ] **Step 4: Run TeamPicker test**

Run: `cd frontend && npm test -- TeamPicker`
Expected: PASS.

- [ ] **Step 5: Wire into GatewayApp; load teams; replace TeamRunForm**

In `GatewayApp/index.jsx`:

- Add state: `const [teams, setTeams] = useState([]);`
- In the screen loader `useEffect`, extend the `teams` branch and add loaders:

```javascript
    } else if (screen === "teams") {
      api.teamRuns().then(setTeamRuns);
      api.teams().then(setTeams);
    } else if (screen === "team-admin") {
      api.personas().then(setPersonas);
      api.teams().then(setTeams);
    } else if (screen === "rules") {
      api.rules().then(setRules);
      api.teams().then(setTeams);
    }
```

- Replace `handleCreateTeamRun` to post team-based payload and start:

```javascript
  async function handleCreateTeamRun(payload) {
    try {
      const created = await api.createTeamRun(payload); // {team_id, goal, run_mode, max_workers}
      if (!created) { toast("Failed to create team run", "error"); return; }
      const started = await api.startTeamRun(created.id);
      if (!started) { toast("Failed to start team run", "error"); return; }
      setCreatingTeamRun(false);
      setTeamRuns(await api.teamRuns());
      setSelectedTeamRunId(started.id);
      toast("Team run started", "success");
    } catch (_error) {
      toast("Failed to create team run", "error");
    }
  }
```

- Replace the `creatingTeamRun` branch body: swap `<TeamRunForm personas={personas} .../>` for `<TeamPicker teams={teams} onStart={handleCreateTeamRun} />`. Add import and remove the now-unused `TeamRunForm` import.

- [ ] **Step 6: Add CSS for TeamPicker**

Append to `styles.css` (reuse existing `team-run-*` layout tokens where possible; new classes):

```css
.tp { display: grid; grid-template-columns: 1fr 380px; gap: 24px; align-items: start; margin-top: 22px; }
.tp-empty { border: 3px solid #000; padding: 24px; color: #808080; }
.tp-form { display: flex; flex-direction: column; gap: 22px; }
.tp-label { font-family: var(--font-headline); font-size: 13px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px; }
.tp-teams { display: flex; flex-wrap: wrap; gap: 8px; }
.tp-team { border: 3px solid #CCC; background: #fff; padding: 8px 14px; cursor: pointer; font-family: var(--font-mono); font-size: 12px; }
.tp-team.active { border-color: #000; background: #000; color: #fff; }
.tp-roster { border: 3px solid #000; }
.tp-roster-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #000; }
.tp-roster-row:last-child { border-bottom: none; }
.tp-avatar { width: 26px; height: 26px; border: 2px solid #000; object-fit: cover; }
.tp-avatar-initials { display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; background: #E8E8E8; }
.tp-roster-name { font-size: 12px; font-weight: 700; flex: 1; }
.tp-roster-role { font-size: 9px; color: #808080; letter-spacing: 1px; }
.tp-goal { border: 3px solid #000; padding: 12px 14px; font-family: var(--font-body); font-size: 15px; min-height: 64px; width: 100%; box-sizing: border-box; }
.tp-settings { display: grid; grid-template-columns: 1fr 200px; gap: 20px; }
.tp-mode { display: flex; border: 3px solid #000; }
.tp-mode-btn { flex: 1; border: none; border-right: 2px solid #000; background: #fff; font-family: var(--font-mono); font-size: 10px; padding: 9px 4px; cursor: pointer; }
.tp-mode-btn:last-child { border-right: none; }
.tp-mode-btn.active { background: #000; color: #fff; }
.tp-mode-desc { font-size: 12px; color: #555; margin-top: 8px; }
.tp-workers { display: flex; border: 3px solid #000; }
.tp-workers button { flex: none; width: 42px; border: none; background: #fff; font-family: var(--font-mono); font-size: 16px; cursor: pointer; }
.tp-workers button:first-child { border-right: 2px solid #000; }
.tp-workers button:last-child { border-left: 2px solid #000; }
.tp-workers-val { flex: 1; text-align: center; padding: 9px 0; font-family: var(--font-mono); font-size: 16px; }
.tp-preview { border: 5px solid #000; position: sticky; top: 0; }
.tp-preview-head { background: #000; color: #fff; padding: 8px 14px; font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; }
.tp-preview-body { padding: 16px; }
.tp-preview-kv { display: grid; grid-template-columns: 88px 1fr; border: 1px solid #000; }
.tp-preview-kv > div { padding: 8px 10px; border-bottom: 1px solid #000; font-family: var(--font-mono); font-size: 12px; }
.tp-preview-kv > .k { border-right: 1px solid #000; font-size: 10px; color: #808080; letter-spacing: 1px; }
.tp-preview-action { margin-top: 14px; }
```

- [ ] **Step 7: Run tests**

Run: `cd frontend && npm test`
Expected: PASS. (If a `TeamRunForm` test exists and now fails due to removal, delete that obsolete test file — the persona-picking form is intentionally replaced.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/organisms/TeamPicker frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: 팀에서 시작하는 New Team Run 흐름(TeamPicker)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Teams 관리 화면 (CRUD + 로스터)

**Files:**
- Create: `frontend/src/components/organisms/TeamsView/index.jsx` (+ test)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` (screen + handlers)
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: `teams`, `personas`.
- Produces: `TeamsView({ teams, personas, onCreate, onUpdate, onDelete })`. Handlers async, return created/updated or bool.

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/organisms/TeamsView/TeamsView.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamsView } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "lead", avatar: "a01" },
  { id: "p2", name: "QA", role: "qa", avatar: "a08" }
];

describe("TeamsView", () => {
  it("creates a team with a leader and members", async () => {
    const onCreate = vi.fn(async () => ({ id: "t1" }));
    render(<TeamsView teams={[]} personas={personas} onCreate={onCreate} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /new team/i }));
    await userEvent.type(screen.getByLabelText(/team name/i), "Release Crew");
    await userEvent.click(screen.getByRole("button", { name: /save team/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "Release Crew" }));
  });

  it("lists existing teams", () => {
    render(<TeamsView teams={[{ id: "t1", name: "Release Crew", leader: { name: "Tech Lead" }, members: [] }]}
      personas={personas} onCreate={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("Release Crew")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- TeamsView`
Expected: FAIL.

- [ ] **Step 3: Implement `TeamsView/index.jsx`**

```javascript
import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const EMPTY = { name: "", description: "", leader_persona_id: "", member_persona_ids: [] };

export function TeamsView({ teams = [], personas = [], onCreate, onUpdate, onDelete }) {
  const [editingId, setEditingId] = useState(null); // null = none, "new" = create
  const [draft, setDraft] = useState(EMPTY);

  useEffect(() => {
    if (editingId === "new" && !draft.leader_persona_id && personas.length) {
      setDraft((d) => ({ ...d, leader_persona_id: personas[0].id }));
    }
  }, [editingId, personas, draft.leader_persona_id]);

  function startCreate() { setDraft(EMPTY); setEditingId("new"); }
  function startEdit(team) {
    setDraft({
      name: team.name, description: team.description || "",
      leader_persona_id: team.leader_persona_id,
      member_persona_ids: [...(team.member_persona_ids || [])]
    });
    setEditingId(team.id);
  }
  function toggleMember(id) {
    setDraft((d) => ({
      ...d,
      member_persona_ids: d.member_persona_ids.includes(id)
        ? d.member_persona_ids.filter((x) => x !== id)
        : [...d.member_persona_ids, id]
    }));
  }
  async function save() {
    const payload = { ...draft, member_persona_ids: draft.member_persona_ids.filter((id) => id !== draft.leader_persona_id) };
    const result = editingId === "new" ? await onCreate(payload) : await onUpdate(editingId, payload);
    if (result) setEditingId(null);
  }

  return (
    <section className="teams-view" aria-label="Teams">
      <div className="teams-view-head">
        <div>
          <h1 className="headline" style={{ fontSize: 34 }}>Teams</h1>
          <div className="teams-view-sub">팀에 페르소나를 할당하고 실행을 시작할 로스터를 구성합니다.</div>
        </div>
        <Button variant="primary" onClick={startCreate}>New team</Button>
      </div>

      <div className="teams-grid">
        <div className="teams-list">
          {teams.map((team) => (
            <div key={team.id} className={`teams-list-row${editingId === team.id ? " active" : ""}`}>
              <button type="button" className="teams-list-open" onClick={() => startEdit(team)}>
                <span className="mono teams-list-name">{team.name}</span>
                <span className="teams-list-lead">{team.leader?.name || "—"} · {(team.members || []).length} members</span>
              </button>
              <Button variant="destructive" size="btn-sm" aria-label={`Delete team ${team.name}`}
                onClick={() => onDelete(team.id)}>Delete</Button>
            </div>
          ))}
          {teams.length === 0 ? <div className="teams-empty mono">아직 팀이 없습니다.</div> : null}
        </div>

        {editingId ? (
          <div className="teams-edit">
            <div className="teams-edit-head mono">{editingId === "new" ? "NEW TEAM" : "EDIT TEAM"}</div>
            <div className="teams-edit-body">
              <label className="teams-edit-field">
                <span className="mono teams-edit-k">TEAM NAME</span>
                <input aria-label="Team name" value={draft.name}
                  onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
              </label>
              <label className="teams-edit-field">
                <span className="mono teams-edit-k">DESCRIPTION</span>
                <input aria-label="Team description" value={draft.description}
                  onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} />
              </label>
              <div className="teams-edit-field">
                <span className="mono teams-edit-k">LEADER</span>
                <div className="teams-persona-choices">
                  {personas.map((persona) => (
                    <button key={persona.id} type="button"
                      aria-pressed={draft.leader_persona_id === persona.id}
                      className={`teams-persona${draft.leader_persona_id === persona.id ? " active" : ""}`}
                      onClick={() => setDraft((d) => ({ ...d, leader_persona_id: persona.id }))}>
                      {persona.name}
                    </button>
                  ))}
                </div>
              </div>
              <div className="teams-edit-field">
                <span className="mono teams-edit-k">MEMBERS</span>
                <div className="teams-persona-choices">
                  {personas.filter((p) => p.id !== draft.leader_persona_id).map((persona) => (
                    <button key={persona.id} type="button"
                      aria-pressed={draft.member_persona_ids.includes(persona.id)}
                      className={`teams-persona${draft.member_persona_ids.includes(persona.id) ? " active" : ""}`}
                      onClick={() => toggleMember(persona.id)}>
                      {persona.name}
                    </button>
                  ))}
                </div>
              </div>
              <div className="teams-edit-actions">
                <Button size="btn-sm" onClick={() => setEditingId(null)}>Cancel</Button>
                <Button size="btn-sm" variant="primary" disabled={!draft.name.trim() || !draft.leader_persona_id}
                  onClick={save}>Save team</Button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Run TeamsView test**

Run: `cd frontend && npm test -- TeamsView`
Expected: PASS.

- [ ] **Step 5: Wire into GatewayApp**

- Add handlers:

```javascript
  async function handleCreateTeam(payload) {
    try {
      const created = await api.createTeam(payload);
      if (!created) { toast("Failed to create team", "error"); return null; }
      setTeams(await api.teams());
      toast("Team created", "success");
      return created;
    } catch (_error) { toast("Failed to create team", "error"); return null; }
  }
  async function handleUpdateTeam(id, payload) {
    try {
      const updated = await api.updateTeam(id, payload);
      if (!updated) { toast("Failed to save team", "error"); return null; }
      setTeams(await api.teams());
      toast("Team saved", "success");
      return updated;
    } catch (_error) { toast("Failed to save team", "error"); return null; }
  }
  async function handleDeleteTeam(id) {
    const ok = await confirm({ title: "DELETE TEAM", message: "Delete this team? Running snapshots are unaffected.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    const done = await api.deleteTeam(id);
    if (!done) { toast("Failed to delete team", "error"); return; }
    setTeams(await api.teams());
    toast("Team deleted", "success");
  }
```

- Add a render branch for `screen === "team-admin"`:

```jsx
      ) : screen === "team-admin" ? (
        <div className="screen">
          <TeamsView
            teams={teams}
            personas={personas}
            onCreate={handleCreateTeam}
            onUpdate={handleUpdateTeam}
            onDelete={handleDeleteTeam}
          />
        </div>
```

- Import `TeamsView`.

- [ ] **Step 6: Add CSS**

```css
.teams-view-head { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 20px; }
.teams-view-sub { font-family: var(--font-mono); font-size: 12px; color: #808080; margin-top: 6px; }
.teams-grid { display: grid; grid-template-columns: 300px 1fr; gap: 20px; align-items: start; }
.teams-list { border: 3px solid #000; }
.teams-list-row { display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #000; padding: 0 10px 0 0; }
.teams-list-row.active { background: #F0F0F0; }
.teams-list-open { flex: 1; text-align: left; border: none; background: none; cursor: pointer; padding: 12px 14px; }
.teams-list-name { display: block; font-size: 13px; font-weight: 700; }
.teams-list-lead { display: block; font-size: 11px; color: #555; margin-top: 2px; }
.teams-empty { padding: 20px; color: #808080; }
.teams-edit { border: 5px solid #000; }
.teams-edit-head { background: #000; color: #fff; padding: 8px 14px; font-size: 11px; letter-spacing: 1px; }
.teams-edit-body { padding: 18px; display: flex; flex-direction: column; gap: 16px; }
.teams-edit-field { display: flex; flex-direction: column; gap: 6px; }
.teams-edit-k { font-size: 10px; letter-spacing: 1px; color: #808080; }
.teams-edit-field input { border: 3px solid #000; padding: 9px 12px; font-family: var(--font-mono); font-size: 13px; }
.teams-persona-choices { display: flex; flex-wrap: wrap; gap: 8px; }
.teams-persona { border: 2px solid #000; background: #fff; padding: 6px 11px; cursor: pointer; font-family: var(--font-mono); font-size: 12px; }
.teams-persona.active { background: #000; color: #fff; }
.teams-edit-actions { display: flex; justify-content: flex-end; gap: 10px; }
```

- [ ] **Step 7: Run tests**

Run: `cd frontend && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/organisms/TeamsView frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: Teams 관리 화면(팀 CRUD와 로스터 할당)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Rules 화면

**Files:**
- Create: `frontend/src/components/organisms/RulesView/index.jsx` (+ test)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: `rules` (`{global, persona_baseline, teams:[{team_id, personality, rules, ...}]}`), `teams` (이름 매핑).
- Produces: `RulesView({ rules, teams, onSaveGlobal, onSavePersonaBaseline, onSaveTeam })`. save 핸들러는 `{personality, rules}` (팀은 `(teamId, payload)`).

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/organisms/RulesView/RulesView.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RulesView } from "./index.jsx";

const rules = {
  global: { personality: "global voice", rules: [{ level: "REQUIRED", text: "no destructive writes" }] },
  persona_baseline: { personality: "persona voice", rules: [{ level: "GUIDELINE", text: "be terse" }] },
  teams: [{ team_id: "t1", personality: "team voice", rules: [] }]
};
const teams = [{ id: "t1", name: "Release Crew" }];

describe("RulesView", () => {
  it("shows global rules by default and saves edits", async () => {
    const onSaveGlobal = vi.fn(async () => ({}));
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={onSaveGlobal}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    expect(screen.getByText("no destructive writes")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSaveGlobal).toHaveBeenCalledWith(expect.objectContaining({
      personality: "global voice",
      rules: expect.arrayContaining([{ level: "REQUIRED", text: "no destructive writes" }])
    }));
  });

  it("switches to persona baseline scope", async () => {
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={vi.fn()}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /persona baseline/i }));
    expect(screen.getByText("be terse")).toBeInTheDocument();
  });

  it("adds a rule", async () => {
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={vi.fn()}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    const inputs = screen.getAllByPlaceholderText(/rule text/i);
    expect(inputs.length).toBeGreaterThan(1);
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- RulesView`
Expected: FAIL.

- [ ] **Step 3: Implement `RulesView/index.jsx`**

```javascript
import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

function nextRules(rules, index, patch) {
  return rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule));
}

export function RulesView({ rules, teams = [], onSaveGlobal, onSavePersonaBaseline, onSaveTeam }) {
  const [scope, setScope] = useState("team"); // "team" | "persona"
  const [selTeam, setSelTeam] = useState("global"); // "global" | teamId
  const [personality, setPersonality] = useState("");
  const [ruleList, setRuleList] = useState([]);
  const [saving, setSaving] = useState(false);

  function currentSet() {
    if (scope === "persona") return rules?.persona_baseline || { personality: "", rules: [] };
    if (selTeam === "global") return rules?.global || { personality: "", rules: [] };
    return (rules?.teams || []).find((t) => t.team_id === selTeam) || { personality: "", rules: [] };
  }

  useEffect(() => {
    const set = currentSet();
    setPersonality(set.personality || "");
    setRuleList((set.rules || []).map((r) => ({ ...r })));
  }, [scope, selTeam, rules]); // eslint-disable-line react-hooks/exhaustive-deps

  const isIndividualTeam = scope === "team" && selTeam !== "global";
  const reqCount = ruleList.filter((r) => r.level === "REQUIRED").length;

  async function save() {
    setSaving(true);
    try {
      const payload = { personality, rules: ruleList };
      if (scope === "persona") await onSavePersonaBaseline(payload);
      else if (selTeam === "global") await onSaveGlobal(payload);
      else await onSaveTeam(selTeam, payload);
    } finally { setSaving(false); }
  }

  return (
    <section className="rules-view" aria-label="Rules">
      <div className="rules-view-head">
        <h1 className="headline" style={{ fontSize: 32 }}>Rules</h1>
        <div className="rules-view-sub">모든 실행과 페르소나가 상속하는 규칙 — 팀 전체에 걸쳐 유지되는 성격과 규칙.</div>
      </div>

      <div className="rules-tabs">
        <button type="button" aria-pressed={scope === "team"}
          className={`rules-tab${scope === "team" ? " active" : ""}`} onClick={() => setScope("team")}>TEAM RULES</button>
        <button type="button" aria-pressed={scope === "persona"}
          className={`rules-tab${scope === "persona" ? " active" : ""}`} onClick={() => setScope("persona")}>PERSONA BASELINE</button>
      </div>

      {scope === "team" ? (
        <div className="rules-team-selector">
          <span className="mono rules-team-k">TEAM</span>
          <button type="button" aria-pressed={selTeam === "global"}
            className={`rules-team-btn${selTeam === "global" ? " active" : ""}`} onClick={() => setSelTeam("global")}>GLOBAL</button>
          {teams.map((team) => (
            <button key={team.id} type="button" aria-pressed={selTeam === team.id}
              className={`rules-team-btn${selTeam === team.id ? " active" : ""}`} onClick={() => setSelTeam(team.id)}>
              {team.name}
            </button>
          ))}
        </div>
      ) : null}

      {isIndividualTeam ? (
        <div className="rules-inherit mono">
          <span className="rules-inherit-tag">INHERITS GLOBAL</span>
          Global 규칙이 그대로 적용됩니다. 아래는 이 팀에만 추가되는 규칙입니다.
        </div>
      ) : null}

      <div className="rules-grid">
        <div>
          <div className="rules-personality">
            <div className="rules-personality-head mono">
              <span>PERSONALITY &amp; VOICE</span>
              <span className="rules-personality-note">FROZEN AT RUN START</span>
            </div>
            <textarea className="rules-personality-input" aria-label="Personality and voice"
              value={personality} onChange={(e) => setPersonality(e.target.value)} />
          </div>

          <div className="rules-list-head">
            <span className="mono">{isIndividualTeam ? "ADDED RULES" : "RULES"}</span>
            <span className="mono rules-counts">{reqCount} required · {ruleList.length - reqCount} guideline</span>
          </div>
          <div className="rules-list">
            {ruleList.map((rule, index) => (
              <div className="rules-row" key={index}>
                <span className="mono rules-n">{String(index + 1).padStart(2, "0")}</span>
                <button type="button"
                  className={`rules-level${rule.level === "REQUIRED" ? " req" : ""}`}
                  aria-label={`Toggle level for rule ${index + 1}`}
                  onClick={() => setRuleList((list) => nextRules(list, index, {
                    level: rule.level === "REQUIRED" ? "GUIDELINE" : "REQUIRED"
                  }))}>
                  {rule.level}
                </button>
                <input className="rules-text" placeholder="rule text"
                  value={rule.text}
                  onChange={(e) => setRuleList((list) => nextRules(list, index, { text: e.target.value }))} />
                <button type="button" className="rules-del" aria-label={`Delete rule ${index + 1}`}
                  onClick={() => setRuleList((list) => list.filter((_, i) => i !== index))}>×</button>
              </div>
            ))}
            <button type="button" className="rules-add mono"
              onClick={() => setRuleList((list) => [...list, { level: "GUIDELINE", text: "" }])}>+ ADD RULE</button>
          </div>

          <div className="rules-save">
            <Button variant="primary" disabled={saving} onClick={save}>{saving ? "Saving..." : "Save"}</Button>
          </div>
        </div>

        <aside className="rules-meta">
          <div className="rules-meta-head mono">{scope === "persona" ? "PERSONA BASELINE" : (selTeam === "global" ? "GLOBAL" : (teams.find((t) => t.id === selTeam)?.name || "TEAM"))}</div>
          <div className="rules-meta-body">
            <div className="rules-enforce">
              <div className="mono rules-enforce-k">ENFORCEMENT</div>
              <div className="rules-enforce-row"><span className="rules-enforce-req" /> <span className="mono">REQUIRED · 강한 지시(가이드)</span></div>
              <div className="rules-enforce-row"><span className="rules-enforce-guide" /> <span className="mono">GUIDELINE · 권고</span></div>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Run RulesView test**

Run: `cd frontend && npm test -- RulesView`
Expected: PASS.

- [ ] **Step 5: Wire into GatewayApp**

- Add state: `const [rules, setRules] = useState(null);`
- Loader in the `screen === "rules"` branch already added in Task 4 Step 5.
- Handlers:

```javascript
  async function handleSaveGlobalRules(payload) {
    const saved = await api.updateGlobalRules(payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
  }
  async function handleSavePersonaBaselineRules(payload) {
    const saved = await api.updatePersonaBaselineRules(payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
  }
  async function handleSaveTeamRules(teamId, payload) {
    const saved = await api.updateTeamRules(teamId, payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
  }
```

- Render branch:

```jsx
      ) : screen === "rules" ? (
        <div className="screen">
          {rules ? (
            <RulesView
              rules={rules}
              teams={teams}
              onSaveGlobal={handleSaveGlobalRules}
              onSavePersonaBaseline={handleSavePersonaBaselineRules}
              onSaveTeam={handleSaveTeamRules}
            />
          ) : null}
        </div>
```

- Import `RulesView`.

- [ ] **Step 6: Add CSS**

```css
.rules-view-sub { font-family: var(--font-mono); font-size: 12px; color: #808080; margin: 6px 0 18px; }
.rules-tabs { display: flex; border: 3px solid #000; width: fit-content; margin-bottom: 20px; }
.rules-tab { border: none; border-right: 3px solid #000; background: #fff; color: #000; font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; padding: 11px 22px; cursor: pointer; }
.rules-tab:last-child { border-right: none; }
.rules-tab.active { background: #000; color: #fff; }
.rules-team-selector { display: flex; align-items: stretch; border: 2px solid #000; width: fit-content; margin-bottom: 16px; flex-wrap: wrap; }
.rules-team-k { display: flex; align-items: center; padding: 0 12px; border-right: 2px solid #000; font-size: 9px; letter-spacing: 1px; color: #808080; background: #F0F0F0; }
.rules-team-btn { border: none; border-right: 2px solid #000; background: #fff; font-family: var(--font-mono); font-size: 11px; letter-spacing: 1px; padding: 7px 14px; cursor: pointer; }
.rules-team-btn.active { background: #000; color: #fff; }
.rules-inherit { border: 3px solid #000; background: #F0F0F0; padding: 10px 14px; display: flex; align-items: center; gap: 10px; margin-bottom: 16px; font-size: 12px; }
.rules-inherit-tag { background: #000; color: #fff; padding: 2px 7px; font-size: 10px; letter-spacing: 1px; }
.rules-grid { display: grid; grid-template-columns: 1fr 300px; gap: 22px; align-items: start; }
.rules-personality { border: 5px solid #000; margin-bottom: 20px; }
.rules-personality-head { background: #000; color: #fff; padding: 8px 14px; display: flex; justify-content: space-between; font-size: 11px; letter-spacing: 1px; }
.rules-personality-note { color: #808080; }
.rules-personality-input { width: 100%; box-sizing: border-box; border: none; padding: 16px; font-family: var(--font-body); font-size: 14px; line-height: 1.65; min-height: 90px; resize: vertical; }
.rules-list-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.rules-counts { font-size: 10px; color: #808080; }
.rules-list { border: 3px solid #000; }
.rules-row { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-bottom: 1px solid #000; }
.rules-n { font-size: 12px; color: #808080; width: 20px; }
.rules-level { border: 2px solid #000; background: #fff; color: #000; font-family: var(--font-mono); font-size: 9px; letter-spacing: 1px; padding: 2px 7px; cursor: pointer; flex: none; }
.rules-level.req { background: #000; color: #fff; }
.rules-text { flex: 1; border: none; border-bottom: 1px solid transparent; font-family: var(--font-body); font-size: 13.5px; padding: 2px 0; }
.rules-text:focus { outline: none; border-bottom-color: #000; }
.rules-del { border: none; background: none; cursor: pointer; font-size: 16px; color: #808080; }
.rules-add { display: flex; width: 100%; border: none; background: #fff; padding: 11px 14px; cursor: pointer; font-size: 11px; letter-spacing: 1px; }
.rules-add:hover { background: #000; color: #fff; }
.rules-save { margin-top: 14px; }
.rules-meta { border: 3px solid #000; }
.rules-meta-head { background: #000; color: #fff; padding: 8px 14px; font-size: 11px; letter-spacing: 1px; }
.rules-meta-body { padding: 14px; }
.rules-enforce { border: 3px solid #000; padding: 10px 12px; }
.rules-enforce-k { font-size: 9px; letter-spacing: 1px; color: #808080; margin-bottom: 5px; }
.rules-enforce-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.rules-enforce-req { width: 11px; height: 11px; background: #000; flex: none; }
.rules-enforce-guide { width: 11px; height: 11px; border: 2px solid #000; flex: none; }
```

- [ ] **Step 7: Run tests**

Run: `cd frontend && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/organisms/RulesView frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: Rules 화면(전역·팀·페르소나 규칙 편집)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Team Run 상세 — DOCUMENTS 탭 + 문서 프리뷰

**Files:**
- Create: `frontend/src/components/organisms/DocumentPreview/index.jsx` (+ test)
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` (+ test)
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` (문서 로더)
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: `documents` (`[{path, size, kind, previewable, modified_at}]`), `onLoadDocument(path) -> {content, kind, previewable, reason}`.
- Produces: `DocumentPreview({ open, doc, onClose })`; TeamRunDetail gains `documents` + `onLoadDocument` props and a `documents` tab.

Note: 상세 화면은 이미 레인·보드에 아바타 이미지를 렌더한다(디자인의 "보드 프로필 이미지" 요구 충족). 이 태스크는 DOCUMENTS 탭과 프리뷰만 추가한다. 나머지 디자인 D 정렬(메타 스트립·탭바)은 이미 근접하므로 탭에 DOCUMENTS를 더하는 선에서 반영한다.

- [ ] **Step 1: Write failing test for DocumentPreview**

Create `frontend/src/components/organisms/DocumentPreview/DocumentPreview.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentPreview } from "./index.jsx";

describe("DocumentPreview", () => {
  it("renders json pretty-printed", () => {
    render(<DocumentPreview open doc={{ path: "data.json", kind: "json", previewable: true, content: '{"a":1}' }} onClose={vi.fn()} />);
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
  });

  it("shows a not-previewable message", () => {
    render(<DocumentPreview open doc={{ path: "img.png", kind: "binary", previewable: false, reason: "binary" }} onClose={vi.fn()} />);
    expect(screen.getByText(/미리보기 불가/i)).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    const { container } = render(<DocumentPreview open={false} doc={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- DocumentPreview`
Expected: FAIL.

- [ ] **Step 3: Implement `DocumentPreview/index.jsx`**

Reuse the existing `MarkdownContent` organism for `md` rendering (import it). For `json`, pretty-print; for text/code, show in `<pre>`.

```javascript
import { MarkdownContent } from "../MarkdownContent/index.jsx";

function prettyJson(content) {
  try { return JSON.stringify(JSON.parse(content), null, 2); }
  catch { return content; }
}

export function DocumentPreview({ open, doc, onClose }) {
  if (!open || !doc) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card doc-preview" role="dialog" aria-modal="true"
        aria-label={`Document ${doc.path}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mono">{doc.path}</span>
          <button type="button" className="modal-close" aria-label="Close preview" onClick={onClose}>×</button>
        </div>
        <div className="doc-preview-body">
          {!doc.previewable ? (
            <div className="doc-preview-unavailable mono">미리보기 불가 · {doc.reason || "unsupported"}</div>
          ) : doc.kind === "md" ? (
            <MarkdownContent text={doc.content || ""} />
          ) : doc.kind === "json" ? (
            <pre className="doc-preview-pre">{prettyJson(doc.content || "")}</pre>
          ) : (
            <pre className="doc-preview-pre">{doc.content || ""}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
```

Note: verify `MarkdownContent`'s prop name by reading `organisms/MarkdownContent/index.jsx`. If it takes `content` instead of `text`, adjust the prop accordingly.

- [ ] **Step 4: Run DocumentPreview test**

Run: `cd frontend && npm test -- DocumentPreview`
Expected: PASS.

- [ ] **Step 5: Write failing test for the DOCUMENTS tab in TeamRunDetail**

Append to `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx` a test that the DOCUMENTS section lists files and clicking one calls `onLoadDocument`. Read the existing test file's `detail` fixture shape first and extend it. Example:

```javascript
it("lists workspace documents and opens a preview", async () => {
  const onLoadDocument = vi.fn(async () => ({ path: "notes.md", kind: "md", previewable: true, content: "# hi" }));
  render(<TeamRunDetail
    detail={baseDetail}
    documents={[{ path: "notes.md", kind: "md", previewable: true, size: 10 }]}
    onLoadDocument={onLoadDocument}
  />);
  await userEvent.click(screen.getByText("notes.md"));
  expect(onLoadDocument).toHaveBeenCalledWith("notes.md");
});
```

`baseDetail` should match the component's expected `detail` shape (run/agents/tasks/messages) — copy from an existing test in the file.

- [ ] **Step 6: Add DOCUMENTS section to `TeamRunDetail/index.jsx`**

- Add props `documents = []`, `onLoadDocument` to the component signature.
- Add state: `const [previewDoc, setPreviewDoc] = useState(null);` and import `DocumentPreview` + `useState` (already imported).
- Add a documents block after the existing activity/results area (or as a new labeled section). Minimal implementation:

```jsx
      <div className="team-section-head">
        <span className="mono team-section-label">Documents</span>
        <span className="mono team-section-count">{documents.length} files</span>
        <span className="team-section-rule" />
      </div>
      <div className="team-docs-list">
        {documents.length ? documents.map((doc) => (
          <button
            key={doc.path}
            type="button"
            className="team-docs-list-row"
            aria-label={`Preview ${doc.path}`}
            disabled={!doc.previewable || !onLoadDocument}
            onClick={async () => {
              if (!onLoadDocument) return;
              const loaded = await onLoadDocument(doc.path);
              setPreviewDoc(loaded || { ...doc, previewable: false, reason: "load failed" });
            }}
          >
            <span className="mono team-docs-name">{doc.path}</span>
            <span className="mono team-docs-kind">{doc.kind}</span>
          </button>
        )) : <div className="team-task-empty mono">No documents in the workspace yet.</div>}
      </div>

      <DocumentPreview open={Boolean(previewDoc)} doc={previewDoc} onClose={() => setPreviewDoc(null)} />
```

(This satisfies the "workspace file browsing + modal preview" requirement. Reuse existing `team-section-head`/`team-task-empty` classes; add the small doc-list classes below.)

- [ ] **Step 7: Wire documents loading in GatewayApp**

- Add state: `const [teamRunDocuments, setTeamRunDocuments] = useState([]);`
- In the `useEffect` that loads `teamRunDetail` when `selectedTeamRunId` changes, also load documents:

```javascript
    api.teamDocuments(selectedTeamRunId).then((docs) => { if (alive) setTeamRunDocuments(docs); });
```

- Also refresh documents when a `team.*` SSE event for the selected run arrives (in the existing SSE handler that re-fetches `teamRunDetail`): add `api.teamDocuments(parsed.team_run_id).then(setTeamRunDocuments);` alongside the detail refetch.
- Pass to `TeamRunDetail`:

```jsx
            <TeamRunDetail
              detail={teamRunDetail}
              documents={teamRunDocuments}
              onLoadDocument={(path) => api.teamDocumentContent(selectedTeamRunId, path)}
              onAddWork={handleAddWork}
              onResume={handleResumeTeamRun}
              onRetryTask={handleRetryTeamTask}
            />
```

- Reset `setTeamRunDocuments([])` when `selectedTeamRunId` is cleared (in the existing effect's `!selectedTeamRunId` branch).

- [ ] **Step 8: Add CSS**

```css
.doc-preview { max-width: 860px; width: 90%; }
.doc-preview-body { padding: 16px; max-height: 70vh; overflow: auto; }
.doc-preview-pre { font-family: var(--font-mono); font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; margin: 0; }
.doc-preview-unavailable { color: #808080; padding: 20px; }
.team-docs-list { display: flex; flex-direction: column; border: 3px solid #000; }
.team-docs-list-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; border: none; border-bottom: 1px solid #000; background: #fff; padding: 10px 14px; cursor: pointer; text-align: left; }
.team-docs-list-row:last-child { border-bottom: none; }
.team-docs-list-row:hover:not(:disabled) { background: #F0F0F0; }
.team-docs-list-row:disabled { cursor: default; color: #808080; }
.team-docs-name { font-size: 12px; }
.team-docs-kind { font-size: 10px; color: #808080; letter-spacing: 1px; }
```

- [ ] **Step 9: Run full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/organisms/DocumentPreview frontend/src/components/organisms/TeamRunDetail frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: Team Run 상세에 워크스페이스 문서 목록·프리뷰 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 통합 빌드 + 회귀 확인

**Files:** none new.

- [ ] **Step 1: Full frontend test**

Run: `cd frontend && npm test`
Expected: PASS (all suites).

- [ ] **Step 2: Build frontend to `frontend_dist`**

Run: `cd frontend && npm run build`
Expected: 빌드 성공, `src/personal_agent_gateway/frontend_dist/` 갱신.

- [ ] **Step 3: Backend regression**

Run (repo root): `python -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit built assets**

```bash
git add src/personal_agent_gateway/frontend_dist
git commit -m "build: Agent Teams 화면 프런트 번들 갱신

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review (frontend)

- API 클라이언트 → Task 1. 내비 → Task 2. 목록 → Task 3. 새 실행(팀) → Task 4. Teams 관리 →
  Task 5. Rules → Task 6. 상세 문서 프리뷰 → Task 7. 빌드/회귀 → Task 8. 스펙의 네 화면 + 팀
  기반 흐름 + 문서 프리뷰를 모두 커버한다.
- 타입/이름 일관성: 목록 카드는 백엔드 enrich 필드명(`leader_name`/`members`/`task_counts`/
  `task_done`/`task_total`/`elapsed_seconds`/`team_id`)과 일치. TeamPicker의 `onStart` payload
  `{team_id, goal, run_mode, max_workers}`가 `createTeamRun` → 백엔드 `CreateTeamRunRequest`와
  일치. RulesView save payload `{personality, rules}`가 백엔드 `RuleSetRequest`와 일치.
- 보드 아바타는 기존 `TeamRunDetail` 렌더를 유지하므로 "보드 프로필 이미지" 요구 충족.
- `MarkdownContent` prop 이름은 Step 3에서 실제 확인 후 맞춘다(플레이스홀더 아님, 확인 지시).

## 검증 (프런트 완료 시)

1. `npm test` 전 스위트 통과, `npm run build` 성공.
2. 앱에서 Teams로 팀 생성 → Rules에서 팀 규칙 편집 → Team Runs에서 New team run(팀 선택) 시작.
3. 목록 카드 필터/진행바/아바타, 상세 보드 아바타, DOCUMENTS 탭에서 md/json 프리뷰 확인.
4. `python -m pytest -q` 백엔드 회귀 없음.
