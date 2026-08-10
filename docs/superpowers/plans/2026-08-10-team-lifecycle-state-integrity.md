# Team lifecycle state integrity implementation plan

> **For Codex:** Required sub-skill: use `superpowers:executing-plans` to
> implement this plan task by task. Use
> `superpowers:test-driven-development` for each behavior change and
> `superpowers:verification-before-completion` before the final commit.

**Goal:** Give HIL waiting, dependency non-execution, and terminal blocking
separate states, then make run/cycle/request/task/series transitions atomic and
recoverable.

**Architecture:** A pure `team_lifecycle.py` module owns status types,
transition rules, dependency disposition, and settlement policy. Existing
services keep persistence and orchestration responsibilities, but lifecycle
mutations become compare-and-set transactions in `TeamRunService`. The runtime,
cycle service, dispatcher, API, and frontend consume the same stored meanings.

**Tech stack:** Python 3.12, SQLite, pytest/pytest-asyncio, FastAPI, React,
Vitest/Testing Library.

**Design:**
`docs/superpowers/specs/2026-08-10-team-lifecycle-state-integrity-design.md`

## Execution precondition

The current `main` worktree contains unrelated uncommitted workspace
inheritance, runtime, frontend, and generated-asset changes. Do not stage or
rewrite them as part of this plan. Before implementation, either finish those
changes or create an isolated worktree from commit `af8abb0` using
`superpowers:using-git-worktrees`.

## Task 1: Centralize lifecycle types and pure policy

**Files:**

- Create: `src/personal_agent_gateway/team_lifecycle.py`
- Create: `tests/test_team_lifecycle.py`
- Modify: `src/personal_agent_gateway/teams.py:31-84`

### Step 1: Write failing transition and terminal-set tests

Cover:

- `waiting_for_user` and `waiting_for_provider` are nonterminal task states;
- `completed`, `skipped`, `blocked`, `failed`, and `canceled` are terminal;
- only the transitions listed in the design are allowed;
- an identical source/target pair is accepted only as explicit idempotent
  confirmation;
- illegal transitions raise a lifecycle-specific `ValueError` containing the
  entity, source, and target.

Use table-driven `pytest.mark.parametrize` cases. Do not instantiate a database
in these tests.

### Step 2: Run the tests and verify the expected failure

```bash
pytest -q tests/test_team_lifecycle.py
```

Expected: import failure because `team_lifecycle.py` does not exist.

### Step 3: Add the minimum pure policy module

Define and export:

```python
TeamRunStatus
CycleStatus
TaskStatus
TERMINAL_RUN_STATUSES
TERMINAL_CYCLE_STATUSES
TERMINAL_TASK_STATUSES
WAITING_TASK_STATUSES
can_transition(entity: str, source: str, target: str) -> bool
require_transition(entity: str, source: str, target: str) -> None
```

Include `waiting_for_user` and `skipped` in `TaskStatus`. Keep frozen sets and a
single transition map. Do not add a generic state-machine class.

Import and re-export the status aliases from `teams.py` so existing imports
continue to work. Replace `_TERMINAL_RUN_STATUSES` usages with the shared
constant.

### Step 4: Run focused tests

```bash
pytest -q tests/test_team_lifecycle.py tests/test_teams.py::test_completed_with_failures_is_terminal
```

Expected: pass.

### Step 5: Commit

```bash
git add src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/teams.py tests/test_team_lifecycle.py
git commit -m "refactor(team-runs): centralize lifecycle policy"
```

## Task 2: Replace dependency blocking with explicit skipping

**Files:**

- Modify: `src/personal_agent_gateway/team_lifecycle.py`
- Modify: `src/personal_agent_gateway/teams.py:1888-1970`
- Modify: `src/personal_agent_gateway/team_runtime.py:2710-2760`
- Modify: `tests/test_team_lifecycle.py`
- Modify: `tests/test_teams.py:1928-1980`
- Modify: `tests/test_team_runtime.py`

### Step 1: Write failing dependency tests

Rename existing `blocks_*` expectations to `skips_*` and assert:

- failed, blocked, canceled, or skipped prerequisite changes a pending
  dependent to `skipped`;
- the error code is `skipped_by_dependency`;
- `finished_at` is populated;
- propagation reaches a transitive dependent in the same call;
- pending, in-progress, provider-waiting, and user-waiting prerequisites leave
  the dependent pending;
- only all-completed prerequisites make the dependent ready;
- repeated propagation returns no new tasks.

