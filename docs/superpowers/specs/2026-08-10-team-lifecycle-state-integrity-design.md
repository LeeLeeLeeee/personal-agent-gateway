# Team lifecycle state integrity

## Goal

Make Team run state represent one unambiguous fact at a time, keep a cycle
resumable across user-input and process-restart boundaries, and prevent an
unfinished run from being reported as terminal `blocked` merely because the
runtime cannot find executable work in one loop iteration.

This design applies to transitions created after deployment. It does not
rewrite historical runs or cycles.

## Problem

The current lifecycle uses `blocked` for three different meanings:

1. a task reached a terminal domain or execution blocker;
2. a task is temporarily waiting for a user decision;
3. a dependent task will not execute because a prerequisite failed.

Those meanings have different recovery rules but share one stored value. The
result is that code cannot tell whether to resume, propagate, cancel, or settle
the state without reconstructing intent from a decision request and the task
graph.

State ownership is also split. For example, publishing a decision request
updates the request and run in `TeamRunService`, then `TeamRuntime` updates the
cycle separately. Answering updates the request, tasks, agents, run, and cycle,
but cycle-series state is maintained elsewhere. A process failure between
those steps can leave projections that disagree.

Finally, terminal-state sets and settlement rules are duplicated in the
runtime, dispatcher, and cycle service. `_terminal_status()` currently falls
back to `blocked` for states that are simply incomplete. That makes a temporary
absence of ready work indistinguishable from a real terminal blocker.

## Scope

This change covers:

- explicit task states for user input and dependency non-execution;
- one shared lifecycle policy for legal transitions and terminal predicates;
- atomic user-decision publish and answer commands;
- dependency propagation and terminal outcome calculation;
- startup reconciliation for partial active states;
- API and Team run detail visibility for the new states.

It does not introduce event sourcing, a new workflow engine, new database
tables, or historical data backfills. Existing operation-ledger behavior stays
the source of truth for model-call recovery.

## State model

### Task states

| State | Meaning | Terminal | Resumable in place |
| --- | --- | --- | --- |
| `pending` | eligible after dependencies complete | no | yes |
| `in_progress` | worker owns execution | no | yes through operation recovery |
| `waiting_for_user` | execution is suspended on a linked decision item | no | yes |
| `waiting_for_provider` | execution is suspended on a recoverable provider operation | no | yes |
| `completed` | required output accepted | yes | no |
| `skipped` | task will not run because a prerequisite ended unsuccessfully | yes | no |
| `blocked` | task reached a terminal domain blocker that this cycle cannot resolve | yes | no |
| `failed` | task reached a terminal execution or validation failure | yes | no |
| `canceled` | execution was explicitly canceled | yes | no |

`blocked` remains a terminal task result. HIL code must never write it.
Dependency propagation writes `skipped`, not `blocked`.

Run and cycle states retain their current names. `waiting_for_user` and
`waiting_for_provider` are nonterminal; `blocked` is terminal. `interrupted`
is resumable but inactive, and is not a success or failure result.

### Transition rules

The lifecycle policy permits only these task transitions in normal execution:

```text
pending -> in_progress | skipped | canceled
in_progress -> waiting_for_user | waiting_for_provider
             | completed | blocked | failed | canceled
waiting_for_user -> pending | canceled
waiting_for_provider -> in_progress | failed | canceled
```

Operation replay may confirm an already-applied target state but may not invent
a second transition. Retries create or requeue work through the existing retry
flow; they do not mutate a terminal `skipped`, `blocked`, or `failed` history
row back to active.

## Shared lifecycle policy

Add `src/personal_agent_gateway/team_lifecycle.py` as a pure module containing:

- typed status aliases and frozen terminal/active/waiting sets;
- `can_transition(entity, source, target)` and transition validation;
- `task_dependency_disposition(tasks, dependencies)`;
- `cycle_execution_disposition(tasks, active_decision, open_operation)`;
- terminal run/cycle outcome resolution.

`teams.py`, `team_runtime.py`, `team_cycles.py`, and
`team_cycle_dispatcher.py` import these definitions. They may not keep private
copies of terminal status sets or use a literal fallback terminal status.

The module decides policy only. It does not access SQLite, emit events, or
schedule work.

## Transactional command ownership

