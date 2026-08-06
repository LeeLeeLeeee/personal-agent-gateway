# Deterministic Team Run Task Ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Team run cycle execute tasks in the order its leader planned, stop a worker's specific "I am blocked" report from hard-failing the run, reset persona lanes at each new cycle, and show elapsed time for the task a running agent is on.

**Architecture:** Task order becomes explicit data (`team_tasks.plan_ordinal`) instead of an accident of `uuid4()` sort. The planner is then required to name its tasks so it can declare real dependency edges, which activates the already-built `team_task_dependencies` machinery. Acceptance handling learns to distinguish a worker-declared outcome (route to leader review) from a server-detected failure (end the task). Two independent UI/lifecycle fixes ride along.

**Tech Stack:** Python 3 + SQLite (`sqlite3`, no ORM), pytest; React + Vitest + Testing Library for the frontend.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-08-06-team-run-task-ordering-design.md`.
- Work happens in the git worktree `C:\Users\Administrator\playground\personal-agent-gateway\.claude\worktrees\team-run-task-ordering` on branch `worktree-team-run-task-ordering`. It has its own `.venv` (editable install of this worktree's `src/`) and its own `frontend/node_modules`, both already provisioned. Never run the parent checkout's `.venv` — it resolves `personal_agent_gateway` to the parent's `src/` and would test the wrong code.
- Backend tests run from the worktree root: `.\.venv\Scripts\python.exe -m pytest <path> -q`.
- Frontend tests run from `frontend/`: `npm test -- <path>`.
- Measured baseline in this worktree (commit `28af25f`, worktree venv, pytest 9.1.1):
  - Full backend suite: **43 failed, 1327 passed, 2 skipped**. All 43 pre-existing failures live in files this plan does not touch (`test_runtime_factory_headless.py`, `test_schedules.py`, `test_team_cycle_recovery.py`, and others).
  - The five files this plan does touch — `tests/test_teams.py`, `tests/test_team_model_effects.py`, `tests/test_team_runtime.py`, `tests/test_team_acceptance.py`, `tests/test_db_agent_teams_schema.py` — are **fully green (341 passed, 1 skipped)**. They must stay green.
  - Frontend: **40 files, 355 tests, all passing**. Must stay green.
  - Completion is judged by **delta**: no test that passed before may fail after.
  - ⚠️ ERRATUM E10: this five-file scope is too narrow and let six regressions through. Gate on the full suite, and record the baseline's **per-file** distribution, not just a total.
- Migrations are append-only. The new migration is number 28; never renumber or edit an existing migration.
- SQLite `cycle_id` is nullable. Always compare it with `is ?`, never `= ?`.
- Do not touch `src/personal_agent_gateway/frontend_dist/**`. It is a build artifact and already has uncommitted changes that are not part of this work.
- Task execution order is **1 → 2 → 3 → 4 → 5**. Each task commits independently.

---

## EXECUTED — read the errata before trusting any step below

This plan was executed on 2026-08-06 and merged to `main` at `b86eab7` (10 commits,
`5bb054d..b86eab7`). **The shipped code is the source of truth, not this document.**

Execution found ten defects in the plan itself, plus a design error inherited from the spec (E8).
Each was corrected in code and reviewed; the steps below still carry the original wording so the
record of what was wrong survives. Every affected step is marked `⚠️ ERRATUM Ex` inline.

Final state: backend 43 failed / 1342 passed / 2 skipped against a 43 / 1327 / 2 baseline —
zero regressions, and the 15 extra passes are exactly this plan's new tests. Frontend 359
passing against 355.

### Errata

**E1 — Task 1 Step 3: the schema test asserts against nothing.**
The snippet calls `database.connection()` without `database.initialize()` first. `connection()`
opens a raw connection and never runs `SCHEMA_SQL` or the migrations, so `pragma table_info`
returns an empty set and the assertion can never pass. Add `database.initialize()`, matching the
style of the other tests in that file.

**E2 — Task 1 Step 9: the ordering clause is wrong and creates a new inversion.**
The plan prescribes `plan_ordinal asc, created_at asc, id asc`. Shipped code uses
**`created_at asc, plan_ordinal asc, id asc`** in `list_tasks`, `list_dependency_ready_tasks`,
and `block_pending_dependency_failures`.

Why the plan's order is wrong: `plan_ordinal` restarts at 0 for every plan and every cycle.
Sorting on it first means (a) an `add_work` plan applied to a cycle that already holds tasks
preempts the pending planned tasks — the same inversion class this plan exists to fix — and
(b) run-wide `list_tasks` interleaves cycles, so the API's `tasks[-limit:]` truncation hides the
*first* task of every plan instead of the oldest tasks. Putting `created_at` first restores
chronology across plans while preserving the fix within a plan, because `apply_plan` stamps one
shared `created_at` on a whole plan so `plan_ordinal` still decides there.

**E3 — Task 1 Step 8: `enumerate(specs)` numbers ordinals per operation, not per cycle.**
That disagrees with `create_task`, which numbers from `max+1` per `(team_run_id, cycle_id)`.
Shipped `apply_plan` computes an `ordinal_base` the same way `create_task` does and offsets from
it. E2's read-side fix alone was judged insufficient because `_now()` is only microsecond-
resolution, so two plans in one cycle could tie on `created_at`.

Consequence for readers: `plan_ordinal` means **sequence within the cycle**, not index within
the plan. Nothing reads it positionally today.

**E4 — Task 2 Step 1: the test does not prove what it claims.**
`set_agent_status` already nulls `current_task_id` and sets `finished_at` on any terminal
transition, so the post-reset `current_task_id is None` assertion passes even if the SQL clause
were removed, and `finished_at` is never asserted at all. The shipped test gives the agent a
genuinely non-null `current_task_id` first and asserts `finished_at` non-null before and null
after, in both phases.

**E5 — Task 2 Step 3: the reset status list is incomplete.**
The plan resets `('completed','failed','canceled')`. Shipped code resets
`('completed','failed','canceled','waiting','blocked')`. Task 5 made `waiting` a terminal parking
state for an agent whose task ended `blocked`, so excluding it left persona lanes pulsing
"WAITING" forever — the exact stale-lane symptom this task exists to remove. `'blocked'` covers
legacy rows that a pre-existing `finish_task` bug persisted as an illegal `AgentStatus`.

**E6 — Task 3 Step 6: the prescribed hook placement violates the Rules of Hooks.**
The plan says to add the `useState`/`useEffect` after `const agents = detail.agents || [];`,
which sits *after* three early returns (`loading`, `loadError`, `!run`). `GatewayApp` mounts
`TeamRunDetail` without a `key` and flips `loading` to false on the same instance, so React would
see two extra hooks appear between renders and throw "Rendered more hooks than during the
previous render". Shipped code puts both hooks above the early returns, beside the existing
`countdownNow` effect, using the same `detail?.` optional-chaining style.

Step 6 also under-specifies the render: two sibling `<span>`s separated only by JSX whitespace
produce no space, so the lane rendered `잔여 P3 7건 수정03:12 경과`. Shipped code inserts an
explicit `{" "}` and adds a `.team-lane-elapsed` rule to
`src/personal_agent_gateway/static/styles.css`. The plan's test could not catch this because
`getByText` matches per text node; the shipped test asserts on the container's combined
`textContent`.

**E7 — Task 4 Step 4: the instruction contradicts this plan's own Step 1 test.**
Adding `"plan_task_id"` to `required_fields` makes an absent key raise the generic
"missing or unknown fields" message, which fails Step 1's
`pytest.raises(ValueError, match="plan_task_id")`. Shipped code leaves the key in the
allowed-extras set and enforces it with the standalone strict check alone. Review enumerated all
five input cases and confirmed the two versions accept and reject an identical input set — only
the error message for the absent-key case differs, and the shipped one is more useful.

**E8 — Task 5: the prescribed worker-declared signal is wrong. This came from the spec, not just the plan.**
Both the spec and Step 5/7 use `worker_declared = outcome.status != "completed"`. That is false
for a case neither document considered: `TeamRuntime._task_outcome` **synthesizes**
`status="blocked", reason_code="invalid_task_outcome"` when a worker's response fails to parse.
That is a server-detected failure the worker never declared, and recording it verbatim turns a
dead run into one that looks like it is waiting for something that will never come. It surfaced
as a baseline regression in `test_worker_prose_cannot_complete_team_run`.

Shipped code introduces `is_worker_declared_outcome()` and `terminal_rejected_status()` in
`team_acceptance.py` and routes all four call sites — ledger apply, ledger replay, legacy gate,
legacy terminal — through them, so the rule cannot drift between paths. Review verified by
enumerating every `TaskOutcome` producer in `src/` that `invalid_task_outcome` is the only
synthesized one.

**E9 — Task 5 Step 2: the test raises before it asserts, and Step 7 fixes only half the legacy path.**
`workspace_changes={}` raises `ValueError` inside `_workspace_changes` before any assertion runs;
use the three-key empty form every other call in that file uses. And Step 7's change at
`team_runtime.py:1427` only affects the terminal status computed *afterwards*. The gate that
actually decides whether the legacy flow reaches leader review is
`team_runtime.py:2231` inside `_recover_task_outcome`, which the plan never names. Without it the
legacy path still hard-returned for worker-declared novel reason codes, leaving the two paths
divergent on exactly the behavior this task targets. Shipped code fixes both, and also aligns the
at-cap terminal state so ledger and legacy agree on task status *and* agent status.

**E10 — Global Constraints: the five-file test scope is too narrow.**
Gating each task on `test_teams`, `test_team_model_effects`, `test_team_runtime`,
`test_team_acceptance`, and `test_db_agent_teams_schema` let six regressions through: fake
task-plan JSON also lives in `test_api_team_runs.py` (4) and `test_team_results.py` (1), and
`test_migrations.py` (1) pins the schema version. A plan that tightens a parser must gate on the
full suite, and its baseline must record the **per-file** failure distribution — a total alone
cannot distinguish "we broke six" from "we broke six and six others got fixed". The cheap way to
isolate a regression is `git worktree add /tmp/x <base>` and re-run only the currently-failing
files there.

**E11 — Final verification: the live-database step is wrong and unnecessary.** See that step for
detail. `Database()` takes a `Path` and needs `.initialize()`; migrations apply automatically on
backend restart via `app.py:480`; and verification was done against a copy rather than production.

**Not defects, recorded for completeness:** three Low findings were parked rather than fixed —
a `team-lane-task-title` class with no CSS rule; no test pinning E3's write-side change (E2's
read-side fix independently prevents the failure); and a comment at `teams.py:1727` calling
`waiting` terminal when it is actually overloaded with the transient provider-wait state.

---

### Task 1: Persist plan order and schedule by it

The root cause. `apply_plan` stamps every task in a plan with one shared `created_at`, so `order by created_at asc, id asc` degenerates to sorting on a random `uuid4().hex`. This task records the plan array index and sorts by it.

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` (add `_migration_28_team_task_plan_ordinal`, register in `MIGRATIONS`)
- Modify: `src/personal_agent_gateway/teams.py:198` (`TeamTask` dataclass), `:1733` (`create_task`), `:1792` (`list_tasks`), `:1867` (`list_dependency_ready_tasks`), `:3341` (`_team_task_from_row`)
- Modify: `src/personal_agent_gateway/team_model_effects.py:119` (`apply_plan` task loop), `:2045` (`_create_task`)
- Test: `tests/test_teams.py`
- Test: `tests/test_team_model_effects.py`
- Test: `tests/test_db_agent_teams_schema.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TeamTask.plan_ordinal: int` (defaults to `0`)
  - `TeamModelEffectService._create_task(connection, operation, spec, now, ordinal: int) -> TeamTask`
  - `TeamRunService.create_task(...)` unchanged in signature; assigns `plan_ordinal` automatically
  - `list_tasks` and `list_dependency_ready_tasks` return rows in `plan_ordinal` order

- [ ] **Step 1: Write the failing scheduling test**

Add to `tests/test_team_model_effects.py`. This must loop — a single-shot assertion passes about 60% of the time against the unfixed code and would not catch a regression.

```python
def test_dependency_ready_tasks_follow_plan_order_not_uuid(tmp_path):
    for index in range(40):
        fix = valid_task_spec("Fix", None)
        fix.update({"plan_task_id": "fix", "depends_on_task_ids": []})
        qa = valid_task_spec("Qa", None)
        qa.update({"plan_task_id": "qa", "depends_on_task_ids": []})
        services = make_completed_operation(
            tmp_path / f"trial{index}",
            stage="cycle_planning",
            result=ValidatedOperationResult("task_plan", {"tasks": [fix, qa]}),
        )

        created = services.effects.apply_plan(services.operation.id)
        ready = services.teams.list_dependency_ready_tasks(
            services.run.id, services.cycle.id
        )

        assert [task.title for task in created] == ["Fix", "Qa"]
        assert [task.plan_ordinal for task in created] == [0, 1]
        assert ready[0].title == "Fix", f"trial {index} scheduled Qa first"
```

- [ ] **Step 2: Write the failing service-level ordering test**

Add to `tests/test_teams.py`. `create_task` is the non-ledger path and must assign ordinals too.

```python
def test_create_task_assigns_increasing_plan_ordinals(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)

    first = teams.create_task(run.id, "First", "First", cycle_id=cycle.id)
    second = teams.create_task(run.id, "Second", "Second", cycle_id=cycle.id)
    other_cycle_task = teams.create_task(run.id, "Loose", "Loose")

    assert first.plan_ordinal == 0
    assert second.plan_ordinal == 1
    assert other_cycle_task.plan_ordinal == 0
    assert [task.title for task in teams.list_tasks(run.id, cycle.id)] == [
        "First",
        "Second",
    ]
```

- [ ] **Step 3: Write the failing schema test** — ⚠️ ERRATUM E1: the snippet below is missing `database.initialize()` and asserts against an always-empty set.

Add to `tests/test_db_agent_teams_schema.py`, matching that file's existing style for column assertions.

```python
def test_team_tasks_has_plan_ordinal_column(tmp_path) -> None:
    database = Database(tmp_path / "app.sqlite")
    with database.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("pragma table_info(team_tasks)")
        }
    assert "plan_ordinal" in columns
```

- [ ] **Step 4: Run the tests to verify they fail**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_model_effects.py::test_dependency_ready_tasks_follow_plan_order_not_uuid tests\test_teams.py::test_create_task_assigns_increasing_plan_ordinals tests\test_db_agent_teams_schema.py::test_team_tasks_has_plan_ordinal_column -q
```

Expected: all three FAIL. The schema and `create_task` tests fail on the missing `plan_ordinal` column/attribute; the scheduling test fails intermittently on `ready[0].title` and definitely on `plan_ordinal`.

- [ ] **Step 5: Add the migration**

Append to `src/personal_agent_gateway/migrations.py`, immediately before the `MIGRATIONS` tuple. The backfill uses `rowid` rank because `apply_plan` inserts in plan-array order, so `rowid` already encodes the intended order for existing rows.

```python
def _migration_28_team_task_plan_ordinal(
    connection: sqlite3.Connection,
) -> None:
    if "plan_ordinal" not in _columns(connection, "team_tasks"):
        connection.execute(
            "alter table team_tasks add column plan_ordinal integer not null default 0"
        )
    connection.execute(
        """
        update team_tasks
        set plan_ordinal = (
            select count(*)
            from team_tasks earlier
            where earlier.team_run_id = team_tasks.team_run_id
              and earlier.cycle_id is team_tasks.cycle_id
              and earlier.rowid < team_tasks.rowid
        )
        """
    )
```

Register it in the `MIGRATIONS` tuple, after entry 27:

```python
    (28, "team-task-plan-ordinal", _migration_28_team_task_plan_ordinal),
```

- [ ] **Step 6: Add the dataclass field and row mapping**

In `src/personal_agent_gateway/teams.py`, add to `TeamTask` (after `acceptance_recovery_attempts: int = 0` at line 217):

```python
    plan_ordinal: int = 0
```

In `_team_task_from_row` (line 3341), add before the closing paren, following the existing `.keys()` guard pattern used by neighbouring optional columns:

```python
        plan_ordinal=(
            int(row["plan_ordinal"])
            if "plan_ordinal" in row.keys() and row["plan_ordinal"] is not None
            else 0
        ),
```

- [ ] **Step 7: Assign the ordinal in `create_task`**

In `src/personal_agent_gateway/teams.py:1733` `create_task`, add `plan_ordinal` to the insert. The value is computed by a self-referencing subquery so the read and write stay in one atomic statement (verified working on SQLite).

Replace the insert statement and its parameter tuple with:

```python
        self._db.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id, status,
                required, acceptance_json, outcome_json, acceptance_result_json,
                result, error_message, created_at, updated_at, started_at, finished_at,
                plan_ordinal
            )
            values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null, ?, ?, ?, ?, ?, ?,
                (
                    select coalesce(max(existing.plan_ordinal), -1) + 1
                    from team_tasks existing
                    where existing.team_run_id = ? and existing.cycle_id is ?
                )
            )
            """,
            (
                task_id,
                team_run_id,
                cycle_id,
                title,
                description,
                owner_agent_id,
                "pending",
                int(required),
                _task_acceptance_json(effective_acceptance),
                None,
                None,
                now,
                now,
                None,
                None,
                team_run_id,
                cycle_id,
            ),
        )
```

- [ ] **Step 8: Assign the ordinal in the ledger path** — ⚠️ ERRATUM E3: `enumerate(specs)` numbers per operation, not per cycle. Shipped code offsets from an `ordinal_base` computed like `create_task`'s.

In `src/personal_agent_gateway/team_model_effects.py`, change the `apply_plan` task loop (line 119):

```python
            specs = _plan_specs(operation)
            tasks = [
                self._create_task(connection, operation, spec, now, ordinal)
                for ordinal, spec in enumerate(specs)
            ]
```

Change the `_create_task` signature (line 2045):

```python
    def _create_task(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        spec: dict[str, object],
        now: str,
        ordinal: int,
    ) -> TeamTask:
```

Change its insert (line 2092) to carry the ordinal explicitly:

```python
        connection.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id,
                status, required, acceptance_json, outcome_json,
                acceptance_result_json, result, error_message, created_at,
                updated_at, started_at, finished_at, plan_ordinal
            ) values (?, ?, ?, ?, ?, ?, 'pending', ?, ?, null, null, null, null,
                      ?, ?, null, null, ?)
            """,
            (
                task_id,
                operation.team_run_id,
                operation.cycle_id,
                spec["title"],
                spec["description"],
                owner_agent_id,
                int(spec["required"]),
                _task_acceptance_json(acceptance),
                now,
                now,
                ordinal,
            ),
        )
```

- [ ] **Step 9: Sort by the ordinal** — ⚠️ ERRATUM E2: the clause below is WRONG. Shipped code uses `created_at asc, plan_ordinal asc, id asc`, and applies it to `block_pending_dependency_failures` too.

In `src/personal_agent_gateway/teams.py`, change both query orderings.

`list_tasks` (line 1792):

```python
                f"select * from team_tasks where {where} "
                "order by plan_ordinal asc, created_at asc, id asc",
```

`list_dependency_ready_tasks` (line 1867):

```python
            order by task.plan_ordinal asc, task.created_at asc, task.id asc
```

Leave `block_pending_dependency_failures` (line 1894) alone — it selects a set to block, not an execution order.

- [ ] **Step 10: Run the new tests to verify they pass**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_model_effects.py::test_dependency_ready_tasks_follow_plan_order_not_uuid tests\test_teams.py::test_create_task_assigns_increasing_plan_ordinals tests\test_db_agent_teams_schema.py::test_team_tasks_has_plan_ordinal_column -q
```

Expected: 3 passed.

- [ ] **Step 11: Run the surrounding suites and compare to baseline**

Before trusting the result, record the baseline on a clean checkout of these three files, then compare:

```
.\.venv\Scripts\python.exe -m pytest tests\test_teams.py tests\test_team_model_effects.py tests\test_team_runtime.py tests\test_db_agent_teams_schema.py -q
```

Expected: no test that passed before this task now fails. Pre-existing failures may remain.

- [ ] **Step 12: Commit**

```bash
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_model_effects.py tests/test_teams.py tests/test_team_model_effects.py tests/test_db_agent_teams_schema.py
git commit -m "fix: 팀 실행 태스크를 계획 순서대로 스케줄링

태스크 실행 순서가 uuid4 정렬로 결정되던 문제를 plan_ordinal 컬럼으로 해결"
```

---

### Task 2: Reset persona status when a new cycle starts

Agents carry the previous cycle's `completed`/`failed` badge into the next cycle because `_activate_cycle` resets only `reinvocations`.

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (add `reset_agents_for_new_cycle` next to `reset_agent_reinvocations` at line 1717)
- Modify: `src/personal_agent_gateway/team_runtime.py:3087` (`_activate_cycle`)
- Test: `tests/test_teams.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `TeamRunService.reset_agents_for_new_cycle(team_run_id: str) -> None`

- [ ] **Step 1: Write the failing test**

⚠️ ERRATUM E4 — this test does not prove the `current_task_id`/`finished_at` clauses; see the errata for the shipped version.
⚠️ ERRATUM E5 — the `waiting` half of the claim below is wrong. Task 5 makes `waiting` a terminal parking state, so the shipped reset DOES include it (and `blocked`). Only `running` must survive.

Add to `tests/test_teams.py`. The assertion that `running` and `waiting` agents are untouched is the important half — operation replay guards (`team_model_effects.py:189, 253, 1147, 1902`) assert exact agent states and would break if those were reset.

`make_cycle_services` creates exactly two agents (one leader, one worker), so the test covers the four statuses in two phases.

```python
def test_reset_agents_for_new_cycle_only_clears_terminal_agents(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    leader = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )

    teams.set_agent_status(leader.id, "completed")
    teams.set_agent_status(worker.id, "running")
    teams.reset_agents_for_new_cycle(run.id)
    by_id = {agent.id: agent for agent in teams.list_agents(run.id)}
    assert by_id[leader.id].status == "pending"
    assert by_id[leader.id].current_task_id is None
    assert by_id[worker.id].status == "running"

    teams.set_agent_status(leader.id, "failed")
    teams.set_agent_status(worker.id, "waiting")
    teams.reset_agents_for_new_cycle(run.id)
    by_id = {agent.id: agent for agent in teams.list_agents(run.id)}
    assert by_id[leader.id].status == "pending"
    assert by_id[worker.id].status == "waiting"
```

- [ ] **Step 2: Run the test to verify it fails**

```
.\.venv\Scripts\python.exe -m pytest tests\test_teams.py::test_reset_agents_for_new_cycle_only_clears_terminal_agents -q
```

Expected: FAIL with `AttributeError: 'TeamRunService' object has no attribute 'reset_agents_for_new_cycle'`.

- [ ] **Step 3: Add the service method** — ⚠️ ERRATUM E5: the shipped status list is `('completed','failed','canceled','waiting','blocked')`, not the three below.

In `src/personal_agent_gateway/teams.py`, directly after `reset_agent_reinvocations` (which ends at line 1723):

```python
    def reset_agents_for_new_cycle(self, team_run_id: str) -> None:
        self.get_team_run(team_run_id)
        self._db.execute(
            """
            update team_agents
            set status = 'pending', current_task_id = null,
                finished_at = null, updated_at = ?
            where team_run_id = ?
              and status in ('completed', 'failed', 'canceled')
            """,
            (_now(), team_run_id),
        )
```

- [ ] **Step 4: Call it at cycle activation**

In `src/personal_agent_gateway/team_runtime.py:3087`, add the call inside the existing `queued` branch. This is the safe moment: no operation for the new cycle has been reserved yet.

```python
    def _activate_cycle(self, cycle_id: str) -> None:
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status == "queued":
            self._teams.reset_agent_reinvocations(cycle.team_run_id)
            self._teams.reset_agents_for_new_cycle(cycle.team_run_id)
        self._teams.set_cycle_status(cycle_id, "running")
```

- [ ] **Step 5: Run the test to verify it passes**

```
.\.venv\Scripts\python.exe -m pytest tests\test_teams.py::test_reset_agents_for_new_cycle_only_clears_terminal_agents -q
```

Expected: 1 passed.

- [ ] **Step 6: Run the surrounding suites and compare to baseline**

```
.\.venv\Scripts\python.exe -m pytest tests\test_teams.py tests\test_team_runtime.py tests\test_team_cycles.py -q
```

Expected: no newly failing test.

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_runtime.py tests/test_teams.py
git commit -m "feat: 새 사이클 시작 시 종료 상태 페르소나를 pending으로 초기화"
```

---

### Task 3: Show elapsed time for the task a running agent is on

Frontend only. `task.started_at` is already in the API payload (`api/team_runs.py:1295`).

Note a deliberate deviation from the design mock: the mock showed `3분 12초 경과`, but `frontend/src/lib/time.js` already exports `fmtElapsed(seconds)` producing `MM:SS`, and `Timeline/index.jsx:83` already uses it for exactly this purpose (a live turn timer). Reusing it keeps one elapsed-time format in the UI, so the lane renders `03:12 경과`.

**Files:**
- Modify: `frontend/src/lib/time.js` (add `elapsedSeconds`)
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:101` (`currentWork`), agent lane render at `:1259`
- Test: `frontend/src/lib/time.test.js`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces:
  - `elapsedSeconds(startedAt: string | null | undefined, nowMs: number) -> number | null` exported from `frontend/src/lib/time.js`
  - `currentWork(agent, task, runStatus) -> { title: string, startedAt: string | null }` (module-local, exported for tests)

- [ ] **Step 1: Write the failing helper test**

Add to `frontend/src/lib/time.test.js`, importing `elapsedSeconds` alongside whatever that file already imports.

```js
describe("elapsedSeconds", () => {
  it("returns whole seconds between the start and now", () => {
    const start = "2026-08-06T04:07:12.000Z";
    const now = Date.parse("2026-08-06T04:10:24.000Z");
    expect(elapsedSeconds(start, now)).toBe(192);
  });

  it("returns null for a missing or unparseable start", () => {
    expect(elapsedSeconds(null, Date.now())).toBeNull();
    expect(elapsedSeconds("not-a-date", Date.now())).toBeNull();
  });

  it("clamps a start in the future to zero", () => {
    const start = "2026-08-06T04:10:00.000Z";
    const now = Date.parse("2026-08-06T04:07:00.000Z");
    expect(elapsedSeconds(start, now)).toBe(0);
  });
});
```

- [ ] **Step 2: Write the failing component test**

Add to `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`. Fake timers keep the one-second tick deterministic.

```js
  it("shows the running agent's task title with elapsed time that ticks", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-06T04:10:24.000Z"));
    try {
      render(
        <TeamRunDetail
          detail={{
            run: { id: "r1", goal: "D3 규약", status: "running", run_mode: "plan_and_execute" },
            agents: [
              {
                id: "a1",
                name: "Tech Lead",
                role: "member",
                status: "running",
                current_task_id: "t1"
              }
            ],
            tasks: [
              {
                id: "t1",
                title: "잔여 P3 7건 수정",
                description: "fix",
                status: "in_progress",
                started_at: "2026-08-06T04:07:12.000Z"
              }
            ],
            messages: []
          }}
        />
      );

      expect(screen.getByText(/잔여 P3 7건 수정/)).toBeInTheDocument();
      expect(screen.getByText(/03:12 경과/)).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(1000);
      });

      expect(screen.getByText(/03:13 경과/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
```

- [ ] **Step 3: Run both tests to verify they fail**

```
cd frontend
npm test -- src/lib/time.test.js src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: FAIL. `elapsedSeconds` is not exported, and the lane renders only the title with no elapsed text.

- [ ] **Step 4: Add the pure helper**

Append to `frontend/src/lib/time.js`:

```js
export function elapsedSeconds(startedAt, nowMs) {
  if (!startedAt) return null;
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return null;
  return Math.max(0, Math.floor((nowMs - started) / 1000));
}
```

- [ ] **Step 5: Return the start time from `currentWork`**

In `frontend/src/components/organisms/TeamRunDetail/index.jsx`, replace `currentWork` at line 101. The leader fallbacks are preserved exactly and carry no start time.

```jsx
export function currentWork(agent, task, runStatus) {
  if (task) return { title: task.title, startedAt: task.started_at || null };
  if (agent.role !== "leader") return { title: "No active task", startedAt: null };
  if (runStatus === "planning") return { title: "Planning tasks", startedAt: null };
  if (runStatus === "running") return { title: "Coordinating agents", startedAt: null };
  if (runStatus === "summarizing") return { title: "Summarizing results", startedAt: null };
  return { title: "No active task", startedAt: null };
}
```

- [ ] **Step 6: Add the tick and render the elapsed time** — ⚠️ ERRATUM E6: the prescribed hook placement violates the Rules of Hooks and crashes, and the render below produces run-together text with no CSS rule. See the errata.

Add the import at the top of the same file, merging into the existing `frontend/src/lib/time.js` import if one is already present:

```jsx
import { elapsedSeconds, fmtElapsed } from "../../../lib/time.js";
```

Inside the `TeamRunDetail` component body, after `const agents = detail.agents || [];` (line 806):

```jsx
  const hasRunningAgent = agents.some((agent) => agent.status === "running");
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!hasRunningAgent) return undefined;
    setNowMs(Date.now());
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasRunningAgent]);
```

Replace the lane's work line at line 1259:

```jsx
                    {(() => {
                      const work = currentWork(agent, currentTask, run.status);
                      const seconds = elapsedSeconds(work.startedAt, nowMs);
                      return (
                        <div className="team-lane-task">
                          <span>{work.title}</span>
                          {seconds === null ? null : (
                            <span className="mono team-lane-elapsed">
                              {fmtElapsed(seconds)} 경과
                            </span>
                          )}
                        </div>
                      );
                    })()}
```

If `useState` or `useEffect` is not already imported in this file, add it to the existing `react` import.

- [ ] **Step 7: Run both tests to verify they pass**

```
cd frontend
npm test -- src/lib/time.test.js src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: all pass, including the pre-existing `TeamRunDetail` tests.

- [ ] **Step 8: Run the full frontend suite and compare to baseline**

```
cd frontend
npm test
```

Expected: no newly failing test.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/time.js frontend/src/lib/time.test.js frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
git commit -m "feat: 팀 실행 상세에서 진행 중 에이전트의 작업과 경과 시간 표시"
```

---

### Task 4: Require `plan_task_id` so the planner can declare dependencies

`_persist_plan_dependencies` already works. It is never exercised because the leader emits `"plan_task_id": null`, and `_parse_task_plan` bars a task from declaring dependencies without one.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:60-107` (`PLANNING_PROMPT`), `:3680-3726` (`_parse_task_plan` field validation)
- Test: `tests/test_team_runtime.py`
- Test: `tests/test_teams.py`

**Interfaces:**
- Consumes: `TeamTask.plan_ordinal` from Task 1 (ordering within a dependency-free plan).
- Produces: `_parse_task_plan` rejects any task lacking a non-empty string `plan_task_id`.

- [ ] **Step 1: Write the failing parser tests**

Add to `tests/test_team_runtime.py`, next to `test_task_plan_rejects_dependency_cycle`. That file imports `_parse_task_plan` directly and each test defines its own local `task` dict, so these do the same.

```python
def test_task_plan_requires_plan_task_id() -> None:
    task = {
        "title": "Research",
        "description": "Research the source.",
        "owner_agent_id": None,
        "required": True,
        "input_artifact_ids": [],
        "acceptance": {
            "required_outputs": ["research.md"],
            "required_verifications": [],
        },
    }

    with pytest.raises(ValueError, match="plan_task_id"):
        _parse_task_plan(json.dumps([{**task, "depends_on_task_ids": []}]))

    with pytest.raises(ValueError, match="plan_task_id"):
        _parse_task_plan(
            json.dumps([{**task, "plan_task_id": None, "depends_on_task_ids": []}])
        )


def test_task_plan_accepts_declared_dependency() -> None:
    task = {
        "title": "Research",
        "description": "Research the source.",
        "owner_agent_id": None,
        "required": True,
        "input_artifact_ids": [],
        "acceptance": {
            "required_outputs": ["research.md"],
            "required_verifications": [],
        },
    }
    payload = [
        {**task, "plan_task_id": "fix", "depends_on_task_ids": []},
        {**task, "plan_task_id": "qa", "depends_on_task_ids": ["fix"]},
    ]

    parsed = _parse_task_plan(json.dumps(payload))

    assert [item["plan_task_id"] for item in parsed] == ["fix", "qa"]
    assert parsed[1]["depends_on_task_ids"] == ["fix"]
```

- [ ] **Step 2: Write the failing blocked-dependent test**

Add to `tests/test_teams.py`. This proves the payoff: a failed prerequisite stops its dependent instead of letting it run against stale inputs.

```python
def test_failed_prerequisite_blocks_dependent_instead_of_running(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    fix = teams.create_task(run.id, "Fix", "Fix", cycle_id=cycle.id)
    qa = teams.create_task(run.id, "Qa", "Qa", cycle_id=cycle.id)
    teams.add_task_dependencies(qa.id, [fix.id])

    assert [t.id for t in teams.list_dependency_ready_tasks(run.id, cycle.id)] == [fix.id]

    teams.set_task_status(fix.id, "failed", error_message="draft-unmodified")
    blocked = teams.block_pending_dependency_failures(run.id, cycle.id)

    assert [t.id for t in blocked] == [qa.id]
    assert teams.get_task(qa.id).error_message == "blocked_by_dependency"
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -k "plan_task_id or declared_dependency" tests\test_teams.py::test_failed_prerequisite_blocks_dependent_instead_of_running -q
```

Expected: the three parser tests FAIL (a null or missing `plan_task_id` is currently accepted). The blocked-dependent test may already pass — that is fine, it is a regression guard for machinery Task 4 puts into real use.

- [ ] **Step 4: Make `plan_task_id` required in the parser** — ⚠️ ERRATUM E7: adding the key to `required_fields` breaks this plan's own Step 1 test. Shipped code enforces the field with the standalone strict check alone.

In `src/personal_agent_gateway/team_runtime.py`, add `"plan_task_id"` to `required_fields` (the set ending at line 3688) and remove it from the optional set at line 3691:

```python
        if not required_fields <= set(item) or set(item) - (
            required_fields | {"input_artifact_ids", "depends_on_task_ids"}
        ):
            raise ValueError("Planner task has missing or unknown fields")
```

Replace the optional-value check at lines 3711-3715 with a strict one:

```python
        plan_task_id = item.get("plan_task_id")
        if not isinstance(plan_task_id, str) or not plan_task_id.strip():
            raise ValueError("Planner task plan_task_id must be a non-empty string")
```

Delete the now-unreachable guard at lines 3725-3726:

```python
        if depends_on_task_ids and plan_task_id is None:
            raise ValueError("Planner task dependencies require plan_task_id")
```

At line 3771, the stored value no longer needs its `None` branch:

```python
                "plan_task_id": plan_task_id.strip(),
```

In `_validate_task_plan_dependencies` (line 3783), the `if task["plan_task_id"]` filters are now always true but remain harmless; leave them.

- [ ] **Step 5: Update the planning prompt**

In `src/personal_agent_gateway/team_runtime.py`, replace the sentence at lines 103-105 of `PLANNING_PROMPT`:

```
   ARTIFACTS below; use [] when the task needs none. plan_task_id is required
   and must be unique in this plan. depends_on_task_ids may reference only
   plan_task_id values in this response. A task that reads, revises, or
   verifies another task's required_outputs MUST list that task in its
   depends_on_task_ids; use [] only when the task truly has no prerequisite.
```

- [ ] **Step 6: Run the tests to verify they pass**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -k "plan_task_id or declared_dependency" tests\test_teams.py::test_failed_prerequisite_blocks_dependent_instead_of_running -q
```

Expected: all pass.

- [ ] **Step 7: Fix fixtures the stricter parser breaks**

Making `plan_task_id` required will fail existing plan fixtures that omit it. One is known in advance: `test_task_plan_requires_and_returns_immutable_acceptance` in `tests/test_team_runtime.py` asserts `"plan_task_id": None` in the parsed output — give its input a real `plan_task_id` and update the expected dict to match. Then run:

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py tests\test_team_model_effects.py -q
```

For every newly failing test, add a unique `"plan_task_id"` to its task fixture. Do not relax the parser to accommodate a fixture — the strictness is the feature. `valid_task_spec` in `tests/test_team_model_effects.py:42` is the highest-value place to fix, since many tests build on it; give it a `plan_task_id` derived from its `title` argument and a `depends_on_task_ids` default of `[]`.

- [ ] **Step 8: Run the surrounding suites and compare to baseline**

```
.\.venv\Scripts\python.exe -m pytest tests\test_teams.py tests\test_team_runtime.py tests\test_team_model_effects.py -q
```

Expected: no newly failing test.

- [ ] **Step 9: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py tests/test_teams.py tests/test_team_model_effects.py
git commit -m "feat: 플래너가 태스크 의존성을 실제로 선언하도록 plan_task_id 필수화"
```

---

### Task 5: Route worker-declared outcomes to leader review

`team_acceptance.py:56` substitutes `task_not_completed` when a worker gives no reason code, and that substitute is in the recoverable allowlist. So a vague "blocked" gets leader review while a specific `blocked / draft-unmodified` is hard-failed. Being specific is punished.

**Files:**
- Modify: `src/personal_agent_gateway/team_acceptance.py:39-40` (`is_recoverable_acceptance_failure`)
- Modify: `src/personal_agent_gateway/team_model_effects.py:1745-1758` (`_apply_task_outcome`), `:1880` (replay call), `:2547-2577` (`_expected_worker_state`)
- Modify: `src/personal_agent_gateway/team_runtime.py:1427-1433` (legacy non-ledger path)
- Test: `tests/test_team_acceptance.py`
- Test: `tests/test_team_model_effects.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–4.
- Produces:
  - `is_recoverable_acceptance_failure(reason_code: str | None, *, worker_declared: bool = False) -> bool`
  - `_expected_worker_state(acceptance, acceptance_recovery_attempts, worker_declared: bool)` — third positional argument added

- [ ] **Step 1: Write the failing predicate test**

Add to the existing `tests/test_team_acceptance.py`, importing `is_recoverable_acceptance_failure` from `personal_agent_gateway.team_acceptance` if it is not already imported there.

```python
def test_worker_declared_outcome_is_recoverable_regardless_of_reason_code() -> None:
    assert is_recoverable_acceptance_failure("draft-unmodified", worker_declared=True)
    assert is_recoverable_acceptance_failure("anything-novel", worker_declared=True)


def test_server_detected_failure_still_follows_the_allowlist() -> None:
    assert not is_recoverable_acceptance_failure("artifact_publication_failed")
    assert is_recoverable_acceptance_failure("required_output_missing")
```

- [ ] **Step 2: Write the failing effect test** — ⚠️ ERRATUM E9: `workspace_changes={}` raises `ValueError` before any assertion runs. Use the three-key empty form the rest of that file uses.

Add to `tests/test_team_model_effects.py`, using the file's existing `make_completed_worker_operation` helper (line 133), which already leaves the task `in_progress` and its agent `running`.

```python
def test_worker_blocked_with_novel_reason_routes_to_leader_review(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=TaskOutcome(
            status="blocked",
            summary="draft is byte-identical to the previous round",
            reason_code="draft-unmodified",
            deliverables=(),
            verifications=(),
        ),
    )
    acceptance = AcceptanceResult(
        accepted=False,
        status="blocked",
        reason_code="draft-unmodified",
        evidence={},
    )

    result = services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes={},
    )

    assert result.next_stage == "acceptance_lead"
    assert result.task.status == "in_progress"
    assert services.teams.get_agent(services.worker.id).status == "running"
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_acceptance.py tests\test_team_model_effects.py::test_worker_blocked_with_novel_reason_routes_to_leader_review -q
```

Expected: FAIL. `is_recoverable_acceptance_failure` takes no `worker_declared` keyword, and the effect writes `status = 'failed'`.

- [ ] **Step 4: Widen the predicate** — ⚠️ ERRATUM E8: the `worker_declared` signal prescribed here and in Steps 5-7 is wrong — a synthesized `invalid_task_outcome` is not worker-declared. Shipped code uses `is_worker_declared_outcome()` / `terminal_rejected_status()` in `team_acceptance.py`.

In `src/personal_agent_gateway/team_acceptance.py`, replace lines 39-40:

```python
def is_recoverable_acceptance_failure(
    reason_code: str | None,
    *,
    worker_declared: bool = False,
) -> bool:
    return worker_declared or reason_code in RECOVERABLE_ACCEPTANCE_REASONS
```

Leave `RECOVERABLE_ACCEPTANCE_REASONS` unchanged — it still governs server-detected rejections.

- [ ] **Step 5: Route and preserve status in the ledger path**

In `src/personal_agent_gateway/team_model_effects.py`, change the branch at lines 1745-1758. `outcome` is already in scope from line 1681.

```python
        elif (
            is_recoverable_acceptance_failure(
                acceptance.reason_code,
                worker_declared=outcome.status != "completed",
            )
            and task.acceptance_recovery_attempts < ACCEPTANCE_RECOVERY_CAP
        ):
            next_stage = "acceptance_lead"
        else:
            connection.execute(
                """
                update team_tasks
                set status = ?, result = null, error_message = ?,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (
                    acceptance.status,
                    acceptance.reason_code or outcome.reason_code,
                    now,
                    now,
                    task.id,
                ),
            )
            connection.execute(
                """
                update team_agents
                set status = ?, current_task_id = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                ("failed" if acceptance.status == "failed" else "waiting", now, now, agent.id),
            )
```

The agent goes to `waiting` rather than `failed` when its task is `blocked`, matching how `_apply_worker_decision` (line 1795) parks an agent whose task is blocked.

- [ ] **Step 6: Keep replay expectations in sync**

Still in `src/personal_agent_gateway/team_model_effects.py`, change `_expected_worker_state` (line 2547) to take the same signal and mirror the new terminal states:

```python
def _expected_worker_state(
    acceptance: dict[str, object] | None,
    acceptance_recovery_attempts: int,
    worker_declared: bool,
) -> tuple[
    Literal["acceptance_lead"] | None,
    Literal["in_progress", "completed", "failed", "blocked"],
    Literal["running", "completed", "failed", "waiting"],
]:
```

Replace its final three lines (2572-2577) with:

```python
    if (
        is_recoverable_acceptance_failure(
            reason_code, worker_declared=worker_declared
        )
        and acceptance_recovery_attempts < ACCEPTANCE_RECOVERY_CAP
    ):
        return "acceptance_lead", "in_progress", "running"
    if acceptance["status"] == "blocked":
        return None, "blocked", "waiting"
    return None, "failed", "failed"
```

Update the call site at line 1880, where `outcome` is already bound on line 1878:

```python
        expected_next_stage, task_status, agent_status = _expected_worker_state(
            acceptance,
            task.acceptance_recovery_attempts,
            outcome.status != "completed",
        )
```

- [ ] **Step 7: Apply the same rule to the legacy path** — ⚠️ ERRATUM E9: this fixes only the terminal status. The gate that decides whether the legacy flow reaches leader review is `team_runtime.py:2231` in `_recover_task_outcome`, which this plan never names.

In `src/personal_agent_gateway/team_runtime.py`, change lines 1427-1433:

```python
                terminal_status = acceptance.status
                if (
                    not acceptance.accepted
                    and is_recoverable_acceptance_failure(
                        acceptance.reason_code,
                        worker_declared=outcome.status != "completed",
                    )
                    and task.acceptance_recovery_attempts
                    >= ACCEPTANCE_RECOVERY_CAP
                ):
                    terminal_status = "failed"
```

- [ ] **Step 8: Run the tests to verify they pass**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_acceptance.py tests\test_team_model_effects.py::test_worker_blocked_with_novel_reason_routes_to_leader_review -q
```

Expected: all pass.

- [ ] **Step 9: Run the surrounding suites and compare to baseline**

```
.\.venv\Scripts\python.exe -m pytest tests\test_team_acceptance.py tests\test_team_model_effects.py tests\test_team_runtime.py tests\test_teams.py -q
```

Expected: no newly failing test. Pay particular attention to replay tests — they are the ones `_expected_worker_state` governs.

- [ ] **Step 10: Commit**

```bash
git add src/personal_agent_gateway/team_acceptance.py src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/team_runtime.py tests/test_team_acceptance.py tests/test_team_model_effects.py
git commit -m "fix: 워커가 선언한 blocked 결과를 하드 실패 대신 리더 검수로 라우팅"
```

---

## Final verification

- [ ] **Run the whole backend suite and compare to the recorded baseline**

```
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: failure count no higher than the baseline recorded before Task 1, and no test that passed then fails now.

- [ ] **Run the whole frontend suite**

```
cd frontend
npm test
```

Expected: all pass.

- [ ] **Confirm the migration applies to the real database**

⚠️ ERRATUM E11 — this step's snippets are wrong in two ways, and the step as written is riskier than it needs to be.

- `Database('data/app.sqlite')` takes a `Path`, not a `str`, and constructing it does not run migrations. `Database(Path(...)).initialize()` is what runs them.
- There is no manual step to perform at all. `app.py:480` calls `db.initialize()` on startup, so **migration 28 applies automatically when the backend restarts**.
- Do not run this against live data to verify it. Run it against a copy — a verification step should not mutate production.

What was actually done, against a copy (original untouched):

```bash
cp data/app.sqlite /tmp/app-migration-test.sqlite
# then, in python: Database(Path("/tmp/app-migration-test.sqlite")).initialize()
```

Result: `schema_version` 27 → 28; the incident cycle `d16e7748` backfilled to ordinal 0 =
`잔여 P3 7건 수정`, ordinal 1 = `잔여 P3 수정본 QA 재검증` — the order the leader planned and the
run failed to execute. Across all 24 task rows: zero duplicate `(cycle, ordinal)` pairs, every
cycle contiguous `0..n-1`. The live database held no illegal agent statuses, so E5's `'blocked'`
cleanup is precautionary rather than corrective.

## Out of scope

Recorded here so they are not silently dropped:

- Making a `blocked` cycle resumable. `_execute_and_synthesize` (`team_runtime.py:2643`) still terminates a cycle on `blocked`. Task 5 changes what gets recorded, not the cycle lifecycle.
- Inferring dependency edges server-side from acceptance contracts.
- Streaming live agent output.
- Recovering run `eec591b4b2f84444a86627b5806a02b9` itself. Its P3 fixes are complete in the workspace draft (633 lines, 34,214 bytes) but the run stays `failed`. That is a separate operational task.
