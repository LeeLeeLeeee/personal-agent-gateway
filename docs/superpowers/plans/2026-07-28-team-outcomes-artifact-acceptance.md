# Team Outcomes and Artifact Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Team task and run completion depend on structured outcomes, immutable acceptance criteria, verified deliverables, and successful artifact publication.

**Architecture:** The planner stores acceptance criteria with each task. Worker final output is parsed into a strict `TaskOutcome`; a separate acceptance service verifies source integrity, deliverables, and verification evidence before TeamRuntime changes state or publishes artifacts.

**Tech Stack:** Python 3.11+, SQLite migrations, FastAPI, React 19/Vitest, existing ArtifactStore.

## Global Constraints

- Depends on the PAG execution-contract and source-staging plan.
- Natural-language content cannot authorize `completed`.
- Leader synthesis is descriptive and cannot override calculated status.
- Existing historic runs are not reclassified.
- Required task `blocked` produces run/cycle `blocked`.
- `completed_with_failures` is allowed only when all required tasks pass and an optional task fails.
- Only declared verified deliverables may be published or included in the normal result package.

---

### Task 1: Add migration 17 and Team outcome fields

**Files:**
- Modify: `src/personal_agent_gateway/db.py`
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_db_agent_teams_schema.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `tests/test_teams.py`

**Interfaces:**
- Produces: `TaskAcceptance`
- Produces: expanded `TeamTask`
- Produces: `TeamRunStatus` and `CycleStatus` including `blocked`

- [ ] **Step 1: Write failing migration tests**

Assert migration 17, applied after the execution-contract schema 16, adds:

```text
team_tasks.required integer not null default 1
team_tasks.acceptance_json text not null default '{}'
team_tasks.outcome_json text
team_tasks.acceptance_result_json text
team_run_cycles.execution_metadata_json text
```

Also migrate a version-15 fixture and assert its existing tasks become required
with empty acceptance and its completed statuses remain unchanged.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_migrations.py tests/test_db_agent_teams_schema.py -q
```

Expected: FAIL because schema version 17 and columns do not exist.

- [ ] **Step 3: Implement migration 17**

Add `_migration_17_team_task_acceptance`. Use idempotent column checks and append:

```python
(17, "team-task-acceptance", _migration_17_team_task_acceptance)
```

Do not update historic task/run statuses.

- [ ] **Step 4: Add typed fields**

Add:

```python
@dataclass(frozen=True)
class TaskAcceptance:
    required_outputs: tuple[str, ...]
    required_verifications: tuple[str, ...]


@dataclass(frozen=True)
class TeamTask:
    id: str
    team_run_id: str
    title: str
    description: str
    owner_agent_id: str | None
    status: TaskStatus
    required: bool
    acceptance: TaskAcceptance
    outcome: dict[str, object] | None
    acceptance_result: dict[str, object] | None
    result: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    cycle_id: str | None = None
    retry_of_task_id: str | None = None
```

Extend `create_task` with keyword-only `required: bool = True` and
`acceptance: TaskAcceptance | None = None`. Retry must copy both fields.

Add `blocked` to run and cycle status literals and terminal-status sets.
`set_task_status` records `finished_at` for `blocked`.

- [ ] **Step 5: Run service tests**

Run:

```powershell
uv run pytest tests/test_migrations.py tests/test_db_agent_teams_schema.py tests/test_teams.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_migrations.py tests/test_db_agent_teams_schema.py tests/test_teams.py
git commit -m "feat: persist Team acceptance outcomes"
```

### Task 2: Require acceptance criteria from the planner

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `src/personal_agent_gateway/teams.py`

**Interfaces:**
- Produces: `_parse_task_plan` entries with `required` and `acceptance`
- Consumes: `TeamRunService.create_task(..., required=, acceptance=)`

- [ ] **Step 1: Write failing plan-parser tests**

The valid planner response is:

```json
[
  {
    "title": "Create D3 guide",
    "description": "Write the integrated guide.",
    "owner_agent_id": "worker-1",
    "required": true,
    "acceptance": {
      "required_outputs": ["outputs/d3-guide.md"],
      "required_verifications": ["markdown-link-check"]
    }
  }
]
```

Reject missing `required`, missing `acceptance`, absolute paths, `..` path
segments, empty verification names, duplicate output paths, and unknown keys.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_team_runtime.py -q -k "plan or acceptance"
```

