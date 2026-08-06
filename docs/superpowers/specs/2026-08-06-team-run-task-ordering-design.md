# Deterministic Team run task ordering, persona cycle reset, and in-progress visibility

> **IMPLEMENTED** — merged to `main` at `b86eab7` on 2026-08-06.
> Section C's design was wrong and was corrected during implementation; section A's ordering
> clause was also revised. **The shipped code is the source of truth.** The full errata live in
> `docs/superpowers/plans/2026-08-06-team-run-task-ordering.md`.

## Goal

Make a Team run cycle execute its tasks in the order the leader planned, stop a
worker's honest "I am blocked" report from killing the run, give each new cycle
a clean persona lane, and let the Team run detail screen show what a running
agent is working on.

## Motivating incident

Team run `eec591b4b2f84444a86627b5806a02b9`, cycle `d16e7748da31481ba64b6bf493d9fb66`
failed with `Required task failed`. The cycle planned two tasks in the correct
order — fix the remaining P3 findings, then re-verify them. Execution ran them
backwards. QA started at 04:07:12 and found the draft byte-identical to the
previous round, correctly refused to certify it, and reported
`status: blocked, reason_code: draft-unmodified`. The fix task then ran at
04:10:25 and succeeded, but the run was already terminal.

Reproduced experimentally against the real code path, 60 trials:

```
plan-order-first:            36/60   plan order held only by chance
uuid-sort predicts ready[0]: 60/60   uuid sort fully determined execution
```

## Root cause

Three defects compound.

**A. Plan order is discarded and replaced by UUID sort.** `apply_plan`
(`team_model_effects.py:106`) computes `now = _now()` once and stamps every task
in the plan with it, so `created_at` ties are structurally guaranteed, not a
clock-resolution accident. `list_dependency_ready_tasks` (`teams.py:1867`)
orders by `created_at asc, id asc`, and `_execute` (`team_runtime.py:1269`)
takes `ready_tasks[0]`. The tiebreaker `id asc` is a random `uuid4().hex`, so
the uuid is the real scheduler. The leader's array order is recorded nowhere.

**B. Dependencies cannot be declared.** The stored planning output for
operation `667c4a74` carries `"plan_task_id": null, "depends_on_task_ids": []`
on both tasks. `plan_task_id` is optional (`team_runtime.py:3691`), and
declaring a dependency requires it (`:3725`), so a leader that omits
`plan_task_id` is structurally barred from expressing order. Zero rows exist in
`team_task_dependencies` for the entire run. The persistence path
(`_persist_plan_dependencies`, `team_model_effects.py:2140`) is correct and
unused.

**C. A worker-declared `blocked` is recorded as a hard `failed`.**
`_apply_task_outcome` (`team_model_effects.py:1745-1758`) routes to leader
review only when `is_recoverable_acceptance_failure(reason_code)` matches a
six-entry allowlist; everything else writes a literal `status = 'failed'`,
discarding `acceptance.status`. There is an inversion here:
`team_acceptance.py:56` substitutes `task_not_completed` when a worker gives no
reason code, and that substitute *is* in the allowlist. A worker that says
"blocked" vaguely gets leader review. A worker that says
`blocked / draft-unmodified` gets hard-failed. Being specific is punished.

## Scope

This change covers deterministic task ordering, planner-declared dependencies,
worker-declared outcome handling, per-cycle persona status reset, and an
elapsed-time indicator on the Team run detail screen.

It does not make `blocked` cycles resumable, does not stream live agent output,
does not infer dependency edges from acceptance contracts, and does not touch
the non-cycle legacy planning path beyond the ordering fix that path inherits.

## A. Plan ordinal

Add `team_tasks.plan_ordinal integer` in migration 28.

| Location | Change |
| --- | --- |
| `migrations.py` | Add column; backfill existing rows by `rowid` rank within `(team_run_id, cycle_id)`. `apply_plan` inserts in plan-array order, so `rowid` already encodes the intended order. |
| `teams.py:198` `TeamTask` | Add `plan_ordinal: int = 0`; map it in `_team_task_from_row`. |
| `team_model_effects.py:2045` `_create_task` | Accept an `ordinal` argument and persist the task's index within the plan array. |
| `teams.py:1733` `create_task` | Default to `max(plan_ordinal) + 1` for the same `(team_run_id, cycle_id)`. `cycle_id` may be NULL, so compare with `is`. |
| `teams.py:1867` `list_dependency_ready_tasks` | `order by plan_ordinal asc, created_at asc, id asc` — ⚠️ WRONG, see below |
| `teams.py:1792` `list_tasks` | Same ordering, so the board and `_collect_outputs` follow plan order too. |

> **⚠️ The ordering clause above is wrong.** Shipped code uses
> `created_at asc, plan_ordinal asc, id asc`, and applies it to
> `block_pending_dependency_failures` as well. `plan_ordinal` restarts at 0 for every plan and
> every cycle, so sorting on it first lets an `add_work` plan preempt pending planned tasks —
> the same inversion class this design exists to fix — and makes run-wide `list_tasks` interleave
> cycles, which breaks the API's `tasks[-limit:]` truncation. See errata E2 and E3 in the plan.

With `max_workers = 1`, `ready_tasks[0]` becomes the lowest pending ordinal, so
a cycle executes in the order the leader wrote.

`rowid asc` as a tiebreaker would fix the incident with no migration, but
`rowid` is a SQLite implementation detail and a scheduling contract should be
explicit. It is acceptable only as an emergency hotfix.

## B. Planner-declared dependencies

- `team_runtime.py:3691, 3711` — promote `plan_task_id` to a required field. A
  plan missing it raises `ValueError`, which the existing
  `_planning_repair_messages` retry path already handles.
