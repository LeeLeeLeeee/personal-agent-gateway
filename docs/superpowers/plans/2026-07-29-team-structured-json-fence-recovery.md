# Team Structured JSON Fence Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Team task plans and worker outcomes to accept one exact outer lowercase `json` Markdown fence while preserving every existing JSON schema, path, acceptance, and evidence validation.

**Architecture:** Add a pure Team-owned envelope normalizer that only trims raw content or unwraps one complete outer `json` fence. Call it at the two strict model-output boundaries, `_parse_task_plan()` and `parse_task_outcome()`, then let their existing JSON and domain validation run unchanged.

**Tech Stack:** Python 3.11+, standard-library string handling and `json`, pytest/pytest-asyncio, Ruff

## Global Constraints

- Accept only raw JSON or one complete lowercase `json` Markdown fence after trimming outer whitespace.
- Do not extract JSON from prose, multiple fences, non-`json` fences, or unterminated fences.
- Do not repair malformed JSON or weaken missing/unknown-field rejection in the exact task-plan and `TaskOutcome` schemas.
- Do not change provider retry counts, Team Run state transitions, APIs, database schemas, or stored error codes.
- Do not apply normalization to mediation, `needs_info`, Library Drafts, delivery-file JSON, or API JSON.
- Do not automatically retry or mutate failed Team Run `af7c358273c54fb0b522b0b66d054a57`.
- Preserve unrelated working-tree changes, especially `src/personal_agent_gateway/health.py` and Archive frontend files.

## File Structure

- Create `src/personal_agent_gateway/team_structured_output.py`: pure envelope recognition and removal only.
- Create `tests/test_team_structured_output.py`: exhaustive unit contract for accepted and rejected envelope shapes.
- Modify `src/personal_agent_gateway/team_runtime.py`: normalize planner content immediately before its existing fence guard and `json.loads`.
- Modify `tests/test_team_runtime.py`: planner consumer and continuous-cycle regressions.
- Modify `src/personal_agent_gateway/team_outcomes.py`: normalize worker final content immediately before its existing empty/fence guard and `json.loads`.
- Modify `tests/test_team_outcomes.py`: fenced success plus ambiguous-envelope and existing validation regressions.

---

### Task 1: Exact JSON Envelope Normalizer

**Files:**
- Create: `tests/test_team_structured_output.py`
- Create: `src/personal_agent_gateway/team_structured_output.py`

**Interfaces:**
- Consumes: model response text as `content: str`.
- Produces: `normalize_json_envelope(content: str) -> str`, returning trimmed raw text or the trimmed body of one exact outer lowercase `json` fence.

- [ ] **Step 1: Write the failing normalizer contract tests**

```python
import pytest

from personal_agent_gateway.team_structured_output import (
    normalize_json_envelope,
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('  {"ok": true}  ', '{"ok": true}'),
        (" \n```json\n{\"ok\": true}\n```\n ", '{"ok": true}'),
        ("```json\r\n{\"ok\": true}\r\n```", '{"ok": true}'),
    ],
)
def test_normalize_json_envelope_accepts_raw_or_one_outer_json_fence(
    content: str,
    expected: str,
) -> None:
    assert normalize_json_envelope(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        'before\n```json\n{"ok": true}\n```',
        '```json\n{"ok": true}\n```\nafter',
        '```json\n{"ok": true}\n```\n```json\n{"other": true}\n```',
        '```json\n{"ok": true}',
        '```JSON\n{"ok": true}\n```',
        '```\n{"ok": true}\n```',
    ],
)
def test_normalize_json_envelope_leaves_ambiguous_fences_invalid(
    content: str,
) -> None:
    assert normalize_json_envelope(content) == content.strip()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_structured_output.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'personal_agent_gateway.team_structured_output'`.

- [ ] **Step 3: Implement the smallest pure normalizer**

```python
def normalize_json_envelope(content: str) -> str:
    """Return raw JSON text, unwrapping one exact outer `json` fence."""
    stripped = content.strip()
    if not stripped.startswith("```json"):
        return stripped

    opening, newline, remainder = stripped.partition("\n")
    if not newline or opening.rstrip("\r") != "```json":
        return stripped

    body, newline, closing = remainder.rpartition("\n")
    if (
        not newline
        or closing.rstrip("\r") != "```"
        or "```" in body
    ):
        return stripped
    return body.strip()
```

- [ ] **Step 4: Run the normalizer contract and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_structured_output.py -q
```

Expected: `9 passed`.

- [ ] **Step 5: Commit the focused deliverable**

```powershell
git add src/personal_agent_gateway/team_structured_output.py tests/test_team_structured_output.py
git commit -m "fix(team): JSON 응답 envelope 정규화 추가"
```

---

