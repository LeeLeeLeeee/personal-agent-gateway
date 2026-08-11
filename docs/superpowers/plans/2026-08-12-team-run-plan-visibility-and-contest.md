# Team Run Plan Visibility and Contest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator see what a team run actually built, see which obligations the leader says nobody owns, and contest the plan so the leader adjudicates with a recorded reason.

**Architecture:** Three parts of one loop, built in order because each depends on the one before it having something to point at. Part 1 is a pure report over data already stored per task, surfaced in the run detail payload and the existing TASKS tab. Part 2 asks the leader for an optional fenced block in its prose synthesis, parsed if present and ignored if not, so a nice-to-have cannot fail a cycle. Part 3 adds a `cycle_contest` stage that rides the existing cycle-request queue, operation ledger, repair seam, and decision-request pause, contributing one genuinely new effect that records the verdict.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest; React (Vite) with Vitest and Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-12-team-run-plan-visibility-and-contest-design.md`

## Global Constraints

- Verification labels must not claim more than happened. `mode: "verified"` means the gate ran a check, and every check kind is a file read (`file_nonempty`, `file_contains`, `file_matches`) — nothing compiles, runs a test, or executes a command. Render `verified` as `파일 내용 확인` and `attested` as `워커 신고`. Never render either as `검증됨`.
- The frontend copy in this area is Korean (`선행 작업 · …` in `TeamTaskCard`). Match it.
- Path resolution inside a run workspace goes through `safe_workspace_file(workspace, relative_path) -> Path | None` from `team_verification_checks.py`, which returns `None` for symlinks, escapes, non-files, and sensitive names. Never join paths by hand.
- `reason` is required on every contest verdict. A verdict without one is a validation failure, not a defaulted field.
- A non-empty `supersedes` requires a non-empty `tasks`.
- Backend suite baseline is **21 pre-existing failures** (`tests/test_api_agents.py`, `tests/test_api_dashboard.py`, `tests/test_runtime_factory_headless.py`). Judge completion by delta: any new failure blocks the task. Full suite takes about 8.5 minutes — run it blocking, not backgrounded.
- `LATEST_SCHEMA_VERSION` is 30. Any migration added here is 31, and `tests/test_migrations.py` hardcodes the latest version, so it must be bumped in the same commit.

---

## Task 1: The build-evidence report

A pure function, so the promised-versus-built comparison is testable without HTTP, a runtime, or a browser.

**Files:**
- Create: `src/personal_agent_gateway/team_build_evidence.py`
- Test: `tests/test_team_build_evidence.py`

**Interfaces:**
- Produces:
  - `task_build_evidence(task: TeamTask, workspace: Path) -> dict[str, object]`
  - `run_build_evidence(tasks: list[TeamTask], workspace: Path) -> dict[str, object]`

  `task_build_evidence` returns:
  ```python
  {
      "promised": ["a.md"],            # sorted task.acceptance.required_outputs
      "declared": ["a.md", "b.md"],    # sorted paths from task.outcome["deliverables"]
      "undeclared_promises": ["c.md"], # promised but not declared
      "extra_declarations": ["b.md"],  # declared but not promised
      "missing_files": ["b.md"],       # declared but not resolvable in the workspace
      "verifications": [
          {"name": "n", "mode": "verified", "status": "passed"},
      ],
      "worker_asserted_only": False,   # acceptance_result.evidence.attested_only
  }
  ```
  `run_build_evidence` returns
  `{"task_count": int, "worker_asserted_only_count": int, "missing_file_count": int}`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from personal_agent_gateway.team_build_evidence import (
    run_build_evidence,
    task_build_evidence,
)
from personal_agent_gateway.teams import (
    RequiredVerification,
    TaskAcceptance,
    TeamTask,
)


def _task(tmp_path, **overrides):
    """A TeamTask is frozen with many fields this report ignores, so build one
    here rather than dragging a whole runtime fixture into a pure-function test.
    """
    base = dict(
        id="t1",
        team_run_id="r1",
        title="Study backend",
        description="",
        owner_agent_id=None,
        status="completed",
        required=True,
        acceptance=TaskAcceptance(
            ("promised.md",), (RequiredVerification("has-export"),)
        ),
        outcome={"deliverables": [{"path": "promised.md"}]},
        acceptance_result={
            "evidence": {
                "verifications": {
                    "has-export": {"mode": "attested", "status": "passed"}
                },
                "attested_only": True,
            }
        },
    )
    base.update(overrides)
    return TeamTask(**_fill_team_task_defaults(base))


def test_evidence_reports_both_directions_of_the_promise(tmp_path):
    """A rejected task's story is the difference between what its contract asked
    for and what the worker declared. Run 699c1915's task 8 promised four files
    and declared seven, and nothing in the UI said so."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    (tmp_path / "extra.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance=TaskAcceptance(("promised.md", "forgotten.md"), ()),
        outcome={
            "deliverables": [{"path": "promised.md"}, {"path": "extra.md"}]
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["promised"] == ["forgotten.md", "promised.md"]
    assert evidence["declared"] == ["extra.md", "promised.md"]
    assert evidence["undeclared_promises"] == ["forgotten.md"]
    assert evidence["extra_declarations"] == ["extra.md"]
    assert evidence["missing_files"] == []


def test_a_declared_file_that_is_not_there_is_reported_missing(tmp_path):
    task = _task(tmp_path, outcome={"deliverables": [{"path": "ghost.md"}]})

    assert task_build_evidence(task, tmp_path)["missing_files"] == ["ghost.md"]


def test_a_sensitive_file_that_is_present_is_not_reported_missing(tmp_path):
    """safe_workspace_file refuses .env by name, not by absence. Reporting a file
    that is plainly there as missing would make the screen lie."""
    (tmp_path / ".env.example").write_text("KEY=", encoding="utf-8")
    task = _task(
        tmp_path, outcome={"deliverables": [{"path": ".env.example"}]}
    )

    assert task_build_evidence(task, tmp_path)["missing_files"] == []


def test_a_path_escaping_the_workspace_counts_as_missing(tmp_path):
    """safe_workspace_file returns None for an escape, and the report must not
    turn that into a file that exists somewhere else on the machine."""
    task = _task(tmp_path, outcome={"deliverables": [{"path": "../outside.md"}]})

    assert task_build_evidence(task, tmp_path)["missing_files"] == ["../outside.md"]


def test_verification_mode_is_carried_through_unchanged(tmp_path):
    """The distinction between a check the gate ran and the worker's own word is
    the whole point; the report must not collapse them."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance_result={
            "evidence": {
                "verifications": {
                    "ran": {"mode": "verified", "status": "passed"},
                    "claimed": {"mode": "attested", "status": "passed"},
                },
                "attested_only": False,
            }
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["verifications"] == [
        {"name": "claimed", "mode": "attested", "status": "passed"},
        {"name": "ran", "mode": "verified", "status": "passed"},
    ]
    assert evidence["worker_asserted_only"] is False


def test_a_task_with_no_outcome_yet_reports_empty_rather_than_raising(tmp_path):
    task = _task(tmp_path, status="in_progress", outcome=None, acceptance_result=None)

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["declared"] == []
    assert evidence["verifications"] == []
    assert evidence["worker_asserted_only"] is False


def test_run_rollup_counts_what_rests_on_the_workers_word(tmp_path):
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    asserted = _task(tmp_path)
    inspected = _task(
        tmp_path,
        id="t2",
        acceptance_result={
            "evidence": {
                "verifications": {"ran": {"mode": "verified", "status": "passed"}},
                "attested_only": False,
            }
        },
    )
    ghost = _task(tmp_path, id="t3", outcome={"deliverables": [{"path": "ghost.md"}]})

    rollup = run_build_evidence([asserted, inspected, ghost], tmp_path)

    assert rollup == {
        "task_count": 3,
        "worker_asserted_only_count": 2,
        "missing_file_count": 1,
    }
```

`_fill_team_task_defaults` is a helper you write in the same test file. `TeamTask`
is a frozen dataclass with fields this report ignores; read its definition at
`src/personal_agent_gateway/teams.py:165` and fill every remaining field with
`None` or a trivial value:

