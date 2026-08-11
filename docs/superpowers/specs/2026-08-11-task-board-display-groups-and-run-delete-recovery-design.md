# Task board display groups and Team run delete recovery

## Goal

Two independent changes that ship together because both are about the Team run
surface being unusable for routine work:

1. The task board must present four columns regardless of how many internal
   task states exist.
2. Deleting a finished Team run must succeed when its git worktree and branch
   were already cleaned up outside the gateway, and must explain itself when it
   legitimately refuses.

Internal state granularity is deliberately unchanged. `TaskStatus` keeps its
nine values; only the board's presentation collapses.

## Part 1: Task board display groups

### Problem

`TEAM_TASK_COLUMNS` in `frontend/src/components/organisms/TeamRunDetail/index.jsx:10`
renders one column per internal task state, currently nine: `pending`,
`in_progress`, `waiting_for_provider`, `waiting_for_user`, `skipped`,
`blocked`, `completed`, `failed`, `canceled`. The grid is
`repeat(auto-fit, minmax(150px, 1fr))`, so every added state narrows every
column. Reading the board now costs more attention than the information it
carries, and each new lifecycle state makes it worse.

A second defect follows from the same list: the board filters with
`task.status === column`, so a task whose state is absent from
`TEAM_TASK_COLUMNS` renders in no column at all. It disappears silently rather
than showing up somewhere wrong, which is the harder failure to notice.

### Display groups

Four groups, in this order:

| Group key | Header | Internal states |
| --- | --- | --- |
| `pending` | `PENDING` | `pending` |
| `in_progress` | `IN PROGRESS` | `in_progress`, `waiting_for_user`, `waiting_for_provider` |
| `completed` | `COMPLETED` | `completed`, `skipped` |
| `unresolved` | `UNRESOLVED` | `blocked`, `failed`, `canceled` |

Rationale for the two non-obvious placements:

- Both `waiting_*` states are work that has started and has not stopped, so
  they belong with `in_progress`. The board answers "where does this run
  stand", and a waiting task stands in the same place as a running one.
- `skipped` means the cycle finished with that task deliberately not executed,
  so it settles with `COMPLETED` rather than reading as a problem. The card
  badge still says `건너뜀`, so the distinction survives where it matters.

`UNRESOLVED` names "ended without being resolved". It covers failure,
blocking, and cancellation without implying that all three need attention
(`NEEDS ATTENTION` overstates a cancellation) or that they are merely paused
(`HALTED` understates a failure).

### Unknown states

`groupForTaskStatus` returns `unresolved` for any state absent from the table.
An unmapped state is by definition not known to be progressing or done, and
being visible in the wrong column is recoverable while being invisible is not.
The card badge prints the raw state, so an unmapped value is identifiable on
sight.

### Card badges are unchanged

The columns collapse; the cards do not. `TeamTaskCard` keeps rendering the
precise internal state (`차단됨`, `FAILED`, `CANCELED`, `PROVIDER WAIT`, …), so
`UNRESOLVED` remains readable at a glance without opening the detail panel.
This is the whole reason four columns are enough.

### Structure

Grouping lives in a new pure module, `frontend/src/lib/taskStatusGroups.js`,
alongside the existing `lib/time.js` and `lib/timeline.js`. It exports
`TASK_STATUS_GROUPS` and `groupForTaskStatus(status)`. `TeamRunDetail` is
already over 1400 lines; a pure module keeps the mapping unit-testable without
mounting the organism, and makes the same grouping reusable if another surface
needs it later.

No CSS change. `.team-task-board` auto-fits, so fewer columns widen the
remaining ones, and the mobile two-column rule still applies.

### Out of scope

- The hero `OPEN TASKS` count and its `OPEN_TASK_STATUSES` set, which counts
  `blocked` as open. It is a different measure from column placement and
  changing it would alter a number the user reads for a different purpose.
- Run and cycle level `StatusBadge`, `ArchiveView`, and `TeamPicker`.
- Backend `TaskStatus`, the API contract, and stored values.

## Part 2: Team run delete recovery

### Root cause