`TeamRunService` remains the database owner for run, cycle, task, agent, and
decision-request lifecycle changes. Its HIL commands also update the linked
auto-series because those rows share the same SQLite transaction and must not
cross a consistency boundary. `TeamCycleService` remains responsible for
creating, claiming, and normally settling cycle requests and series.
Cross-row lifecycle operations become named commands instead of a series of
public `set_*_status` calls.

Every command starts `begin immediate`, validates its expected source states,
updates all affected rows, and commits once. Updates use status predicates in
SQL so a stale caller affects zero rows and receives a conflict instead of
silently overwriting newer state.

Public orchestration paths stop using unrestricted status setters. Low-level
setters can remain temporarily for unrelated legacy flows, but the new HIL,
dependency, and settlement paths use validated commands only.

### Publish a user decision

`publish_decision_request(team_run_id, cycle_id)` atomically:

1. validates one non-empty `collecting` request for the active cycle;
2. verifies every blocking task is `waiting_for_user`;
3. changes the request to `awaiting_user`;
4. changes the run and cycle to `waiting_for_user`;
5. changes only agents owning blocking tasks to `waiting`;
6. changes the linked active auto-series to `paused_user` with this cycle as
   `paused_cycle_id`;
7. writes the `user_decision_requested` message.

`TeamRuntime._publish_user_decision_request` no longer writes cycle state after
this command.

When a question is created, the operation-effect transaction changes its
blocking task from `in_progress` to `waiting_for_user`. Run-level questions
without a blocking task identify the leader explicitly so the service does not
put unrelated provider-waiting agents into the HIL state.

The dispatcher no longer performs a second `pause_for_user` write after
publish. The single command makes run, cycle, request, task, agent, and series
state visible together.

### Answer a user decision

`answer_decision_request(...)` atomically:

1. validates run `waiting_for_user`, cycle `waiting_for_user`, request
   `awaiting_user`, and the submitted revision;
2. stores all answers and resolves the request;
3. changes only linked `waiting_for_user` tasks to `pending`;
4. resets only the agents associated with those tasks, plus an explicitly
   recorded leader for a run-level question;
5. changes run and cycle to `running`;
6. changes the linked `paused_user` auto-series back to `running`, clearing its
   pause reason and paused cycle ID;
7. writes the answer messages.

The API schedules `dispatcher.resume()` after commit. If the process stops
after commit but before scheduling, startup reconciliation detects a stale
`running` cycle with no live worker, changes it to `interrupted`, and exposes a
normal resume action. It must not report the cycle as `blocked`.

For pre-deployment active requests only, the answer/cancel command may accept a
linked `blocked` task as a narrow compatibility case. Linkage through the
specific active decision request is required; no unlinked `blocked` task is
resumed. New writes always use `waiting_for_user`.

### Cancel a user decision

Canceling an awaiting request changes only its linked `waiting_for_user` tasks
to `canceled`. It must not cancel every `blocked` task in the run. Run, cycle,
request, and linked-agent state change in the same transaction.

## Dependency scheduling

For each `pending` task:

- all prerequisites `completed`: the task is ready;
- any prerequisite terminal and not `completed`: change the task to `skipped`
  with stable reason `skipped_by_dependency`;
- otherwise: leave it `pending`.

Propagation runs to a fixed point so a skipped task causes its dependents to be
skipped in the same scheduling pass. The dependency graph remains the source
for identifying the root prerequisite; no new reason column or duplicated
dependency-ID payload is added.

Waiting prerequisites do not propagate. In particular, a task waiting for user
input leaves all dependents `pending`, and the scheduler reports the cycle as
waiting rather than terminal.

## Settlement

Settlement is a total policy function with an explicit incomplete result:

```text
active decision exists                         -> waiting_for_user
recoverable provider operation exists          -> waiting_for_provider
any task is pending/in_progress/waiting         -> incomplete (no settlement)
required failed task or failed root dependency  -> failed
required blocked task or blocked root dependency-> blocked
explicit cycle cancellation                     -> canceled
all required tasks completed, optional issue    -> completed_with_failures
all required tasks completed, no optional issue -> completed
```

A required `skipped` task resolves its root dependency cause. A failed root
produces `failed`; a blocked root produces `blocked`. A required task can never
be considered satisfied merely because it was skipped.

The runtime continues looping only while the disposition is incomplete and
there is either ready work or a recoverable wait. If the graph is incomplete
with no ready work and no recognized wait, it raises a lifecycle-integrity
error that records the unresolved task IDs and dependency states. It does not
default to `blocked`.

