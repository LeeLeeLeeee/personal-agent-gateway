# Team Run Acceptance Internal Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route recoverable Team Task acceptance failures through Lead review and bounded Worker resubmission instead of immediately failing the Team Run.

**Architecture:** Keep the existing strict `TeamAcceptanceService` as the final gate. Add a task-scoped recovery counter and atomic audit record to `TeamRunService`, then let `TeamRuntime` ask the Lead for one of four structured decisions: retry the Worker, revise acceptance and retry, ask the user, or fail. Reuse the existing `in_progress` status and team message API so internal recovery remains visible only in Task details.

**Tech Stack:** Python 3.12, asyncio, dataclasses, SQLite migrations, pytest, FastAPI, React 19, Vitest, Testing Library, Vite.

## Global Constraints

- Recoverable acceptance work gets at most `2` Lead/Worker correction attempts per Task.
- Recovery attempts are independent of `rounds_used` and Agent `reinvocations`.
- Internal recovery keeps Task=`in_progress` and Run/Cycle=`running`; do not add a `reviewing` status.
- Lead may revise acceptance but may not loosen SPACE policy or frozen rules.
- Every revised acceptance must have at least one output or verification, contain no duplicates, and use bounded relative output paths.
- Revised acceptance never retroactively approves the previous Worker outcome; the Worker must resubmit.
- `artifact_publication_failed`, `input_snapshot_modified`, model/process errors, and unexpected Python exceptions do not enter this recovery loop.
- Internal review is not an Overview error; it is visible only in the selected Task's detail activity.
- Run only directly related backend tests, the exact frontend component test, and the frontend production build. Do not run the full test suite without a separate decision.

---

## File Map

| File | Responsibility in this change |
| --- | --- |
| `src/personal_agent_gateway/migrations.py` | Schema v19 migration for the task-scoped recovery counter |
| `src/personal_agent_gateway/teams.py` | Persist the counter, revised acceptance, and `acceptance_review` message atomically |
| `src/personal_agent_gateway/team_runtime.py` | Lead review protocol, response parser, bounded recovery loop, Worker resubmission |
| `src/personal_agent_gateway/team_acceptance.py` | Preserve strict acceptance; expose recoverable reason classification without weakening checks |
| `src/personal_agent_gateway/api/team_runs.py` | Include `acceptance_recovery_attempts` in task payloads |
| `frontend/src/components/organisms/TeamRunDetail/index.jsx` | Group and render per-Task internal review history |
| `src/personal_agent_gateway/static/styles.css` | Minimal styling for internal review entries |
| `src/personal_agent_gateway/frontend_dist/**` | Production frontend bundle generated from the final source state |
| `tests/test_migrations.py` | Migration default and idempotence |
| `tests/test_teams.py` | Atomic recovery persistence and counter behavior |
| `tests/test_team_acceptance.py` | Recoverable/nonrecoverable reason classification |
| `tests/test_team_runtime.py` | Lead decisions, resubmission, limits, state preservation, cleanup |
| `tests/test_api_team_runs.py` | Task payload exposes the recovery counter |
| `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx` | Task-only review history and no Overview error |

---

### Task 1: Persist Task-Scoped Recovery State and Audit Records

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py:658-684`
- Modify: `src/personal_agent_gateway/teams.py:141-159`
- Modify: `src/personal_agent_gateway/teams.py:1006-1050`
- Modify: `src/personal_agent_gateway/teams.py:2207-2248`
- Test: `tests/test_migrations.py`
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces: `ACCEPTANCE_RECOVERY_CAP = 2` in `teams.py` as the single source of truth
- Produces: `TeamTask.acceptance_recovery_attempts: int`
- Produces:

```python
def record_acceptance_review(
    self,
    task_id: str,
    leader_agent_id: str,
    worker_agent_id: str,
    *,
    action: Literal["retry_worker", "revise_acceptance", "ask_user", "fail"],
    reason_code: str,
    reason: str,
    instruction: str | None,
    acceptance_after: TaskAcceptance | None,
    rejected_deliverables: tuple[str, ...],
    rejected_verifications: tuple[str, ...],
) -> TeamTask:
    ...