Add a pure-policy test for the prerequisite disposition table.

### Step 2: Run focused tests and verify they fail

```bash
pytest -q tests/test_team_lifecycle.py tests/test_teams.py -k 'dependency'
```

Expected: old code writes `blocked`/`blocked_by_dependency` and does not know
`skipped`.

### Step 3: Implement dependency disposition

Add a pure helper that classifies prerequisite statuses as `ready`, `waiting`,
or `skip`. Rename `block_pending_dependency_failures` to
`skip_pending_dependency_failures` and change its fixed-point update to:

```sql
status = 'skipped'
error_message = 'skipped_by_dependency'
```

Preserve current task ordering. Update the runtime call site. Do not retain a
compatibility alias unless another repository call site still requires it.

### Step 4: Run focused service and runtime tests

```bash
pytest -q tests/test_team_lifecycle.py tests/test_teams.py -k 'dependency'
pytest -q tests/test_team_runtime.py -k 'dependency or terminal_status'
```

Expected: dependency tests pass; terminal tests may still expose the old
settlement fallback and are addressed in Task 4.

### Step 5: Commit

```bash
git add src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_runtime.py tests/test_team_lifecycle.py tests/test_teams.py tests/test_team_runtime.py
git commit -m "feat(team-runs): distinguish skipped dependencies"
```

## Task 3: Make user-decision transitions atomic

**Files:**

- Modify: `src/personal_agent_gateway/teams.py:2358-2860`
- Modify: `src/personal_agent_gateway/team_runtime.py:3100-3145`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py`
- Modify: `src/personal_agent_gateway/team_cycles.py:1008-1035`
- Modify: `tests/test_teams.py:1762-1895`
- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_team_cycles.py:658-690`

### Step 1: Write failing HIL transaction tests

Build a continuous run with a dispatching request, cycle, auto-series, two
tasks, and two agents. Assert:

- `defer_task_for_user_decision` writes task `waiting_for_user`;
- publish atomically writes request `awaiting_user`, run/cycle
  `waiting_for_user`, only linked agents `waiting`, and auto-series
  `paused_user` with `paused_cycle_id`;
- an unrelated agent waiting on a provider operation is untouched;
- invalid source state or empty request rolls back every row and writes no
  message;
- answer resolves the request, requeues only linked waiting tasks, resets only
  linked agents, changes run/cycle/series to `running`, and clears series pause
  fields;
- a stale revision rolls back all rows;
- a linked legacy `blocked` task is accepted only when referenced by the active
  pre-deployment request;
- an unlinked `blocked` task is never requeued.

Add a cancel test proving only linked `waiting_for_user` tasks are canceled and
an unrelated terminal blocked task is unchanged.

### Step 2: Run the tests and verify old semantics fail

```bash
pytest -q tests/test_teams.py -k 'decision_request or waiting_decision'
```

Expected: old code writes task `blocked`, changes all waiting agents, updates
the cycle separately, and leaves series state outside the answer transaction.

### Step 3: Implement compare-and-set HIL commands

Change `defer_task_for_user_decision` to write `waiting_for_user` with
`where status = 'in_progress'` and require one updated row.

Refactor publish, answer, and cancel so each:

- begins `immediate`;
- loads the linked cycle request and optional auto-series inside the same
  connection;
- validates all source statuses before mutation;
- uses status predicates and `_require_one_updated` for authoritative rows;
- updates only agent IDs derived from blocking task owners, or the leader for a
  run-level question;
- writes the domain message before committing.

For legacy compatibility, accept `blocked` only for task IDs listed in the
active request's `blocking_task_ids`. New transitions always write
`waiting_for_user`.

### Step 4: Remove split orchestration writes

Delete the post-publish cycle status write and the separate
`TeamCycleService.pause_for_user()` call from the runtime/dispatcher path.
Keep `pause_for_user()` only if startup reconciliation or old tests still need
it; otherwise remove it and its isolated tests.

After answer commit, `dispatcher.resume()` remains the only scheduling side
effect.

### Step 5: Run HIL regression tests

```bash
pytest -q tests/test_teams.py -k 'decision_request or waiting_decision'
pytest -q tests/test_team_runtime.py -k 'decision or waiting_for_user'
pytest -q tests/test_team_cycles.py -k 'pause or waiting'
pytest -q tests/test_api_team_runs.py -k 'decision or waiting'
```

Expected: pass.

### Step 6: Commit