```python
def _fill_team_task_defaults(values: dict) -> dict:
    defaults = {
        "cycle_id": None,
        "ordinal": 0,
        "retry_of_task_id": None,
        "result": None,
        "error_message": None,
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "acceptance_recovery_attempts": 0,
    }
    return {**defaults, **values}
```

If `TeamTask` has a field this helper does not name, the constructor raises and
tells you which one — add it rather than guessing.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_build_evidence.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'personal_agent_gateway.team_build_evidence'`

- [ ] **Step 3: Write the module**

```python
from pathlib import Path

from personal_agent_gateway.file_safety import is_sensitive_file
from personal_agent_gateway.team_verification_checks import safe_workspace_file
from personal_agent_gateway.teams import TeamTask


def _is_missing(workspace: Path, relative_path: str) -> bool:
    """Absent, or unreachable for a reason that is not absence.

    safe_workspace_file also refuses .env and .env.* by name, so asking it alone
    would report a file that is plainly there as missing and the screen would be
    telling the operator something false. Sensitive names are checked separately
    against the resolved path, still inside the workspace.
    """
    if safe_workspace_file(workspace, relative_path) is not None:
        return False
    root = workspace.resolve()
    try:
        candidate = (root / relative_path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return True
    return not (candidate.is_file() and is_sensitive_file(candidate.name))


def task_build_evidence(task: TeamTask, workspace: Path) -> dict[str, object]:
    """Compare what a task's contract asked for against what came back.

    Everything here is already stored; it has simply never been shown together.
    The two directions of the difference matter separately: a promise with no
    declaration is work that may not have happened, while a declaration with no
    promise is work outside the contract -- which the gate rejects outright, so
    without this view a rejected task looks like a failure with no explanation.
    """
    outcome = task.outcome or {}
    deliverables = outcome.get("deliverables") or []
    declared_paths = {
        str(entry.get("path"))
        for entry in deliverables
        if isinstance(entry, dict) and entry.get("path")
    }
    promised = set(task.acceptance.required_outputs)
    evidence = (task.acceptance_result or {}).get("evidence") or {}
    recorded = evidence.get("verifications") or {}

    return {
        "promised": sorted(promised),
        "declared": sorted(declared_paths),
        "undeclared_promises": sorted(promised - declared_paths),
        "extra_declarations": sorted(declared_paths - promised),
        "missing_files": sorted(
            path for path in declared_paths if _is_missing(workspace, path)
        ),
        "verifications": [
            {
                "name": name,
                "mode": str(entry.get("mode")),
                "status": str(entry.get("status")),
            }
            for name, entry in sorted(recorded.items())
            if isinstance(entry, dict)
        ],
        "worker_asserted_only": bool(evidence.get("attested_only")),
    }


def run_build_evidence(
    tasks: list[TeamTask], workspace: Path
) -> dict[str, object]:
    """The two numbers worth putting at the top of a run.

    Both say how much of the run's verdict rests on the workers' own word rather
    than on anything the gate looked at.
    """
    per_task = [task_build_evidence(task, workspace) for task in tasks]
    return {
        "task_count": len(per_task),
        "worker_asserted_only_count": sum(
            1 for item in per_task if item["worker_asserted_only"]
        ),
        "missing_file_count": sum(len(item["missing_files"]) for item in per_task),
    }
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_build_evidence.py -q -p no:randomly`
Expected: PASS, 7 tests.

- [ ] **Step 5: Lint**

Run: `python -m ruff check src/personal_agent_gateway/team_build_evidence.py tests/test_team_build_evidence.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_build_evidence.py tests/test_team_build_evidence.py
git commit -m "feat(team-runs): report what a task promised against what it built"
```

---

## Task 2: Carry the evidence in the detail payload

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` — `_task_payload` (around line 1301) and `get_team_run_detail` (around line 360)
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `task_build_evidence`, `run_build_evidence` from Task 1.
- Produces: each entry of `detail["tasks"]` gains `build_evidence` (the Task 1 per-task dict); `detail` gains `build_evidence_summary` (the rollup dict).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_team_runs.py`. Follow the setup in
`test_team_run_detail_aggregate_includes_documents_summary` (around line 1683)
for `authenticated_client`, `create_persona`, `create_team`, and run creation.

```python
def test_team_run_detail_shows_what_each_task_built(tmp_path: Path) -> None:
    """The operator cannot contest a plan whose coverage they cannot see, so the
    promised-versus-built comparison has to reach the client."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    team_id = create_team(client, leader_id, [member_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Show build evidence",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["working_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "kept.md").write_text("x", encoding="utf-8")
    service = client.app.state.team_run_service
    task = service.create_task(
        run["id"],
        "Write the guide",
        "Write it.",
        acceptance=TaskAcceptance(("kept.md", "forgotten.md"), ()),
    )
    service.record_task_outcome_for_test(
        task.id,
        outcome={"deliverables": [{"path": "kept.md"}, {"path": "ghost.md"}]},
        acceptance_result={
            "evidence": {
                "verifications": {"n": {"mode": "attested", "status": "passed"}},
                "attested_only": True,
            }
        },
    )

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    evidence = detail["tasks"][0]["build_evidence"]
    assert evidence["undeclared_promises"] == ["forgotten.md"]
    assert evidence["extra_declarations"] == ["ghost.md"]
    assert evidence["missing_files"] == ["ghost.md"]
    assert evidence["verifications"] == [
        {"name": "n", "mode": "attested", "status": "passed"}
    ]
    assert detail["build_evidence_summary"] == {
        "task_count": 1,
        "worker_asserted_only_count": 1,
        "missing_file_count": 1,
    }
```

`record_task_outcome_for_test` does not exist. Do not add a test-only writer to
the service. Instead set the two columns directly, which is what the surrounding
tests in this file do for state the API cannot reach:

```python
    with client.app.state.database.connection() as connection:
        connection.execute(
            "update team_tasks set status = 'completed', outcome_json = ?, "
            "acceptance_result_json = ? where id = ?",
            (
                json.dumps({"deliverables": [{"path": "kept.md"}, {"path": "ghost.md"}]}),
                json.dumps(
                    {
                        "evidence": {
                            "verifications": {
                                "n": {"mode": "attested", "status": "passed"}
                            },
                            "attested_only": True,
                        }
                    }
                ),
                task.id,
            ),
        )
```

Check the top of `tests/test_api_team_runs.py` for whether `json` and
`TaskAcceptance` are already imported and add only what is missing.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly -k shows_what_each_task_built`
Expected: FAIL with `KeyError: 'build_evidence'`

- [ ] **Step 3: Wire it into the payload**

In `_task_payload`, add a parameter and a key. It already takes
`failure_shape` this way, so follow that:

```python
def _task_payload(
    task: TeamTask,
    depends_on_task_ids: list[str] | None = None,
    failure_shape: dict[str, object] | None = None,
    build_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
```

and inside the returned dict, next to `"failure_shape": failure_shape,`:

```python
        "build_evidence": build_evidence,
```

In `get_team_run_detail`, after `failure_shapes` is computed:

```python
    workspace = _resolved_workspace(run)
    task_evidence = {
        task.id: task_build_evidence(task, workspace) for task in selected_tasks
    }
```

pass it in the `tasks` comprehension:

```python
        "tasks": [
            _task_payload(
                task,
                task_dependencies.get(task.id, []),
                failure_shapes.get(task.id),
                task_evidence.get(task.id),
            )
            for task in selected_tasks
        ],
```

and add the rollup beside `document_summary`:

```python
        "build_evidence_summary": run_build_evidence(selected_tasks, workspace),
```

Import both functions at the top of the module.

Note that `selected_tasks` is `tasks[-limit:]`, so the rollup describes the
window the client received rather than the whole run. That is the honest scope
for a number rendered next to that list.

- [ ] **Step 4: Run it and watch it pass**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py
git commit -m "feat(api): carry build evidence in the team run detail payload"
```

---

## Task 3: Show it

`TeamRunDetail/index.jsx` is 1563 lines. Put this in its own file in the same
directory rather than growing it further.

**Files:**
- Create: `frontend/src/components/organisms/TeamRunDetail/BuildEvidence.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` — the task detail dialog (next to `<FailureShape …/>`) and the TASKS tab header
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: `task.build_evidence` and `detail.build_evidence_summary` from Task 2.
- Produces: `BuildEvidence` and `BuildEvidenceSummary` React components, both named exports.

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe("TeamRunDetail", …)` block.

```jsx
  it("shows what a task promised against what it built", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          build_evidence_summary: {
            task_count: 3,
            worker_asserted_only_count: 2,
            missing_file_count: 1,
          },
          tasks: [{
            id: "t1",
            title: "Write the guide",
            status: "completed",
            build_evidence: {
              promised: ["kept.md", "forgotten.md"],
              declared: ["kept.md", "ghost.md"],
              undeclared_promises: ["forgotten.md"],
              extra_declarations: ["ghost.md"],
              missing_files: ["ghost.md"],
              verifications: [
                { name: "ran", mode: "verified", status: "passed" },
                { name: "claimed", mode: "attested", status: "passed" }
              ],
              worker_asserted_only: false
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText(/워커 신고만으로 통과 2/)).toBeInTheDocument();
    expect(screen.getByText(/없는 파일 1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open task Write the guide" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Write the guide" });

    expect(within(dialog).getByText(/forgotten\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText(/ghost\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText(/파일 내용 확인/)).toBeInTheDocument();
    expect(within(dialog).getByText(/워커 신고/)).toBeInTheDocument();
    expect(within(dialog).queryByText("검증됨")).not.toBeInTheDocument();
  });

  it("renders nothing for a task with no build evidence", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [],
          tasks: [{ id: "t1", title: "Fresh task", status: "pending" }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Fresh task" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Fresh task" });

    expect(within(dialog).queryByText(/약속한 파일/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run it and watch it fail**

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: FAIL — the text is not rendered.

- [ ] **Step 3: Write the components**

`frontend/src/components/organisms/TeamRunDetail/BuildEvidence.jsx`:

```jsx
// Every verification check kind is a file read -- file_nonempty, file_contains,
// file_matches. Nothing compiles, runs a test, or executes a command. So
// "verified" means the gate read the file and looked at its text, and labelling
// it 검증됨 would tell the operator something untrue.
const MODE_LABEL = { verified: "파일 내용 확인", attested: "워커 신고" };

export function BuildEvidence({ evidence }) {
  if (!evidence) return null;
  const {
    promised = [],
    declared = [],
    undeclared_promises: undeclared = [],
    extra_declarations: extra = [],
    missing_files: missing = [],
    verifications = []
  } = evidence;
  if (!promised.length && !declared.length && !verifications.length) return null;

  return (
    <div>
      <div className="mono team-task-dialog-label">약속한 파일 · 만든 파일</div>
      <div className="team-task-diagnostic mono">
        <div>{`약속 ${promised.length} · 신고 ${declared.length}`}</div>
        {undeclared.length ? <div>{`신고 안 된 약속: ${undeclared.join(", ")}`}</div> : null}
        {extra.length ? <div>{`계약 밖 신고: ${extra.join(", ")}`}</div> : null}
        {missing.length ? <div>{`신고했으나 없는 파일: ${missing.join(", ")}`}</div> : null}
        {verifications.map((item) => (
          <div key={item.name}>
            {`${item.name} · ${MODE_LABEL[item.mode] || item.mode} · ${String(item.status || "").toUpperCase()}`}
          </div>
        ))}
      </div>
    </div>
  );
}

export function BuildEvidenceSummary({ summary }) {
  if (!summary) return null;
  return (
    <span className="mono">
      {`워커 신고만으로 통과 ${summary.worker_asserted_only_count} / ${summary.task_count} · 없는 파일 ${summary.missing_file_count}`}
    </span>
  );
}
```

In `index.jsx`, import both and render them. Next to the existing
`<FailureShape shape={task.failure_shape} />` inside the task dialog:

```jsx
          <BuildEvidence evidence={task.build_evidence} />
```

In the TASKS tab, beside the existing counts (the test looks for it while the
TASKS tab is active — find where `FILES 1` / `REPORTS 1` are rendered and put it
in the same row):

```jsx
          <BuildEvidenceSummary summary={detail.build_evidence_summary} />
```

- [ ] **Step 4: Run the frontend suite**

Run: `npm --prefix frontend test`
Expected: 41 files pass. Ignore up to 2 `ArchiveView` timeout flakes; anything else is yours.

- [ ] **Step 5: Verify against the real run**

Rebuild and restart so the bundle and the Python change both load:

```bash
npm start
```

Then open run `699c1915fa764be598586d2f8bb3a170` in the UI and confirm from its
stored data: the TASKS tab shows a non-zero `워커 신고만으로 통과` count, and the
task titled `관리자 콘텐츠 등록·LLM 재가공 백엔드 구현` shows its verifications as
`파일 내용 확인`, not as tested. Record what you saw. If the count is zero, the
wiring is wrong — that run has tasks accepted on file-existence checks alone.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/organisms/TeamRunDetail/
git commit -m "feat(team-runs): show promised versus built, naming how each check passed"
```

---

## Task 4: Parse a coverage-gaps block out of a prose synthesis

The synthesis is prose, not a JSON object — `_validated_synthesis_result` accepts
either a plain-text summary or an `ask_user` resolution. So this parses an
optional fenced block and leaves the summary otherwise untouched.

**Files:**
- Create: `src/personal_agent_gateway/team_coverage_report.py`
- Test: `tests/test_team_coverage_report.py`

**Interfaces:**
- Produces: `extract_coverage_gaps(text: str) -> tuple[str, list[dict[str, str]] | None]` — the summary with the block removed, and the parsed gaps, or `None` when there was no usable block.

- [ ] **Step 1: Write the failing test**

```python
from personal_agent_gateway.team_coverage_report import extract_coverage_gaps


def test_a_valid_block_is_parsed_and_removed_from_the_summary():
    text = (
        "Built the admin backend and the study screen.\n\n"
        "```coverage-gaps\n"
        '[{"obligation": "T-04 discard a draft", '
        '"document": "docs/service-plan.md §4", "note": "no task owns this"}]\n'
        "```\n"
    )

    summary, gaps = extract_coverage_gaps(text)

    assert summary == "Built the admin backend and the study screen."
    assert gaps == [
        {
            "obligation": "T-04 discard a draft",
            "document": "docs/service-plan.md §4",
            "note": "no task owns this",
        }
    ]


def test_an_empty_list_means_the_leader_reported_no_gaps():
    """Distinct from not reporting at all: the UI says different things for
    'reported none' and 'did not report', because they mean different things."""
    summary, gaps = extract_coverage_gaps("Done.\n\n```coverage-gaps\n[]\n```\n")

    assert summary == "Done."
    assert gaps == []


def test_no_block_reports_nothing_and_leaves_the_summary_alone():
    summary, gaps = extract_coverage_gaps("Done.")

    assert summary == "Done."
    assert gaps is None


def test_malformed_json_is_treated_as_no_report_and_never_raises():
    """Synthesis is a leader stage, and a leader stage that cannot be parsed
    costs the cycle. Trading a run for a nice-to-have field is the wrong
    exchange, so a broken block degrades to 'not reported'."""
    text = "Done.\n\n```coverage-gaps\n[{oh no\n```\n"

    summary, gaps = extract_coverage_gaps(text)

    assert summary == "Done."
    assert gaps is None


def test_entries_that_are_not_objects_are_dropped_not_fatal():
    text = '```coverage-gaps\n["just a string", {"obligation": "T-09"}]\n```'

    _, gaps = extract_coverage_gaps(text)

    assert gaps == [{"obligation": "T-09", "document": "", "note": ""}]


def test_a_block_without_the_obligation_field_is_dropped():
    text = '```coverage-gaps\n[{"document": "d.md"}]\n```'

    _, gaps = extract_coverage_gaps(text)

    assert gaps == []
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_coverage_report.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the module**

```python
import json
import re

_BLOCK = re.compile(
    r"```coverage-gaps\s*\n(.*?)\n?```",
    re.DOTALL,
)


def extract_coverage_gaps(
    text: str,
) -> tuple[str, list[dict[str, str]] | None]:
    """Pull an optional coverage-gaps block out of a leader's prose summary.

    Returns the summary without the block, and the gaps -- or None when the
    leader did not report. None and [] are deliberately different: one means the
    leader said nothing, the other means it claimed full coverage, and only the
    second is a claim the operator can contest.

    Nothing here raises. Synthesis is a leader stage, so a parse failure costs
    the cycle, and a block that is optional by design must not be able to do
    that.
    """
    match = _BLOCK.search(text or "")
    if match is None:
        return (text or "").strip(), None
    summary = (text[: match.start()] + text[match.end():]).strip()
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return summary, None
    if not isinstance(payload, list):
        return summary, None
    gaps: list[dict[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        obligation = entry.get("obligation")
        if not isinstance(obligation, str) or not obligation.strip():
            continue
        gaps.append(
            {
                "obligation": obligation.strip(),
                "document": str(entry.get("document") or ""),
                "note": str(entry.get("note") or ""),
            }
        )
    return summary, gaps
```

- [ ] **Step 4: Run and watch it pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_coverage_report.py -q -p no:randomly`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_coverage_report.py tests/test_team_coverage_report.py
git commit -m "feat(team-runs): parse an optional coverage-gaps block from a synthesis"
```

---

## Task 5: Ask the leader for gaps, store them, show them

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `SYNTHESIS_PROMPT` (line 167) and `SYNTHESIS_CONTRACT_PROMPT`, and `_validated_synthesis_result` (around line 3239)
- Modify: `src/personal_agent_gateway/api/team_runs.py` — `_cycle_payload`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Test: `tests/test_team_runtime.py`, `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: `extract_coverage_gaps` from Task 4.
- Produces: the synthesis result payload gains `coverage_gaps` (a list, or absent); `_cycle_payload` gains `coverage_gaps`.

- [ ] **Step 1: Write the failing backend test**

Add to `tests/test_team_runtime.py`, using the operation-runtime fixture the
neighbouring synthesis tests use.

```python
@pytest.mark.asyncio
async def test_synthesis_records_the_gaps_the_leader_reported(tmp_path):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review("Fix the missing citation check."), []),
        ModelResponse(
            "Built the backend.\n\n```coverage-gaps\n"
            '[{"obligation": "T-04 discard", "document": "docs/plan.md §4"}]\n```',
            [],
        ),
    ]

    run = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert run.status == "completed"
    cycle = setup.teams.get_cycle(setup.cycle.id)
    assert cycle.summary == "Built the backend."
    operation = setup.operations.get_by_key(f"{setup.cycle.id}:cycle_synthesis:0")
    assert operation.result_json["coverage_gaps"] == [
        {"obligation": "T-04 discard", "document": "docs/plan.md §4", "note": ""}
    ]


@pytest.mark.asyncio
async def test_a_synthesis_with_no_block_still_completes_the_cycle(tmp_path):
    """The block is optional by construction. A leader that omits it must not
    cost the cycle -- that is the whole reason it is not a required field."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review("Fix the missing citation check."), []),
        ModelResponse("Built the backend.", []),
    ]

    run = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert run.status == "completed"
    operation = setup.operations.get_by_key(f"{setup.cycle.id}:cycle_synthesis:0")
    assert "coverage_gaps" not in operation.result_json
