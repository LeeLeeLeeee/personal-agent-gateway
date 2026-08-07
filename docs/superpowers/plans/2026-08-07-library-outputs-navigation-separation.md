# Library and Outputs Navigation Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the user-facing Archive workspace with separate Library and Outputs screens while preserving the existing Archive knowledge workflow and Artifact browser behavior.

**Architecture:** `GatewayApp` becomes the direct caller for both top-level screens: `screen === "library"` renders the existing `ArchiveView`, and `screen === "outputs"` renders the existing `ArtifactsView`. `ArchiveView` keeps Archive API and Knowledge Request ownership but drops the embedded Artifact pass-through; `ArtifactsView` keeps its current API/state ownership and receives the same fallback list and refresh callback from `GatewayApp`.

**Tech Stack:** React 19, Vitest 4, Testing Library, Vite 6, global CSS, existing `api/client.js` contracts.

## Global Constraints

- Work only in `C:\Users\Administrator\playground\personal-agent-gateway\.worktrees\archive-map-removal` on branch `refactor/library-outputs-separation`.
- The approved design is `docs/superpowers/specs/2026-08-07-library-outputs-navigation-separation-design.md`.
- Do not modify Archive/Artifact backend APIs, services, schemas, migrations, or backend tests.
- Keep `ArchiveView`, `ArtifactsView`, `/api/archive/*`, `ArchiveService`, and general `archive-*`/`artifacts-*` technical names.
- Do not add URL routing, deep links, global caches, new loading abstractions, or component/controller extraction.
- Keep Chat's existing Artifact fallback load and registered-path behavior.
- Preserve the dirty main checkout; build only inside this isolated worktree and do not copy files from the main checkout.
- Do not launch PAG, LMG, or `scripts/start_local_runtime.ps1` from a Codex-managed command on Windows.
- Follow RED → verify RED → minimal GREEN → verify GREEN for behavior changes.
- Stage and commit only the files named by the current task.

---

### Task 1: Split top-level screen ownership

**Files:**
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx:3-12`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx:1-30,273-315,730-993`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx:109-168`

**Interfaces:**
- Consumes: existing `NAV`, `GatewayApp.screen`, `ArchiveView`, `ArtifactsView`, `api.artifacts()`, and the `artifacts`/`setArtifacts` fallback collection.
- Produces: screen keys `library` and `outputs`; `ArchiveView` rendered directly for Library; `ArtifactsView({ artifacts, onChange })` rendered directly for Outputs; no `archive` screen key.

- [ ] **Step 1: Replace the old Archive integration test with separate failing screen-boundary tests**

In `GatewayApp.test.jsx`, replace `opens the Archive workspace from the primary navigation` with these tests. Reuse the existing `status`, `sessions`, `artifact`, `installFetch()`, and `renderGatewayApp()` helpers exactly as declared in the file.

```jsx
it("opens Library without loading Outputs data", async () => {
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions },
    "GET /api/history": { events: [] },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/dashboard/usage": { weekly: { used: 0, limit: 0 } },
    "GET /api/operations": { items: [], counts: {} },
    "GET /api/archive/entries?status=published": { entries: [] },
    "GET /api/archive/entries?status=draft": { entries: [] },
    "GET /api/personas": { personas: [] },
    "GET /api/archive/requests": { requests: [] }
  });

  await renderGatewayApp({ openChat: false });
  await userEvent.click(await screen.findByRole("button", { name: "Library" }));

  expect(await screen.findByRole("heading", { name: "Archive" })).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/api/archive/entries?status=published");
  expect(fetch).not.toHaveBeenCalledWith("/api/artifacts");
});

