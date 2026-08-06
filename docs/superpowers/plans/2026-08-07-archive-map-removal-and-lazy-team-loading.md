# Archive Map Removal and Lazy Documentation Team Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Archive Map from every application layer and reduce Archive entry requests by loading documentation Team Runs only when Requests is opened.

**Architecture:** Keep `ArchiveView` as the existing workflow owner, but limit its base load to entries, drafts, personas, and Knowledge Requests. Add one component-local Team Run request state for lazy loading and retry, remove the frontend Map projection, and delete the unused client, route, and backend graph read model without a compatibility alias.

**Tech Stack:** React 19, Vite 6, Vitest 4, Testing Library, FastAPI, pytest, SQLite

**Design:** `docs/superpowers/specs/2026-08-07-archive-map-removal-and-lazy-team-loading-design.md`

## Global Constraints

- Execute this plan in an isolated worktree created with `superpowers:using-git-worktrees`; do not run the frontend production build in the current dirty main checkout.
- Do not include or rewrite the existing main-checkout changes under `src/personal_agent_gateway/frontend_dist/**` or `.claude/worktrees/**`.
- Remove `GET /api/archive/map` immediately; do not retain a deprecated route, empty response, or compatibility flag.
- Preserve Library search, Draft review, publish/revise/delete, Artifact management, and all Knowledge Request mutations.
- Keep Artifact list ownership in `GatewayApp` during this phase.
- Do not split `ArchiveView`, rename Archive sections, add a status board, or change navigation outside removal of the Map tab.
- Do not change SQLite schema or stored Archive data.
- Load Team Runs only for Requests, cache a successful result until `ArchiveView` unmounts, and expose an explicit retry after failure.
- Use TDD for every changed behavior: observe RED before production changes, then run the same test GREEN.
- Touch only files listed by the active task; unrelated cleanup is out of scope.

---

