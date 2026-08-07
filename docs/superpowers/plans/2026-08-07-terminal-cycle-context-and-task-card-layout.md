# Terminal Cycle Context and Task Card Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass every final previous-cycle state and its useful results to the next Team cycle while keeping long failure diagnostics inside the task card.

**Architecture:** Keep AUTO success settlement separate from previous-context eligibility. Build an immutable textual context snapshot at request enqueue time from the selected cycle and its tasks, and use one latest-final query for manual-adjacent, Hook, Knowledge Request, and AUTO continuation paths. Render card diagnostics outside the metadata flex row and constrain the board's intrinsic sizing.

**Tech Stack:** Python 3.12, SQLite, pytest, React 19, Vitest, Testing Library, Vite, plain CSS

## Global Constraints

- Eligible previous states are exactly `completed`, `completed_with_failures`, `failed`, `blocked`, and `canceled`.
- `queued`, `running`, `waiting_for_provider`, `waiting_for_user`, and `interrupted` remain ineligible because they can still change.
- Do not change AUTO slot settlement semantics: only `completed` and `completed_with_failures` settle a successful slot automatically.
- Reuse `team_cycle_requests.previous_summary_text`; do not add a migration or dependency.
- Snapshot status, cycle summary/error, and task status/result/outcome/error at enqueue time.
- Preserve full diagnostic text in the task dialog; clamp only the board card.
- Do not start PAG or LMG from a Codex-managed Windows command.

## File map

- `src/personal_agent_gateway/team_cycles.py`: final-cycle selection, validation, and context serialization.
- `src/personal_agent_gateway/team_cycle_dispatcher.py`: leader instruction context block.
- `src/personal_agent_gateway/hook_runner.py`: Hook latest-final lookup.
- `src/personal_agent_gateway/api/archive.py`: Knowledge Request latest-final lookup.
- `tests/test_team_cycles.py`: eligibility, rejection, snapshot, and AUTO continuation tests.
- `tests/test_team_cycle_dispatcher.py`: leader context prompt test.
- `frontend/src/components/molecules/TeamTaskCard/index.jsx`: card structure.
- `frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx`: card DOM contract.
- `src/personal_agent_gateway/static/styles.css`: intrinsic-width and clamping rules.

---

### Task 1: Final previous-cycle snapshot contract

**Files:**
- Modify: `tests/test_team_cycles.py`
- Modify: `src/personal_agent_gateway/team_cycles.py`
- Modify: `src/personal_agent_gateway/hook_runner.py`
- Modify: `src/personal_agent_gateway/api/archive.py`

**Interfaces:**
- Produces: `TeamCycleService.latest_final_cycle(team_run_id: str) -> TeamRunCycle | None`
- Produces: `_previous_cycle_context(connection: sqlite3.Connection, cycle: sqlite3.Row) -> str`
- Preserves: `_SETTLED_CYCLE_STATUSES` as AUTO successful-slot statuses.
- Stores: generated context in `TeamCycleRequest.previous_summary_text`.

- [ ] **Step 1: Write failing eligibility and context tests**

Add this parameterized case to `tests/test_team_cycles.py`:

```python
@pytest.mark.parametrize(
    "status",
    ["completed", "completed_with_failures", "failed", "blocked", "canceled"],
)
def test_final_cycle_can_be_snapshotted_as_previous_context(
    tmp_path: Path, status: str
) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    previous = teams.create_cycle(run.id, "manual", f"previous-{status}")
    teams.set_cycle_status(previous.id, status, summary="previous result")
    request = cycles.enqueue_request(
        run.id, "manual", f"client-{status}", "next",
        previous_cycle_id=previous.id,
    )
    assert request.previous_cycle_id == previous.id
    assert f"STATUS: {status.upper()}" in request.previous_summary_text
    assert "previous result" in request.previous_summary_text
```

Add a failed-cycle case that creates one completed task with `result="Applied the remaining fixes"`, one failed task with `outcome.summary="Draft was unchanged"`, and cycle/task error `Required task failed`. Assert the snapshot contains `STATUS: FAILED`, both `- [STATUS] title` entries, both task details, and the cycle error. Extend rejection coverage so foreign and `running` cycles still raise `ValueError` containing `final cycle`.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_cycles.py -k "previous_context or snapshots_previous_cycle" -q
```

Expected: non-success final states are rejected and the completed case lacks the explicit status.

- [ ] **Step 3: Implement final-context serialization and selection**

Import `json` in `team_cycles.py` and add this constant without widening `_SETTLED_CYCLE_STATUSES`:

```python
_PREVIOUS_CONTEXT_CYCLE_STATUSES = {
    "completed", "completed_with_failures", "failed", "blocked", "canceled",
}
```

Implement `_previous_cycle_context`. Emit `STATUS` unconditionally; append non-empty cycle `SUMMARY` and `ERROR` blocks; query tasks by `cycle_id` ordered by `created_at, plan_ordinal, id`; append `- [STATUS] title`; prefer task `result`, otherwise `json.loads(outcome_json)["summary"]`; append a distinct task error. Strip text and avoid duplicate error/detail lines.

Change `_enqueue_request` to validate against `_PREVIOUS_CONTEXT_CYCLE_STATUSES` and store the generated context. Preserve the `previous_snapshot` retry path unchanged.

Rename `latest_settled_cycle` to `latest_final_cycle`, query all five final states, and update Hook and Archive callers. Update AUTO due-request selection to the same states so CONTINUE after failure passes that failed cycle into the next slot.

- [ ] **Step 4: Run domain/caller tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_cycles.py tests/test_hook_runner.py tests/test_api_team_runs.py -q
```