it("opens Outputs without loading Library data", async () => {
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions },
    "GET /api/history": { events: [] },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/dashboard/usage": { weekly: { used: 0, limit: 0 } },
    "GET /api/operations": { items: [], counts: {} },
    "GET /api/artifacts": { artifacts: [artifact] }
  });

  await renderGatewayApp({ openChat: false });
  await userEvent.click(await screen.findByRole("button", { name: "Outputs" }));

  expect(await screen.findByRole("heading", { name: "Artifacts" })).toBeInTheDocument();
  expect(await screen.findByText("release-report.md")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/api/artifacts");
  expect(fetch).not.toHaveBeenCalledWith("/api/archive/entries?status=published");
});
```

- [ ] **Step 2: Run the new tests and verify the screen contract is RED**

Run:

```powershell
npm --prefix frontend test -- src/components/containers/GatewayApp/GatewayApp.test.jsx -t "Library|Outputs"
```

Expected: FAIL because Sidebar has no `Library` or `Outputs` buttons and `GatewayApp` has no matching screen branches.

- [ ] **Step 3: Add Library and Outputs navigation items**

In `Sidebar/index.jsx`, replace the Archive entry in `NAV` with adjacent Library and Outputs entries:

```jsx
export const NAV = [
  { key: "dashboard", label: "Dashboard" },
  { key: "chat", label: "Chat" },
  { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" },
  { key: "hooks", label: "Hooks" },
  { key: "library", label: "Library" },
  { key: "outputs", label: "Outputs" },
  { key: "operations", label: "Operations" },
  { key: "settings", label: "Settings" }
];
```

- [ ] **Step 4: Give GatewayApp direct ownership of both screens**

In `GatewayApp/index.jsx`:

1. Import `ArtifactsView` next to `ArchiveView`.
2. Change the screen-specific Artifact fallback load from `archive` to `outputs`.
3. Replace the `archive` render branch with separate `library` and `outputs` branches.

```jsx
import { ArchiveView } from "../../organisms/ArchiveView/index.jsx";
import { ArtifactsView } from "../../organisms/ArtifactsView/index.jsx";
```

```jsx
} else if (screen === "outputs") {
  load(api.artifacts(), setArtifacts);
} else if (screen === "chat") {
  load(api.artifacts(), setArtifacts);
  api.personas().then(setPersonas).catch(() => {});
```

Insert these exact branches into the repository's existing nested ternary chain immediately before the `jobs` branch:

```jsx
) : screen === "library" ? (
  <div className="screen">
    <ArchiveView />
  </div>
) : screen === "outputs" ? (
  <div className="screen">
    <ArtifactsView
      artifacts={artifacts}
      onChange={() => api.artifacts().then(setArtifacts).catch(setScreenError)}
    />
  </div>
) : screen === "jobs" ? (
```

- [ ] **Step 5: Run the focused screen tests and verify GREEN**

Run:

```powershell
npm --prefix frontend test -- src/components/containers/GatewayApp/GatewayApp.test.jsx -t "Library|Outputs"
```

Expected: both new tests PASS. The Library test still observes the temporary `Archive` heading and the Outputs test still observes the temporary `Artifacts` heading; Task 2 changes those user-facing labels.

- [ ] **Step 6: Commit the screen ownership change**

```powershell
git add frontend/src/components/organisms/Sidebar/index.jsx frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "refactor(navigation): split Library and Outputs screens"
```

---

### Task 2: Make Library and Outputs user-facing structure coherent

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx:1-4,100-195,393-983`
- Modify: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx:1-180`
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx:131-137`
- Modify: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx:1-94`
- Modify: `src/personal_agent_gateway/static/styles.css:4786-4807,5106-5138`

**Interfaces:**
- Consumes: Task 1's `library` and `outputs` screen branches.
- Produces: `ArchiveView({ client = api })` with `published`, `drafts`, and `requests` tabs only; `ArtifactsView` with user-facing `Outputs` heading; no Archive-owned Artifact props or wrapper styles.

- [ ] **Step 1: Write the failing Library-only presentation contract**

In `ArchiveView.test.jsx`:

1. Remove the `api` import and `artifact` fixture, which become unused when embedded Artifact tests are removed.
2. Delete the wrapper padding test and the two embedded Artifact behavior tests.
3. Replace the Knowledge/Work Outputs guide test with the following Library-only contract.

```jsx
it("presents the knowledge lifecycle as Published, Drafts, and Requests", async () => {
  render(<ArchiveView client={makeClient()} />);

  expect(await screen.findByRole("heading", { name: "Library" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Published/ })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  expect(screen.getByRole("tab", { name: /Drafts/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Requests/ })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /Artifacts/ })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Knowledge lifecycle" }))
    .toHaveTextContent(/Requests.*Drafts.*Library/i);
  expect(screen.queryByRole("region", { name: "Work outputs" })).not.toBeInTheDocument();
});
```

Update existing tests that click the old Library tab to use the Published label:

```jsx
await userEvent.click(screen.getByRole("tab", { name: "Published" }));
```

- [ ] **Step 2: Move general Artifact layout assertions to the Artifact owner and add the Outputs heading contract**

At the top of `ArtifactsView.test.jsx`, add the stylesheet helpers already used by `ArchiveView.test.jsx`:

```jsx
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const styles = readFileSync(
  resolve(process.cwd(), "../src/personal_agent_gateway/static/styles.css"),
  "utf8"
);
```

Add these tests inside `describe("ArtifactsView", ...)`:

```jsx
it("presents the standalone workspace as Outputs", async () => {
  renderView();
  expect(screen.getByRole("heading", { name: "Outputs" })).toBeInTheDocument();
});

it("keeps Artifact metadata and grouped rows compact", () => {
  expect(styles).toMatch(
    /\.artifact-card-meta\s*\{[^}]*white-space:\s*nowrap;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/
  );
  expect(styles).toMatch(
    /\.artifact-groups\s*\{\s*display:\s*grid;\s*gap:\s*18px;/
  );
  expect(styles).toMatch(
    /\.artifact-row-open\s*\{[^}]*grid-template-columns:\s*38px minmax\(0, 1fr\) 150px;/
  );
});
```

- [ ] **Step 3: Run the component tests and verify the presentation contract is RED**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/organisms/ArtifactsView/ArtifactsView.test.jsx
```

Expected failures:

- `ArchiveView` still has heading `Archive`, default tab `Library`, and an Artifacts tab/guide.
- `ArtifactsView` still has heading `Artifacts`.

- [ ] **Step 4: Remove the embedded Outputs surface from ArchiveView**

In `ArchiveView/index.jsx`:

1. Remove the `ArtifactsView` import.
2. Change the signature and default tab.
3. Replace every transition to the old `library` tab value with `published`.
4. Rename the heading and first tab.
5. Remove the Work Outputs guide card, Artifacts tab, and Artifacts panel.

The component boundary becomes:

```jsx
export function ArchiveView({ client = api }) {
  const [tab, setTab] = useState("published");
```

The published-entry transitions must use:

```jsx
setTab(entry.status === "draft" ? "drafts" : "published");
```

```jsx
setTab("published");
```

The page and first tab become:

```jsx
<h1 className="headline">Library</h1>
```

```jsx
<button
  type="button"
  role="tab"
  aria-label="Published"
  aria-selected={tab === "published"}
  className={tab === "published" ? "active" : ""}
  onClick={() => setTab("published")}
>
  PUBLISHED <span>{entries.length}</span>
</button>
```

Update the Library/Drafts panel guard and search condition:

```jsx
{!loading && ["published", "drafts"].includes(tab) ? (
```

```jsx
{tab === "published" ? (
```

Keep the Knowledge lifecycle card and its `aria-label="Knowledge lifecycle"`. Do not add a replacement Outputs link or cross-domain panel.

- [ ] **Step 5: Rename only the ArtifactsView heading**

In `ArtifactsView/index.jsx`, keep the component/API names and replace only the user-facing heading:

```jsx
<h1 className="headline">Outputs</h1>
```

- [ ] **Step 6: Remove only orphan Archive-owned Artifact styles**

In `styles.css`, delete these complete selector blocks:

```css
.archive-artifacts
.archive-artifacts > .artifacts-view
.archive-artifacts-boundary
.archive-artifacts-boundary strong
.archive-artifacts-boundary p
.archive-guide-card-artifacts
```

Change the retained Knowledge guide to one column:

```css
.archive-guide {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    margin: 20px 0;
}
```

Keep every general `.artifact-*`, `.artifacts-*`, `.archive-library*`, `.archive-editor*`, and `.archive-request*` block.

- [ ] **Step 7: Run component and container regressions and verify GREEN**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: all three files PASS. Existing Archive Request Team Run lazy/retry/stale-response tests and Artifact Saved/Recent/Cleanup/browser tests remain green.

- [ ] **Step 8: Commit the user-facing structure**

```powershell
git add frontend/src/components/organisms/ArchiveView/index.jsx frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "refactor(library): separate reusable knowledge from Outputs"
```

---

### Task 3: Guard Outputs mutation refresh at the new caller boundary

**Files:**
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**Interfaces:**
- Consumes: Task 1's direct `ArtifactsView` render and `onChange={() => api.artifacts().then(setArtifacts).catch(setScreenError)}` callback.
- Produces: an integration test proving single Artifact deletion refreshes the Outputs fallback collection through `GatewayApp`.

- [ ] **Step 1: Add the failing refresh integration test**

Add this test next to the Outputs navigation test in `GatewayApp.test.jsx`:

```jsx
it("refreshes the Outputs fallback list after an Artifact is deleted", async () => {
  let artifactListReads = 0;
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions },
    "GET /api/history": { events: [] },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/dashboard/usage": { weekly: { used: 0, limit: 0 } },
    "GET /api/operations": { items: [], counts: {} },
    "GET /api/artifacts": () => {
      artifactListReads += 1;
      return response({ artifacts: artifactListReads === 1 ? [artifact] : [] });
    },
    "DELETE /api/artifacts/artifact-1": {}
  });

  await renderGatewayApp({ openChat: false });
  await userEvent.click(await screen.findByRole("button", { name: "Outputs" }));
  await userEvent.click(await screen.findByRole("button", { name: "Open release-report.md" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));

  await waitFor(() => expect(artifactListReads).toBe(2));
  confirmSpy.mockRestore();
});
```

- [ ] **Step 2: Run the integration test and observe its result before changing production**

Run:

```powershell
npm --prefix frontend test -- src/components/containers/GatewayApp/GatewayApp.test.jsx -t "refreshes the Outputs fallback"
```

Expected: PASS if Task 1 preserved the callback exactly. If it fails, the permitted minimal production fix is only to restore `ArtifactsView.onChange` to the existing `api.artifacts().then(setArtifacts).catch(setScreenError)` callback in the `outputs` render branch; do not add a new refresh abstraction.

- [ ] **Step 3: Run the complete focused frontend contract**

Run:

```powershell
npm --prefix frontend test -- src/api/client.test.js src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: all four files PASS.

- [ ] **Step 4: Commit the caller-boundary regression test**

```powershell
git add frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "test(outputs): preserve Artifact refresh at screen boundary"
```

---

### Task 4: Run full frontend verification and refresh production assets

**Files:**
- Modify: `src/personal_agent_gateway/frontend_dist/index.html`
- Replace generated hashes as produced by Vite: `src/personal_agent_gateway/frontend_dist/assets/index-*.js`
- Replace generated hashes as produced by Vite: `src/personal_agent_gateway/frontend_dist/assets/index-*.css`

**Interfaces:**
- Consumes: Tasks 1-3 source and tests.
- Produces: fully verified source plus tracked production assets generated from that exact source.

- [ ] **Step 1: Run the full frontend suite with bounded concurrency**

```powershell
npm --prefix frontend test -- --maxWorkers=1
```

Expected: every frontend test file and test passes with zero failures.

- [ ] **Step 2: Build the production frontend inside the isolated worktree**

```powershell
npm --prefix frontend run build
```

Expected: Vite exits `0` and writes only this worktree's `src/personal_agent_gateway/frontend_dist`. Existing warnings about non-module `highlight.min.js`, build-time `github-dark.min.css`, or unresolved `PretendardVariable.woff2` are baseline warnings, not permission to ignore a new error.

- [ ] **Step 3: Verify removed and preserved frontend contracts**

Run the screen-key scan:

```powershell
rg -n 'key: "archive"|screen === "archive"|setScreen\("archive"\)' frontend/src/components/organisms/Sidebar frontend/src/components/containers/GatewayApp
```

Expected: zero matches.

Run the embedded Outputs scan:

```powershell
rg -n 'ArtifactsView|artifacts=|onArtifactChange|archive-artifacts|archive-artifacts-boundary|archive-guide-card-artifacts' frontend/src/components/organisms/ArchiveView src/personal_agent_gateway/static/styles.css
```

Expected: zero matches. General `.artifacts-*` selectors outside the scanned ArchiveView path remain.

Run the Phase 1 Map regression scan:

```powershell
rg -n 'ArchiveMap|archiveMap|archive-map-|archive-legend-line|/api/archive/map' frontend/src src/personal_agent_gateway --glob '!**/*.test.*'
```

Expected: zero production or generated-asset matches.

- [ ] **Step 4: Verify backend code and contracts were untouched**

```powershell
git diff --name-only 3eb505b...HEAD -- src/personal_agent_gateway/archive.py src/personal_agent_gateway/artifacts.py src/personal_agent_gateway/api/archive.py src/personal_agent_gateway/api/artifacts.py tests/test_archive.py tests/test_artifacts.py tests/test_api_archive.py tests/test_api_artifacts.py
```

Expected: zero paths. `src/personal_agent_gateway/static/styles.css` and `frontend_dist` are frontend assets and are intentionally outside this backend check.

- [ ] **Step 5: Check generated changes and whitespace**

```powershell
git status --short src/personal_agent_gateway/frontend_dist
git diff --stat -- src/personal_agent_gateway/frontend_dist
git diff --check
```

Expected: only the old generated asset hashes, new generated asset hashes, and `index.html` reference change under `frontend_dist`; whitespace check exits `0`.

- [ ] **Step 6: Commit generated production assets**

```powershell
git add src/personal_agent_gateway/frontend_dist
git commit -m "build: refresh Library and Outputs frontend assets"
```

---

### Task 5: Record the verified implementation

**Files:**
- Create: `docs/reports/2026-08-07-library-outputs-navigation-separation-implementation.md`
- Modify through generator only: `docs/registry.json`

**Interfaces:**
- Consumes: exact command results from Tasks 3-4 and the final commit list.
- Produces: a searchable completion report that distinguishes verified results from pre-existing backend baseline failures.

- [ ] **Step 1: Create the implementation report with observed results**

Create the report with this fixed frontmatter and section structure. Under `Verification`, copy the literal pass counts, build asset sizes, warnings, and static-scan counts printed by Tasks 3-4; do not estimate or reuse Phase 1 counts.

```markdown
---
title: Library와 Outputs navigation 분리 구현 결과
type: report
domain: personal-agent-gateway
feature: library-outputs-separation
status: done
aliases:
  - Library Outputs 분리 결과
  - Archive 2단계 개편 결과
tags:
  - archive
  - artifacts
  - navigation
updated_at: 2026-08-07
---

# Library와 Outputs navigation 분리 구현 결과

## Summary

사용자용 Archive 진입점을 Library와 Outputs로 분리했다. Library는 Published,
Drafts, Requests Knowledge workflow만 소유하고, Outputs는 기존 Artifact browser를
독립 top-level 화면으로 제공한다. Archive/Artifact backend 계약은 변경하지 않았다.

## Changes

- Sidebar와 `GatewayApp.screen`을 `library`와 `outputs`로 분리했다.
- Library 진입에서 Artifact fallback 조회와 embedded Artifact UI를 제거했다.
- Outputs가 Artifact browser와 mutation refresh를 직접 제공한다.
- Archive 내부 domain/API 이름과 모든 backend code를 유지했다.
- tracked production frontend assets를 검증된 source에서 다시 생성했다.

## Verification

- Focused frontend: Task 3의 실제 Vitest 결과를 기록한다.
- Full frontend: Task 4의 실제 test file/test pass count를 기록한다.
- Production build: Task 4의 실제 module count, asset size와 baseline warning을 기록한다.
- Static scans: screen key, embedded Outputs, Archive Map 검색의 실제 match count를 기록한다.
- Backend boundary: 지정한 backend source/test diff가 0개였음을 기록한다.
- `git diff --check`: 실제 결과를 기록한다.

## Follow-ups

- 다음 단계는 Jobs, Schedules, Hooks를 Automations shell로 통합하는 별도 설계다.
- Library editor와 Requests panel 내부 분리는 실제 유지보수 압력이 확인될 때 별도 계획으로 다룬다.
```

- [ ] **Step 2: Rebuild the docs registry with the available generator**

The repository has no local `scripts/build_docs_registry.mjs` in this worktree, so run the installed dev-docs generator:

```powershell
node "C:\Users\Administrator\.claude\skills\dev-docs\scripts\build_docs_registry.mjs"
```

Expected: `docs/registry.json` includes `docs/reports/2026-08-07-library-outputs-navigation-separation-implementation.md` and reports the new total document count.

- [ ] **Step 3: Verify the final repository state**

```powershell
git diff --check
git status --short
git log --oneline 3eb505b..HEAD
```

Expected: only the new report and regenerated registry are uncommitted before the documentation commit; the log contains the source, test, and build commits from Tasks 1-4.

- [ ] **Step 4: Commit the implementation report**

```powershell
git add docs/reports/2026-08-07-library-outputs-navigation-separation-implementation.md docs/registry.json
git commit -m "docs: record Library and Outputs separation"
```

- [ ] **Step 5: Confirm the branch is clean**

```powershell
git status --short
```

Expected: no output.