```bash
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/team_cycles.py tests/test_teams.py tests/test_team_runtime.py tests/test_team_cycles.py
git commit -m "fix(team-runs): make user decision state atomic"
```

## Task 4: Make settlement incomplete-aware and root-cause preserving

**Files:**

- Modify: `src/personal_agent_gateway/team_lifecycle.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `src/personal_agent_gateway/team_runtime.py:2700-2765,3281-3300`
- Modify: `tests/test_team_lifecycle.py`
- Modify: `tests/test_team_runtime.py:4241-4275`

### Step 1: Write failing settlement tests

Add table-driven cases proving:

- pending, in-progress, provider-waiting, or user-waiting task returns no
  terminal status;
- an active decision returns `waiting_for_user` before dependency propagation;
- an open provider operation returns `waiting_for_provider`;
- required failed/blocked tasks produce failed/blocked;
- required skipped task follows the first terminal root dependency through a
  transitive chain;
- completed required tasks plus optional failed/blocked/skipped task produce
  `completed_with_failures`;
- all required tasks completed and optional tasks completed produce
  `completed`;
- an unresolved dependency deadlock produces an integrity error instead of
  `blocked`.

Represent the dependency map as task ID to prerequisite IDs. Include the task
IDs in the integrity-error assertion.

### Step 2: Run the tests and verify the fallback failure

```bash
pytest -q tests/test_team_lifecycle.py tests/test_team_runtime.py -k 'terminal_status or settlement or disposition'
```

Expected: `_terminal_status()` returns `blocked` for incomplete cases.

### Step 3: Implement the pure execution disposition

Add a small immutable result with kinds:

```text
incomplete | waiting_for_user | waiting_for_provider | terminal
```

For `terminal`, include the terminal cycle status. Traverse dependency IDs only
when a required task is skipped. Detect dependency cycles defensively and raise
the lifecycle-integrity error with involved IDs.

Add `TeamRunService.list_task_dependency_map(team_run_id, cycle_id)` using one
query. Replace `_terminal_status()` with the shared policy and remove its
literal `blocked` fallback.

### Step 4: Wire the runtime loop

Evaluate active decision and open provider operation before propagating
dependency skips. Continue only when disposition is incomplete and ready work
exists. Return the explicit waiting states without settling. Raise the
integrity error when incomplete tasks have neither ready work nor a recognized
wait.

### Step 5: Run runtime regression tests

```bash
pytest -q tests/test_team_lifecycle.py
pytest -q tests/test_team_runtime.py -k 'terminal_status or dependency or waiting or settlement'
```

Expected: pass.

### Step 6: Commit

```bash
git add src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_runtime.py tests/test_team_lifecycle.py tests/test_team_runtime.py
git commit -m "fix(team-runs): settle cycles from explicit disposition"
```

## Task 5: Remove terminal-state drift and reconcile startup HIL

**Files:**

- Modify: `src/personal_agent_gateway/team_cycles.py:1-55,961-980`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py:1-55,297-330`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `tests/test_team_cycles.py`
- Modify: `tests/test_team_cycle_dispatcher.py`
- Modify: `tests/test_team_cycle_recovery.py`

### Step 1: Write failing consistency/restart tests

Cover:

- dispatcher and cycle service import the same terminal-cycle set;
- a non-empty collecting request plus linked waiting tasks is published during
  reconciliation without a second model call;
- an awaiting request repairs run, cycle, linked task/agent, and series HIL
  projections;
- repeated reconciliation writes no duplicate decision message;
- stale running cycle with no recoverable operation becomes interrupted;
- restart after answer commit but before `resume()` leaves an interrupted,
  explicitly resumable cycle rather than running or blocked;
- terminal cycles still settle their dispatching request idempotently.

### Step 2: Run the tests and verify failure

```bash
pytest -q tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_recovery.py -k 'reconcile or terminal or waiting'
```

Expected: duplicated constants and partial HIL projections violate the new
assertions.

### Step 3: Centralize predicates and add reconciliation command

Import `TERMINAL_CYCLE_STATUSES` in both services and delete local copies.

Add one `TeamRunService.reconcile_lifecycle(protected_cycle_ids)` command that
returns the cycle IDs it repaired. It must:

- publish a valid collecting request exactly once;
- project an awaiting request to HIL state;
- avoid changing cycles protected by open operation-ledger recovery.

Call it in `TeamCycleDispatcher.reconcile()` after provider-operation recovery
claims are known and before cycle-request settlement.

Use the existing interruption path for unclaimed active cycles. Do not add a
second reconciliation subsystem.

