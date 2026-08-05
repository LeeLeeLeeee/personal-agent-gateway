# Artifact Browser Provenance, Search, and Safe Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build one readable Archive artifact browser for Chat, Team, Job, schedule, and manual files, with server search, useful preview, and safe single/bulk deletion.

**Architecture:** Persist one explicit creation origin on each artifact; use chat_turns to retain the initiating Chat action and resolve source breadcrumbs in a cursor-paged browser endpoint. Keep Team input tables as consumption references, and calculate browser groups from resolved domain labels instead of raw metadata. The Archive frontend receives resolved rows, renders compact grouped lists, and preserves blocked selections after a structured deletion result.

**Tech Stack:** Python 3, FastAPI, SQLite migrations, pytest, React 19, Vite, Vitest, Testing Library, existing static CSS.

## Global constraints

- Do not start PAG/LMG from Codex; only run one-shot tests/builds.
- No automatic deletion when expires_at passes.
- Do not add full-text file-content indexing, a recycle bin, generic context trees, or user collections.
- Raw IDs and internal paths are technical details, not default Archive list content.
- Existing DELETE /api/artifacts/{id} remains; the new batch endpoint is the browser contract.
- Every production behavior starts with a test that has been observed failing.

---

## File structure

| Path | Responsibility |
|---|---|
| src/personal_agent_gateway/migrations.py | Schema version 26 and deterministic legacy backfill |
| src/personal_agent_gateway/chat_turns.py | Durable Chat turn create/finish/read service |
| src/personal_agent_gateway/jobs.py | source_chat_turn_id job lineage |
| src/personal_agent_gateway/runtime.py | Pass active turn into Chat-created Jobs |
| src/personal_agent_gateway/api/chat_sessions.py | Create/finish a Chat turn around one request |
| src/personal_agent_gateway/artifacts.py | Origin model, browser query, usage resolution, batch delete |
| src/personal_agent_gateway/team_artifact_publisher.py | Explicit Team task origin |
| src/personal_agent_gateway/team_results.py | Explicit Team package origin |
| src/personal_agent_gateway/job_worker.py | Explicit Job output origin |
| src/personal_agent_gateway/api/artifacts.py | Browser and batch-delete routes |
| frontend/src/api/client.js | Browser and structured-delete client calls |
| frontend/src/components/organisms/ArtifactsView/index.jsx | Compact grouped browser and selection toolbar |
| frontend/src/components/organisms/ArtifactModal/index.jsx | MIME-based preview and technical-details disclosure |
| src/personal_agent_gateway/static/styles.css | Compact list and responsive inspector styles |
| tests/test_artifacts.py, tests/test_api_artifacts.py | Service/API contracts |
| tests/test_jobs.py, tests/test_app.py | Chat-turn producer lineage |
| frontend/src/**/ArtifactsView.test.jsx | Browser flows |
| frontend/src/**/ArtifactModal.test.jsx | Preview/detail flows |

## Task 1: Persist Chat turns and origin fields

**Files:**
- Modify: src/personal_agent_gateway/migrations.py
- Modify: src/personal_agent_gateway/artifacts.py
- Create: src/personal_agent_gateway/chat_turns.py
- Test: tests/test_artifacts.py
- Test: tests/test_jobs.py

**Consumes:** Database and existing ArtifactStore.
**Produces:** ChatTurnService; Artifact fields origin_kind, artifact_role, source_chat_turn_id, source_team_task_id, source_team_run_id, source_cycle_id, origin_group_label_snapshot, origin_item_label_snapshot.

- [ ] **Step 1: Write failing migration/service tests**

~~~
def test_artifact_backfill_prefers_team_task_origin(tmp_path: Path):
    store = migrated_store_with_task_metadata(tmp_path)
    artifact = store.get("legacy-task-artifact")
    assert artifact.origin_kind == "team_task_output"
    assert artifact.source_team_task_id == "task-1"

def test_chat_turn_keeps_excerpt_and_terminal_status(tmp_path: Path):
    turns = ChatTurnService(Database(tmp_path / "app.db"))
    turns.create("turn-1", "session-1", "event-1", "Create a release note")
    turns.finish("turn-1", "completed")
    assert turns.get("turn-1").status == "completed"
~~~

The first test catches a migration that groups Team output as legacy; the second catches an ephemeral request ID.

- [ ] **Step 2: Run the tests and verify RED**

Run: pytest tests/test_artifacts.py tests/test_jobs.py -q