Expected: FAIL because current plans contain only title/description/owner.

- [ ] **Step 3: Update the planning prompt and strict parser**

Require exactly the fields shown above. `required_outputs` may be empty only for
analysis-only tasks; such tasks must still have at least one required
verification. A task with neither output nor verification is invalid.

The retry prompt remains “Return ONLY a JSON array” and includes the same
schema. If both attempts are malformed, planning fails instead of inventing
acceptance criteria.

- [ ] **Step 4: Store immutable acceptance**

Pass parsed `required` and `TaskAcceptance` to `create_task`. No API may mutate
acceptance after `start_task`; retry creates a new task snapshot.

- [ ] **Step 5: Run tests**

Run:

```powershell
uv run pytest tests/test_team_runtime.py tests/test_teams.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py tests/test_team_runtime.py tests/test_teams.py
git commit -m "feat: plan immutable Team acceptance"
```

### Task 3: Parse strict worker `TaskOutcome`

**Files:**
- Create: `src/personal_agent_gateway/team_outcomes.py`
- Create: `tests/test_team_outcomes.py`
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Produces: `TaskOutcome`
- Produces: `TaskOutcomeError(code="invalid_task_outcome")`
- Produces: `parse_task_outcome(content: str) -> TaskOutcome`

- [ ] **Step 1: Write failing parser tests**

Use:

```python
def test_failure_prose_is_not_completion():
    with pytest.raises(TaskOutcomeError) as exc:
        parse_task_outcome("권한이 없어 작업하지 못했습니다.")
    assert exc.value.code == "invalid_task_outcome"
```

Also cover valid `completed`, `blocked`, and `failed` JSON; reject code fences,
unknown statuses, missing summary, absolute/escaping deliverables, duplicate
verification names, invalid verification statuses, and non-object JSON.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_team_outcomes.py -q
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the exact types**

```python
TaskOutcomeStatus = Literal["completed", "blocked", "failed"]
VerificationStatus = Literal["passed", "failed"]

@dataclass(frozen=True)
class Deliverable:
    path: str
    kind: str

@dataclass(frozen=True)
class VerificationEvidence:
    name: str
    status: VerificationStatus
    evidence: str

@dataclass(frozen=True)
class TaskOutcome:
    status: TaskOutcomeStatus
    summary: str
    reason_code: str | None
    deliverables: tuple[Deliverable, ...]
    verifications: tuple[VerificationEvidence, ...]
```

Parse the whole stripped response as one JSON object. Do not extract a JSON
substring from prose.

- [ ] **Step 4: Require the outcome in the worker prompt**

Append the exact JSON schema and state that the final response must contain
only that object. `needs_info` mediation may remain prose during intermediate
turns; only the final response uses `TaskOutcome`.

On malformed final output, persist the raw response as diagnostic agent output
and return a synthetic blocked outcome with `invalid_task_outcome`.

- [ ] **Step 5: Run tests**

Run:

```powershell
uv run pytest tests/test_team_outcomes.py tests/test_team_runtime.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/team_outcomes.py src/personal_agent_gateway/team_runtime.py tests/test_team_outcomes.py tests/test_team_runtime.py
git commit -m "feat: require structured Team task outcomes"
```

### Task 4: Verify acceptance and publish declared deliverables

**Files:**
- Create: `src/personal_agent_gateway/team_acceptance.py`
- Create: `tests/test_team_acceptance.py`
- Create: `src/personal_agent_gateway/team_artifact_publisher.py`
- Create: `tests/test_team_artifact_publisher.py`
- Modify: `src/personal_agent_gateway/artifacts.py`
- Modify: `tests/test_artifacts.py`

**Interfaces:**
- Produces: `TeamAcceptanceService.evaluate(...) -> AcceptanceResult`
- Produces: `TeamArtifactPublisher.publish(...) -> tuple[Artifact, ...]`
- Consumes: `SourceStager.verify`
- Consumes: `ArtifactStore.register_existing_file`

- [ ] **Step 1: Write failing acceptance tests**

Cover:

- all required outputs and verifications pass;
- a missing output fails with `required_output_missing`;
- an undeclared outcome deliverable fails with `undeclared_deliverable`;
- a file outside workspace, symlink, directory, and `.env` fail safely;
- a modified `_inputs` snapshot blocks with `input_snapshot_modified`;
- a required verification with `failed` fails acceptance;
- publication failure returns `artifact_publication_failed`.