```

`operation.result_json` is the attribute name on `TeamModelOperation` holding the
validated result; confirm it against
`src/personal_agent_gateway/team_model_operations.py:95` and use the real name.

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k coverage_gaps or no_block`
Expected: FAIL — the summary still contains the fenced block.

- [ ] **Step 3: Ask for the block in the prompt**

Add to `SYNTHESIS_PROMPT`, after the existing option 1 line, and the same
sentences to `SYNTHESIS_CONTRACT_PROMPT`:

```
If any obligation in the accepted specification documents is owned by no task,
append a fenced block listing them, and nothing else after it:
```coverage-gaps
[{"obligation": "short name", "document": "path §section", "note": "why it is unowned"}]
```
Send an empty list if every obligation is owned. Omit the block entirely if you
did not check.
```

The three-way distinction is deliberate: a list of gaps, an explicit claim of
full coverage, and a silence are different statements, and the operator is shown
which one they got.

- [ ] **Step 4: Parse it in the validator**

In `_validated_synthesis_result`, the no-contract branch currently returns
`ValidatedOperationResult("synthesis", {"summary": content})`. Run the content
through the parser first and carry the gaps alongside the summary:

```python
        summary, gaps = extract_coverage_gaps(content)
        payload: dict[str, object] = {"summary": summary}
        if gaps is not None:
            payload["coverage_gaps"] = gaps
        if contract is None:
            return ValidatedOperationResult("synthesis", payload)
```