```

- `retry_worker` and `revise_acceptance` increment the counter; `ask_user` and `fail` only write the audit record.
- `acceptance_after` is required only for `revise_acceptance`; other actions preserve the current contract.
- Later tasks consume the updated `TeamTask` and the `acceptance_review` message.

- [ ] **Step 1: Write the migration test**

Add a focused test to `tests/test_migrations.py`:

```python
def test_migration_19_adds_acceptance_recovery_counter_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table team_tasks (id text primary key)")
    connection.execute("insert into team_tasks values ('task-1')")

    _migration_19_team_acceptance_recovery(connection)
    _migration_19_team_acceptance_recovery(connection)

    row = connection.execute(
        "select acceptance_recovery_attempts from team_tasks where id = 'task-1'"
    ).fetchone()
    assert row["acceptance_recovery_attempts"] == 0
```

Import `_migration_19_team_acceptance_recovery` in the existing migration test import block.

- [ ] **Step 2: Run the migration test and verify it fails**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_19_adds_acceptance_recovery_counter_idempotently -q
```

Expected: FAIL because migration 19 does not exist.

- [ ] **Step 3: Add schema migration 19**

Add:

```python
def _migration_19_team_acceptance_recovery(
    connection: sqlite3.Connection,
) -> None:
    if "acceptance_recovery_attempts" not in _columns(connection, "team_tasks"):
        connection.execute(
            "alter table team_tasks add column "
            "acceptance_recovery_attempts integer not null default 0"
        )
```

Append this exact migration entry:

```python
(19, "team-acceptance-recovery", _migration_19_team_acceptance_recovery),
```

- [ ] **Step 4: Run the migration test and verify it passes**

Run the same focused command. Expected: PASS.

- [ ] **Step 5: Write service tests for atomic review persistence**

Add two tests to `tests/test_teams.py`.

The first creates an in-progress Task with acceptance `TaskAcceptance((), ("source-check",))`,
calls `record_acceptance_review(... action="retry_worker" ...)`, and asserts:

```python
assert updated.status == "in_progress"
assert updated.acceptance_recovery_attempts == 1
assert updated.acceptance == TaskAcceptance((), ("source-check",))
review = teams.list_messages(run.id)[-1]
assert review.kind == "acceptance_review"
assert review.sender_agent_id == leader_agent.id
assert review.recipient_agent_id == worker_agent.id
assert review.metadata["task_id"] == task.id
assert review.metadata["attempt"] == 1
assert review.metadata["action"] == "retry_worker"
assert review.metadata["reason_code"] == "undeclared_deliverable"
```

The second calls `action="revise_acceptance"` with:

```python
acceptance_after=TaskAcceptance(
    ("docs/knowledge/d3-review.md",),
    ("source-check",),
)
```

and asserts the counter, updated acceptance, `acceptance_before`, and
`acceptance_after` were committed together. Also call `ask_user` afterward and assert the
counter remains unchanged.

- [ ] **Step 6: Run the service tests and verify they fail**

Run:

```powershell
pytest tests/test_teams.py -q -k "acceptance_review"
```

Expected: FAIL because the field and service method do not exist.

- [ ] **Step 7: Implement the TeamTask field and atomic service method**

Add to `TeamTask`:

```python
acceptance_recovery_attempts: int = 0
```

Hydrate it in `_team_task_from_row`:

```python
acceptance_recovery_attempts=(
    int(row["acceptance_recovery_attempts"])
    if "acceptance_recovery_attempts" in row.keys()
    else 0
),
```

`create_task` can rely on the database default. Implement `record_acceptance_review` with
`begin immediate` and do all of the following inside that transaction:

1. Load the Task and verify both agents belong to the same Team Run.
2. Require Task status `in_progress`.
3. For `retry_worker` and `revise_acceptance`, increment
   `acceptance_recovery_attempts`.
   Reject the transaction with `ValueError("Acceptance recovery limit reached")` when the
   current value is already `ACCEPTANCE_RECOVERY_CAP`.
4. For `revise_acceptance`, replace `acceptance_json` with
   `_task_acceptance_json(acceptance_after)`. Require `acceptance_after` for this action
   and reject it for the other three actions.
5. Insert one `team_messages` row with kind `acceptance_review`.
6. Store this metadata shape:

