# Team Task Artifact Inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Make every cross-run artifact used by a Team task an explicit, immutable, staged task input rather than an absolute path discovered in prose.

**Architecture:** The delegation API snapshots selected artifact IDs onto the cycle request, then copies them to the created cycle. A planner can assign only IDs from that cycle catalog. Before invoking a worker, PAG verifies and copies each declared artifact under the current workspace \`inputs/\` directory, and the worker receives only those relative paths.

**Tech Stack:** Python 3.13, FastAPI/Pydantic, SQLite migrations, pytest.

## Global Constraints

- Do not grant providers an external read root or relax their sandbox.
- Historical artifacts are usable only when selected by ID before dispatch.
- Task prose and previous worker reports never create input bindings.
- Omitting \`artifact_ids\` preserves existing knowledge-request delegation.
- Stage only files registered in \`artifacts\`, verifying SHA-256 and size.

---

### Task 1: Persist cycle artifact catalogs

**Files:**
- Modify: \`src/personal_agent_gateway/migrations.py\`
- Modify: \`src/personal_agent_gateway/team_cycles.py\`
- Modify: \`src/personal_agent_gateway/teams.py\`
- Modify: \`src/personal_agent_gateway/api/archive.py\`
- Test: \`tests/test_api_archive.py\`
- Test: \`tests/test_team_cycles.py\`
- Test: \`tests/test_db_agent_teams_schema.py\`

**Interfaces:**
- Produces: \`TeamCycleInputArtifact\`, \`list_request_input_artifacts(request_id)\`, and \`list_cycle_input_artifacts(cycle_id)\`.

- [ ] **Step 1: Write failing delegation tests**

\`\`\`python
def test_delegate_snapshots_selected_artifact(tmp_path: Path) -> None:
    artifact = create_artifact(client, "draft.md", "source")
    response = client.post(url, json={"team_run_id": run.id, "artifact_ids": [artifact.id]})
    assert response.status_code == 200
    request_id = response.json()["cycle_request"]["id"]
    assert [item.artifact_id for item in cycles.list_request_input_artifacts(request_id)] == [artifact.id]


def test_delegate_rejects_unknown_artifact(tmp_path: Path) -> None:
    response = client.post(url, json={"team_run_id": run.id, "artifact_ids": ["missing"]})
    assert response.status_code == 404
\`\`\`

- [ ] **Step 2: Verify the tests fail**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_api_archive.py -k "delegate and artifact" -q\`

Expected: FAIL because \`artifact_ids\` and the input-catalog methods do not exist.

- [ ] **Step 3: Add immutable catalog tables and API binding**

\`\`\`sql
create table team_cycle_request_input_artifacts (
    cycle_request_id text not null references team_cycle_requests(id) on delete cascade,
    artifact_id text not null references artifacts(id) on delete restrict,
    relative_path text not null, sha256 text not null, size_bytes integer not null,
    created_at text not null, primary key (cycle_request_id, artifact_id)
);
create table team_cycle_input_artifacts (
    cycle_id text not null references team_run_cycles(id) on delete cascade,
    artifact_id text not null references artifacts(id) on delete restrict,
    relative_path text not null, sha256 text not null, size_bytes integer not null,
    created_at text not null, primary key (cycle_id, artifact_id)
);
\`\`\`

Implement migration 22, validate every submitted artifact exists and is a file, and snapshot its registered path, hash, and size. Add \`artifact_ids: list[str] = Field(default_factory=list)\` to \`DelegateKnowledgeRequest\`. In \`TeamRunService.create_cycle\`, copy request catalog rows to the cycle in its existing transaction.

- [ ] **Step 4: Verify and commit**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_db_agent_teams_schema.py tests\test_team_cycles.py tests\test_api_archive.py -q\`

Expected: PASS.

\`\`\`powershell
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/api/archive.py tests/test_db_agent_teams_schema.py tests/test_team_cycles.py tests/test_api_archive.py
git commit -m "feat: snapshot team cycle input artifacts"
\`\`\`

### Task 2: Validate and persist task input declarations

**Files:**
- Modify: \`src/personal_agent_gateway/team_runtime.py\`
- Modify: \`src/personal_agent_gateway/team_model_effects.py\`
- Modify: \`src/personal_agent_gateway/teams.py\`
- Modify: \`src/personal_agent_gateway/migrations.py\`
- Test: \`tests/test_team_runtime.py\`
- Test: \`tests/test_team_model_effects.py\`

**Interfaces:**
- Consumes: \`list_cycle_input_artifacts(cycle_id)\`.
- Produces: \`TeamTaskInputArtifact\` and \`list_task_input_artifacts(task_id)\`.

- [ ] **Step 1: Write failing plan tests**

\`\`\`python
def test_task_plan_rejects_input_not_selected_for_cycle() -> None:
    payload = {"title": "Review", "description": "Review", "owner_agent_id": worker.id,
               "required": True, "input_artifact_ids": ["outside"], "acceptance": EMPTY_ACCEPTANCE}
    with pytest.raises(ValueError, match="unknown task input artifact"):
        parse_task_plan(json.dumps([payload]), allowed_input_artifact_ids=set())


def test_apply_plan_persists_selected_task_input(setup) -> None:
    task = setup.effects.apply_plan(setup.completed_plan.id)[0]
    assert setup.teams.list_task_input_artifacts(task.id)[0].artifact_id == setup.artifact.id
\`\`\`

- [ ] **Step 2: Verify the tests fail**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_model_effects.py -k "input_artifact" -q\`

Expected: FAIL because task plans have no \`input_artifact_ids\` field.

- [ ] **Step 3: Add plan-time input enforcement**

\`\`\`sql
create table team_task_input_artifacts (
    task_id text not null references team_tasks(id) on delete cascade,
    artifact_id text not null references artifacts(id) on delete restrict,
    relative_path text not null, sha256 text not null, size_bytes integer not null,
    staged_path text not null, created_at text not null,
    primary key (task_id, artifact_id)
);
\`\`\`

Add migration 23 and \`TeamTaskInputArtifact\`. Require \`input_artifact_ids\` in task-plan JSON (an empty list is valid). Pass the cycle catalog into plan validation; reject unknown or duplicate IDs. Update planner prompts to show an ID/title/relative-path catalog and explicitly forbid all unlisted IDs. Persist task input rows atomically when applying the model operation, deriving \`inputs/<artifact-id>/<basename>\` as \`staged_path\`.

- [ ] **Step 4: Verify and commit**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_model_effects.py -q\`

Expected: PASS.

\`\`\`powershell
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/migrations.py tests/test_team_runtime.py tests/test_team_model_effects.py
git commit -m "feat: bind team task inputs to cycle artifacts"
\`\`\`

### Task 3: Stage verified inputs immediately before worker invocation

**Files:**
- Create: \`src/personal_agent_gateway/team_task_inputs.py\`
- Modify: \`src/personal_agent_gateway/team_runtime.py\`
- Test: \`tests/test_team_task_inputs.py\`
- Test: \`tests/test_team_runtime.py\`

**Interfaces:**
- Produces: \`TaskInputManifest(paths: tuple[str, ...], sha256: str)\` from \`TaskInputStager.stage(task, workspace_root)\`.

- [ ] **Step 1: Write failing staging tests**

\`\`\`python
def test_stage_copies_frozen_artifact_under_inputs(tmp_path: Path) -> None:
    manifest = stager.stage(task, workspace)
    assert (workspace / "inputs" / artifact.id / "draft.md").read_text() == "source"
    assert manifest.paths == (f"inputs/{artifact.id}/draft.md",)


def test_stage_rejects_hash_changed_artifact(tmp_path: Path) -> None:
    source.write_text("changed")
    with pytest.raises(TaskInputUnavailable, match="input_artifact_unavailable"):
        stager.stage(task, workspace)
\`\`\`

- [ ] **Step 2: Verify the tests fail**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_team_task_inputs.py -q\`

Expected: FAIL because \`TaskInputStager\` does not exist.

- [ ] **Step 3: Implement staging and prompt exposure**

\`\`\`python
class TaskInputStager:
    def stage(self, task: TeamTask, workspace_root: Path) -> TaskInputManifest:
        records = self._teams.list_task_input_artifacts(task.id)
        staged = [self._copy_verified(record, workspace_root) for record in records]
        manifest_path = self._write_manifest(task.id, workspace_root, staged)
        return TaskInputManifest(tuple(item.relative_path for item in staged), _sha256(manifest_path))
\`\`\`

Create a JSON manifest under \`workspace/inputs/.manifests/<task-id>.json\`. Reject missing files, non-files, path escape, size mismatch, or hash mismatch using \`TaskInputUnavailable("input_artifact_unavailable")\`. Call it before the worker model operation and add an \`ALLOWED TASK INPUTS\` prompt block containing only staged relative paths.

- [ ] **Step 4: Verify and commit**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_team_task_inputs.py tests\test_team_runtime.py -q\`

Expected: PASS.

\`\`\`powershell
git add src/personal_agent_gateway/team_task_inputs.py src/personal_agent_gateway/team_runtime.py tests/test_team_task_inputs.py tests/test_team_runtime.py
git commit -m "feat: stage verified team task inputs"
\`\`\`

### Task 4: Add the \`dfbf2063\` regression guard

**Files:**
- Modify: \`tests/test_team_runtime.py\`
- Modify: \`tests/test_api_archive.py\`
- Modify: \`tests/test_team_cycle_dispatcher.py\`

**Interfaces:**
- Consumes: Tasks 1–3 catalog, plan validation, and stager.
- Produces: regression evidence that an external path in an earlier report cannot bind an input.

- [ ] **Step 1: Write the failing regression**

\`\`\`python
async def test_prior_report_cannot_bind_an_unselected_historical_artifact(tmp_path: Path) -> None:
    historical = create_artifact(tmp_path, "d3-curriculum-draft.md", "old")
    cycle = create_delegated_cycle(tmp_path, artifact_ids=[])
    teams.add_message(cycle.team_run_id, kind="agent_output",
                      content=f"Review C:/historical/{historical.id}/d3-curriculum-draft.md",
                      cycle_id=cycle.id)
    with pytest.raises(ValueError, match="unknown task input artifact"):
        apply_plan_with_inputs(cycle, [historical.id])
\`\`\`

- [ ] **Step 2: Verify the regression fails before the guard**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -k "unselected_historical_artifact" -q\`

Expected: FAIL until plan application reads only the persisted cycle catalog.

- [ ] **Step 3: Complete only the required integration glue**

Ensure task-plan application gets allowed IDs only from \`list_cycle_input_artifacts(operation.cycle_id)\`. It must not parse messages, previous task results, or task descriptions to infer inputs. Keep empty catalogs valid for ordinary knowledge requests.

- [ ] **Step 4: Run focused verification and commit**

Run: \`.\.venv\Scripts\python.exe -m pytest tests\test_api_archive.py tests\test_team_cycles.py tests\test_team_runtime.py tests\test_team_model_effects.py tests\test_team_cycle_dispatcher.py tests\test_team_task_inputs.py tests\test_execution_contract.py -q\`

Expected: PASS.

\`\`\`powershell
git add tests/test_api_archive.py tests/test_team_cycles.py tests/test_team_runtime.py tests/test_team_model_effects.py tests/test_team_cycle_dispatcher.py tests/test_team_task_inputs.py tests/test_execution_contract.py
git commit -m "test: cover unselected team task artifacts"
\`\`\`

## Final verification

- [ ] Run \`git diff --check\`.
- [ ] Run the focused verification command from Task 4.
- [ ] Run \`.\.venv\Scripts\python.exe -m pytest -q\` and record unrelated environment failures separately.
- [ ] Confirm migrations 22 and 23 preserve existing runs with empty input catalogs.
