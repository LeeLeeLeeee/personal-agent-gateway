# Team Task Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute Team tasks only after declared prerequisites complete and block dependents when a prerequisite is unsuccessful.

**Architecture:** Planner JSON gains `plan_task_id` and `depends_on_task_ids`; plan application maps those local keys to task IDs and persists dependency edges atomically. The runtime queries only ready tasks and propagates prerequisite failure to pending dependents before calculating the cycle result.

**Tech Stack:** Python 3.13, SQLite migrations, pytest.

## Global Constraints

- Do not infer dependencies from task prose or file paths.
- Dependencies must be in the same Team Run and cycle.
- Legacy plans that omit dependency fields remain valid with no edges.
- Only `completed` prerequisites make a task runnable.
- `failed`, `blocked`, or `canceled` prerequisites produce `blocked_by_dependency` for every pending dependent.

---

### Task 1: Persist dependency edges and readiness queries

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Test: `tests/test_db_agent_teams_schema.py`
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces `TeamTaskDependency(task_id: str, depends_on_task_id: str)`.
- Produces `add_task_dependencies(task_id, prerequisite_ids)`.
- Produces `list_dependency_ready_tasks(team_run_id, cycle_id)`.
- Produces `block_pending_dependency_failures(team_run_id, cycle_id)`.

- [ ] **Step 1: Write failing service tests**

```python
def test_dependency_ready_tasks_wait_for_prerequisite(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    research = teams.create_task(run.id, "Research", "Research", cycle_id=cycle.id)
    draft = teams.create_task(run.id, "Draft", "Draft", cycle_id=cycle.id)
    teams.add_task_dependencies(draft.id, [research.id])
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [research]
    teams.set_task_status(research.id, "completed")
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [draft]

def test_failed_prerequisite_blocks_transitive_dependents(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    research, draft, qa = create_dependency_chain(teams, run, cycle)
    teams.set_task_status(research.id, "failed", error_message="source failed")
    assert [item.id for item in teams.block_pending_dependency_failures(run.id, cycle.id)] == [draft.id, qa.id]
    assert teams.get_task(draft.id).error_message == "blocked_by_dependency"
```

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_teams.py -k "dependency_ready or dependency_failures" tests\test_db_agent_teams_schema.py -q`

Expected: FAIL because dependency storage and service methods do not exist.

- [ ] **Step 3: Add minimum storage and query implementation**

```sql
create table team_task_dependencies (
    task_id text not null references team_tasks(id) on delete cascade,
    depends_on_task_id text not null references team_tasks(id) on delete cascade,
    primary key (task_id, depends_on_task_id),
    check (task_id <> depends_on_task_id)
);
create index idx_team_task_dependencies_prerequisite
on team_task_dependencies(depends_on_task_id);
```

Validate each edge in one transaction. Readiness returns pending tasks whose prerequisites are all completed, ordered by creation time and ID. Failure propagation loops until no pending task with an unsuccessful prerequisite remains, covering transitive chains.

- [ ] **Step 4: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_teams.py -k "dependency_ready or dependency_failures" tests\test_db_agent_teams_schema.py -q`

Expected: PASS.

```powershell
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_teams.py tests/test_db_agent_teams_schema.py; git commit -m "feat: persist team task dependencies"
```

### Task 2: Bind planner declarations to persisted task edges

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `src/personal_agent_gateway/team_model_operations.py`
- Modify: `src/personal_agent_gateway/team_model_effects.py`
- Test: `tests/test_team_runtime.py`
- Test: `tests/test_team_model_effects.py`

**Interfaces:**
- New planner tasks use `plan_task_id: str` and `depends_on_task_ids: list[str]`.
- `apply_plan(operation_id)` persists tasks and edges in one transaction.

- [ ] **Step 1: Write failing parser and effect tests**