Apply the same to the contract branch: the block is stripped from the human
summary while `contract_payload` keeps the content the contract validated, since
the contract's own validator has already accepted that exact text and rewriting
it would invalidate it.

Import `extract_coverage_gaps` at the top of the module.

`_valid_synthesis` decides whether this payload is acceptable, and it lives in
`team_model_effects.py` — reachable from the registry
`team_model_effect_result_validators()` at line 3096, not from
`_built_in_result_validators()` in `team_model_operations.py`. There are two
registries: the built-in one carries the three planning stages and is always
applied, and the injected one carries everything else; the service constructor
merges them and raises on a duplicate stage-and-kind pair. Widen `_valid_synthesis`
to allow the optional `coverage_gaps` key or the operation is rejected as an
invalid result.

- [ ] **Step 5: Run and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 6: Surface it**

The cycle row has no `coverage_gaps` column and should not gain one — the ledger
already owns this state, and a column would be a second copy to keep in step. So
`_cycle_payload` takes it as an argument the way `_task_payload` takes
`failure_shape`:

```python
def _cycle_payload(
    cycle: TeamRunCycle,
    coverage_gaps: list[dict[str, str]] | None = None,
) -> dict[str, object]:
```

with `"coverage_gaps": coverage_gaps,` in the returned dict. In
`get_team_run_detail`, build the lookup from each cycle's applied synthesis
operation before the payload is assembled:

```python
    operations = request.app.state.team_model_operation_service
    coverage_by_cycle = {}
    for cycle in cycles:
        synthesis = next(
            (
                operation
                for operation in operations.list_for_cycle(cycle.id)
                if operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}
                and operation.status == "applied"
            ),
            None,
        )
        if synthesis is not None:
            coverage_by_cycle[cycle.id] = (synthesis.result_json or {}).get(
                "coverage_gaps"
            )
```

and pass it in: `_cycle_payload(cycle, coverage_by_cycle.get(cycle.id))`.

Use the real attribute name for the stored result — confirm it on
`TeamModelOperation` at `team_model_operations.py:95` rather than assuming
`result_json`.

- [ ] **Step 7: Write and pass the frontend test**

```jsx
  it("distinguishes a leader that reported no gaps from one that did not report", async () => {
    const { rerender } = render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          cycles: [{ id: "c1", sequence: 1, status: "completed", coverage_gaps: [] }]
        }}
      />
    );
    expect(screen.getByText(/누락 없다고 보고함/)).toBeInTheDocument();

    rerender(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          cycles: [{ id: "c1", sequence: 1, status: "completed" }]
        }}
      />
    );
    expect(screen.getByText(/커버리지를 보고하지 않음/)).toBeInTheDocument();
  });
```

Render it where cycles are already listed (`index.jsx` around line 1249 renders
`cycle.error_message`). Three states: a list of gaps, `누락 없다고 보고함` for an
empty list, and `커버리지를 보고하지 않음` when the field is absent.

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py \
        src/personal_agent_gateway/team_model_operations.py \
        src/personal_agent_gateway/api/team_runs.py \
        frontend/src/components/organisms/TeamRunDetail/ \
        tests/test_team_runtime.py
git commit -m "feat(team-runs): ask the leader which obligations no task owns"
```

---

## Task 6: Declare the contest stages

Do this before any contest behaviour: `tests/test_team_repair_stages.py` holds a
completeness test that fails the moment a stage exists without a repair mapping,
which is what catches the sites a new stage is read in.

**Files:**
- Modify: `src/personal_agent_gateway/team_model_operations.py:18` (the `OperationStage` literal) and `_built_in_result_validators()` at line 628
- Modify: `src/personal_agent_gateway/team_repair_stages.py:14`
- Test: `tests/test_team_repair_stages.py`, `tests/test_team_model_operations.py`

**Interfaces:**
- Produces: stages `cycle_contest` and `cycle_contest_repair`; `_valid_contest_verdict(payload: dict[str, object]) -> bool`; result kind `contest_verdict`.

- [ ] **Step 1: Write the failing validator tests**

Add to `tests/test_team_model_operations.py`:

```python
from personal_agent_gateway.team_model_operations import _valid_contest_verdict