Expected: FAIL because ChatTurnService and explicit artifact origins do not exist.

- [ ] **Step 3: Add the minimal schema and models**

Create chat_turns with id, session_id, user_event_id, prompt_excerpt, status, created_at, and finished_at. Add nullable origin columns and jobs.source_chat_turn_id. Add source and (origin_kind, created_at, id) indexes. Add a version-26 migration that maps task metadata, package metadata, Job, session, then legacy in that order. Update Artifact and row mapping to expose the new fields.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: pytest tests/test_artifacts.py tests/test_jobs.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/artifacts.py src/personal_agent_gateway/chat_turns.py tests/test_artifacts.py tests/test_jobs.py
git commit -m "feat: persist artifact origins"
~~~

## Task 2: Propagate Chat and Team producer identity

**Files:**
- Modify: src/personal_agent_gateway/jobs.py
- Modify: src/personal_agent_gateway/runtime.py
- Modify: src/personal_agent_gateway/api/chat_sessions.py
- Modify: src/personal_agent_gateway/job_worker.py
- Modify: src/personal_agent_gateway/team_artifact_publisher.py
- Modify: src/personal_agent_gateway/team_results.py
- Test: tests/test_app.py
- Test: tests/test_artifacts.py

**Consumes:** ChatTurnService and origin-aware ArtifactStore.
**Produces:** Jobs linked to Chat turns; all producer call sites set a controlled origin_kind and artifact_role.

- [ ] **Step 1: Write failing lineage tests**

~~~
def test_chat_request_persists_turn_and_links_created_job(client):
    response = client.post("/api/sessions/session-1/chat", json={"message": "run pwd"})
    turn_id = response.json()["request_id"]
    assert client.app.state.job_service.list_jobs()[0].source_chat_turn_id == turn_id

def test_team_task_publication_writes_explicit_origin(tmp_path: Path):
    artifact = publish_team_task_output(tmp_path)
    assert (artifact.origin_kind, artifact.source_team_task_id) == (
        "team_task_output", "task-1"
    )
~~~

- [ ] **Step 2: Run the tests and verify RED**

Run: pytest tests/test_app.py tests/test_artifacts.py -q

Expected: FAIL because Chat request IDs are not persistent producer context and Team writes metadata only.

- [ ] **Step 3: Implement the smallest propagation path**

Create the turn before AgentRuntime.handle_user_message. Pass its ID into the runtime and then JobService.create_job. Finish the turn as completed, cancelled, or failed. Set job_output, team_task_output, team_run_package, and manual/chat upload origins at their publisher/registration call sites. Keep old metadata for compatibility.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: pytest tests/test_app.py tests/test_artifacts.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/personal_agent_gateway/jobs.py src/personal_agent_gateway/runtime.py src/personal_agent_gateway/api/chat_sessions.py src/personal_agent_gateway/job_worker.py src/personal_agent_gateway/team_artifact_publisher.py src/personal_agent_gateway/team_results.py tests/test_app.py tests/test_artifacts.py
git commit -m "feat: link artifact producers to chat turns"
~~~

## Task 3: Add browser query and server-side search

**Files:**
- Modify: src/personal_agent_gateway/artifacts.py
- Modify: src/personal_agent_gateway/api/artifacts.py
- Test: tests/test_artifacts.py
- Test: tests/test_api_artifacts.py

**Consumes:** Explicit origin columns and domain source tables.
**Produces:** ArtifactBrowserItem and GET /api/artifacts/browser.

- [ ] **Step 1: Write failing search tests**

~~~
def test_browser_search_matches_task_title_not_raw_id(tmp_path: Path):
    store = seeded_browser_store(tmp_path)
    page = store.browser_page(segment="saved", query="verify chart", limit=20)
    assert [item.artifact.id for item in page.items] == ["task-output"]
    assert page.items[0].breadcrumbs[-1].label == "Verify chart examples"

def test_browser_search_finds_catalog_item_past_default_page(tmp_path: Path):
    store = seeded_browser_store(tmp_path, artifact_count=205)
    assert [item.artifact.id for item in store.browser_page(
        segment="saved", query="needle", limit=20
    ).items] == ["needle-artifact"]
~~~

- [ ] **Step 2: Run the tests and verify RED**

Run: pytest tests/test_artifacts.py tests/test_api_artifacts.py -q

Expected: FAIL because no resolved browser page exists.