- `PLANNING_PROMPT` (`team_runtime.py:86-105`) — state that a task which reads
  or verifies another task's `required_outputs` must list that task in
  `depends_on_task_ids`.

Once edges exist, `block_pending_dependency_failures`
(`team_runtime.py:2637`) becomes effective: when a prerequisite fails, its
dependents are marked `blocked_by_dependency` instead of running against stale
inputs.

## C. Worker-declared outcomes

> **⚠️ This section's design is WRONG and was corrected during implementation.** Shipped in
> `main` at `b86eab7`; see errata E8 and E9 in
> `docs/superpowers/plans/2026-08-06-team-run-task-ordering.md`.
>
> `outcome.status != "completed"` is not a valid signal for "the worker declared this".
> `TeamRuntime._task_outcome` **synthesizes** `status="blocked", reason_code="invalid_task_outcome"`
> when a worker's response fails to parse — a server-detected failure the worker never declared.
> Treating it as worker-declared turns a dead run into one that looks like it is waiting for
> something that will never come. It surfaced as a regression in
> `test_worker_prose_cannot_complete_team_run`.
>
> Shipped code introduces `is_worker_declared_outcome()` and `terminal_rejected_status()` in
> `team_acceptance.py`, and routes all four call sites — ledger apply, ledger replay, legacy gate,
> legacy terminal — through them so the rule cannot drift between paths.
>
> This section also names only `team_runtime.py:1429` for the legacy path. That changes the
> terminal status but not the gate that decides whether the legacy flow reaches leader review at
> all, which is `team_runtime.py:2231` in `_recover_task_outcome`. Both needed the signal.

```python
# team_acceptance.py
def is_recoverable_acceptance_failure(
    reason_code: str | None, *, worker_declared: bool = False
) -> bool:
    return worker_declared or reason_code in RECOVERABLE_ACCEPTANCE_REASONS
```

- Call sites `team_model_effects.py:1746` and `team_runtime.py:1429` pass
  `worker_declared=outcome.status != "completed"`.
- `team_model_effects.py:1751` — replace the literal `'failed'` with
  `acceptance.status`, so a `blocked` outcome that exhausts the recovery cap
  lands as `blocked` rather than `failed`.
- Server-detected infrastructure failures (`artifact_publication_failed`) keep
  hard-failing.

The rule: **an outcome the worker declared goes to leader review; a failure the
server detected ends the task.**

`_expected_worker_state` (`team_model_effects.py:1880-1902`) validates task and
agent state when replaying an applied operation. Changing the terminal status
written requires updating those expectations in the same step.

## D. Persona reset on a new cycle

`_activate_cycle` (`team_runtime.py:3087`) currently resets only
`reinvocations` when a cycle moves `queued -> running`, so persona lanes carry
the previous cycle's badges. Run `eec591b4` presently shows a mix of `failed`,
`completed`, and `pending` agents from cycle 5.

Add a `teams.py` service method called from the same `queued` branch:

```sql
update team_agents
set status = 'pending', current_task_id = null, finished_at = null, updated_at = ?
where team_run_id = ? and status in ('completed', 'failed', 'canceled')
```

Only terminal statuses reset. `running` and `waiting` agents are left alone so
that operation replay guards (`team_model_effects.py:189, 253, 1147, 1902`),
which assert specific agent states, keep holding. The leader is included: it is
driven to `completed` or `failed` at cycle end and otherwise carries a stale
badge into the next cycle.

`_activate_cycle` is the safe point because no operation for the new cycle has
been reserved yet.

## E. In-progress visibility

`task.started_at` is already exposed by `_task_payload`
(`api/team_runs.py:1295`), so this is a frontend-only change.

`currentWork()` (`TeamRunDetail/index.jsx:101`) returns only a title today.

```
+-- 테크 리드            [RUNNING] LIVE --+
|  잔여 P3 7건 수정: 미해소 3건…          |
|  3분 12초 경과                          |
+-----------------------------------------+
```

- Split `currentWork` into a pure function returning `{ title, startedAt }`,
  preserving its existing leader fallbacks (`Planning tasks`,
  `Coordinating agents`, `Summarizing results`, `No active task`), which carry
  no elapsed time.
- Add a pure elapsed-time formatter (`3분 12초`, `1시간 4분`).
- Drive a one-second tick only while `agents.some(a => a.status === "running")`,
  so an idle detail screen does not re-render.

There is no intermediate progress signal to show: `agent_output` messages are
written only when a task finishes. Elapsed time against `started_at` is the
whole of what the data supports without new plumbing.

## Testing

| Target | Check |
| --- | --- |
| A | After `apply_plan`, `ready[0]` is the first task of the plan array — asserted over repeated trials, not once |
| A | Three-stage chain (research -> draft -> QA) executes in plan order |
| B | A plan without `plan_task_id` is rejected; a failed prerequisite leaves its dependent `blocked_by_dependency` |
| C | A worker `blocked` with an unregistered reason code routes to `acceptance_lead` and is not written as `failed` |
| C | Exhausting the recovery cap on a `blocked` outcome records `blocked` |
| D | After cycle activation, terminal agents are `pending` while `running` and `waiting` agents are untouched |
| E | `currentWork` and the elapsed formatter, as pure unit tests |

The repeated-trial assertion for A is load-bearing. A single-shot assertion
passes roughly 60% of the time against the unfixed code and would not catch a
regression.

## Sequencing

Five independent commits, one per section, in order **A -> D -> E -> B -> C**.

A is the root cause and comes first. D and E are independent of A and of each
other. B changes leader output and benefits from A already being in place. C is
last because it also adjusts replay expectations.

Completion is judged by delta against baseline: `main` already carries roughly
32 backend test failures and 227 ruff findings, so the bar is that nothing new
breaks.
