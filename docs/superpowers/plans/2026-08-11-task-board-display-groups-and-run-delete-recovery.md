# Task board display groups and Team run delete recovery implementation plan

> **For the implementer:** Use `superpowers:executing-plans` to work task by
> task, `superpowers:test-driven-development` for every behavior change, and
> `superpowers:verification-before-completion` before claiming any task done.

**Goal:** Collapse the task board to four display columns without changing
stored task states, and make deleting a finished Team run work when its git
worktree and branch were already cleaned up outside the gateway.

**Architecture:** A new pure frontend module owns the state-to-column mapping;
`TeamRunDetail` becomes a consumer of it and stops carrying the column list.
On the backend, `TeamSpaceManager.cleanup` gains presence checks so that
missing git state is treated as already cleaned while a real removal failure
still raises. The API contract, `TaskStatus`, and stored values are untouched.

**Tech stack:** React 19, Vite, Vitest/Testing Library, Python 3.13, FastAPI,
SQLite, pytest.

**Design:**
`docs/superpowers/specs/2026-08-11-task-board-display-groups-and-run-delete-recovery-design.md`

## Execution precondition

`main` is clean at `1c7530c`. Create a feature branch before the first commit —
do not commit these changes directly to `main`.

The two streams are independent: Tasks 2-3 (board) and Tasks 4-6 (delete) can
be done in either order. Task 1 comes first, Task 7 last.

## Task 1: Record the pre-change baseline

Both suites have pre-existing failures on `main`, and the backend suite is
environment-sensitive. Completion of later tasks is judged by delta against a
recorded baseline, not by an absolute green run.

### Step 1: Capture and record

Run each suite once and write the counts plus the per-file failure distribution
into this plan under "Baseline record" below:

- Backend, blocking (the suite takes roughly 7 minutes; do not background it):
  `python -m pytest -q`
- Frontend: `npm --prefix frontend test`

Note any failure that mentions `-n auto` parallel contention separately; those
are known test-infra flakes, not signal.

### Baseline record

Recorded 2026-08-11 on `feat/task-board-display-groups` at `1c7530c`, before any
source change.

- **backend: 21 failed / 1426 passed / 4 skipped** (435s)
  - `tests/test_runtime_factory_headless.py` — 16 failures, all
    `TypeError: ProviderExecutionCapabilities.__init__() got an unexpected
    keyword argument 'ready'`. The test's local `_AgentRegistry` fake has
    drifted from the real dataclass.
  - `tests/test_api_agents.py` — 4 failures
    (`test_agents_returns_safe_catalog`, and the three
    `test_active_session_config_*` cases).
  - `tests/test_api_dashboard.py` — 1 failure
    (`test_dashboard_usage_returns_provider_usage`).
  - No `-n auto` contention failures appeared; this run was serial.
  - None of these touch `space_policies`, `teams`, or `api/team_runs`.
- **frontend: 0 failed / 369 passed** across 40 files — but see the correction
  below; that run was lucky.

Correction found while verifying the merge: `ArchiveView.test.jsx` has two
load-dependent flakes,
`previews the current private draft in a modal before publishing` and
`turns a persona request into a user-authored Library draft and fulfills it
only on publish`. They time out at the 5s default when the full suite runs
in parallel and pass when `ArchiveView` runs alone (15/15). Confirmed
pre-existing by checking out `1c7530c` — the baseline commit, with none of this
work applied — where the same two tests fail the same way (2 failed / 367
passed). Treat the frontend baseline as "0-2 failed, ArchiveView only".

## Task 2: Add the task status group module

**Files:**

- Create: `frontend/src/lib/taskStatusGroups.js`
- Create: `frontend/src/lib/taskStatusGroups.test.js`

### Step 1: Write the failing mapping tests

Cover:

- `pending` maps to group `pending`;
- `in_progress`, `waiting_for_user`, and `waiting_for_provider` map to
  `in_progress`;
- `completed` and `skipped` map to `completed`;
- `blocked`, `failed`, and `canceled` map to `unresolved`;
- an unmapped value (for example `"invented_state"`), an empty string, and
  `undefined` map to `unresolved`;
- `TASK_STATUS_GROUPS` has exactly four entries, ordered `pending`,
  `in_progress`, `completed`, `unresolved`, with headers `PENDING`,
  `IN PROGRESS`, `COMPLETED`, `UNRESOLVED`;