Expected: all selected tests pass after raw-summary expectations are updated to the context format.

- [ ] **Step 5: Commit**

```powershell
git add -- src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/hook_runner.py src/personal_agent_gateway/api/archive.py tests/test_team_cycles.py
git commit -m "fix(team-cycles): preserve final cycle context"
```

### Task 2: Leader context prompt label

**Files:**
- Modify: `tests/test_team_cycle_dispatcher.py`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py`

**Interfaces:**
- Consumes: `TeamCycleRequest.previous_summary_text` containing complete context.
- Produces: `PREVIOUS CYCLE CONTEXT\n<snapshot>` in the leader effective instruction.

- [ ] **Step 1: Write the failing prompt assertion**

Rename the existing `previous_summary` dispatcher test to `previous_context` and require:

```python
assert call[2] == (
    "next work\n\nPREVIOUS CYCLE CONTEXT\n"
    "STATUS: COMPLETED\n\nSUMMARY\nprevious result"
)
```

Apply the same assertion to `get_cycle_effective_instruction`.

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_cycle_dispatcher.py -k previous_context -q
```

Expected: failure because the dispatcher still emits `PREVIOUS CYCLE SUMMARY`.

- [ ] **Step 3: Change only the dispatcher label**

```python
if request.previous_summary_text:
    instruction += "\n\nPREVIOUS CYCLE CONTEXT\n" + request.previous_summary_text
```

- [ ] **Step 4: Run and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_cycle_dispatcher.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add -- src/personal_agent_gateway/team_cycle_dispatcher.py tests/test_team_cycle_dispatcher.py
git commit -m "fix(team-cycles): label previous cycle context"
```

### Task 3: Intrinsic-width-safe task cards

**Files:**
- Modify: `frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx`
- Modify: `frontend/src/components/molecules/TeamTaskCard/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Preserves: `TeamTaskCard` props, click behavior, and dialog data.
- Produces: `.team-task-diagnostic` outside `.team-task-meta`.

- [ ] **Step 1: Write a failing long-diagnostic structure test**

```jsx
it("keeps a long failure diagnostic outside compact metadata", () => {
  const diagnostic = "Required task failed: " + "a-very-long-path/".repeat(20);
  const { container } = render(
    <TeamTaskCard
      task={{ ...task, status: "failed", error_message: diagnostic }}
      owner={{ name: "QA Reviewer", persona_snapshot: {} }}
      reportCount={1}
      onOpen={vi.fn()}
    />
  );
  const diagnosticNode = screen.getByText(diagnostic);
  expect(diagnosticNode).toHaveClass("team-task-diagnostic");
  expect(diagnosticNode.closest(".team-task-meta")).toBeNull();
  expect(container.querySelector(".team-task-meta")).toHaveTextContent("FILES 0");
  expect(container.querySelector(".team-task-meta")).toHaveTextContent("REPORTS 1");
});
```

- [ ] **Step 2: Run and verify RED**

```powershell
npm --prefix frontend test -- --run src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx
```

Expected: diagnostic has the old class and remains inside metadata.

- [ ] **Step 3: Separate markup and constrain intrinsic sizing**

Render the diagnostic before `.team-task-meta`:

```jsx
{noteText ? (
  <div className={`team-task-diagnostic mono team-task-note-${task.status === "failed" ? "danger" : "warning"}`}>
    {noteText}
  </div>
) : null}
```

Remove the old note span from metadata. In `styles.css`, change the board to `repeat(5, minmax(0, 1fr))`; add `min-width: 0` to column, column body, card, title, and metadata; add `overflow: hidden` to the card; make `.team-task-diagnostic` a three-line `-webkit-box` clamp with `overflow-wrap: anywhere`; make file/report counts `flex: none`; and constrain the remaining reason-code `.team-task-note` with `min-width: 0`, hidden overflow, and `overflow-wrap: anywhere`.

- [ ] **Step 4: Run tests/build and verify GREEN**

```powershell
npm --prefix frontend test -- --run src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
npm run build:frontend
```

- [ ] **Step 5: Commit**

```powershell
git add -- frontend/src/components/molecules/TeamTaskCard/index.jsx frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "fix(team-runs): contain task failure diagnostics"
```

### Task 4: Integrated verification

**Files:**
- Verify only; modify a file only when a regression traces directly to Tasks 1-3.

**Interfaces:**
- Consumes: all prior task deliverables.
- Produces: final evidence for API, dispatch, UI, and build agreement.

- [ ] **Step 1: Run diff and lint checks**

```powershell
git diff --check HEAD~3
.\.venv\Scripts\python.exe -m ruff check src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/hook_runner.py src/personal_agent_gateway/api/archive.py tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py
```

- [ ] **Step 2: Run backend regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py tests/test_hook_runner.py tests/test_api_team_runs.py -q
```

- [ ] **Step 3: Run frontend regressions and build**

```powershell
npm --prefix frontend test -- --run src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/hooks/useTeamRunController.test.jsx
npm run build:frontend
```

- [ ] **Step 4: Inspect final state**

```powershell
git status --short
git log -5 --oneline
```

Expected: only intentional generated build assets, if tracked, remain for a dedicated build commit; otherwise the worktree is clean.