def test_a_verdict_without_a_reason_is_invalid():
    """A verdict with no reason is worthless as a record, which is half of why
    this feature exists -- so it is a parse failure, not a defaulted field."""
    assert not _valid_contest_verdict({"kind": "reject", "reason": ""})
    assert not _valid_contest_verdict({"kind": "reject"})


def test_reject_carries_no_tasks_and_amend_carries_at_least_one():
    task = {
        "title": "Fix §1",
        "description": "Correct the reversed decision.",
        "owner_agent_id": None,
        "required": True,
        "acceptance": {"required_outputs": ["docs/srs.md"], "required_verifications": []},
    }
    assert _valid_contest_verdict({"kind": "reject", "reason": "task 7 covers it"})
    assert not _valid_contest_verdict(
        {"kind": "reject", "reason": "no", "tasks": [task]}
    )
    # The prompt shows every key, so a model will fill them all in. An empty
    # value for a field this kind does not use has to pass, or the repair is
    # spent on nearly every verdict.
    assert _valid_contest_verdict(
        {"kind": "reject", "reason": "no", "tasks": [], "question": None,
         "supersedes": []}
    )
    assert _valid_contest_verdict(
        {"kind": "amend", "reason": "ok", "tasks": [task], "question": ""}
    )
    assert not _valid_contest_verdict(
        {"kind": "amend", "reason": "ok", "tasks": [task],
         "question": "why are you asking?"}
    )
    assert _valid_contest_verdict({"kind": "amend", "reason": "agreed", "tasks": [task]})
    assert not _valid_contest_verdict({"kind": "amend", "reason": "agreed", "tasks": []})


def test_ask_back_needs_a_question():
    assert _valid_contest_verdict(
        {"kind": "ask_back", "reason": "ambiguous", "question": "which one?"}
    )
    assert not _valid_contest_verdict({"kind": "ask_back", "reason": "ambiguous"})


def test_overturning_a_decision_requires_the_work_to_correct_it():
    """If the leader admits an agreed decision is being reversed, correcting the
    document that still states the old decision comes out of the same verdict.
    Run 699c1915 reversed one with nothing but a quiet document edit."""
    task = {
        "title": "Fix §1",
        "description": "Correct the reversed decision.",
        "owner_agent_id": None,
        "required": True,
        "acceptance": {"required_outputs": ["docs/srs.md"], "required_verifications": []},
    }
    supersedes = [{"document_path": "docs/srs.md", "decision": "use a vetted library"}]
    assert not _valid_contest_verdict(
        {"kind": "amend", "reason": "r", "tasks": [], "supersedes": supersedes}
    )
    assert not _valid_contest_verdict(
        {"kind": "reject", "reason": "r", "supersedes": supersedes}
    )
    assert _valid_contest_verdict(
        {"kind": "amend", "reason": "r", "tasks": [task], "supersedes": supersedes}
    )


def test_an_unknown_kind_is_invalid():
    assert not _valid_contest_verdict({"kind": "whatever", "reason": "r"})
```

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_operations.py -q -p no:randomly -k contest or verdict`
Expected: FAIL — `ImportError: cannot import name '_valid_contest_verdict'`

- [ ] **Step 3: Add the stages and the validator**

In `team_model_operations.py`, add `"cycle_contest"` and `"cycle_contest_repair"`
to the `OperationStage` literal, then:

```python
_CONTEST_KINDS = {"amend", "partial", "reject", "ask_back"}


def _valid_contest_verdict(payload: dict[str, object]) -> bool:
    if set(payload) - {"kind", "reason", "tasks", "question", "supersedes"}:
        return False
    kind = payload.get("kind")
    if kind not in _CONTEST_KINDS:
        return False
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list) or not all(
        _valid_task_spec(task) for task in tasks
    ):
        return False
    if kind in {"amend", "partial"} and not tasks:
        return False
    if kind in {"reject", "ask_back"} and tasks:
        return False
    question = payload.get("question")
    if kind == "ask_back":
        if not isinstance(question, str) or not question.strip():
            return False
    # A model shown the full object in the prompt will send every key, so a
    # present-but-empty question on an amend is the normal case, not a defect.
    # Rejecting it would burn the repair on almost every verdict. A question with
    # actual content on a kind that has no question to ask is still wrong.
    elif question not in (None, ""):
        return False
    supersedes = payload.get("supersedes") or []
    if not isinstance(supersedes, list):
        return False
    for entry in supersedes:
        if not isinstance(entry, dict) or set(entry) != {
            "document_path",
            "decision",
        }:
            return False
        if not all(
            isinstance(entry[key], str) and entry[key].strip() for key in entry
        ):
            return False
    # Admitting a reversal without producing the work to correct the document
    # is exactly the FSRS episode this rule exists to prevent.
    if supersedes and not tasks:
        return False
    return True
```

Register it in `_built_in_result_validators()` at line 628, beside the planning
stages:

```python
        "cycle_contest": {"contest_verdict": _valid_contest_verdict},
        "cycle_contest_repair": {"contest_verdict": _valid_contest_verdict},
```

That registry rather than `team_model_effect_result_validators()` in
`team_model_effects.py`, because a verdict is task specs plus a decision and
`_valid_task_spec` is already here — `team_model_operations` is a leaf module that
must not import the domain modules above it. The constructor merges both
registries and raises on a duplicate stage-and-kind pair, so registering in both
places is an error, not a belt-and-braces measure.

In `team_repair_stages.py`, add `"cycle_contest": "cycle_contest_repair",`.

- [ ] **Step 4: Run the stage and validator tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py tests/test_team_model_operations.py -q -p no:randomly`
Expected: all pass. If the completeness test fails, it is naming a stage with no
repair mapping — add the mapping rather than relaxing the test.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_model_operations.py \
        src/personal_agent_gateway/team_repair_stages.py \
        tests/test_team_model_operations.py
git commit -m "feat(operations): declare the contest stages and their verdict shape"
```

---

## Task 7: Apply a verdict

**Files:**
- Modify: `src/personal_agent_gateway/team_model_effects.py:56` (the planning-stage set) and a new `apply_contest_verdict`
- Test: `tests/test_team_model_effects.py`

**Interfaces:**
- Consumes: `_valid_contest_verdict` and the `cycle_contest` stage from Task 6.
- Produces: `TeamModelEffectService.apply_contest_verdict(operation_id: str) -> ContestOutcome`, where `ContestOutcome` is a frozen dataclass `(kind: str, reason: str, tasks: list[TeamTask], question: str | None, supersedes: tuple[dict[str, str], ...])`.

- [ ] **Step 1: Write the failing test**

This file already has `make_completed_operation(tmp_path, *, stage, result)`
(line 57), which reserves an operation for the leader on a queued cycle and
completes it with the result you pass, and `valid_task_spec(title,
owner_agent_id)` (line 42). Use both.

