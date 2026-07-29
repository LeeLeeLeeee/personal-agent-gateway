# Team Cycle SPACE Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each continuous Team cycle capture and execute with the Team's current SPACE policy instead of reusing the parent Run's stale policy.

**Architecture:** Add a SPACE snapshot to `team_run_cycles`, capture it atomically during normal and retry cycle creation, and resolve agent execution from the task's cycle before falling back to the Run snapshot. Keep isolated writes while allowing `read_mode="all"` to use the provider's unbounded read scope without staging.

**Tech Stack:** Python 3.12, SQLite migrations, FastAPI payload helpers, pytest.

## Global Constraints

- Resolve Team SPACE once when a cycle is created; do not re-read it during that cycle.
- Do not overwrite the parent Run's SPACE snapshot.
- Existing cycles without a cycle snapshot must fall back to the Run snapshot.
- Duplicate cycle creation must preserve the first snapshot.
- Run only the focused tests named in this plan; do not run the full suite.

---

## File Map

- `src/personal_agent_gateway/db.py`: fresh-database cycle schema.
- `src/personal_agent_gateway/migrations.py`: migration 18 for existing databases.
- `src/personal_agent_gateway/teams.py`: cycle snapshot model, capture, persistence, and row conversion.
- `src/personal_agent_gateway/app.py`: task-cycle-first execution policy selection.
- `src/personal_agent_gateway/api/team_runs.py`: diagnostic cycle payload.
- `src/personal_agent_gateway/execution_contract.py`: `all + isolated` compilation.
- `tests/test_migrations.py`: migration coverage.
- `tests/test_teams.py`: normal, duplicate, and retry cycle snapshot behavior.
- `tests/test_app_team_factory.py`: execution chooses the cycle snapshot.
- `tests/test_execution_contract.py`: isolated write plus unbounded read behavior.

### Task 1: Persist the current Team SPACE on every new cycle

**Files:**
- Modify: `src/personal_agent_gateway/db.py`
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Test: `tests/test_migrations.py`
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces: `TeamRunCycle.space_policy: dict | None`
- Produces: migration 18 column `team_run_cycles.space_policy_snapshot_json`
- Consumes: `SpacePolicyService.resolve(team_id=..., persona_id=...)`

- [ ] **Step 1: Write the migration failure test**

```python
from personal_agent_gateway.migrations import _migration_18_team_cycle_space_snapshot


def test_migration_18_adds_nullable_cycle_space_snapshot() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table team_run_cycles (id text primary key)")

    _migration_18_team_cycle_space_snapshot(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(team_run_cycles)")
    }
    assert "space_policy_snapshot_json" in columns
```

- [ ] **Step 2: Run the migration test and verify RED**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_18_adds_nullable_cycle_space_snapshot -q
```

Expected: collection fails because `_migration_18_team_cycle_space_snapshot` does not exist.

- [ ] **Step 3: Add the schema and migration**

Add `space_policy_snapshot_json text` to the `team_run_cycles` declaration in
`db.py`, then add and register:

```python
def _migration_18_team_cycle_space_snapshot(connection: sqlite3.Connection) -> None:
    if "space_policy_snapshot_json" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column space_policy_snapshot_json text"
        )
```

- [ ] **Step 4: Run the migration test and verify GREEN**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_18_adds_nullable_cycle_space_snapshot -q
```

Expected: PASS.

- [ ] **Step 5: Write failing cycle snapshot tests**

Add a test fixture with a real Team and Team SPACE service, then verify:

```python
def test_continuous_cycle_captures_latest_team_space_and_duplicate_keeps_it(tmp_path):
    db, teams, spaces, team, run = make_continuous_team_with_space(tmp_path)
    spaces.upsert(
        "team", team.id,
        read_mode="all", read_path=None,
        write_mode="isolated", workspace_path=None,
    )

    first = teams.create_cycle(run.id, "manual", "source-1")
    spaces.upsert(
        "team", team.id,
        read_mode="none", read_path=None,
        write_mode="isolated", workspace_path=None,
    )
    duplicate = teams.create_cycle(run.id, "manual", "source-1")
    second = teams.create_cycle(run.id, "manual", "source-2")

    assert first.space_policy["read_mode"] == "all"
    assert duplicate.space_policy == first.space_policy
    assert second.space_policy["read_mode"] == "none"
    assert teams.get_team_run(run.id).space_policy["read_mode"] == "none"
```