- [ ] **Step 3: Implement browser items and route**

Add browser_page(segment, query, file_kind, source_kind, limit, cursor). Resolve Chat session/turn, Team run/cycle/task, Job/schedule, manual, and legacy breadcrumbs through explicit joins; use snapshots when a source is absent. Apply escaped case-insensitive LIKE across title, filename, role, tags, labels, and technical IDs before cursor pagination. Return flat items, segment counts, and opaque next_cursor. Reuse existing cleanup eligibility for the cleanup segment.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: pytest tests/test_artifacts.py tests/test_api_artifacts.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/personal_agent_gateway/artifacts.py src/personal_agent_gateway/api/artifacts.py tests/test_artifacts.py tests/test_api_artifacts.py
git commit -m "feat: add searchable artifact browser"
~~~

## Task 4: Add structured safe deletion

**Files:**
- Modify: src/personal_agent_gateway/artifacts.py
- Modify: src/personal_agent_gateway/api/artifacts.py
- Test: tests/test_artifacts.py
- Test: tests/test_api_artifacts.py

**Consumes:** Existing Team input-reference tables.
**Produces:** ArtifactDeleteResult and POST /api/artifacts/delete.

- [ ] **Step 1: Write failing deletion tests**

~~~
def test_batch_delete_reports_task_input_and_removes_safe_item(tmp_path: Path):
    store = seeded_referenced_artifacts(tmp_path)
    result = store.delete_many(["free", "task-input"])
    assert result.deleted_ids == ("free",)
    assert result.blocked[0].artifact_id == "task-input"
    assert result.blocked[0].references[0].kind == "team_task_input"

def test_batch_delete_rejects_duplicate_ids(client):
    response = client.post("/api/artifacts/delete", json={"artifact_ids": ["a1", "a1"]})
    assert response.status_code == 422
~~~

- [ ] **Step 2: Run the tests and verify RED**

Run: pytest tests/test_artifacts.py tests/test_api_artifacts.py -q

Expected: FAIL because deletion returns boolean/generic conflict information only.

- [ ] **Step 3: Implement result and route**

Resolve request, cycle, and task input usages with readable labels. Recheck references immediately before each deletion. Return deleted_ids, blocked entries with references, and missing_ids; reject empty, duplicate, and over-200 requests before mutation. Keep current single DELETE endpoint for compatibility and audit every deleted ID.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: pytest tests/test_artifacts.py tests/test_api_artifacts.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/personal_agent_gateway/artifacts.py src/personal_agent_gateway/api/artifacts.py tests/test_artifacts.py tests/test_api_artifacts.py
git commit -m "feat: report blocked artifact deletions"
~~~

## Task 5: Build the compact Artifact browser

**Files:**
- Modify: frontend/src/api/client.js
- Modify: frontend/src/api/client.test.js
- Modify: frontend/src/components/organisms/ArtifactsView/index.jsx
- Modify: frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx
- Modify: src/personal_agent_gateway/static/styles.css

**Consumes:** api.artifactBrowser(params) and api.deleteArtifacts(ids).
**Produces:** Server-search UI, resolved source groups, separate cleanup/normal selection state.

- [ ] **Step 1: Write failing browser UI tests**

~~~
it("searches resolved Team source labels through the browser API", async () => {
  api.artifactBrowser.mockResolvedValue(browserPage("Verify chart examples"));
  renderView();
  await userEvent.type(screen.getByRole("search", { name: "Search artifacts" }), "verify");
  expect(await screen.findByText("Verify chart examples")).toBeInTheDocument();
});

it("keeps blocked artifacts selected after partial deletion", async () => {
  api.deleteArtifacts.mockResolvedValue({
    deleted_ids: ["free"],
    blocked: [{ artifact_id: "used", references: [{ label: "QA task" }] }],
    missing_ids: []
  });
  renderView(browserItems("free", "used"));
  await selectRows("free", "used");
  await userEvent.click(screen.getByRole("button", { name: /선택 삭제/ }));
  expect(await screen.findByText(/QA task/)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /used/ })).toBeChecked();
});
~~~

- [ ] **Step 2: Run the tests and verify RED**

Run: npm --prefix frontend test -- ArtifactsView

Expected: FAIL because the current view filters a prop array into cards and lacks these client methods.

- [ ] **Step 3: Implement the browser state and list**