```python
def test_task_plan_rejects_dependency_cycle() -> None:
    payload = [valid_task("research", ["draft"]), valid_task("draft", ["research"])]
    with pytest.raises(ValueError, match="dependency cycle"):
        _parse_task_plan(json.dumps(payload))

def test_apply_plan_persists_dependency_edges(tmp_path) -> None:
    services = make_completed_operation(tmp_path, stage="cycle_planning", result=plan_with_research_then_draft())
    research, draft = services.effects.apply_plan(services.operation.id)
    assert [edge.depends_on_task_id for edge in services.teams.list_task_dependencies(draft.id)] == [research.id]
```

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_model_effects.py -k "dependency" -q`

Expected: FAIL because the plan schema and effect mapping do not exist.

- [ ] **Step 3: Implement validation and atomic mapping**

Update `PLANNING_PROMPT` and the operation-ledger shape validator. `_parse_task_plan` accepts legacy missing fields as `plan_task_id=None` and `depends_on_task_ids=[]`, and rejects duplicate keys, self-references, missing keys, and graph cycles. `apply_plan` creates all tasks, maps local keys to task IDs, inserts edges before the plan note, and compares those edges during replay.

- [ ] **Step 4: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_model_effects.py -k "dependency or plan" -q`

Expected: PASS including legacy plan fixtures.

```powershell
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_model_operations.py src/personal_agent_gateway/team_model_effects.py tests/test_team_runtime.py tests/test_team_model_effects.py; git commit -m "feat: bind task plan dependencies"
```

### Task 3: Schedule only ready tasks and block dependents before cycle failure

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Test: `tests/test_team_runtime.py`
- Test: `tests/test_team_cycle_dispatcher.py`

**Interfaces:**
- `_execute` consumes `list_dependency_ready_tasks`.
- `_execute_and_synthesize` calls `block_pending_dependency_failures` before `_terminal_status`.

- [ ] **Step 1: Write failing runtime tests**

```python
async def test_runtime_runs_prerequisite_before_dependent(tmp_path) -> None:
    setup = make_operation_runtime(tmp_path)
    research, draft = create_dependent_tasks(setup)
    completed = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    assert completed.status == "completed"
    assert worker_task_order(setup) == [research.id, draft.id]

async def test_runtime_blocks_dependents_before_failing_cycle(tmp_path) -> None:
    setup = make_operation_runtime(tmp_path)
    research, draft, qa = create_dependency_chain(setup)
    setup.worker_client.responses = [failed_outcome("source failed")]
    failed = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    assert failed.status == "failed"
    assert [setup.teams.get_task(item.id).status for item in (draft, qa)] == ["blocked", "blocked"]
```

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_cycle_dispatcher.py -k "prerequisite_before or blocks_dependents" -q`

Expected: FAIL because `_execute` uses the first pending task and terminal status happens before dependency propagation.

- [ ] **Step 3: Implement dependency-aware scheduling**

Select the first ready task rather than `pending[0]`. When no task is ready, return without invoking a provider. After each execution pass, block pending dependency failures before computing terminal status so a failed prerequisite produces explicit blocked descendants.

- [ ] **Step 4: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_cycle_dispatcher.py -k "prerequisite_before or blocks_dependents" -q`

Expected: PASS.

```powershell
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py; git commit -m "feat: schedule dependency-ready team tasks"
```

### Task 4: Guard the `94a9b74d` research → draft → QA regression

**Files:**
- Modify: `tests/test_teams.py`
- Modify: `tests/test_team_runtime.py`

- [ ] **Step 1: Write the three-stage readiness regression**

```python
def test_research_then_draft_then_qa_is_the_only_ready_order(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    research, draft, qa = create_research_draft_qa_chain(teams, run, cycle)
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [research]
    teams.set_task_status(research.id, "completed")
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [draft]
    teams.set_task_status(draft.id, "completed")
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [qa]
```

- [ ] **Step 2: Verify focused coverage and quality**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_teams.py tests\test_team_runtime.py tests\test_team_model_effects.py tests\test_team_cycle_dispatcher.py -q`

Expected: PASS.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 3: Commit regression coverage**

```powershell
git add tests/test_teams.py tests/test_team_runtime.py tests/test_team_model_effects.py tests/test_team_cycle_dispatcher.py; git commit -m "test: cover team task dependency ordering"
```