- [ ] **Step 2: Write failing publisher tests**

Assert the publisher copies only declared files and metadata contains:

```python
{
    "source_path": "outputs/d3-guide.md",
    "sha256": expected_sha,
    "task_id": task.id,
    "cycle_id": cycle.id,
    "team_run_id": run.id,
}
```

Assert `%SystemDrive%/...db` and other undeclared files are not registered.

- [ ] **Step 3: Confirm failures**

Run:

```powershell
uv run pytest tests/test_team_acceptance.py tests/test_team_artifact_publisher.py -q
```

Expected: FAIL because both services are absent.

- [ ] **Step 4: Implement acceptance**

`AcceptanceResult` contains:

```python
@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    status: Literal["completed", "blocked", "failed"]
    reason_code: str | None
    evidence: dict[str, object]
```

Canonicalize with `Path.resolve`, require `relative_to(workspace_root)`, reject
symlinks/non-files/sensitive names, compare declared output sets exactly, then
verify required evidence and input integrity.

- [ ] **Step 5: Implement publication**

Compute SHA-256 before copying. Use the existing ArtifactStore boundary and a
destination:

```text
team-runs/{run_id}/{cycle_id_or_run}/deliverables/{task_id}/{file_name}
```

If any copy or registration fails, delete registrations created by the current
publication attempt and return failure. Do not delete unrelated historic
artifacts.

- [ ] **Step 6: Run tests**

Run:

```powershell
uv run pytest tests/test_team_acceptance.py tests/test_team_artifact_publisher.py tests/test_artifacts.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/team_acceptance.py src/personal_agent_gateway/team_artifact_publisher.py src/personal_agent_gateway/artifacts.py tests/test_team_acceptance.py tests/test_team_artifact_publisher.py tests/test_artifacts.py
git commit -m "feat: verify and publish Team deliverables"
```

### Task 5: Replace TeamRuntime success transitions

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `src/personal_agent_gateway/hook_runner.py`
- Modify: `tests/test_hook_runner.py`

**Interfaces:**
- Consumes: `TaskOutcome`
- Consumes: `TeamAcceptanceService`
- Consumes: `TeamArtifactPublisher`
- Produces: calculated task/run/cycle status

- [ ] **Step 1: Add regression tests for false completion**

Add a test whose model returns:

```text
로컬 도구가 실패해 산출물을 만들 수 없었습니다.
```

Assert task and run are `blocked`, not `completed`.

Add tests for:

- required task blocked → run/cycle blocked;
- required task failed → run/cycle failed;
- all required tasks complete → completed;
- optional task failed after required tasks pass → completed_with_failures;
- QA task outcome `failed`/Not Ready → failed;
- leader synthesis saying “completed” cannot override calculated failed state.

- [ ] **Step 2: Confirm failures**

Run:

```powershell
uv run pytest tests/test_team_runtime.py tests/test_hook_runner.py -q
```

Expected: FAIL under the current returned-string completion path.

- [ ] **Step 3: Replace `_execute` transition logic**

After `_run_task`:

1. persist the structured outcome;
2. map claimed `blocked`/`failed` directly;
3. for claimed `completed`, run acceptance and publication;
4. call `finish_task` with the calculated status and persisted evidence.

Do not call `finish_task(..., "completed")` directly from returned content.

- [ ] **Step 4: Replace `_terminal_status`**

Calculate required and optional task groups:

```python
if any(t.required and t.status == "failed" for t in tasks):
    return "failed"
if any(t.required and t.status == "blocked" for t in tasks):
    return "blocked"
if all(t.status == "completed" for t in tasks if t.required):
    if any(t.status in {"blocked", "failed"} for t in tasks if not t.required):
        return "completed_with_failures"
    return "completed"
return "blocked"
```

Handle an empty task list only for `planning_only`; an executing run with no
tasks fails planning.

- [ ] **Step 5: Propagate blocked status to Hook delivery**

Hook-run handling must not treat blocked Team cycles as success. Store an
actionable blocked error and leave any source request unresolved for retry or
user action.

- [ ] **Step 6: Run tests**

Run:

```powershell
uv run pytest tests/test_team_runtime.py tests/test_hook_runner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/hook_runner.py tests/test_team_runtime.py tests/test_hook_runner.py
git commit -m "fix: gate Team completion on acceptance"
```