### Task 1: Remove the frontend Map and lazy-load documentation Team Runs

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx:1-388,431-498`
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx:1,100-748,983-1034,1341-1490`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx:140-160`
- Modify: `src/personal_agent_gateway/static/styles.css:4761,5141-5471,5658-5696`

**Interfaces:**
- Consumes: `client.archiveEntries()`, `client.personas()`, `client.knowledgeRequests()`, and `client.teamRuns()`.
- Produces: component-local `teamRunsStatus` with values `idle | loading | ready | error`.
- Produces: Requests-only `loadDocumentationTeams()` retry behavior.
- Preserves: `editEntry()`, `beginRequestDraft()`, `delegateRequest()`, and `onArtifactChange` contracts.

- [ ] **Step 1: Add RED tests for Map removal and one-time lazy Team Run loading**

Keep the existing `archiveMap` mock temporarily so the failure is about the new behavior, then add this test after the Artifact tests:

```jsx
it("removes Map and loads documentation teams only once on Requests", async () => {
  const client = makeClient();
  render(<ArchiveView client={client} />);

  await screen.findByRole("heading", { name: "Archive" });

  expect(screen.queryByRole("tab", { name: "Map" })).not.toBeInTheDocument();
  expect(client.teamRuns).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
  await waitFor(() => expect(client.teamRuns).toHaveBeenCalledOnce());

  await userEvent.click(screen.getByRole("tab", { name: "Library" }));
  await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
  expect(client.teamRuns).toHaveBeenCalledOnce();
});
```

- [ ] **Step 2: Add a RED test for isolated failure and retry**

Add a test that proves Team Run failure does not block direct Request actions:

```jsx
it("keeps direct Request actions available and retries Team Run loading", async () => {
  const client = makeClient();
  client.teamRuns
    .mockReset()
    .mockRejectedValueOnce(new Error("Team Runs unavailable"))
    .mockResolvedValueOnce([documentationTeam]);

  render(<ArchiveView client={client} />);

  await screen.findByRole("heading", { name: "Archive" });
  await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));

  const retry = await screen.findByRole("button", { name: "Retry team loading" });
  expect(screen.getByRole("button", {
    name: `Write ${request.title} in Library`
  })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Later" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  expect(screen.getByRole("button", {
    name: `Send ${request.title} to documentation team`
  })).toBeDisabled();

  await userEvent.click(retry);

  await waitFor(() => expect(client.teamRuns).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("button", {
    name: `Send ${request.title} to documentation team`
  })).toBeEnabled();
});
```

- [ ] **Step 3: Add a Gateway boundary assertion that Map is not fetched**

Remove the `"GET /api/archive/map"` response from the Archive fixture in
`GatewayApp.test.jsx` and add this assertion after Archive renders:

```jsx
expect(fetch).not.toHaveBeenCalledWith("/api/archive/map");
```

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: FAIL because Map is still present, `teamRuns()` runs during initial
`Promise.all`, no retry action exists, and GatewayApp still reaches the Map
fixture.

- [ ] **Step 5: Remove Map helpers, component, state, tab, and panel**

In `ArchiveView/index.jsx`:

- remove `useRef` from the React import;
- delete `MAP_COLUMNS` through the end of `ArchiveMap` (`index.jsx:100-671` in
  the pre-change file);
- delete `graph` and `selectedNodeId` state;
- remove Map from the five Archive tabs;
- delete the `tab === "map"` panel;
- retain `editEntry()` and `beginRequestDraft()` because non-Map panels use
  them.

The import becomes:

```jsx
import { useCallback, useEffect, useMemo, useState } from "react";
```

- [ ] **Step 6: Limit base Archive loading to four requests**

Replace the six-value `loadData()` destructuring and `Promise.all` with:

```jsx
const [
  nextEntries,
  nextDrafts,
  nextPersonas,
  nextRequests
] = await Promise.all([
  client.archiveEntries(),
  client.archiveEntries({ status: "draft" }),
  client.personas(),
  client.knowledgeRequests()
]);
setEntries(nextEntries);
setDrafts(nextDrafts);
setPersonas(nextPersonas);
setRequests(nextRequests);
```

Do not load or clear Team Runs in `loadData()`. Mutation-driven Archive refresh
must retain a successful Team Run cache.

- [ ] **Step 7: Implement Requests-only Team Run state, loading, and retry**

Add the request state next to `teamRuns`:

```jsx
const [teamRuns, setTeamRuns] = useState([]);
const [teamRunsStatus, setTeamRunsStatus] = useState("idle");
```

Add this callback after `loadData()`:

```jsx
const loadDocumentationTeams = useCallback(async () => {
  if (["loading", "ready"].includes(teamRunsStatus)) return;
  setTeamRunsStatus("loading");
  setError(null);
  try {
    setTeamRuns(await client.teamRuns());
    setTeamRunsStatus("ready");
  } catch (nextError) {
    setTeamRunsStatus("error");
    setError(nextError);
  }
}, [client, teamRunsStatus]);
```

Reset the cache when the injected client changes, and automatically load only
when Requests first becomes active:

```jsx
useEffect(() => {
  setTeamRuns([]);
  setTeamRunsStatus("idle");
}, [client]);