```python
def test_applying_an_amend_creates_its_tasks_and_records_the_reason(tmp_path):
    """apply_plan already turns task specs into tasks, so an amend reuses it
    rather than growing a second way to create one."""
    services = make_completed_operation(
        tmp_path,
        stage="cycle_contest",
        result=ValidatedOperationResult(
            "contest_verdict",
            {
                "kind": "amend",
                "reason": "The plan left T-04 unowned.",
                "tasks": [valid_task_spec("Own discard", None)],
            },
        ),
    )

    outcome = services.effects.apply_contest_verdict(services.operation.id)

    assert outcome.kind == "amend"
    assert outcome.reason == "The plan left T-04 unowned."
    assert [task.title for task in outcome.tasks] == ["Own discard"]
    adjudications = [
        message
        for message in services.teams.list_messages(services.run.id)
        if message.kind == "plan_adjudication"
    ]
    assert len(adjudications) == 1
    assert "T-04" in adjudications[0].content
    assert services.operations.get(services.operation.id).status == "applied"


def test_applying_a_reject_creates_no_tasks_but_still_records_it(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_contest",
        result=ValidatedOperationResult(
            "contest_verdict",
            {"kind": "reject", "reason": "Task 7 already covers it."},
        ),
    )

    outcome = services.effects.apply_contest_verdict(services.operation.id)

    assert outcome.kind == "reject"
    assert outcome.tasks == []
    assert services.teams.list_tasks(services.run.id, services.cycle.id) == []
    assert any(
        message.kind == "plan_adjudication"
        for message in services.teams.list_messages(services.run.id)
    )


def test_a_superseded_decision_appears_in_the_record(tmp_path):
    """The FSRS episode left no trace precisely because the reversal was never
    written down anywhere a reader would find it."""
    services = make_completed_operation(
        tmp_path,
        stage="cycle_contest",
        result=ValidatedOperationResult(
            "contest_verdict",
            {
                "kind": "amend",
                "reason": "Allowing the in-repo implementation.",
                "tasks": [valid_task_spec("Correct srs section 1", None)],
                "supersedes": [
                    {
                        "document_path": "docs/english-learning/srs-algorithm.md",
                        "decision": "use a vetted FSRS library",
                    }
                ],
            },
        ),
    )

    services.effects.apply_contest_verdict(services.operation.id)

    content = next(
        message.content
        for message in services.teams.list_messages(services.run.id)
        if message.kind == "plan_adjudication"
    )
    assert "srs-algorithm.md" in content
    assert "use a vetted FSRS library" in content


def test_applying_twice_is_idempotent(tmp_path):
    """Every other effect in this module replays instead of doubling, because
    resume re-enters an applied operation after a restart."""
    services = make_completed_operation(
        tmp_path,
        stage="cycle_contest",
        result=ValidatedOperationResult(
            "contest_verdict",
            {
                "kind": "amend",
                "reason": "The plan left T-04 unowned.",
                "tasks": [valid_task_spec("Own discard", None)],
            },
        ),
    )

    first = services.effects.apply_contest_verdict(services.operation.id)
    second = services.effects.apply_contest_verdict(services.operation.id)

    assert [task.id for task in first.tasks] == [task.id for task in second.tasks]
    assert len(services.teams.list_tasks(services.run.id, services.cycle.id)) == 1
    assert (
        len(
            [
                message
                for message in services.teams.list_messages(services.run.id)
                if message.kind == "plan_adjudication"
            ]
        )
        == 1
    )
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_effects.py -q -p no:randomly -k contest`
Expected: FAIL — `AttributeError: 'TeamModelEffectService' object has no attribute 'apply_contest_verdict'`

- [ ] **Step 3: Implement it**

Follow `apply_plan` (line 108) for the transaction shape, the `applied` replay
branch, and the `completed` precondition. Reuse its task-creation path for the
`tasks` list so there is one way to create a task from a spec. Write one
`team_messages` row with kind `plan_adjudication`, sender the cycle's leader
agent, tied to the cycle, whose content states the verdict kind, the reason, and
each `supersedes` entry as `document_path — decision`.

Add `"cycle_contest"` and `"cycle_contest_repair"` to the stage set at line 56.

- [ ] **Step 4: Run and watch it pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_effects.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_model_effects.py tests/test_team_model_effects.py
git commit -m "feat(operations): apply a contest verdict and record its reason"
```

---

## Task 8: Adjudicate in the runtime

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — a `CONTEST_PROMPT` constant, `adjudicate_contest`, and the `_execute` allowlist at line 1405
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `apply_contest_verdict` from Task 7; `_invoke_with_repair` and `raise_system_decision` already in the tree.
- Produces: `TeamRuntime.adjudicate_contest(team_run_id: str, cycle_id: str, objection: str) -> ContestOutcome`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_an_amend_verdict_creates_the_task_it_promised(tmp_path):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps({
                "kind": "amend",
                "reason": "T-04 had no owner.",
                "tasks": [{
                    "title": "Own discard",
                    "description": "Implement T-04.",
                    "owner_agent_id": None,
                    "required": True,
                    "acceptance": {
                        "required_outputs": ["src/discard.py"],
                        "required_verifications": [],
                    },
                }],
            }),
            [],
        ),
    ]

    outcome = await setup.runtime.adjudicate_contest(
        setup.run.id, setup.cycle.id, "T-04 and T-15 have no owner"
    )

    assert outcome.kind == "amend"
    assert [t.title for t in setup.teams.list_tasks(setup.run.id)] == ["Own discard"]


@pytest.mark.asyncio
async def test_a_verdict_with_no_reason_is_repaired_once(tmp_path):
    """The repair seam every stage now goes through gives this for free; the
    test is here to prove cycle_contest is actually on it."""
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.lead_client.responses = [
        ModelResponse(json.dumps({"kind": "reject"}), []),
        ModelResponse(json.dumps({"kind": "reject", "reason": "task 7 covers it"}), []),
    ]

    outcome = await setup.runtime.adjudicate_contest(
        setup.run.id, setup.cycle.id, "nothing owns T-04"
    )

    assert outcome.kind == "reject"
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:cycle_contest_repair:0"
    ).status == "applied"


@pytest.mark.asyncio
async def test_ask_back_pauses_the_run_for_the_user(tmp_path):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps({
                "kind": "ask_back",
                "reason": "The objection could mean two things.",
                "question": "Do you mean T-04 or T-12?",
            }),
            [],
        ),
    ]

    await setup.runtime.adjudicate_contest(
        setup.run.id, setup.cycle.id, "discard is missing"
    )

    assert setup.teams.get_team_run(setup.run.id).status == "waiting_for_user"
    request = setup.teams.get_active_decision_request(setup.run.id)
    assert "T-04 or T-12" in request.items[0]["question"]


@pytest.mark.asyncio
async def test_the_prompt_carries_the_previous_rejection(tmp_path):
    """A leader that cannot see why it refused last time will either repeat the
    refusal blindly or contradict itself."""
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.lead_client.responses = [
        ModelResponse(json.dumps({"kind": "reject", "reason": "task 7 covers it"}), []),
    ]
    await setup.runtime.adjudicate_contest(
        setup.run.id, setup.cycle.id, "nothing owns T-04"
    )
    setup.lead_client.responses = [
        ModelResponse(json.dumps({"kind": "reject", "reason": "still covered"}), []),
    ]

    await setup.runtime.adjudicate_contest(
        setup.run.id, setup.cycle.id, "task 7 does not cover T-04"
    )

    assert "task 7 covers it" in setup.lead_client.requests[-1][0]["content"]
```

`setup.lead_client.requests` is how the fake client records what it was sent —
confirm the attribute name on the fake in this test file and use the real one.

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k contest`
Expected: FAIL — `AttributeError: … has no attribute 'adjudicate_contest'`

- [ ] **Step 3: Implement it**

Add the prompt. It must give the leader the plan, the objection, and the earlier
verdicts, and must state the verdict shape:

```python
CONTEST_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
The user is contesting the plan, not adding work to it. Judge the objection.
Goal: {goal}
Current tasks:
{tasks}
Earlier objections and how you ruled on them:
{history}
The user's objection:
{objection}

Return ONLY one JSON object:
{{"kind":"amend|partial|reject|ask_back","reason":"why you ruled this way",
"tasks":[{{"title":"...","description":"...","owner_agent_id":"member id or null",
"required":true,"acceptance":{{"required_outputs":["..."],"required_verifications":["..."]}}}}],
"question":"ask_back only","supersedes":[{{"document_path":"...","decision":"..."}}]}}
reason is required for every kind. amend and partial carry at least one task;
reject and ask_back carry none. If ruling for the user reverses a decision an
accepted document still states, list it in supersedes and include a task that
corrects that document -- a supersedes entry without a task is rejected.
If the objection is that a finished task's acceptance criteria were too narrow,
do not try to rewrite that task: create a follow-up task carrying the criteria
that should have been there. A settled task's contract cannot be revised."""
```

That last instruction is not decoration. `record_acceptance_decision`'s
`revise_acceptance` requires the task to be `in_progress`, and a contest is
adjudicated after the cycle settles, so a leader that tries to amend a finished
contract has no path that works. Telling it the available move is cheaper than
adding an effect that rewrites terminal state.

`adjudicate_contest` builds the messages, calls `_invoke_with_repair` with stage
`cycle_contest`, ordinal 0, parser `_validated_contest_verdict`, and
`on_exhausted=None` — leader escalation already covers a second failure. Then
call `apply_contest_verdict`. For `ask_back`, publish through
`raise_system_decision(team_run_id, cycle_id, topic=…, question=…)` and return
the outcome; the run lands `waiting_for_user` exactly as a leader escalation
does.

Build `{history}` from `team_messages` rows of kind `plan_adjudication` for the
run, oldest first, rendering each as its kind and reason.

Add `"cycle_contest"` and `"cycle_contest_repair"` to the `continue` group of
`_execute`'s allowlist at line 1405 — a recovered contest is preplanning, like
add-work, and must not be treated as an unknown stage.

**Then handle it in `_recover_open_operation`, which is the step easy to miss and
expensive to skip.** That method ends with

```python
        raise OperationConflict(
            f"Open operation stage {operation.stage} is not recoverable here"
        )