Add a retry assertion to the existing retry-cycle test:

```python
assert retry_cycle.space_policy["read_mode"] == "all"
```

- [ ] **Step 6: Run the cycle tests and verify RED**

Run:

```powershell
pytest tests/test_teams.py -q -k "cycle_captures_latest_team_space or retry_cycle"
```

Expected: FAIL because `TeamRunCycle` has no `space_policy` and cycle inserts do not store it.

- [ ] **Step 7: Implement minimal cycle snapshot persistence**

Add the model field:

```python
space_policy: dict | None = None
```

Add one helper that resolves Team policy when the Run belongs to a Team and
otherwise preserves the Run snapshot for legacy/direct service callers:

```python
def _space_policy_snapshot_for_cycle(self, run: TeamRun) -> str:
    if run.team_id:
        return policy_json(
            self._space_policies.resolve(team_id=run.team_id).policy
        )
    if run.space_policy:
        return json.dumps(run.space_policy, ensure_ascii=False, sort_keys=True)
    raise RuntimeError("Team run has no SPACE policy")
```

Call the helper for each new normal or retry cycle and store the returned JSON
in the cycle insert. Parse it in `_team_run_cycle_from_row()`:

```python
space_policy=(
    json.loads(row["space_policy_snapshot_json"])
    if "space_policy_snapshot_json" in row.keys()
    and row["space_policy_snapshot_json"]
    else None
),
```

Use the already-loaded Run row and leader agent/persona data inside the retry
transaction; do not update `team_runs.space_policy_snapshot_json`.

- [ ] **Step 8: Run focused Task 1 tests**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_18_adds_nullable_cycle_space_snapshot tests/test_teams.py -q -k "migration_18 or cycle_captures_latest_team_space or retry_cycle"
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_migrations.py tests/test_teams.py
git commit -m "fix(team): cycle별 SPACE snapshot 저장"
```

### Task 2: Execute and report the cycle SPACE snapshot

**Files:**
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `tests/test_app_team_factory.py`
- Modify: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `TeamRunCycle.space_policy`
- Produces: `_cycle_payload(cycle)["space_policy"]`

- [ ] **Step 1: Write the failing model-factory test**

Extend `_TeamRuns.get_cycle()` to return a `space_policy`, then add:

```python
def test_factory_uses_task_cycle_space_before_run_space(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    source = tmp_path / "shared"
    workspace.mkdir(parents=True)
    source.mkdir()
    (source / "evidence.txt").write_text("evidence", encoding="utf-8")
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "r1" / "artifacts"),
        space_policy=_space_policy(None, read_mode="none"),
        cycle_space_policy=_space_policy(str(source), read_mode="selected"),
    )

    client = _factory(_config(tmp_path), team_runs)(
        _agent("codex", workspace_path=str(workspace), current_task_id="task-1")
    )

    inputs = Path(client._execution["read_roots"][0])
    assert client._execution["workspace_root"] == str(workspace)
    assert inputs == workspace / "_inputs"
    assert (inputs / "01-shared" / "evidence.txt").is_file()
```

- [ ] **Step 2: Run the model-factory test and verify RED**

Run:

```powershell
pytest tests/test_app_team_factory.py::test_factory_uses_task_cycle_space_before_run_space -q
```

Expected: FAIL because the factory compiles the Run's `none` snapshot.

- [ ] **Step 3: Select the cycle policy before compilation**

In `_team_model_factory()`, resolve the task and cycle before
`contexts.for_session()`:

```python
space_snapshot = run.space_policy if run else None
task = None
cycle = None
if team_runs is not None and agent.current_task_id is not None:
    task = team_runs.get_task(agent.current_task_id)
    if task.cycle_id is not None:
        cycle = team_runs.get_cycle(task.cycle_id)
        if cycle.space_policy is not None:
            space_snapshot = cycle.space_policy