`TeamSpaceManager.cleanup` (`src/personal_agent_gateway/space_policies.py:259-274`)
guards the worktree removal with `working_root.exists()` but runs
`git branch -D <branch>` unconditionally whenever `worktree_branch` is set.
`_run_git` raises `ValueError` on any non-zero exit
(`space_policies.py:335-337`), `TeamRunService.delete_team_run` propagates it,
and `api/team_runs.py:911-912` maps `ValueError` to HTTP 409.

So a finished run whose worktree and branch were already removed outside the
gateway can never be deleted. Observed on run
`6357caf8a0d44134969df02f2e41a8fb` (`status=completed`, `write_mode=worktree`,
`worktree_branch=team-run/6357caf8a0d44134969df02f2e41a8fb`, repository
`.worktrees/agent`). Reproducing the exact command cleanup issues:

```
$ git -C .worktrees/agent branch -D team-run/6357caf8a0d44134969df02f2e41a8fb
error: branch 'team-run/6357caf8a0d44134969df02f2e41a8fb' not found
exit=1
```

The branch is gone, `working_root` (`…/project`) does not exist, and
`.worktrees/agent` is no longer a registered worktree — it is a plain directory
inside the main repository, so `git -C` there resolves to the main repository.
Nothing about this state is unsafe to delete; the 409 is an artifact of stale
bookkeeping.

A second defect hides the first: `api.deleteTeamRun`
(`frontend/src/api/client.js:519-522`) returns only `response.ok`, discarding
the 409 detail, so `useTeamRunController.js:573-576` can only toast
`Failed to delete team run`. The reason never reaches the screen.

### Fix rule: absence skips, refusal still fails

Cleanup treats *missing* worktree state as already cleaned and *present but
undeletable* state as an error:

- If the policy repository is not a git repository, skip worktree and branch
  cleanup entirely and remove `run_root`. There is no repository to leak into.
- Remove the worktree only when `working_root` exists (unchanged behavior).
- Delete the branch only when it resolves — checked with
  `git rev-parse --verify --quiet refs/heads/<branch>`. If it does not resolve,
  skip.
- If a branch or worktree that does exist fails to be removed, keep raising.
  Swallowing that would leak branches into the user's repository silently,
  which is the failure mode this rule is specifically shaped to avoid.

`run_root` removal is unchanged and still runs last.

### Surfacing the refusal

`api.deleteTeamRun` returns `{ ok, status, detail }`, and
`handleDeleteTeamRun` toasts the server detail when present, falling back to
`Failed to delete team run`. This keeps the remaining legitimate 409 (running
runs cannot be deleted) and any future refusal legible instead of anonymous.

Only `deleteTeamRun` changes. Other boolean-returning delete helpers in
`client.js` are left alone; broadening the signature change is unrelated to
this defect.

### Out of scope

- `shutil.rmtree` failures on Windows file locks. No evidence of that failure
  mode here, and inventing retry logic for an unobserved error would be
  speculative. The fix above surfaces the message if it ever occurs.
- The orphaned workspace directory `data/workspace/c7428e97d19d4bb8bcd609f91adaf35b`,
  which has no `team_runs` row. Noted, not deleted, and not this change's
  concern.

## Verification

- New `frontend/src/lib/taskStatusGroups.test.js`: all nine states map to the
  table above, and an unmapped state maps to `unresolved`.
- `TeamRunDetail.test.jsx`: the board renders exactly four columns with the
  four headers; `skipped` lands in `COMPLETED`; `blocked`, `failed`, and
  `canceled` land in `UNRESOLVED`.
- New cleanup tests over a temporary git repository: a missing branch is
  skipped, an existing branch is deleted, an existing branch whose deletion
  fails still raises, and a non-repository policy path skips git work.
- `tests/test_api_team_runs.py`: deleting a worktree-policy run whose branch is
  already gone returns success and removes the row.
- `client.test.js` and `useTeamRunController.test.jsx`: a 409 detail reaches
  the toast.
- Full backend and frontend suites, judged against the recorded pre-change
  baseline, then `npm run build:frontend` so the served `frontend_dist` matches
  source. That bundle is gitignored rather than committed, so the rebuild is for
  live verification only.