```

so a stage it does not name is permanently unrecoverable — and `_execute` calls
it *before* consulting the allowlist you just widened, meaning a restart in the
middle of a contest would strand the cycle no matter what line 1405 says. Add a
branch beside the planning one (line 653) for `cycle_contest` and
`cycle_contest_repair`: on `completed`, apply the verdict; on `prepared`, rebuild
the objection from `get_cycle_effective_instruction(cycle_id)` and re-invoke via
`_invoke_existing_operation`, mirroring what the add-work branch does with its
instruction.

- [ ] **Step 4: Write the failing recovery test**

```python
@pytest.mark.asyncio
async def test_a_prepared_contest_is_resumable_after_a_restart(tmp_path):
    """Without a recovery branch this raises "is not recoverable here" and the
    cycle can never move again."""
    setup = make_operation_runtime(tmp_path, cycle_instruction="T-04 has no owner")
    setup.operations.reserve(
        _operation_spec(
            setup.run,
            setup.cycle.id,
            setup.teams.get_agent(setup.run.leader_agent_id),
            "cycle_contest",
            0,
            [{"role": "user", "content": "irrelevant"}],
        )
    )
    setup.lead_client.responses = [
        ModelResponse(json.dumps({"kind": "reject", "reason": "task 7 covers it"}), []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:cycle_contest:0"
    ).status == "applied"
```

The reserved operation's request digest must match the messages the recovery path
rebuilds, or `reserve` refuses the re-invocation with "already bound to another
request" — that is exactly what the digest is for, and it is why the branch has to
rebuild the prompt from the cycle's stored instruction rather than from anything
held in memory.

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k prepared_contest`
Expected: FAIL with `OperationConflict: Open operation stage cycle_contest is not recoverable here`, then PASS once the branch exists.

- [ ] **Step 5: Run the whole runtime suite**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runtime): let the leader adjudicate a contested plan"
```

---

## Task 9: Route a contest through the cycle queue

**Files:**
- Modify: `src/personal_agent_gateway/team_cycles.py:1242` (the `source_type` allowlist)
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py` — the dispatch call site around line 208 and `_resume_operation` at line 343
- Modify: `src/personal_agent_gateway/team_run_orchestrator.py` — an `adjudicate_contest` scheduling method beside `continue_cycle`
- Modify: `src/personal_agent_gateway/team_provider_recovery.py:678, 737`
- Test: `tests/test_team_cycle_dispatcher.py`, `tests/test_team_cycles.py`

**Interfaces:**
- Consumes: `TeamRuntime.adjudicate_contest` from Task 8.
- Produces: `source_type` `"contest"` accepted by `enqueue_request`; `TeamRunOrchestrator.adjudicate_contest(team_run_id, cycle_id, objection) -> asyncio.Task`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_cycles.py`, using that file's existing
`make_cycle_services(tmp_path, "triggered")`:

```python
def test_a_contest_request_can_be_enqueued(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")

    created = cycles.enqueue_request(
        run.id, "contest", "client-1", "T-04 has no owner"
    )

    assert created.source_type == "contest"
    assert created.status == "queued"


def test_the_same_contest_twice_returns_the_same_request(tmp_path):
    """contest joins the idempotent group, so a double-submitted objection does
    not queue two adjudications of the same thing."""
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")

    first = cycles.enqueue_request(run.id, "contest", "client-1", "T-04 has no owner")
    second = cycles.enqueue_request(run.id, "contest", "client-1", "T-04 has no owner")

    assert first.id == second.id


def test_a_contest_waits_while_another_request_is_dispatching(tmp_path):
    """Serialization is already there -- claim_next refuses while a request is
    dispatching -- and it is why a contest cannot reproduce the mid-flight
    collision /add-work caused with cancel."""
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycles.enqueue_request(run.id, "manual", "client-1", "do the work")
    assert cycles.claim_next(run.id) is not None
    cycles.enqueue_request(run.id, "contest", "client-2", "T-04 has no owner")

    assert cycles.claim_next(run.id) is None
```

In `tests/test_team_cycle_dispatcher.py`, `make_dispatcher_services(tmp_path)`
(line 53) already wires a `RecordingOrchestrator`. Add an
`adjudicate_contest(team_run_id, cycle_id, objection)` method to that recorder
that appends to a new `contests` list and returns a completed task the same way
its `run_cycle` does — extend the existing recorder rather than adding a second
one.

```python
@pytest.mark.asyncio
async def test_the_dispatcher_adjudicates_a_contest_instead_of_planning(tmp_path):
    services = make_dispatcher_services(tmp_path)
    services.cycles.enqueue_request(
        services.run.id, "contest", "client-1", "T-04 has no owner"
    )

    await services.dispatcher.run_one(services.run.id)

    assert [objection for _run, _cycle, objection in services.orchestrator.contests] == [
        "T-04 has no owner"
    ]
    assert services.orchestrator.cycles == []


@pytest.mark.asyncio
async def test_a_manual_request_still_plans(tmp_path):
    """Guards the branch: adding the contest path must not divert ordinary work."""
    services = make_dispatcher_services(tmp_path)
    services.cycles.enqueue_request(services.run.id, "manual", "client-1", "do it")

    await services.dispatcher.run_one(services.run.id)

    assert services.orchestrator.contests == []
    assert len(services.orchestrator.cycles) == 1
```

`services.orchestrator.cycles` is whatever the recorder already names its
`run_cycle` log — read `RecordingOrchestrator` and use the real attribute.

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py -q -p no:randomly -k contest`
Expected: FAIL — `ValueError: Unsupported cycle request source: contest`

- [ ] **Step 3: Implement the routing**

In `team_cycles.py`, add `"contest"` to the idempotent group alongside
`{"manual", "hook", "knowledge_request"}` in both places at lines 1242 and 1261 —
`knowledge_request` is the precedent for a different purpose on the same queue.

In the dispatcher, branch on the claimed request rather than always calling
`run_cycle`:

```python
            if request.source_type == "contest":
                await self._orchestrator.adjudicate_contest(
                    team_run_id, cycle.id, instruction
                )
            else:
                await self._orchestrator.run_cycle(
                    team_run_id, cycle.id, instruction
                )
```

In `_resume_operation`, treat a recovered `cycle_contest` or
`cycle_contest_repair` like the add-work branch: read the cycle's effective
instruction and schedule `adjudicate_contest`.

In `team_provider_recovery.py`, add both stages to the two preplanning
predicates so a recovered contest validates against `cycle.status == "queued"`
and `task_id is None`, and restores to the same statuses add-work does.

In the orchestrator, add beside `continue_cycle`:

```python
    def adjudicate_contest(
        self, team_run_id: str, cycle_id: str, objection: str
    ) -> asyncio.Task:
        runtime = self._runtime_provider()

        async def execute() -> TeamRun:
            await runtime.adjudicate_contest(team_run_id, cycle_id, objection)
            return await runtime.resume(team_run_id, cycle_id)

        return self._schedule(team_run_id, cycle_id, execute)
```

`resume` after the verdict is what executes an amend's new tasks and settles a
rejected contest's cycle. Add `adjudicate_contest` to `TeamRuntimeProtocol`.

- [ ] **Step 4: Run and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py tests/test_team_provider_recovery.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_cycles.py \
        src/personal_agent_gateway/team_cycle_dispatcher.py \
        src/personal_agent_gateway/team_run_orchestrator.py \
        src/personal_agent_gateway/team_provider_recovery.py \
        tests/test_team_cycles.py tests/test_team_cycle_dispatcher.py
git commit -m "feat(team-runs): route a contest through the cycle request queue"
```

---

## Task 10: The endpoint and the payload

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` — a `ContestRequest` model, `POST /{team_run_id}/contests`, and `contests` in the detail payload
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `enqueue_request` with `source_type` `"contest"` from Task 9.
- Produces: `POST /api/team-runs/{id}/contests` taking `{"objection": str, "client_request_id": str}` and returning `{"cycle_request": …, "queue_position": int}`; `detail["contests"]` as a list of `{objection, kind, reason, supersedes, created_at}`.

- [ ] **Step 1: Write the failing test**

Use `create_standard_run(client.app, leader_id, [member_id])` (line 554), which
creates the run through the service directly — the same helper the 409-guard
tests use, and the reason a previous plan's test failed was reaching for a
`continuous` run whose guards fire first.

```python
def test_contesting_the_plan_queues_a_request(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    create_team(client, leader_id, [member_id])
    run = create_standard_run(client.app, leader_id, [member_id])

    response = client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "T-04 and T-15 have no owner", "client_request_id": "c1"},
    )

    assert response.status_code == 200
    assert response.json()["cycle_request"]["source_type"] == "contest"


def test_contesting_the_same_objection_twice_is_idempotent(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    create_team(client, leader_id, [member_id])
    run = create_standard_run(client.app, leader_id, [member_id])
    payload = {"objection": "T-04 has no owner", "client_request_id": "c1"}

    first = client.post(f"/api/team-runs/{run['id']}/contests", json=payload).json()
    second = client.post(f"/api/team-runs/{run['id']}/contests", json=payload).json()

    assert first["cycle_request"]["id"] == second["cycle_request"]["id"]


def test_a_canceled_run_refuses_a_contest(tmp_path: Path) -> None:
    """claim_next raises for a canceled run, and enqueue_request refuses too, so
    the endpoint has to surface that as a 409 rather than a 500."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    create_team(client, leader_id, [member_id])
    run = create_standard_run(client.app, leader_id, [member_id])
    client.post(f"/api/team-runs/{run['id']}/cancel")

    response = client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "too late", "client_request_id": "c1"},
    )

    assert response.status_code == 409
```

If `enqueue_request` turns out not to refuse a canceled run — `claim_next` does,
but they are separate methods — then this test is telling you the endpoint needs
the same explicit `canceled` guard `add_work` carries at `api/team_runs.py:849`.
Add the guard; do not weaken the test.

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly -k contest`
Expected: FAIL with 404 — the route does not exist.

- [ ] **Step 3: Add the endpoint**

Copy `trigger_cycle` (line 202) — it already has the `require_intake_open` gate,
the `KeyError` → 404 and `ValueError` → 409 mapping, the queued event, the
dispatcher enqueue, and the audit record. Change the source type to `"contest"`,
the event type to `team.contest.queued`, and the audit action to
`team_runs.contest_plan`.

For `detail["contests"]`, join each `contest` cycle request to the
`plan_adjudication` message written for its cycle, so the objection and the
verdict appear as one row. A request that has not been adjudicated yet carries a
null `kind`.

- [ ] **Step 4: Run and watch it pass**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py
git commit -m "feat(api): accept a plan contest and report its verdict"
```

---

## Task 11: Contest from the UI

**Files:**
- Modify: `frontend/src/api/client.js` — a `contestPlan` call
- Create: `frontend/src/components/organisms/TeamRunDetail/ContestPanel.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: `POST /api/team-runs/{id}/contests` and `detail.contests` from Task 10.
- Produces: `ContestPanel` as a named export.

- [ ] **Step 1: Write the failing test**

```jsx
  it("lets the operator contest the plan and shows how it was ruled on", async () => {
    const onContest = vi.fn().mockResolvedValue({ ok: true });
    render(
      <TeamRunDetail
        onContestPlan={onContest}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          contests: [{
            objection: "T-04 has no owner",
            kind: "reject",
            reason: "task 7 covers it",
            supersedes: [],
            created_at: "2026-08-12T00:00:00Z"
          }]
        }}
      />
    );

    expect(screen.getByText(/task 7 covers it/)).toBeInTheDocument();

    await userEvent.type(
      screen.getByRole("textbox", { name: /계획에 이의/ }),
      "T-15 also has no owner"
    );
    await userEvent.click(screen.getByRole("button", { name: /이의 보내기/ }));

    expect(onContest).toHaveBeenCalledWith("r1", "T-15 also has no owner");
  });