useEffect(() => {
  if (tab === "requests" && teamRunsStatus === "idle") {
    void loadDocumentationTeams();
  }
}, [loadDocumentationTeams, tab, teamRunsStatus]);
```

The `error` state remains the existing Archive alert. A failed Team Run load
does not clear requests or change the base `loading` state.

- [ ] **Step 8: Make delegation controls reflect Team Run request state**

In the Requests header, expose retry only after failure:

```jsx
{teamRunsStatus === "error" ? (
  <Button size="btn-sm" onClick={loadDocumentationTeams}>
    Retry team loading
  </Button>
) : null}
```

Use `teamRunsStatus` in the Team select and Send button:

```jsx
const documentationTeamsReady = teamRunsStatus === "ready";
```

```jsx
disabled={busy || !documentationTeamsReady || !documentationTeams.length}
```

```jsx
disabled={busy || !documentationTeamsReady || !selectedTeamId}
```

Use an explicit empty option while results are unavailable:

```jsx
{!documentationTeams.length ? (
  <option value="">
    {teamRunsStatus === "loading"
      ? "Loading documentation teams…"
      : teamRunsStatus === "error"
        ? "Documentation teams unavailable"
        : "No triggered team available"}
  </option>
) : documentationTeams.map((run) => (
  <option key={run.id} value={run.id}>
    {run.team_name || run.goal}
  </option>
))}
```

- [ ] **Step 9: Remove Map-only tests, fixture data, and imports**

In `ArchiveView.test.jsx`:

- remove `archiveMap` from `makeClient()`;
- delete the seven Map rendering/count/lane/viewport tests at the pre-change
  lines `221-388`;
- remove `fireEvent` and `within` from the Testing Library import;
- retain the new Map-absence and lazy-loading tests;
- update the existing delegation test so it waits for lazy Team Runs before
  clicking Send:

```jsx
const send = screen.getByRole("button", {
  name: "Send Rollback checklist to documentation team"
});
await waitFor(() => expect(send).toBeEnabled());
await userEvent.click(send);
```

The import becomes:

```jsx
import { render, screen, waitFor } from "@testing-library/react";
```

- [ ] **Step 10: Remove every Map-only style selector**

Delete:

- the standalone `.archive-map-node:focus-visible` selector;
- `.archive-map-layout` through `.archive-map-empty`;
- `.archive-legend-line*` selectors;
- all responsive `.archive-map-*` rules.

Do not remove adjacent Archive Library, Request, or Artifact styles.

- [ ] **Step 11: Run the focused tests and verify GREEN**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: both test files pass. The lazy-loading test observes exactly one
successful Team Run fetch, and the retry test observes exactly two attempts.

- [ ] **Step 12: Verify frontend Map presentation is gone**

Run:

```powershell
rg -n "ArchiveMap|archive-map-|archive-legend-line" frontend/src/components/organisms/ArchiveView src/personal_agent_gateway/static/styles.css
```

Expected: no output and exit code `1`.

- [ ] **Step 13: Commit the frontend behavior change**

```powershell
git add frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx frontend/src/components/organisms/ArchiveView/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "refactor(archive): remove Map and lazy-load documentation teams"
```

---

### Task 2: Remove the frontend Archive Map client contract

**Files:**
- Modify: `frontend/src/api/client.test.js:575-637`
- Modify: `frontend/src/api/client.js:299-301`

**Interfaces:**
- Removes: `api.archiveMap(): Promise<{nodes: object[], edges: object[]}>`.
- Preserves: all Archive entry, revision, request-status, and delegation client methods.

- [ ] **Step 1: Add a RED assertion for the removed client method**

Rename the client test to `supports Archive Library, revision, and request
endpoints` and add this assertion at its start:

```js
expect(api).not.toHaveProperty("archiveMap");
```

- [ ] **Step 2: Run the client test and verify RED**

Run:

```powershell
npm --prefix frontend test -- src/api/client.test.js -t "Archive Library"
```

Expected: FAIL because `api.archiveMap` still exists.

- [ ] **Step 3: Delete the client method and its response-order assertions**

Delete from `client.js`:

```js
async archiveMap() {
  return jsonOrNull(await fetch("/api/archive/map"));
},
```

In `client.test.js`:

- remove the seventh `{ nodes: [], edges: [] }` mock response;
- remove `await expect(api.archiveMap())...`;
- remove the seventh `/api/archive/map` fetch assertion;
- leave the first six Archive endpoint assertions unchanged.

- [ ] **Step 4: Run the client test and verify GREEN**

Run:

```powershell
npm --prefix frontend test -- src/api/client.test.js -t "Archive Library"
```

Expected: PASS with six mocked Archive requests.

- [ ] **Step 5: Verify no frontend JavaScript Map contract remains**

Run:

```powershell
rg -n "archiveMap|/api/archive/map" frontend/src -g "*.js" -g "*.jsx"
```

Expected: no output and exit code `1`.

- [ ] **Step 6: Commit the client contract removal**

```powershell
git add frontend/src/api/client.js frontend/src/api/client.test.js
git commit -m "refactor(api): remove Archive Map client contract"
```

---

### Task 3: Remove the backend Map route and graph read model

**Files:**
- Modify: `tests/test_api_archive.py:59-114`
- Modify: `tests/test_archive.py:78-99,221-281`
- Modify: `src/personal_agent_gateway/api/archive.py:178-184`
- Modify: `src/personal_agent_gateway/archive.py:857-1095`

**Interfaces:**
- Removes: authenticated `GET /api/archive/map`.
- Removes: `ArchiveService.graph(*, limit: int = 100)`.
- Preserves: Archive entry, request, draft, binding, revision, delegation, and deletion methods.

- [ ] **Step 1: Add a RED API removal test**

Rename `test_library_publish_search_revision_and_map_api` to
`test_library_publish_search_and_revision_api`. Remove its graph payload
assertions, then add a separate test:

```python
def test_archive_map_api_is_removed(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.get("/api/archive/map")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the removal test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api_archive.py::test_archive_map_api_is_removed -q
```

Expected: FAIL because the current route returns `200`.

- [ ] **Step 3: Delete the route and graph service**

Delete from `api/archive.py`:

```python
@router.get("/map")
def archive_map(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return request.app.state.archive_service.graph()
```

Delete `ArchiveService.graph()` in full from `archive.py`. Do not remove the
`sqlite3` import: later row conversion and transaction helpers still use its
types.

- [ ] **Step 4: Remove graph-only service assertions**

In `tests/test_archive.py`:

- keep `test_persona_request_is_a_gap_not_retrievable_knowledge`, but remove
  its `archive.graph()` node assertions; the existing empty search assertion
  remains its durable behavior contract;
- delete `test_graph_connects_knowledge_request_team_and_review_draft` in
  full;
- leave Draft origin, request assignment, and publish tests unchanged.

- [ ] **Step 5: Run focused backend tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_archive.py tests/test_api_archive.py -q
```

Expected: both files pass, including an authenticated `404` for the deleted
endpoint.

- [ ] **Step 6: Verify backend Map symbols are gone**

Run:

```powershell
rg -n "def graph|archive_map|/api/archive/map|archive_service\.graph" src/personal_agent_gateway tests -g "*.py"
```

Expected: only the intentional `test_archive_map_api_is_removed` test name and
its `/api/archive/map` path literal may remain. There must be no route, method
definition, or service call.

- [ ] **Step 7: Commit the backend removal**

```powershell
git add tests/test_api_archive.py tests/test_archive.py src/personal_agent_gateway/api/archive.py src/personal_agent_gateway/archive.py
git commit -m "refactor(archive): remove Map API and graph read model"
```

---

### Task 4: Run full verification and record the implementation

**Files:**
- Create: `docs/reports/2026-08-07-archive-map-removal-and-lazy-team-loading-implementation.md`
- Modify: `docs/registry.json` through the generator only

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: verified Phase 1 implementation and a searchable completion record.

- [ ] **Step 1: Run the focused frontend contract**

```powershell
npm --prefix frontend test -- src/api/client.test.js src/components/organisms/ArchiveView/ArchiveView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: all three files pass.

- [ ] **Step 2: Run the focused backend contract**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_archive.py tests/test_api_archive.py -q
```

Expected: both files pass.

- [ ] **Step 3: Run the full frontend suite with bounded concurrency**

```powershell
npm --prefix frontend test -- --maxWorkers=1
```

Expected: all frontend tests pass with zero failed files and zero failed tests.

- [ ] **Step 4: Build the production frontend in the isolated worktree**

```powershell
npm --prefix frontend run build
```

Expected: Vite exits `0` and writes the production assets in the isolated
worktree only. Do not copy generated files into the dirty main checkout.

- [ ] **Step 5: Commit the generated production assets from the isolated worktree**

Review the generated asset diff, then commit only the isolated worktree build
output:

```powershell
git status --short src/personal_agent_gateway/frontend_dist
git diff --stat -- src/personal_agent_gateway/frontend_dist
git add src/personal_agent_gateway/frontend_dist
git commit -m "build: refresh Archive frontend assets"
```

Expected: the committed assets correspond to the verified source from Tasks
1-3. No generated change from the dirty main checkout is copied or staged.

- [ ] **Step 6: Verify the removed surface and preserved source tree**

Run:

```powershell
rg -n "ArchiveMap|archiveMap|archive-map-|archive-legend-line|archive_service\.graph|/api/archive/map" frontend/src src/personal_agent_gateway tests -g "*.jsx" -g "*.js" -g "*.py" -g "*.css"
```

Expected: no production reference. The only permitted matches are the
intentional removed-route test name and `/api/archive/map` literal in
`tests/test_api_archive.py`.

Run:

```powershell
git diff --check
```

Expected: exit `0` with no whitespace errors.

- [ ] **Step 7: Write the implementation report with actual verification counts**

Create the report with this fixed metadata. After the commands finish, replace
the five verification instructions below with their observed counts and output
summary before staging the report:

```markdown
---
title: Archive Map 제거 및 Documentation Team 지연 조회 구현 결과
type: report
domain: personal-agent-gateway
feature: archive-map-removal
status: done
aliases:
  - Archive Map 제거 결과
  - Archive Team Runs 지연 조회
tags:
  - archive
  - performance
  - cleanup
updated_at: 2026-08-07
---

# Archive Map 제거 및 Documentation Team 지연 조회 구현 결과

## Summary

Archive Map UI, frontend client, API route, backend graph read model과 전용
스타일을 제거했다. Archive 기본 조회에서 Team Runs를 제외하고 Requests
최초 진입 시 조회·캐시·재시도하도록 변경했다.

## Changes

- Archive 기본 조회를 entries, drafts, personas, requests로 제한했다.
- Team Runs 실패가 직접 Request 작업을 막지 않으며 retry할 수 있다.
- `/api/archive/map`은 인증 상태에서도 404를 반환한다.
- Library, Draft, Artifact, Knowledge Request workflow는 유지했다.

## Verification

- Focused frontend: 실행 결과의 file/test count를 기록한다.
- Focused backend: 실행 결과의 test count를 기록한다.
- Full frontend: 실행 결과의 file/test count를 기록한다.
- Production build: Vite exit 0과 출력 asset 요약을 기록한다.
- Static removal scan과 `git diff --check`: 통과 결과를 기록한다.

## Follow-ups

- 다음 단계는 Knowledge와 Outputs 정보 구조 분리 설계다.
```

- [ ] **Step 8: Rebuild and verify the docs registry**

```powershell
node C:/Users/Administrator/.claude/skills/dev-docs/scripts/build_docs_registry.mjs
rg -n "2026-08-07-archive-map-removal-and-lazy-team-loading-implementation" docs/registry.json
```

Expected: the generator exits `0` and the new report path appears once.

- [ ] **Step 9: Review final scope before committing**

```powershell
git status --short
git diff --stat
git diff --check
```

Expected: only Tasks 1-4 files and isolated-worktree build outputs are changed.
No unrelated source, main-checkout `frontend_dist`, or `.claude/worktrees`
content is included.

- [ ] **Step 10: Commit the verification record**

```powershell
git add docs/reports/2026-08-07-archive-map-removal-and-lazy-team-loading-implementation.md docs/registry.json
git commit -m "docs: Archive Map 제거 결과 기록"
```

---

## Plan Self-Review Checklist

- [x] Every in-scope design requirement maps to a task and test.
- [x] No task introduces Knowledge/Outputs navigation or other Phase 2 work.
- [x] `teamRunsStatus` names and values are consistent across tests and implementation.
- [x] The backend route and service are removed only after the `404` test is observed RED.
- [x] The full build runs only in the isolated worktree.
- [x] Static scans distinguish the intentional removed-route test string from production references.