- every value of the backend `TaskStatus` union is covered by exactly one
  group — assert against a literal list of the nine states so that adding a
  tenth state to the backend without updating the table fails this test.

### Step 2: Implement

Export `TASK_STATUS_GROUPS` (array of `{ key, label, statuses }`) and
`groupForTaskStatus(status)`. Keep the module pure: data and functions only, no
imports, no React.

### Step 3: Verify

`npm --prefix frontend test -- taskStatusGroups`

## Task 3: Render four columns in TeamRunDetail

**Files:**

- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:10-20`
  (remove `TEAM_TASK_COLUMNS`) and `:1358-1391` (board render)
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

### Step 1: Write the failing board tests

With a run whose tasks cover all nine states, assert:

- exactly four `.team-task-column` elements render;
- their headers read `PENDING`, `IN PROGRESS`, `COMPLETED`, `UNRESOLVED` in
  that order;
- the `skipped` task's title appears inside the `COMPLETED` column;
- the `blocked`, `failed`, and `canceled` task titles appear inside the
  `UNRESOLVED` column;
- the `waiting_for_user` and `waiting_for_provider` task titles appear inside
  the `IN PROGRESS` column;
- each column header count equals the number of cards in that column.

### Step 2: Implement

Import `TASK_STATUS_GROUPS` and `groupForTaskStatus`, delete
`TEAM_TASK_COLUMNS`, map over the groups, filter with
`groupForTaskStatus(task.status) === group.key`, and render `group.label`
instead of `column.replace("_", " ")`. Key the column by `group.key`.

Do not change `TeamTaskCard`, the per-status badge, card ordering within a
column, or any CSS. Task order within a column stays the incoming
`visibleTasks` order.

### Step 3: Verify

`npm --prefix frontend test -- TeamRunDetail` and confirm no other frontend
test regressed against the Task 1 baseline.

## Task 4: Make worktree cleanup tolerate missing git state

**Files:**

- Modify: `src/personal_agent_gateway/space_policies.py:259-274`
- Modify: `tests/test_space_policies.py` (follow the temp-repo pattern at
  `:107-139`)

### Step 1: Write the failing cleanup tests

Using a temporary git repository and a prepared worktree space:

- cleanup succeeds and removes `run_root` when the branch was already deleted
  and `working_root` was already removed — this is the reported failure;
- cleanup still deletes a branch that does exist;
- cleanup raises `ValueError` when a branch that exists cannot be deleted
  (force a failure, for example by checking the branch out in the repository so
  `git branch -D` refuses);
- cleanup skips git work and still removes `run_root` when the policy
  `workspace_path` is not a git repository.

### Step 2: Implement

In `cleanup`, before touching git, confirm the policy repository resolves as a
git repository (`git rev-parse --git-dir`); if not, skip both worktree and
branch cleanup. Keep the existing `working_root.exists()` guard for
`worktree remove`. Before `branch -D`, confirm the branch resolves with
`git rev-parse --verify --quiet refs/heads/<branch>` and skip when it does not.

Add a non-raising helper for these probe calls rather than making `_run_git`
tolerant — `_run_git` must keep raising, because Task 4's third test depends on
a real removal failure still failing.

### Step 3: Verify

`python -m pytest tests/test_space_policies.py -q`

## Task 5: Cover delete at the service and API layers

**Files:**

- Modify: `tests/test_teams.py` (near the existing delete tests at `:886-952`)
- Modify: `tests/test_api_team_runs.py` (near `:2050-2103`)

### Step 1: Write the failing tests

- `TeamRunService.delete_team_run` removes a worktree-policy run whose branch
  and `working_root` are already gone, and removes its `team_tasks` and
  `team_agents` rows;
- `DELETE /api/team-runs/{id}` returns 200 `{"deleted": true}` for that run
  instead of 409;
- the existing 409 for a running run and 404 for a missing run still hold —
  confirm the existing tests still pass rather than duplicating them.

### Step 2: Verify

`python -m pytest tests/test_teams.py tests/test_api_team_runs.py -q`

No implementation step: Task 4 is the fix. If these tests do not pass on Task
4's implementation, the root cause differs from the design and investigation
resumes with `superpowers:systematic-debugging` before any further change.

## Task 6: Surface the delete refusal reason

**Files:**

- Modify: `frontend/src/api/client.js:519-522`
- Modify: `frontend/src/hooks/useTeamRunController.js:564-580`
- Modify: `frontend/src/api/client.test.js`
- Modify: `frontend/src/hooks/useTeamRunController.test.jsx`

### Step 1: Write the failing tests

- `deleteTeamRun` returns `{ ok: false, status: 409, detail: "<server detail>" }`
  when the server refuses, and `{ ok: true, status: 200, detail: null }` on
  success;
- `deleteTeamRun` returns `ok: false` with a null detail when the error body is
  absent or not JSON, without throwing;
- `handleDeleteTeamRun` toasts the server detail on refusal, and falls back to
  `Failed to delete team run` when there is no detail;
- a successful delete still clears the selection, refreshes the list, and
  toasts success.

### Step 2: Implement

Change only `deleteTeamRun` in `client.js`. Leave the other boolean-returning
delete helpers alone.

### Step 3: Verify

`npm --prefix frontend test -- client useTeamRunController`

## Task 7: Rebuild assets and verify end to end

### Step 1: Rebuild the served bundle

`npm run build:frontend`, then confirm
`src/personal_agent_gateway/frontend_dist` reflects the new sources.

Correction found during execution: `frontend_dist/` is now listed in
`.gitignore:10`, so the bundle is **not** a committed artifact any more. A few
old bundle files are still tracked because the ignore rule was added without
`git rm --cached`. Build for the live verification below, then restore the
tracked dist paths so the commit carries source, tests, and docs only.
Untracking the leftovers is a separate repo-hygiene change, deliberately not
bundled here.

### Step 2: Full suites against the baseline

Run both suites blocking, and compare to the Task 1 baseline. Any new failure
blocks completion; unchanged pre-existing failures do not.

### Step 3: Live verification

Restart the backend so the Python changes load (`npm run stop` then
`npm start`, or restart the runtime already listening on 8787 — the running
process predates these changes).

Then, in the browser:

- open a Team run's TASKS tab and confirm four columns with the four headers;
- delete run `6357caf8a0d44134969df02f2e41a8fb`, the run that reproduced the
  defect, and confirm it disappears, its `team_runs` row is gone, and
  `data/workspace/6357caf8a0d44134969df02f2e41a8fb` is removed;
- confirm the branch `team-run/6357caf8a0d44134969df02f2e41a8fb` is still
  absent from `.worktrees/agent` and that no unexpected branch was created.

Record the observed results. Do not claim completion from test output alone —
the reported bug was a live-state bug that no existing test covered.

### Live results (2026-08-11)

Runtime restarted through the launcher (`npm run stop`, then
`npm run start:no-build`): PAG pid 21996, LMG pid 8500, both ready. The server
serves the rebuilt bundle — `/` references `assets/index-BwsXWqi6.js`.

The HTTP delete could not be driven from the agent session: `/api/team-runs`
answers 401 without a real login cookie, and no browser automation was
available in this session. The delete was therefore exercised against the live
database through the real `TeamRunService` built from the live `AppConfig` —
the same code path the API calls, minus the auth layer that
`tests/test_api_team_runs.py` covers.

Run `6357caf8a0d44134969df02f2e41a8fb`, read back from the live database, held
exactly the diagnosed state: `status=completed`, `write_mode=worktree`,
`workspace_path=.worktrees/agent`, `worktree_branch=team-run/6357caf8…`,
`working_root=…/project` (absent on disk). After the fix:

- `delete_team_run` returned without raising, where it previously raised
  `ValueError: Git worktree command failed: error: branch … not found`;
- the `team_runs` row is gone (`get_team_run` raises `KeyError`; one run,
  `eec591b4…`, remains);
- `data/workspace/6357caf8a0d44134969df02f2e41a8fb` is removed;
- the repository branch list is byte-identical before and after, and no
  `team-run/*` branch exists — nothing was leaked or wrongly deleted.

Board rendering was verified by component tests rather than a live browser, for
the same lack of browser automation.

### Backend suite result

21 failed / 1432 passed / 4 skipped (429s). Identical failure list to the
baseline, passes up by exactly the 6 new backend tests. No regression.
Frontend: 41 files / 389 passed, up from 40 / 369, no failures.

### Step 4: Commit

Commit on the feature branch: the spec, this plan with its filled-in baseline
and live results, source changes, tests, and the rebuilt `frontend_dist`.