```python
{
    "task_id": task_id,
    "attempt": current_attempts + 1,
    "reason_code": reason_code,
    "action": action,
    "reason": reason,
    "instruction": instruction,
    "acceptance_before": json.loads(task["acceptance_json"]),
    "acceptance_after": (
        json.loads(_task_acceptance_json(acceptance_after))
        if acceptance_after is not None
        else None
    ),
    "rejected_deliverables": list(rejected_deliverables),
    "rejected_verifications": list(rejected_verifications),
}
```

Use the Lead as sender and Worker as recipient. Use `instruction or reason` as message
content. For non-consuming `ask_user` and `fail` actions, metadata still describes the next
attempt number while the persisted counter remains unchanged.

- [ ] **Step 8: Run the focused persistence tests**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_19_adds_acceptance_recovery_counter_idempotently tests/test_teams.py -q -k "migration_19 or acceptance_review"
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_migrations.py tests/test_teams.py
git commit -m "feat(team): acceptance 복구 상태 저장"
```

---

### Task 2: Define and Validate the Lead Review Protocol

**Files:**
- Modify: `src/personal_agent_gateway/team_acceptance.py`
- Modify: `src/personal_agent_gateway/team_runtime.py:94-123`
- Modify: `src/personal_agent_gateway/team_runtime.py:1190-1260`
- Test: `tests/test_team_acceptance.py`
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `ACCEPTANCE_RECOVERY_CAP` from `teams.py`
- Produces:

```python
RECOVERABLE_ACCEPTANCE_REASONS = frozenset({
    "undeclared_deliverable",
    "required_output_missing",
    "unsafe_deliverable",
    "required_verification_failed",
    "task_not_completed",
    "invalid_task_outcome",
})
```

- Produces:

```python
@dataclass(frozen=True)
class AcceptanceReviewResolution:
    kind: Literal["retry_worker", "revise_acceptance", "ask_user", "fail"]
    reason: str
    instruction: str | None = None
    acceptance: TaskAcceptance | None = None
    decision: dict[str, object] | None = None
    reason_code: str | None = None
```

- Produces:

```python
def _parse_acceptance_review_resolution(
    content: str,
) -> AcceptanceReviewResolution:
    ...
```

- Later tasks use this parser and constant without changing their names.

- [ ] **Step 1: Write acceptance classification tests**

Add to `tests/test_team_acceptance.py`:

```python
@pytest.mark.parametrize(
    "reason_code",
    [
        "undeclared_deliverable",
        "required_output_missing",
        "unsafe_deliverable",
        "required_verification_failed",
        "task_not_completed",
        "invalid_task_outcome",
    ],
)
def test_recoverable_acceptance_reason_codes(reason_code: str) -> None:
    assert is_recoverable_acceptance_failure(reason_code)


@pytest.mark.parametrize(
    "reason_code",
    ["input_snapshot_modified", "artifact_publication_failed", "model_failed"],
)
def test_infrastructure_acceptance_failures_are_not_recoverable(
    reason_code: str,
) -> None:
    assert not is_recoverable_acceptance_failure(reason_code)
```

- [ ] **Step 2: Run the classification tests and verify they fail**

Run:

```powershell
pytest tests/test_team_acceptance.py -q -k "recoverable_acceptance or infrastructure_acceptance"
```

Expected: FAIL because the classifier does not exist.

- [ ] **Step 3: Add the explicit recoverable reason classifier**

Add `RECOVERABLE_ACCEPTANCE_REASONS` and:

```python
def is_recoverable_acceptance_failure(reason_code: str | None) -> bool:
    return reason_code in RECOVERABLE_ACCEPTANCE_REASONS