space_policy = policy_from_snapshot(space_snapshot)
```

Reuse the loaded `task` and `cycle` when recording execution metadata.

- [ ] **Step 4: Run the model-factory test and verify GREEN**

Run:

```powershell
pytest tests/test_app_team_factory.py::test_factory_uses_task_cycle_space_before_run_space -q
```

Expected: PASS.

- [ ] **Step 5: Write and satisfy the API payload test**

Add a cycle detail assertion:

```python
assert detail["cycles"][0]["space_policy"]["read_mode"] == "all"
```

Verify RED:

```powershell
pytest tests/test_api_team_runs.py -q -k "cycle_space_policy"
```

Then add to `_cycle_payload()`:

```python
"space_policy": cycle.space_policy,
```

Verify GREEN with the same command.

- [ ] **Step 6: Run focused Task 2 tests**

Run:

```powershell
pytest tests/test_app_team_factory.py tests/test_api_team_runs.py -q -k "task_cycle_space or cycle_space_policy or persists_compiled_cycle_execution_metadata"
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py tests/test_app_team_factory.py tests/test_api_team_runs.py
git commit -m "fix(team): cycle SPACE로 agent 실행"
```

### Task 3: Allow all-read with isolated writes

**Files:**
- Modify: `src/personal_agent_gateway/execution_contract.py`
- Modify: `tests/test_execution_contract.py`

**Interfaces:**
- Consumes: `SpacePolicy(read_mode="all", write_mode="isolated")`
- Produces: `CompiledExecution` with the isolated workspace and no staged input manifest.

- [ ] **Step 1: Replace the combined failure test with explicit behavior tests**

Keep `home + isolated` fail-closed and add:

```python
def test_all_read_with_isolated_write_uses_unstaged_workspace(tmp_path: Path) -> None:
    staging = FakeStaging(tmp_path / "run")

    compiled = compile_execution(
        _requirements(requires_sources=True),
        _policy("all", None),
        _capabilities(),
        staging,
    )

    assert compiled.workspace_root == (tmp_path / "run").resolve()
    assert compiled.read_roots == ()
    assert compiled.input_manifest_path is None
    assert staging.calls == []
```

- [ ] **Step 2: Run the isolated-all test and verify RED**

Run:

```powershell
pytest tests/test_execution_contract.py::test_all_read_with_isolated_write_uses_unstaged_workspace -q
```

Expected: FAIL with `source_scope_requires_selection`.

- [ ] **Step 3: Implement the isolated-all branch**

Before the existing `home`/bounded-selection validation, return:

```python
if policy.read_mode == "all":
    return CompiledExecution(
        workspace_root=workspace_root,
        read_roots=(),
        sandbox=sandbox,
        permission_mode=permission_mode,
        approval_policy="never" if sandbox else "",
        network=requirements.network,
        input_manifest_path=None,
        input_manifest_sha256=None,
    )
```

Leave `home + isolated` and mismatched `selected` policies unchanged.

- [ ] **Step 4: Run focused execution-contract tests**

Run:

```powershell
pytest tests/test_execution_contract.py -q -k "all_read_with_isolated_write or home or selected or no_source"
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/personal_agent_gateway/execution_contract.py tests/test_execution_contract.py
git commit -m "fix(space): isolated workspace의 전체 읽기 허용"
```

### Task 4: Focused regression verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: evidence that the changed migration, cycle, factory, API, and execution-contract paths pass together.

- [ ] **Step 1: Run the related test files**

```powershell
pytest tests/test_migrations.py tests/test_teams.py tests/test_app_team_factory.py tests/test_execution_contract.py tests/test_api_team_runs.py -q -k "migration_18 or cycle or space or factory or execution"
```

Expected: PASS with no warnings or errors.

- [ ] **Step 2: Check the surgical diff**

```powershell
git status --short
git diff --check
git diff --stat main...
```

Expected: only the files listed in this plan are changed; `git diff --check`
prints nothing.

- [ ] **Step 3: Commit any test-only correction**

Only if Task 4 required a correction directly related to this behavior, stage
the exact planned files that changed:

```powershell
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/execution_contract.py tests/test_migrations.py tests/test_teams.py tests/test_app_team_factory.py tests/test_api_team_runs.py tests/test_execution_contract.py
git commit -m "test(team): cycle SPACE 회귀 검증 보강"
```