### Task 6: Replace full-workspace packaging

**Files:**
- Modify: `src/personal_agent_gateway/team_results.py`
- Modify: `tests/test_team_results.py`
- Modify: `src/personal_agent_gateway/app.py`

**Interfaces:**
- Consumes: published deliverable metadata
- Produces: result, manifest, and verification artifacts without default workspace ZIP

- [ ] **Step 1: Write failing packaging tests**

Assert:

- `workspace.zip` is absent for isolated runs;
- manifest lists published deliverables only;
- result contains cycle objective when present;
- result contains protocol version, execution metadata, task outcome,
  acceptance result, and artifact hashes;
- undeclared workspace files do not appear.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_team_results.py -q
```

Expected: FAIL because current packaging walks the whole workspace.

- [ ] **Step 3: Remove default workspace traversal**

Delete `workspace.zip` from `_PACKAGE_FILES` and normal `build`. Build
`file-manifest.json` from artifacts published for the current run/cycle.

Keep `workspace_snapshot` only for task change diagnostics; it is not an
artifact allowlist.

- [ ] **Step 4: Correct result metadata**

Use:

```python
objective = (
    self._teams.get_cycle_objective(cycle_id)
    if cycle_id is not None
    else run.goal
)
```

Include the frozen execution metadata and all acceptance fields. Do not
fabricate these fields for historic runs.

- [ ] **Step 5: Run tests**

Run:

```powershell
uv run pytest tests/test_team_results.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/team_results.py src/personal_agent_gateway/app.py tests/test_team_results.py
git commit -m "fix: package declared Team artifacts only"
```

### Task 7: Expose blocked and acceptance state through API and UI

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `tests/test_api_team_runs.py`
- Modify: `frontend/src/components/molecules/TeamTaskCard/index.jsx`
- Modify: `frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx`
- Modify: `frontend/src/components/molecules/TeamRunCard/index.jsx`
- Modify: `frontend/src/components/molecules/TeamRunCard/TeamRunCard.test.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `frontend/src/hooks/useTeamRunController.js`
- Modify: `frontend/src/hooks/useTeamRunController.test.jsx`

**Interfaces:**
- Produces: API fields `required`, `acceptance`, `outcome`, `acceptance_result`
- Produces: visible blocked status and reason

- [ ] **Step 1: Write failing API tests**

Assert task payloads expose the four acceptance fields and run/cycle payloads
accept `blocked` as terminal. Polling helpers must stop on blocked.

- [ ] **Step 2: Write failing component tests**

Render blocked run/task fixtures and assert:

- status text is “차단됨”;
- stable reason code and safe diagnostic are visible;
- required outputs and verification results are shown;
- blocked is not styled or announced as completed.

- [ ] **Step 3: Confirm failures**

Run:

```powershell
uv run pytest tests/test_api_team_runs.py -q
Set-Location frontend
npm test -- TeamTaskCard TeamRunCard TeamRunDetail useTeamRunController
Set-Location ..
```

Expected: backend and frontend tests fail on missing blocked/acceptance support.

- [ ] **Step 4: Implement API and UI state**

Add `blocked` to terminal status collections. Serialize stored JSON as objects,
not strings. Use existing status presentation patterns and add a distinct
blocked label; do not add new workflow controls.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_api_team_runs.py -q
Set-Location frontend
npm test -- TeamTaskCard TeamRunCard TeamRunDetail useTeamRunController
npm run build
Set-Location ..
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py frontend/src/components frontend/src/hooks
git commit -m "feat: expose blocked Team acceptance"
```

### Task 8: Run complete PAG verification

**Files:**
- Modify only files required by failures caused by this plan.

**Interfaces:**
- Verifies all prior tasks.

- [ ] **Step 1: Run backend verification**

```powershell
uv run pytest -q
uv run ruff check .
```

Expected: PASS with zero failures and zero Ruff diagnostics.

- [ ] **Step 2: Run frontend verification**

```powershell
Set-Location frontend
npm test
npm run build
Set-Location ..
```

Expected: PASS.

- [ ] **Step 3: Check diff**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files are modified.

- [ ] **Step 4: Route any failure back to its owning task**

Do not create an empty verification commit. If a command fails, return to the
task that introduced the failure, add a reproducing test there, fix it, rerun
this complete verification task, and commit with that owning task's message.