```

Do not alter `TeamAcceptanceService.evaluate` ordering or weaken any existing rejection.

- [ ] **Step 4: Write parser tests for all four Lead decisions**

In `tests/test_team_runtime.py`, add focused tests that parse:

```json
{"resolution":{"kind":"retry_worker","instruction":"Remove the undeclared deliverable and resubmit.","reason":"The contract declares no output."}}
```

```json
{"resolution":{"kind":"revise_acceptance","acceptance":{"required_outputs":["docs/knowledge/d3-review.md"],"required_verifications":["source-check"]},"instruction":"Resubmit the document under the revised contract.","reason":"The task goal requires a reusable draft."}}
```

```json
{"resolution":{"kind":"ask_user","topic":"publication scope","question":"Should this be published?","why_needed":"The goal is ambiguous.","options":[],"recommended_option_id":null,"blocking_scope":"task"}}
```

```json
{"resolution":{"kind":"fail","reason_code":"unrecoverable_contract","summary":"The request conflicts with frozen rules."}}
```

Assert the dataclass fields exactly. Add rejection cases for an empty `reason`, empty
acceptance, duplicated values, `../outside.md`, and an unknown `kind`.

- [ ] **Step 5: Run parser tests and verify they fail**

Run:

```powershell
pytest tests/test_team_runtime.py -q -k "acceptance_review_resolution"
```

Expected: FAIL because the parser and dataclass do not exist.

- [ ] **Step 6: Add the Lead prompt and strict parser**

Add `ACCEPTANCE_REVIEW_PROMPT` instructing the Lead to:

- decide only from goal, Cycle instruction, frozen rules, SPACE, Task contract, outcome,
  failure reason, changed paths, history, and remaining attempts;
- prefer Worker correction when the contract is valid;
- revise acceptance only when the contract itself is wrong;
- ask the user only for a consequential choice the Team cannot infer;
- never approve the current rejected outcome retroactively;
- return exactly one of the four JSON forms.

Implement `_parse_acceptance_review_resolution`. For `revise_acceptance`, reuse the same
acceptance validation rules as `_parse_task_plan`: non-empty strings, no duplicates, at
least one output or verification, and `_safe_relative_output` for every output. For
`ask_user`, reuse the normalized shape produced by `_parse_mediation_resolution`.
Malformed content must raise `ValueError("Invalid acceptance review resolution")`; do not
silently convert it to a Worker instruction.

- [ ] **Step 7: Run the protocol tests**

Run:

```powershell
pytest tests/test_team_acceptance.py -q -k "recoverable_acceptance or infrastructure_acceptance"
pytest tests/test_team_runtime.py -q -k "acceptance_review_resolution"
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```powershell
git add src/personal_agent_gateway/team_acceptance.py src/personal_agent_gateway/team_runtime.py tests/test_team_acceptance.py tests/test_team_runtime.py
git commit -m "feat(team): Lead acceptance 검토 프로토콜 추가"
```

---

### Task 3: Execute Bounded Lead Review and Worker Resubmission

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:420-543`
- Modify: `src/personal_agent_gateway/team_runtime.py:620-697`
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `TeamRunService.record_acceptance_review(...)`
- Consumes: `is_recoverable_acceptance_failure(reason_code)`
- Consumes: `_parse_acceptance_review_resolution(content)`
- Produces:

```python
async def _review_acceptance(
    self,
    run: TeamRun,
    leader: TeamAgent,
    worker: TeamAgent,
    task: TeamTask,
    outcome: TaskOutcome,
    acceptance: AcceptanceResult,
    working_root: Path,
    task_snapshot: WorkspaceSnapshot,
) -> AcceptanceReviewResolution:
    ...
```

- Produces:

```python
async def _recover_task_outcome(
    self,
    run: TeamRun,
    leader: TeamAgent,
    worker: TeamAgent,
    task: TeamTask,
    outcome: TaskOutcome,
    acceptance: AcceptanceResult,
    working_root: Path,
    task_snapshot: WorkspaceSnapshot,
    staged_inputs: StagedInputs | None,
) -> tuple[TeamTask, TaskOutcome, AcceptanceResult] | UserDecisionResolution:
    ...