The cycle service and dispatcher use the same terminal predicates from
`team_lifecycle.py`, so request settlement and UI state cannot disagree about
whether a cycle has ended.

## Startup reconciliation

Reconciliation applies these deterministic rules before accepting new Team run
work:

1. A cycle with an open recoverable provider operation follows the existing
   operation-ledger recovery path.
2. An `awaiting_user` request projects run and cycle to `waiting_for_user` and
   its linked tasks to `waiting_for_user`; its auto-series projects to
   `paused_user`.
3. A non-empty `collecting` request whose linked tasks already wait for user is
   published locally. No model call is repeated.
4. A `running` run/cycle with no live registry worker and no open provider
   operation becomes `interrupted`.
5. A terminal cycle settles its dispatching request through the existing
   idempotent cycle-settlement transaction.

Each repair is idempotent. Re-running startup reconciliation produces no
additional messages or status changes after consistency is restored.

## API and UI

The API exposes stored status values without translating `waiting_for_user` or
`skipped` into `blocked`. Each task payload gains an additive
`depends_on_task_ids` field populated from the existing dependency table; no
existing field changes meaning.

The Team run detail task board includes the missing states:

- `WAITING FOR USER`
- `WAITING FOR PROVIDER`
- `SKIPPED`
- `CANCELED`

The badges remain explicit even if the board groups both waiting states or
terminal issue states for layout. A `skipped_by_dependency` card shows the
blocking prerequisite titles derived from `depends_on_task_ids` and the task
list already present in the detail response.

Run and cycle badges continue to show `INPUT NEEDED` for `waiting_for_user`.
The detail action area shows the answer form only when the active request and
run/cycle state agree. A mismatch shows a lifecycle-integrity diagnostic and a
resume/reload action instead of pretending the run is active.

## Compatibility and rollout

No schema migration is required because status columns are text and all new
meaning is represented by existing rows and relationships.

Rollout order:

1. ship shared policy and tests without changing writes;
2. change HIL writes and transactional commands;
3. change dependency propagation and settlement;
4. add reconciliation rules;
5. expose the new task states in the UI;
6. remove duplicated terminal constants after every caller imports the policy.

Historical rows remain untouched. Legacy active decision requests receive only
the linked-task compatibility behavior described above.

## Error handling and observability

Lifecycle conflicts are reported with entity ID, expected source status, and
actual status. Integrity errors include the cycle ID and unresolved task IDs,
but not prompt or answer content.

Existing domain messages and audit events remain the external history. Add
structured lifecycle fields to logs for:

- transition command name;
- run and cycle IDs;
- source and target statuses;
- stale-transition rejection;
- reconciliation rule applied;
- unresolved dependency root IDs.

No second event store is introduced.

## Verification

### Pure policy tests

- every allowed and rejected task transition;
- terminal and resumable status sets;
- dependency disposition for complete, waiting, failed, blocked, canceled, and
  transitively skipped prerequisites;
- settlement returns incomplete instead of defaulting to `blocked`;
- required skipped tasks resolve to their root failure class.

### Service transaction tests

- publish changes request, linked tasks/agents, run, cycle, and message together;
- any invalid source state leaves every row unchanged;
- answer requeues only linked waiting tasks and rejects stale revisions;
- cancel does not mutate unrelated blocked tasks;
- legacy linked blocked tasks are accepted only for a pre-deployment active
  decision request;
- repeated publish, answer, cancel, and reconciliation calls are idempotent or
  return a conflict without partial writes.

### Runtime and dispatcher tests

- user question -> wait -> answer -> same cycle resumes -> dependent executes;
- a waiting prerequisite never skips its dependent;
- failed prerequisite skips its dependency chain and settles by root cause;
- process restart after publish restores `waiting_for_user` and `paused_user`;
- process restart after answer commit but before scheduling exposes
  `interrupted`, not `running` or `blocked`;
- dispatcher and cycle service agree on every terminal cycle status.

### Frontend tests

- all task statuses render in the task board;
- `waiting_for_user` visibly renders `INPUT NEEDED`/`WAITING FOR USER`;
- skipped cards show dependency context;
- the answer form is hidden for inconsistent or stale request state.

The critical end-to-end regression is a two-task chain where task A asks the
user, task B depends on A, the process restarts while waiting, the user answers,
and both tasks complete in the original cycle without any intermediate
`blocked` or `skipped` write.