### Step 4: Run cycle/recovery tests

```bash
pytest -q tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_recovery.py
```

Expected: pass.

### Step 5: Commit

```bash
git add src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/team_cycle_dispatcher.py tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_recovery.py
git commit -m "fix(team-runs): reconcile lifecycle state on startup"
```

## Task 6: Expose dependency context and explicit task states in the UI

**Files:**

- Modify: `src/personal_agent_gateway/api/team_runs.py:1276-1305`
- Modify: `tests/test_api_team_runs.py:1146-1245`
- Modify: `frontend/src/components/atoms/StatusBadge/index.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:1-20,1320-1380`
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

### Step 1: Write failing API payload test

Create a prerequisite and dependent task, call the Team run detail endpoint,
and assert the dependent payload contains:

```json
{"depends_on_task_ids": ["<prerequisite-id>"]}
```

Also assert `waiting_for_user` and `skipped` pass through unchanged.

### Step 2: Run the API test and verify failure

```bash
pytest -q tests/test_api_team_runs.py -k 'task_payload'
```

Expected: `depends_on_task_ids` is absent.

### Step 3: Add the additive API field

Load the dependency map once in each list/detail handler and pass the IDs into
`_task_payload`; do not execute one query per task. Individual task responses
may call `list_task_dependencies(task.id)` once. Update all four
`_task_payload` call sites and preserve every existing field.

### Step 4: Write failing frontend tests

Assert:

- waiting-for-user task card is visible and labeled `INPUT NEEDED` or
  `WAITING FOR USER`;
- waiting-for-provider, skipped, and canceled tasks are not omitted from the
  board;
- skipped dependency card names its prerequisite;
- blocked retains its distinct label and is not rendered as skipped;
- the answer form is hidden when run/cycle/request HIL states disagree.

### Step 5: Run frontend tests and verify failure

```bash
npm --prefix frontend test -- TeamRunDetail.test.jsx
```

Expected: statuses absent from `TEAM_TASK_COLUMNS` are not rendered.

### Step 6: Implement the UI

Add status labels for `waiting_for_provider` and `skipped`. Extend or group the
task columns without changing task ordering inside a column. Compute
prerequisite titles from the already-loaded task array and
`depends_on_task_ids`; do not add another request.

Add only the CSS required for the new badges/columns and keep the current Team
run visual language.

### Step 7: Run API and frontend tests

```bash
pytest -q tests/test_api_team_runs.py -k 'task_payload or decision'
npm --prefix frontend test -- TeamRunDetail.test.jsx
npm --prefix frontend test
```

Expected: pass.

### Step 8: Commit

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py frontend/src/components/atoms/StatusBadge/index.jsx frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat(team-runs): show waiting and skipped task states"
```

## Task 7: End-to-end regression and full verification

**Files:**

- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_team_cycle_recovery.py`
- Modify: documentation only if implementation differs from the approved design

### Step 1: Add the critical end-to-end regression

Create task A and dependent task B in one cycle. Task A asks the user. Simulate
restart after publish, reconcile, answer, resume the same cycle, complete A,
then complete B.

Assert A and B are not `blocked`/`skipped` after defer, publish, reconciliation,
answer, and each resumed execution boundary. Also assert the same cycle ID is
retained, the decision request resolves once, and the cycle/request/series
settle completed.

### Step 2: Run the regression alone

```bash
pytest -q tests/test_team_runtime.py tests/test_team_cycle_recovery.py -k 'user_wait_dependency_restart'
```

Expected: pass.

### Step 3: Run backend verification

```bash
pytest -q
```

Expected: all tests pass.

### Step 4: Run frontend verification and build

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: all tests pass and Vite produces the frontend bundle. Build warnings
for runtime-resolved `/static/vendor/*` assets are acceptable only if they are
the existing warnings; no new warning is accepted.

### Step 5: Review the diff for scope and generated artifacts

```bash
git diff --check
git status --short
git diff --stat
```

Do not commit pre-existing unrelated files. Include rebuilt
`src/personal_agent_gateway/frontend_dist` only if this repository's current
release workflow requires generated frontend assets in the same feature
commit; otherwise leave them out and state that explicitly.

### Step 6: Update design errata if required

If implementation proves any approved design statement incorrect, add a short
erratum to the design document naming the shipped behavior and reason. Do not
silently diverge.

### Step 7: Final commit

```bash
git add <only lifecycle implementation and test files>
git commit -m "test(team-runs): cover resumable dependency lifecycle"
```