```

- `_execute` publishes artifacts and calls `finish_task` only after this helper returns a
  terminal accepted or failed result.

- [ ] **Step 1: Write the undeclared-deliverable recovery test**

Create a plan with `required_outputs: []` and `required_verifications:
["source-check"]`. Script:

1. Worker first response declares `docs/d3.md` and passes `source-check`.
2. Lead returns `retry_worker` with an instruction to remove the file and deliverable.
3. Worker second response declares no deliverables and passes `source-check`.
4. Lead synthesis returns a summary.

The fake Worker must actually create `docs/d3.md` on its first call and delete it on its
second call so cleanup is real. Assert:

```python
assert completed.status == "completed"
assert task.status == "completed"
assert task.acceptance_recovery_attempts == 1
assert not (Path(run.working_root) / "docs/d3.md").exists()
assert [m.kind for m in teams.list_messages(run.id)].count("acceptance_review") == 1
assert task.error_message is None
```

- [ ] **Step 2: Write the acceptance-revision recovery test**

Script the Lead to return `revise_acceptance` adding `docs/d3.md`, then have the Worker
resubmit the same path and passed verification. Assert the Task's stored acceptance contains
the path, the second outcome is accepted, the artifact publisher is called once, and the
first rejected outcome is never published.

- [ ] **Step 3: Write limit, routing, and state tests**

Add focused tests for:

- two failed correction attempts result in Task/Cycle/Run `failed`, with no third Worker
  resubmission;
- update the existing `test_worker_prose_cannot_complete_team_run` fixture so the Lead
  issues two `retry_worker` decisions, the Worker returns invalid prose three times, and the
  assertions become final `failed` plus `acceptance_recovery_attempts == 2`;
- while the Lead is reviewing, Task remains `in_progress` and Run/Cycle remain `running`;
- `ask_user` delegates through `defer_task_for_user_decision` and does not increment
  `acceptance_recovery_attempts`;
- `fail` finishes immediately with the Lead's stable reason code;
- `input_snapshot_modified` and `artifact_publication_failed` never call the Lead review
  model;
- malformed Lead output gets one strict JSON retry without consuming a recovery attempt;
- a reported undeclared path that is removed from the resubmitted JSON but remains on disk
  is rejected again;
- a canceled runtime during Lead review still follows the existing `CancelledError` path.

- [ ] **Step 4: Run the runtime recovery tests and verify they fail**

Run:

```powershell
pytest tests/test_team_runtime.py -q -k "acceptance_recovery or acceptance_revision or acceptance_review"
```

Expected: FAIL because `_execute` still terminates on the first rejection.

- [ ] **Step 5: Implement Lead review generation**

`_review_acceptance` must build the prompt from current authoritative state:

```python
task = self._teams.get_task(task.id)
remaining = ACCEPTANCE_RECOVERY_CAP - task.acceptance_recovery_attempts
changes = workspace_changes(task_snapshot, workspace_snapshot(working_root))
```

Include `asdict(outcome)`, `asdict(acceptance)`, current acceptance, prior
`acceptance_review` messages for this Task, and `remaining`. Call the Lead model. On parser
failure, call the same Lead model once more with:

```text
Return ONLY one valid acceptance review JSON object. No prose or code fences.
```

Persist the Lead's new upstream session id after each call.

Import `WorkspaceSnapshot` from `team_results` and `Literal` from `typing`; do not duplicate
the snapshot type locally.

- [ ] **Step 6: Implement the bounded recovery loop**

In `_recover_task_outcome`:

1. Persist every rejected `outcome` and `acceptance_result` before Lead review.
2. Return unchanged when the reason is nonrecoverable.
3. Return unchanged when
   `acceptance_recovery_attempts >= ACCEPTANCE_RECOVERY_CAP`.
4. Ask the Lead for a resolution.
5. For `ask_user`, write an audit record without incrementing and return
   `UserDecisionResolution`.
6. For `fail`, write an audit record and return a rejected `AcceptanceResult` using the
   Lead's reason code.
7. For `retry_worker` or `revise_acceptance`, atomically record the review, increment the
   counter, and update acceptance when supplied.
8. Resume the same Worker session with the Lead instruction and the authoritative current
   acceptance JSON.
9. Append a new `agent_output` message for every resubmission.
10. Evaluate the new outcome and repeat until accepted, nonrecoverable, user-deferred, or
    the cap is reached.

For a prior `undeclared_deliverable`, calculate:

```python
rejected_paths = {
    item.path for item in outcome.deliverables
    if item.path not in task.acceptance.required_outputs
}
```

Before accepting the next outcome, each rejected path must either be in the revised
`required_outputs` or no longer exist under `working_root`. Use resolved bounded paths; do
not follow or delete an outside-workspace path. If an undeclared path remains, return
`undeclared_deliverable` again and include the path in the next Lead context.

- [ ] **Step 7: Integrate recovery without duplicating publication**

Refactor `_execute` only enough to:

- keep the original pre-Task `workspace_snapshot`;
- evaluate the initial outcome;
- call `_recover_task_outcome`;
- reuse the existing user-decision deferral block;
- publish artifacts only for the final accepted outcome;
- persist the final outcome and call `finish_task` once;
- keep Task, Worker, Run, and Cycle active during recovery;
- emit `team.task.updated` after each persisted internal review so the existing controller
  refreshes detail data.

Do not change `_terminal_status`, manual retry behavior, or unrelated orchestration.

- [ ] **Step 8: Run the focused runtime tests**

Run:

```powershell
pytest tests/test_team_runtime.py -q -k "acceptance_recovery or acceptance_revision or acceptance_review or worker_prose_cannot_complete"
```

Expected: PASS.

- [ ] **Step 9: Run existing strict acceptance tests**

Run:

```powershell
pytest tests/test_team_acceptance.py -q
```

Expected: PASS; strict rejection behavior remains intact.

- [ ] **Step 10: Commit Task 3**

```powershell
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team): acceptance 실패 내부 복구 실행"
```

---

### Task 4: Expose Recovery Attempts and Render Task-Only Review History

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py:1250-1275`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:110-250`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:742-777`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:1368-1381`
- Modify: `src/personal_agent_gateway/static/styles.css:3384-3428`
- Modify: `src/personal_agent_gateway/frontend_dist/**` (generated by Vite)
- Test: `tests/test_api_team_runs.py:664-713`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: `TeamTask.acceptance_recovery_attempts`
- Consumes: existing message payloads where `kind === "acceptance_review"` and
  `metadata.task_id` identifies the Task
- Produces task payload field: `"acceptance_recovery_attempts": int`
- Produces frontend helper:

```javascript
function groupAcceptanceReviewsByTask(messages) {
  const grouped = new Map();
  for (const message of messages) {
    if (message.kind !== "acceptance_review" || !message.metadata?.task_id) continue;
    const reviews = grouped.get(message.metadata.task_id) || [];
    reviews.push(message);
    grouped.set(message.metadata.task_id, reviews);
  }
  return grouped;
}
```

- [ ] **Step 1: Extend the API payload test**

In `test_team_task_payload_exposes_acceptance_outcome_and_result`, assert:

```python
assert payload["acceptance_recovery_attempts"] == 0
```

Start the Task with its Worker, add one `retry_worker` acceptance review through the
service, fetch tasks again, and assert the field is `1`.

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```powershell
pytest tests/test_api_team_runs.py::test_team_task_payload_exposes_acceptance_outcome_and_result -q
```

Expected: FAIL because the payload field is absent.

- [ ] **Step 3: Add the task payload field**

Add:

```python
"acceptance_recovery_attempts": task.acceptance_recovery_attempts,
```

No new endpoint or response wrapper is needed.

- [ ] **Step 4: Write the Task detail frontend test**

Render a running Team Run with one `in_progress` Task and one message:

```javascript
{
  id: "review-1",
  kind: "acceptance_review",
  sender_agent_id: "lead",
  recipient_agent_id: "worker",
  content: "Resubmit without the undeclared file.",
  metadata: {
    task_id: "t1",
    attempt: 1,
    reason_code: "undeclared_deliverable",
    action: "retry_worker",
    reason: "The contract declares no output.",
    instruction: "Resubmit without the undeclared file.",
    acceptance_before: {
      required_outputs: [],
      required_verifications: ["source-check"]
    },
    acceptance_after: null
  },
  created_at: "2026-07-30T00:00:00Z"
}
```

Assert before opening the Task:

```javascript
expect(screen.queryByText("undeclared_deliverable")).not.toBeInTheDocument();
expect(screen.queryByText("INTERNAL REVIEW")).not.toBeInTheDocument();
```

Open Task `t1`, then assert:

```javascript
expect(within(dialog).getByText("INTERNAL REVIEW · 1")).toBeInTheDocument();
expect(within(dialog).getByText("undeclared_deliverable")).toBeInTheDocument();
expect(within(dialog).getByText("RETRY WORKER")).toBeInTheDocument();
expect(within(dialog).getByText("Resubmit without the undeclared file.")).toBeInTheDocument();
```

Add a second assertion that a review for a different Task is not rendered in this dialog.

- [ ] **Step 5: Run the frontend test and verify it fails**

Run:

```powershell
npm --prefix frontend test -- --run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: FAIL because Task detail does not group or render internal reviews.

- [ ] **Step 6: Render internal reviews only inside Task detail**

Create `groupAcceptanceReviewsByTask`, derive:

```javascript
const acceptanceReviewsByTask = groupAcceptanceReviewsByTask(messages);
const selectedTaskReviews = selectedTask
  ? newestFirst(acceptanceReviewsByTask.get(selectedTask.id) || [])
  : [];
```

Pass `reviews={selectedTaskReviews}` to `TaskDetailDialog`. Add an `INTERNAL REVIEW · N`
section only when `reviews.length > 0`. Render attempt, uppercased action, reason code,
reason, and instruction. Put `acceptance_before`/`acceptance_after` inside a native
`<details>` element so the default view stays compact.

Do not add review messages to Overview error cards, Task status badges, or user decision
counts. The existing general Activity tab may continue showing the audit message because it
already shows all messages.

- [ ] **Step 7: Add minimal styles**

Add only:

```css
.team-acceptance-review-list {
    display: grid;
    gap: 8px;
}
.team-acceptance-review {
    border: var(--bd-sm);
    padding: 10px;
}
.team-acceptance-review-head {
    display: flex;
    justify-content: space-between;
    gap: 8px;
}
.team-acceptance-review-contract pre {
    overflow: auto;
    white-space: pre-wrap;
}
```

Match existing typography and spacing; do not redesign the dialog.

- [ ] **Step 8: Run the focused API and frontend tests**

Run:

```powershell
pytest tests/test_api_team_runs.py::test_team_task_payload_exposes_acceptance_outcome_and_result -q
npm --prefix frontend test -- --run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: PASS.

- [ ] **Step 9: Build the production frontend**

Run:

```powershell
npm run build:frontend
```

Expected: Vite build succeeds and refreshes `src/personal_agent_gateway/frontend_dist`.

- [ ] **Step 10: Commit Task 4**

```powershell
git add src/personal_agent_gateway/api/team_runs.py frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/personal_agent_gateway/static/styles.css src/personal_agent_gateway/frontend_dist tests/test_api_team_runs.py
git commit -m "feat(team): 내부 acceptance 검토 이력 표시"
```

---

### Task 5: Focused End-to-End Regression Check

**Files:**
- Verify only; modify a source file only if a focused test exposes a regression directly
  caused by Tasks 1-4.

**Interfaces:**
- Consumes all prior Task interfaces.
- Produces a verified feature branch with no unrelated changes.

- [ ] **Step 1: Run the related backend tests**

Run:

```powershell
pytest tests/test_migrations.py tests/test_teams.py tests/test_team_acceptance.py tests/test_team_runtime.py tests/test_api_team_runs.py -q -k "migration_19 or acceptance or task_payload"
```

Expected: PASS. This intentionally excludes unrelated backend test files.

- [ ] **Step 2: Run the exact frontend component test**

Run:

```powershell
npm --prefix frontend test -- --run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: PASS.

- [ ] **Step 3: Rebuild the frontend from the final source state**

Run:

```powershell
npm run build:frontend
```

Expected: Vite build succeeds.

- [ ] **Step 4: Inspect the final diff**

Run:

```powershell
git status --short
git diff --check
git diff --stat HEAD~4
```

Confirm every changed file is listed in the File Map and every changed line traces to
acceptance recovery or its Task detail audit UI.

- [ ] **Step 5: Stop on any failure**

If Step 1-3 fails, return to the Task that owns the failing file, add a reproducing focused
test there, apply the minimum direct correction, repeat that Task's exact test command, and
commit with that Task's listed file set. Do not create an empty verification commit.

---

## Completion Evidence

Before reporting completion, capture:

- the focused pytest command and passing count;
- the exact TeamRunDetail Vitest command and passing count;
- the final Vite build result;
- `git status --short --branch`;
- the commits created by Tasks 1-4;
- confirmation that no full-suite test was run.

The feature is complete only when:

1. a recoverable first rejection no longer fails the Cycle;
2. Lead can retry the Worker or revise acceptance and require resubmission;
3. two unsuccessful internal corrections become the first user-visible failure;
4. nonrecoverable infrastructure failures retain existing behavior;
5. Overview stays non-error during internal recovery;
6. Task detail shows the full internal review audit trail.