### Task 2: Task-Plan Parser and Continuous-Cycle Recovery

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:1-25`
- Modify: `src/personal_agent_gateway/team_runtime.py:1092-1097`
- Modify: `tests/test_team_runtime.py:1-20`
- Modify: `tests/test_team_runtime.py:318-422`
- Modify: `tests/test_team_runtime.py:1348-1428`

**Interfaces:**
- Consumes: `normalize_json_envelope(content: str) -> str` from Task 1.
- Produces: `_parse_task_plan(content: str) -> list[dict[str, object]]` accepting a valid raw array or one exact outer `json` fence; `TeamRuntime.add_work()` keeps its existing retry and task-creation behavior.

- [ ] **Step 1: Add a fenced task-plan parser regression**

Add a valid complete task object so the test exercises only envelope handling:

```python
def test_task_plan_accepts_one_outer_json_fence() -> None:
    tasks = _parse_task_plan(
        """```json
[{
  "title": "Create D3 guide",
  "description": "Write the integrated guide.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": ["outputs/d3-guide.md"],
    "required_verifications": ["markdown-link-check"]
  }
}]
```"""
    )

    assert tasks[0]["title"] == "Create D3 guide"
    assert tasks[0]["acceptance"] == TaskAcceptance(
        required_outputs=("outputs/d3-guide.md",),
        required_verifications=("markdown-link-check",),
    )
```

- [ ] **Step 2: Add parser rejection cases proving strictness remains**

```python
@pytest.mark.parametrize(
    "payload",
    [
        'before\n```json\n[]\n```',
        '```json\n[]\n```\nafter',
        '```JSON\n[]\n```',
        '```json\n[{\n```',
    ],
)
def test_task_plan_rejects_ambiguous_json_envelopes(payload: str) -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _parse_task_plan(payload)
```

Keep `test_task_plan_rejects_incomplete_or_unsafe_acceptance` unchanged; it is the regression gate for exact fields, bounded paths, duplicate outputs, and non-empty acceptance.

- [ ] **Step 3: Run parser tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -q -k "task_plan_accepts_one_outer_json_fence or task_plan_rejects_ambiguous_json_envelopes"
```

Expected: the valid fenced-plan test fails with `Planner response must not use code fences`; ambiguous cases remain rejected.

- [ ] **Step 4: Connect the normalizer to `_parse_task_plan()`**

Add the import:

```python
from personal_agent_gateway.team_structured_output import normalize_json_envelope
```

Replace only the first line inside `_parse_task_plan()`:

```python
def _parse_task_plan(content: str) -> list[dict[str, object]]:
    stripped = normalize_json_envelope(content)
    if stripped.startswith("```"):
        raise ValueError("Planner response must not use code fences")
    raw = json.loads(stripped)
```

Do not alter the existing array, field, owner, acceptance, duplicate, or path checks.

- [ ] **Step 5: Run parser and existing validation tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -q -k "task_plan"
```

Expected: all selected tests pass.

- [ ] **Step 6: Add the continuous-cycle runtime regression**

Use the real `TeamRuntime.add_work()` and `resume()` sequence so a fenced plan must create a task before execution can proceed:

```python
@pytest.mark.asyncio
async def test_continuous_cycle_with_fenced_plan_creates_tasks_and_resumes(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-fenced-plan")
    fenced_plan = """```json
[{
  "title": "Process request",
  "description": "Produce the requested result.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": [],
    "required_verifications": ["worker-result"]
  }
}]
```"""
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [fenced_plan, "cycle summary"],
            ["worker result"],
        ),
    )

    created = await runtime.add_work(run.id, "process request", cycle.id)
    completed = await runtime.resume(run.id, cycle.id)

    assert [task.title for task in created] == ["Process request"]
    assert completed.status == "completed"
    assert teams.get_cycle(cycle.id).status == "completed"
```

- [ ] **Step 7: Run the cycle regression and relevant runtime suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_runtime.py -q -k "fenced_plan or continuous_run_executes_and_synthesizes_each_cycle_in_isolation or add_work_creates_pending_tasks"
```

Expected: all selected tests pass, proving valid fenced output no longer consumes the planner retry or prevents `resume()`.

- [ ] **Step 8: Commit the planner recovery**

```powershell
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "fix(team): fenced task plan 처리"
```

---

### Task 3: Worker TaskOutcome Recovery

**Files:**
- Modify: `src/personal_agent_gateway/team_outcomes.py:1-45`
- Modify: `tests/test_team_outcomes.py:1-100`
- Modify: `tests/test_team_runtime.py:150-235`

**Interfaces:**
- Consumes: `normalize_json_envelope(content: str) -> str` from Task 1.
- Produces: `parse_task_outcome(content: str) -> TaskOutcome` accepting a valid raw object or one exact outer `json` fence while continuing to raise `TaskOutcomeError(code="invalid_task_outcome")` for all invalid content.

- [ ] **Step 1: Add a valid fenced worker outcome regression**

```python
def test_parses_task_outcome_inside_one_outer_json_fence() -> None:
    payload = {
        "status": "completed",
        "summary": "Verification finished.",
        "reason_code": None,
        "deliverables": [
            {"path": "outputs/report.md", "kind": "markdown"}
        ],
        "verifications": [
            {
                "name": "pytest",
                "status": "passed",
                "evidence": "42 tests passed",
            }
        ],
    }
    outcome = parse_task_outcome(
        f"```json\n{json.dumps(payload)}\n```"
    )

    assert outcome.status == "completed"
    assert outcome.summary == "Verification finished."
    assert outcome.deliverables == (
        Deliverable("outputs/report.md", "markdown"),
)
```

- [ ] **Step 2: Replace the obsolete fenced-empty rejection with ambiguous fences**

Remove only `"```json\n{}\n```"` from the existing malformed payload list because a fenced object now reaches schema validation. Add these payloads to the same list:

```python
'before\n```json\n{}\n```',
'```json\n{}\n```\nafter',
'```JSON\n{}\n```',
'```json\n{}\n```\n```json\n{}\n```',
```

The existing missing-field, invalid-status, absolute/traversal path, duplicate verification, and invalid evidence-status cases remain unchanged.

- [ ] **Step 3: Run outcome tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_outcomes.py -q
```