```

- [ ] **Step 2: Run and watch it fail**

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: FAIL — no such textbox.

- [ ] **Step 3: Build the panel**

`ContestPanel` renders the existing contests (objection, verdict kind, reason,
and each `supersedes` entry) plus a labelled textarea and a submit button that
calls `onContestPlan(runId, objection)` and clears on success. A contest with a
null `kind` renders as `판정 대기`. Add `contestPlan` to `frontend/src/api/client.js`
following `deleteTeamRun`'s shape, which returns `{ok, status, detail}` so the
caller can surface a rejection reason rather than a bare failure.

- [ ] **Step 4: Run the frontend suite**

Run: `npm --prefix frontend test`
Expected: 41 files pass, ArchiveView flakes aside.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/TeamRunDetail/
git commit -m "feat(team-runs): contest the plan from the run detail view"
```

---

## Task 12: Verify the whole loop and finish

- [ ] **Step 1: Full backend suite, blocking**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: **21 failed**, the same pre-existing set, with passes up by this plan's
new tests. Any other failure blocks completion. Do not background this run — a
backgrounded suite delivers no notification until it exits and the summary is
what matters.

- [ ] **Step 2: Full frontend suite**

Run: `npm --prefix frontend test`
Expected: 41 files pass.

- [ ] **Step 3: Lint**

Run: `python -m ruff check src/personal_agent_gateway/ tests/`
Expected: `All checks passed!` on both. `tests/test_team_runtime.py` carried a
pre-existing unused import of `_operation_spec`; Task 8's recovery test uses it,
so that error should be gone by now. If ruff still reports it, Task 8's test is
not importing what it should.

- [ ] **Step 4: Live verification**

Restart so both the Python changes and the rebuilt bundle load:

```bash
npm run stop && npm start
```

Then, against the real database:

- Open run `699c1915fa764be598586d2f8bb3a170`. Confirm the TASKS tab shows a
  non-zero `워커 신고만으로 통과` count and that task
  `관리자 콘텐츠 등록·LLM 재가공 백엔드 구현` shows `파일 내용 확인` for its
  verifications. This run's contracts were satisfied by file reads, so a zero
  count means the wiring is wrong.
- Contest the plan on a run you can afford to move: send
  `T-04 and T-15 have no owner`. Confirm the request queues rather than
  interrupting, that a verdict comes back with a reason, and that the reason
  appears in the panel. If the verdict is `amend`, confirm the new task exists
  and carries the contract the verdict named.
- Send a second contest and confirm the leader's prompt carried the first
  verdict — check the `cycle_contest` operation's stored request, or the
  `plan_adjudication` messages, rather than trusting the model's wording.

Write down what you actually observed, including anything you could not arrange.
The defects this plan addresses are ones no test caught, so test output alone is
not evidence the live path works.

- [ ] **Step 5: Commit the verification record and finish**

Append what you observed to the spec's own verification section, commit, then use
`superpowers:finishing-a-development-branch`.

```bash
git add docs/superpowers/specs/2026-08-12-team-run-plan-visibility-and-contest-design.md
git commit -m "docs(team-runs): record what the contest loop did live"
```

---

## Deliberately not in this plan

- **Finding 1** — acceptance rejecting a worker whose declared deliverables are
  not exactly `required_outputs`. That rule ended run `699c1915` and needs its own
  design; Task 1 makes the rejection legible but does not change the rule.
- **Finding 2's second half** — letting the environment declare what it can
  verify, so a planner cannot write a contract whose only checks are file reads
  for code that cannot be built here. Parts 1 and 2 make the weakness visible.
  Nothing here adds a check kind that compiles or runs anything.
- **Machine extraction of obligations from documents.** Part 2 asks the leader
  and shows what it said, including when it said nothing. It does not read
  `service-plan.md` and count `T-xx` itself.