Add encoded browser-query and batch-delete client methods. Debounce search, ignore stale responses, reset pages on filter change, and merge loaded items by artifact ID. Group resolved breadcrumbs, render compact rows, and keep raw fields out of default display. Add a deliberate normal-selection mode; cleanup retains its own unchecked set. Do not implement implicit select-all.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: npm --prefix frontend test -- ArtifactsView

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: browse and search artifacts by source"
~~~

## Task 6: Improve preview and responsive details

**Files:**
- Modify: frontend/src/components/organisms/ArtifactModal/index.jsx
- Modify: frontend/src/components/organisms/ArtifactModal/ArtifactModal.test.jsx
- Modify: src/personal_agent_gateway/static/styles.css

**Consumes:** artifact MIME type, filename extension, and api.artifactText(id).
**Produces:** image/media/PDF/Markdown/text/fallback preview paths with collapsed technical details.

- [ ] **Step 1: Write failing preview/detail tests**

~~~
it("renders Markdown content instead of the binary fallback", async () => {
  vi.spyOn(api, "artifactText").mockResolvedValue("# Release notes\n\nReady.");
  render(<ArtifactModal artifact={markdownArtifact} onClose={() => {}} />);
  expect(await screen.findByRole("heading", { name: "Release notes" })).toBeInTheDocument();
});

it("hides the internal path until technical details expand", () => {
  render(<ArtifactModal artifact={artifact} onClose={() => {}} />);
  expect(screen.queryByText(artifact.relative_path)).not.toBeVisible();
  fireEvent.click(screen.getByText("기술 정보"));
  expect(screen.getByText(artifact.relative_path)).toBeVisible();
});
~~~

- [ ] **Step 2: Run the tests and verify RED**

Run: npm --prefix frontend test -- ArtifactModal

Expected: FAIL because Markdown is gated by artifact type and the path is always displayed.

- [ ] **Step 3: Implement MIME-based preview and inspector presentation**

Render Markdown through MarkdownContent with pathRegistration={false}; render text/*, JSON, YAML, XML, source files, and logs as text. Preserve image/media/PDF behavior and the binary fallback. Put MIME, path, IDs, hashes, and raw metadata in a closed details element. Use the Archive layout CSS to show a right inspector on wide screens while retaining modal behavior on narrow screens and existing Chat callers.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: npm --prefix frontend test -- ArtifactModal

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add frontend/src/components/organisms/ArtifactModal/index.jsx frontend/src/components/organisms/ArtifactModal/ArtifactModal.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: improve artifact previews"
~~~

## Task 7: Integrate and verify

**Files:**
- Modify if needed: frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx
- Modify: docs/registry.json through the registry script only

- [ ] **Step 1: Write a failing Archive boundary integration test**

~~~
it("keeps source artifact browser results separate from published Library entries", async () => {
  render(<ArchiveView artifacts={browserArtifacts} onArtifactChange={vi.fn()} />);
  await userEvent.click(screen.getByRole("tab", { name: /Artifacts/ }));
  expect(screen.getByText("Chat session")).toBeInTheDocument();
  expect(screen.queryByText("Published Library entry")).not.toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run the test and verify RED**

Run: npm --prefix frontend test -- ArchiveView

Expected: FAIL until Archive fixtures and client wiring accept the browser contract.

- [ ] **Step 3: Make only integration compatibility changes**

Keep ArchiveView as owner and adjust test fixtures/client wiring only as required by the new browser contract.

- [ ] **Step 4: Verify focused and full suites**

Run:

~~~
pytest tests/test_artifacts.py tests/test_api_artifacts.py tests/test_jobs.py tests/test_app.py -q
npm --prefix frontend test
npm run build:frontend
~~~

Expected: all commands PASS.

- [ ] **Step 5: Rebuild docs registry and commit**

~~~
node C:/Users/Administrator/.claude/skills/dev-docs/scripts/build_docs_registry.mjs
git add frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx docs/registry.json
git commit -m "test: cover artifact browser integration"
~~~

## Plan self-review

- Spec coverage: Tasks 1–2 implement durable provenance; Task 3 resolves grouping and search; Task 4 protects deletion; Tasks 5–6 implement browser/preview UX; Task 7 checks Archive integration and regressions.
- Placeholder scan: no unresolved placeholders or undefined interfaces remain.
- Type consistency: source_chat_turn_id, origin_kind, artifact_role, ArtifactBrowserItem, artifactBrowser, and deleteArtifacts use the same names across producer, API, client, and UI tasks.