Expected: only `test_parses_task_outcome_inside_one_outer_json_fence` fails with `TaskOutcomeError`; all invalid cases still pass.

- [ ] **Step 4: Connect the normalizer to `parse_task_outcome()`**

Add:

```python
from personal_agent_gateway.team_structured_output import normalize_json_envelope
```

Change only the envelope line:

```python
def parse_task_outcome(content: str) -> TaskOutcome:
    stripped = normalize_json_envelope(content)
    if not stripped or stripped.startswith("```"):
        raise TaskOutcomeError()
```

Leave exact object fields, status, reason code, deliverable path, duplicate, and verification evidence validation unchanged.

- [ ] **Step 5: Run outcome tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_outcomes.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Add a runtime acceptance-path regression for a fenced worker outcome**

Add beside `test_worker_final_response_is_parsed_as_task_outcome`:

```python
@pytest.mark.asyncio
async def test_fenced_worker_outcome_reaches_normal_acceptance_path(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Inspect",
        "Inspect dashboard",
        acceptance=TaskAcceptance((), ("pytest",)),
    )
    payload = json.dumps(
        {
            "status": "completed",
            "summary": "Done",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "pytest",
                    "status": "passed",
                    "evidence": "tests passed",
                }
            ],
        }
    )
    runtime = TeamRuntime(
        teams,
        lambda _agent: FakeModel(
            f"```json\n{payload}\n```",
            normalize_worker=False,
        ),
    )

    outcome = await runtime._run_task(
        run,
        leader_agent,
        worker_agent,
        task,
    )

    assert outcome.status == "completed"
    assert outcome.reason_code is None
    assert outcome.verifications[0].name == "pytest"
```

- [ ] **Step 7: Run parser and runtime outcome regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_outcomes.py tests\test_team_runtime.py -q -k "task_outcome or worker_outcome or worker_final_response"
```

Expected: all selected tests pass; malformed worker output still maps to `invalid_task_outcome`.

- [ ] **Step 8: Commit the worker recovery**

```powershell
git add src/personal_agent_gateway/team_outcomes.py tests/test_team_outcomes.py tests/test_team_runtime.py
git commit -m "fix(team): fenced task outcome 처리"
```

---

### Task 4: Scope and Regression Verification

**Files:**
- Verify only; no source changes expected.

**Interfaces:**
- Consumes: completed changes from Tasks 1-3.
- Produces: evidence that Team structured output is recovered without affecting unrelated protocols or the wider backend.

- [ ] **Step 1: Run all focused Team structured-output and runtime tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_team_structured_output.py tests\test_team_outcomes.py tests\test_team_runtime.py tests\test_team_run_orchestrator.py tests\test_team_cycle_dispatcher.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run protocol-adjacent regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_archive.py tests\test_runtime.py tests\test_team_results.py -q
```

Expected: all tests pass, covering unchanged Library Draft and result flows.

- [ ] **Step 3: Run Ruff on the touched source and tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check src\personal_agent_gateway\team_structured_output.py src\personal_agent_gateway\team_runtime.py src\personal_agent_gateway\team_outcomes.py tests\test_team_structured_output.py tests\test_team_runtime.py tests\test_team_outcomes.py
```

Expected: `All checks passed!`

- [ ] **Step 4: Run the complete backend suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass. If an unrelated pre-existing failure appears, record the exact test and demonstrate that all focused gates above remain green.

- [ ] **Step 5: Confirm the surgical diff and protected files**

Run:

```powershell
git diff -- src/personal_agent_gateway/team_structured_output.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_outcomes.py tests/test_team_structured_output.py tests/test_team_runtime.py tests/test_team_outcomes.py
git status --short
```

Expected: implementation changes are limited to the six planned files; unrelated dirty Archive files and `src/personal_agent_gateway/health.py` remain untouched.
