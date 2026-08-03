# Typed Acceptance Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a task's acceptance carry checks the server runs itself, so "this verification passed" stops meaning "the worker said so".

**Architecture:** A required verification becomes `{name, check}` where `check` is one of four file-based types or absent. A new leaf module owns the check vocabulary, the workspace path safety, and the runner. `TaskAcceptance` carries the richer type; a plain string stays valid and parses to a check-less (attested) verification, so stored rows and in-flight runs keep working. `TeamAcceptanceService` runs every check itself and ignores the worker's self-reported status for those, keeping today's rule only for attested ones.

**Tech Stack:** Python 3.12, FastAPI, SQLite (raw `sqlite3`), pytest / pytest-asyncio, ruff.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-team-output-contract-enforcement-design.md`, **Part B only**. Part A (the cycle output contract) is already merged.
- This plan is **backend only and deliberately inert in production**: nothing here teaches the leader to emit checks, and nothing changes the UI. A follow-up plan does both. Until then every task keeps behaving exactly as it does today, because every stored verification is a plain string and therefore attested.
- Backend interpreter and commands run from the repo root (or the worktree root, if one is in use):
  - Test: `.venv/Scripts/python.exe -m pytest tests/<file> -v`
- The repository is NOT clean at baseline. On main, `pytest -q` is roughly **31-32 failed / ~1228 passed / 2 skipped**, all failures in `tests/test_runtime_factory_headless.py` and `tests/test_team_cycle_recovery.py`, and some flake between runs. `ruff check .` reports **227 pre-existing findings**. Judge your work by the delta, never by absolute green. Do not run the whole suite while iterating; do not run `ruff check .` — lint only the files you touch.
- No database migration. `acceptance_json` is a JSON text column; the parser accepts both the old and the new shape.
- Checks never execute a process. File reads are capped at 1 MB.
- The four check types are exactly `file_nonempty`, `file_contains`, `file_matches`, `json_parses`. Do not add a fifth.
- Korean Conventional Commit subjects, matching the existing history.

---

### Task 1: The check vocabulary and runner

**Files:**
- Create: `src/personal_agent_gateway/team_verification_checks.py`
- Test: `tests/test_team_verification_checks.py`

**Interfaces:**
- Consumes: `is_sensitive_file` from `personal_agent_gateway.file_safety`.
- Produces:
  - `VerificationCheck(type: str, path: str, value: str = "", pattern: str = "")` — frozen
  - `CheckResult(passed: bool, evidence: str)` — frozen
  - `CHECK_TYPES: tuple[str, ...]`
  - `parse_verification_check(value: object) -> VerificationCheck` — raises `ValueError` on anything invalid
  - `verification_check_payload(check: VerificationCheck) -> dict[str, str]` — the JSON form, omitting the unused field
  - `safe_workspace_file(workspace: Path, relative_path: str) -> Path | None` — resolved path, or `None` when unsafe or absent
  - `run_verification_check(check: VerificationCheck, workspace: Path) -> CheckResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_team_verification_checks.py`:

```python
import json
from pathlib import Path

import pytest

from personal_agent_gateway.team_verification_checks import (
    VerificationCheck,
    parse_verification_check,
    run_verification_check,
    safe_workspace_file,
    verification_check_payload,
)


def _workspace(tmp_path: Path, name: str, content: str) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / name).write_text(content, encoding="utf-8")
    return workspace


def test_file_nonempty_passes_on_content_and_fails_on_whitespace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "# Draft\n")
    (workspace / "blank.md").write_text("   \n\t\n", encoding="utf-8")

    assert run_verification_check(
        VerificationCheck("file_nonempty", "draft.md"), workspace
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_nonempty", "blank.md"), workspace
    ).passed


def test_file_contains_matches_a_substring(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "before <library_draft>{} after")

    assert run_verification_check(
        VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
        workspace,
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_contains", "draft.md", value="</missing>"),
        workspace,
    ).passed


def test_file_matches_applies_a_regex(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "## Week 3\n")

    assert run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern=r"^## Week \d+$"),
        workspace,
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern=r"^## Day \d+$"),
        workspace,
    ).passed


def test_json_parses_distinguishes_valid_from_invalid(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "ok.json", json.dumps({"a": 1}))
    (workspace / "bad.json").write_text("{not json", encoding="utf-8")

    assert run_verification_check(
        VerificationCheck("json_parses", "ok.json"), workspace
    ).passed
    assert not run_verification_check(
        VerificationCheck("json_parses", "bad.json"), workspace
    ).passed


def test_a_missing_file_fails_rather_than_raising(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")

    result = run_verification_check(
        VerificationCheck("file_nonempty", "absent.md"), workspace
    )

    assert not result.passed
    assert "absent.md" in result.evidence


def test_an_oversized_file_fails_rather_than_being_read(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "huge.md", "x" * 1_000_001)

    result = run_verification_check(
        VerificationCheck("file_contains", "huge.md", value="x"), workspace
    )

    assert not result.passed
    assert "too large" in result.evidence


def test_an_uncompilable_pattern_fails_rather_than_raising(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")

    result = run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern="("), workspace
    )

    assert not result.passed


def test_safe_workspace_file_rejects_escapes_and_sensitive_names(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=1", encoding="utf-8")

    assert safe_workspace_file(workspace, "draft.md") is not None
    assert safe_workspace_file(workspace, "../outside.md") is None
    assert safe_workspace_file(workspace, ".env") is None
    assert safe_workspace_file(workspace, "absent.md") is None


def test_parse_verification_check_accepts_each_type_and_rejects_the_rest() -> None:
    assert parse_verification_check(
        {"type": "file_nonempty", "path": "a.md"}
    ) == VerificationCheck("file_nonempty", "a.md")
    assert parse_verification_check(
        {"type": "file_contains", "path": "a.md", "value": "x"}
    ) == VerificationCheck("file_contains", "a.md", value="x")

    for invalid in (
        None,
        "file_nonempty",
        {"type": "shell", "path": "a.md"},
        {"type": "file_nonempty"},
        {"type": "file_nonempty", "path": ""},
        {"type": "file_contains", "path": "a.md"},
        {"type": "file_matches", "path": "a.md"},
        {"type": "file_nonempty", "path": "a.md", "value": "x"},
        {"type": "file_nonempty", "path": "../a.md"},
    ):
        with pytest.raises(ValueError):
            parse_verification_check(invalid)


def test_payload_round_trips_without_the_unused_field() -> None:
    check = VerificationCheck("file_contains", "a.md", value="x")

    payload = verification_check_payload(check)

    assert payload == {"type": "file_contains", "path": "a.md", "value": "x"}
    assert parse_verification_check(payload) == check
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_verification_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.team_verification_checks'`

- [ ] **Step 3: Write the implementation**

Create `src/personal_agent_gateway/team_verification_checks.py`:

```python
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from personal_agent_gateway.file_safety import is_sensitive_file

MAX_CHECK_BYTES = 1_000_000
CHECK_TYPES = ("file_nonempty", "file_contains", "file_matches", "json_parses")
_VALUE_TYPES = {"file_contains"}
_PATTERN_TYPES = {"file_matches"}


@dataclass(frozen=True)
class VerificationCheck:
    type: str
    path: str
    value: str = ""
    pattern: str = ""


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str


def parse_verification_check(value: object) -> VerificationCheck:
    if not isinstance(value, dict):
        raise ValueError("Verification check must be an object")
    check_type = value.get("type")
    if check_type not in CHECK_TYPES:
        raise ValueError(f"Unknown verification check type: {check_type!r}")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Verification check requires a path")
    if not _safe_relative(path.strip()):
        raise ValueError("Verification check path must be relative and bounded")
    expected = {"type", "path"}
    if check_type in _VALUE_TYPES:
        expected.add("value")
    if check_type in _PATTERN_TYPES:
        expected.add("pattern")
    if set(value) != expected:
        raise ValueError(f"Verification check fields must be exactly {sorted(expected)}")
    detail = ""
    if check_type in _VALUE_TYPES:
        detail = value["value"]
    elif check_type in _PATTERN_TYPES:
        detail = value["pattern"]
    if check_type in _VALUE_TYPES | _PATTERN_TYPES:
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError(f"Verification check {check_type} requires a non-empty value")
    return VerificationCheck(
        type=check_type,
        path=path.strip(),
        value=detail if check_type in _VALUE_TYPES else "",
        pattern=detail if check_type in _PATTERN_TYPES else "",
    )


def verification_check_payload(check: VerificationCheck) -> dict[str, str]:
    payload = {"type": check.type, "path": check.path}
    if check.type in _VALUE_TYPES:
        payload["value"] = check.value
    if check.type in _PATTERN_TYPES:
        payload["pattern"] = check.pattern
    return payload


def safe_workspace_file(workspace: Path, relative_path: str) -> Path | None:
    root = workspace.resolve()
    candidate = root / relative_path
    current = candidate
    while current != root:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
        if root not in current.parents and current != root:
            break
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    if is_sensitive_file(candidate.name):
        return None
    return candidate


def run_verification_check(check: VerificationCheck, workspace: Path) -> CheckResult:
    resolved = safe_workspace_file(workspace, check.path)
    if resolved is None:
        return CheckResult(False, f"{check.type}: {check.path} is missing or not readable")
    try:
        size = resolved.stat().st_size
        if size > MAX_CHECK_BYTES:
            return CheckResult(False, f"{check.type}: {check.path} is too large ({size} bytes)")
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(False, f"{check.type}: {check.path} could not be read: {exc}")
    if check.type == "file_nonempty":
        passed = bool(text.strip())
        return CheckResult(passed, f"file_nonempty: {check.path} {'has' if passed else 'has no'} content")
    if check.type == "file_contains":
        passed = check.value in text
        return CheckResult(passed, f"file_contains: {check.path} {'contains' if passed else 'lacks'} the value")
    if check.type == "file_matches":
        try:
            pattern = re.compile(check.pattern, re.MULTILINE)
        except re.error as exc:
            return CheckResult(False, f"file_matches: pattern did not compile: {exc}")
        passed = pattern.search(text) is not None
        return CheckResult(passed, f"file_matches: {check.path} {'matched' if passed else 'did not match'}")
    try:
        json.loads(text)
    except ValueError as exc:
        return CheckResult(False, f"json_parses: {check.path} is not valid JSON: {exc}")
    return CheckResult(True, f"json_parses: {check.path} parsed")


def _safe_relative(value: str) -> bool:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        value not in {"", "."}
        and not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
        and ".." not in windows.parts
    )
```

`safe_workspace_file` is deliberately a copy of the logic in `TeamAcceptanceService._safe_file` — Task 4 replaces that private helper with a call to this one, so the duplication lasts only until then. Do not delete `_safe_file` in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_verification_checks.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_verification_checks.py tests/test_team_verification_checks.py
git commit -m "feat: 검증 체크 어휘와 실행기 추가"
```

---

### Task 2: `TaskAcceptance` carries required verifications as objects

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (`TaskAcceptance`, `_task_acceptance_json`, `_validate_task_acceptance`, `_team_task_from_row`)
- Test: `tests/test_teams.py`

**Interfaces:**
- Consumes: `VerificationCheck`, `parse_verification_check`, `verification_check_payload` from Task 1.
- Produces:
  - `RequiredVerification(name: str, check: VerificationCheck | None = None)` — frozen, defined in `teams.py` next to `TaskAcceptance`
  - `TaskAcceptance.required_verifications: tuple[RequiredVerification, ...]`
  - `parse_required_verifications(value: object) -> tuple[RequiredVerification, ...]` — module-level in `teams.py`, accepts a list whose items are either a plain non-empty string or `{"name": str, "check": {...} | None}`; raises `ValueError` otherwise
  - `_task_acceptance_json` emits a plain string for a check-less verification and an object for a checked one

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_teams.py`:

```python
def test_required_verifications_accept_plain_strings_and_objects() -> None:
    parsed = parse_required_verifications(
        [
            "source-url-verification",
            {
                "name": "marker-format",
                "check": {
                    "type": "file_contains",
                    "path": "draft.md",
                    "value": "<library_draft>",
                },
            },
            {"name": "reviewed", "check": None},
        ]
    )

    assert [item.name for item in parsed] == [
        "source-url-verification",
        "marker-format",
        "reviewed",
    ]
    assert parsed[0].check is None
    assert parsed[1].check == VerificationCheck(
        "file_contains", "draft.md", value="<library_draft>"
    )
    assert parsed[2].check is None


def test_required_verifications_reject_malformed_items() -> None:
    for invalid in (
        "not-a-list",
        [""],
        [{"name": ""}],
        [{"check": {"type": "file_nonempty", "path": "a.md"}}],
        [{"name": "x", "check": {"type": "shell", "path": "a.md"}}],
        [{"name": "x", "extra": 1}],
        ["dup", "dup"],
    ):
        with pytest.raises(ValueError):
            parse_required_verifications(invalid)


def test_acceptance_json_round_trips_both_shapes(tmp_path: Path) -> None:
    teams, run, cycle = _run_with_cycle_and_agents(tmp_path)
    acceptance = TaskAcceptance(
        required_outputs=("draft.md",),
        required_verifications=(
            RequiredVerification("source-url-verification"),
            RequiredVerification(
                "marker-format",
                VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
            ),
        ),
    )

    task = teams.create_task(
        run.id,
        "Write the draft",
        "Write it.",
        cycle_id=cycle.id,
        acceptance=acceptance,
    )

    assert teams.get_task(task.id).acceptance == acceptance


def test_stored_string_verifications_still_load(tmp_path: Path) -> None:
    teams, run, cycle = _run_with_cycle_and_agents(tmp_path)
    task = teams.create_task(
        run.id,
        "Write the draft",
        "Write it.",
        cycle_id=cycle.id,
        acceptance=TaskAcceptance(("draft.md",), (RequiredVerification("legacy"),)),
    )
    with teams._db.connection() as connection:
        connection.execute(
            "update team_tasks set acceptance_json = ? where id = ?",
            (
                '{"required_outputs": ["draft.md"], "required_verifications": ["legacy"]}',
                task.id,
            ),
        )

    loaded = teams.get_task(task.id)

    assert loaded.acceptance.required_verifications == (RequiredVerification("legacy"),)
```

`_run_with_cycle_and_agents` is a helper you add beside the file's existing helpers; build it from the same fixtures the file already uses to create a run with a cycle, and extend it only as far as `create_task` needs. Reuse `_run_with_cycle` if it already gives you everything.

Reaching into `teams._db` in the fourth test is deliberate: it is the only way to produce the genuine pre-change stored shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_teams.py -k verification -v`
Expected: FAIL with `ImportError` / `NameError` for `parse_required_verifications` and `RequiredVerification`

- [ ] **Step 3: Write the implementation**

3a. Add the dataclass beside `TaskAcceptance` in `teams.py`:

```python
@dataclass(frozen=True)
class RequiredVerification:
    name: str
    check: VerificationCheck | None = None


@dataclass(frozen=True)
class TaskAcceptance:
    required_outputs: tuple[str, ...]
    required_verifications: tuple[RequiredVerification, ...]
```

Import `VerificationCheck`, `parse_verification_check`, and `verification_check_payload` from `personal_agent_gateway.team_verification_checks`.

3b. Add the parser at module level:

```python
def parse_required_verifications(value: object) -> tuple[RequiredVerification, ...]:
    if not isinstance(value, list):
        raise ValueError("Required verifications must be a list")
    parsed: list[RequiredVerification] = []
    names: set[str] = set()
    for raw in value:
        if isinstance(raw, str):
            name, check = raw, None
        elif isinstance(raw, dict):
            if set(raw) - {"name", "check"}:
                raise ValueError("Required verification fields must be name and check")
            name = raw.get("name")
            raw_check = raw.get("check")
            check = None if raw_check is None else parse_verification_check(raw_check)
        else:
            raise ValueError("Required verification must be a string or an object")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Required verification requires a name")
        normalized = name.strip()
        if normalized in names:
            raise ValueError("Acceptance has duplicate required verifications")
        names.add(normalized)
        parsed.append(RequiredVerification(normalized, check))
    return tuple(parsed)
```

3c. Rewrite `_task_acceptance_json` so a check-less verification stays a plain string — that keeps stored rows readable and keeps the digest stable for unchanged plans:

```python
def _task_acceptance_json(acceptance: TaskAcceptance) -> str:
    return json.dumps(
        {
            "required_outputs": list(acceptance.required_outputs),
            "required_verifications": [
                item.name
                if item.check is None
                else {"name": item.name, "check": verification_check_payload(item.check)}
                for item in acceptance.required_verifications
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
```

3d. `_validate_task_acceptance` compares names rather than objects:

```python
def _validate_task_acceptance(acceptance: TaskAcceptance) -> None:
    outputs = acceptance.required_outputs
    verifications = acceptance.required_verifications
    if not outputs and not verifications:
        raise ValueError("Acceptance requires an output or verification")
    if len(set(outputs)) != len(outputs):
        raise ValueError("Acceptance has duplicate required outputs")
    names = [item.name for item in verifications]
    if len(set(names)) != len(names):
        raise ValueError("Acceptance has duplicate required verifications")
    if any(not item.strip() for item in (*outputs, *names)):
        raise ValueError("Acceptance items must not be blank")
    if any(not _safe_relative_task_output(path) for path in outputs):
        raise ValueError("Acceptance output path must be relative and bounded")
```

3e. `_team_task_from_row` parses through the new function:

```python
        acceptance=TaskAcceptance(
            required_outputs=tuple(acceptance.get("required_outputs", ())),
            required_verifications=parse_required_verifications(
                acceptance.get("required_verifications", [])
            ),
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_teams.py -v`
Expected: FAIL for other tests in the file that construct `TaskAcceptance((), ("worker-result",))` with bare strings — those are Task 3's job. Note which ones fail and confirm the four new tests pass:
`.venv/Scripts/python.exe -m pytest tests/test_teams.py -k verification -v` → PASS.

Do not fix the other call sites here; do not commit a red file either. If `tests/test_teams.py` has such call sites, update just those in this task so the file is green, and leave the other test files to Task 3.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat: acceptance 검증을 이름·체크 객체로 표현"
```

---

### Task 3: Propagate the type through every construction site

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (three `TaskAcceptance(...)` sites and `_parse_revised_acceptance`)
- Modify: `src/personal_agent_gateway/team_model_effects.py` (two `TaskAcceptance(...)` sites)
- Modify: `src/personal_agent_gateway/team_model_operations.py` (`_valid_acceptance`)
- Test: `tests/test_team_runtime.py`, `tests/test_team_model_effects.py`, `tests/test_team_model_operations.py`

**Interfaces:**
- Consumes: `parse_required_verifications` and `RequiredVerification` from Task 2.
- Produces: no new public interface. Every place that builds a `TaskAcceptance` from a model payload routes its `required_verifications` through `parse_required_verifications`, and the operation-ledger validator accepts both shapes.

- [ ] **Step 1: Find every site and write the failing tests**

Run first and record the list in your report:

```bash
grep -rn "TaskAcceptance(" src tests
grep -rn "required_verifications" src
```

At the time of writing the production sites are `team_runtime.py` (the operation-payload parse, the plan parse, and `_parse_revised_acceptance`) and `team_model_effects.py` (the plan-apply parse and the `revise_acceptance` validation). Locate them by name, not by line number.

Add to `tests/test_team_model_operations.py`:

```python
def test_valid_acceptance_accepts_both_verification_shapes() -> None:
    assert _valid_acceptance(
        {"required_outputs": ["a.md"], "required_verifications": ["reviewed"]}
    )
    assert _valid_acceptance(
        {
            "required_outputs": ["a.md"],
            "required_verifications": [
                {
                    "name": "marker",
                    "check": {"type": "file_contains", "path": "a.md", "value": "x"},
                }
            ],
        }
    )
    assert not _valid_acceptance(
        {
            "required_outputs": ["a.md"],
            "required_verifications": [
                {"name": "marker", "check": {"type": "shell", "path": "a.md"}}
            ],
        }
    )
    assert not _valid_acceptance({"required_outputs": [], "required_verifications": []})
```

Add to `tests/test_team_runtime.py` a test that a leader plan carrying a checked verification survives planning into the stored task:

Name it `test_planned_task_keeps_a_checked_verification`. Find an existing test in that file that drives planning with a leader plan response and then asserts on the stored task's acceptance, and copy its setup verbatim, changing only the acceptance object inside the leader's plan JSON to:

```json
{"required_outputs": ["draft.md"],
 "required_verifications": [{"name": "marker",
   "check": {"type": "file_contains", "path": "draft.md", "value": "<library_draft>"}}]}
```

The assertion is:

```python
    assert stored_task.acceptance.required_verifications == (
        RequiredVerification(
            "marker",
            VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
        ),
    )
```

If no existing test asserts on a planned task's acceptance, say so in your report and assert instead on the task returned by `teams.list_tasks(run.id, cycle.id)` after the planning call the harness already makes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_model_operations.py -k acceptance tests/test_team_runtime.py -k checked_verification -v`
Expected: FAIL — `_valid_acceptance` rejects the object form, and the planning path raises or drops the check

- [ ] **Step 3: Write the implementation**

3a. In `team_model_operations.py`, `_valid_acceptance` accepts either shape without importing the parser (this module is a leaf the parser depends on — keep the dependency direction):

```python
def _valid_acceptance(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "required_outputs",
        "required_verifications",
    }:
        return False
    outputs = value["required_outputs"]
    verifications = value["required_verifications"]
    return (
        _valid_string_list(outputs)
        and isinstance(verifications, list)
        and all(_valid_required_verification(item) for item in verifications)
        and bool(outputs or verifications)
    )


def _valid_required_verification(value: object) -> bool:
    if _nonempty_text(value):
        return True
    if not isinstance(value, dict) or set(value) - {"name", "check"}:
        return False
    if not _nonempty_text(value.get("name")):
        return False
    check = value.get("check")
    return check is None or _valid_verification_check(check)


def _valid_verification_check(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    check_type = value.get("type")
    if check_type not in {"file_nonempty", "file_contains", "file_matches", "json_parses"}:
        return False
    if not _nonempty_text(value.get("path")):
        return False
    expected = {"type", "path"}
    if check_type == "file_contains":
        expected.add("value")
    if check_type == "file_matches":
        expected.add("pattern")
    if set(value) != expected:
        return False
    detail_key = "value" if check_type == "file_contains" else "pattern"
    if detail_key in expected:
        return _nonempty_text(value.get(detail_key))
    return True
```

This duplicates the vocabulary in a second place. That is deliberate and worth one comment in the code: `team_model_operations` is a leaf module that `team_verification_checks` must not depend on in reverse, and this validator only gates the ledger's stored shape. Keep the type names in one tuple constant if you prefer, but do not create an import cycle.

3b. At each `TaskAcceptance(...)` site that builds from a model payload, replace `required_verifications=tuple(payload["required_verifications"])` with `required_verifications=parse_required_verifications(payload["required_verifications"])`. The surrounding `try/except (KeyError, TypeError, ValueError)` blocks already catch what the parser raises — confirm that at each site rather than assuming, and say so in your report.

3c. `_parse_revised_acceptance` in `team_runtime.py` currently uses `_string_list(...)` for verifications. Route it through `parse_required_verifications` too, keeping the surrounding `ValueError` contract.

3d. Update any test that constructs `TaskAcceptance((), ("worker-result",))` with bare strings to `TaskAcceptance((), (RequiredVerification("worker-result"),))`. There are several across the test suite; the grep in Step 1 finds them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_teams.py tests/test_team_runtime.py tests/test_team_model_effects.py tests/test_team_model_operations.py tests/test_team_acceptance.py tests/test_api_team_runs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py \
  src/personal_agent_gateway/team_model_effects.py \
  src/personal_agent_gateway/team_model_operations.py tests/
git commit -m "feat: 모든 acceptance 생성 지점에 검증 객체 전파"
```

---

### Task 4: The server runs the checks

**Files:**
- Modify: `src/personal_agent_gateway/team_acceptance.py`
- Test: `tests/test_team_acceptance.py`

**Interfaces:**
- Consumes: `run_verification_check` and `safe_workspace_file` from Task 1, `RequiredVerification` from Task 2.
- Produces: `AcceptanceResult.evidence` gains `attested_only: bool` and a per-verification `mode` of `"verified"` or `"attested"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_team_acceptance.py`. It already has `_task(*, outputs=..., verifications=...)` and `_outcome(*, deliverables=..., verifications=...)` helpers and builds a workspace containing `outputs/report.md`; these tests reuse both.

```python
def _marker_check() -> VerificationCheck:
    return VerificationCheck("file_contains", "outputs/report.md", value="<library_draft>")


def _workspace_with_report(tmp_path: Path, content: str) -> Path:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text(content, encoding="utf-8")
    return workspace


def test_a_server_check_decides_regardless_of_the_worker_claim(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "# Report\nNo marker here.\n")
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("marker", "passed", "파일 본문 기준 단일 마커 확인"),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == "required_verification_failed"


def test_a_passing_server_check_is_recorded_as_verified(tmp_path: Path) -> None:
    workspace = _workspace_with_report(
        tmp_path, "prose\n<library_draft>{}</library_draft>"
    )
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))

    result = TeamAcceptanceService().evaluate(
        task, _outcome(verifications=()), workspace
    )

    assert result.accepted is True
    assert result.evidence["verifications"]["marker"]["mode"] == "verified"
    assert result.evidence["attested_only"] is False


def test_an_attested_verification_keeps_the_self_reported_rule(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "report")
    task = _task(verifications=(RequiredVerification("reviewed"),))
    outcome = _outcome(
        verifications=(VerificationEvidence("reviewed", "passed", "read it"),)
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is True
    assert result.evidence["verifications"]["reviewed"]["mode"] == "attested"
    assert result.evidence["attested_only"] is True


def test_an_attested_verification_the_worker_omitted_still_fails(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "report")
    task = _task(verifications=(RequiredVerification("reviewed"),))

    result = TeamAcceptanceService().evaluate(
        task, _outcome(verifications=()), workspace
    )

    assert result.accepted is False
    assert result.reason_code == "required_verification_failed"
```

Import `RequiredVerification` from `personal_agent_gateway.teams` and `VerificationCheck` from `personal_agent_gateway.team_verification_checks`.

The first test is the one that matters most: it is the exact scenario from the incident that motivated this work — a `library-draft-marker-format-check` reported as passed with confident Korean evidence, while no file contained the marker. Today that acceptance succeeds; after this task it must not.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_acceptance.py -v`
Expected: FAIL — the worker's claim is currently trusted, so the first test's acceptance succeeds

- [ ] **Step 3: Write the implementation**

Replace the verification loop in `TeamAcceptanceService.evaluate` with one that decides checked verifications itself:

```python
        verification_by_name = {
            verification.name: verification for verification in outcome.verifications
        }
        recorded: dict[str, dict[str, str]] = {}
        verified_count = 0
        for required in task.acceptance.required_verifications:
            reported = verification_by_name.get(required.name)
            if required.check is not None:
                outcome_result = run_verification_check(required.check, workspace)
                recorded[required.name] = {
                    "mode": "verified",
                    "status": "passed" if outcome_result.passed else "failed",
                    "evidence": outcome_result.evidence,
                }
                if not outcome_result.passed:
                    return _rejected("failed", "required_verification_failed")
                verified_count += 1
                continue
            if reported is None or reported.status != "passed":
                return _rejected("failed", "required_verification_failed")
            recorded[required.name] = {
                "mode": "attested",
                "status": reported.status,
                "evidence": reported.evidence,
            }
```

and build the evidence from it:

```python
        return AcceptanceResult(
            accepted=True,
            status="completed",
            reason_code=None,
            evidence={
                "deliverables": sorted(declared),
                "verifications": recorded,
                "attested_only": verified_count == 0,
            },
        )
```

Replace `_safe_file` with a call to Task 1's shared helper so the path rules live in one place:

```python
def _safe_file(workspace: Path, relative_path: str) -> bool:
    return safe_workspace_file(workspace, relative_path) is not None
```

`workspace` in `evaluate` is already `workspace_root.resolve()`; pass that same value to `run_verification_check`.

Note what this deliberately does not do: a worker's self-reported entry for a checked verification is ignored entirely — it may be absent, or claim `failed` while the check passes. The server's result is the only one recorded.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_acceptance.py tests/test_team_runtime.py -v`
Expected: PASS. `test_team_runtime.py` exercises acceptance end to end, so a shape mistake in the evidence dict surfaces there.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_acceptance.py tests/test_team_acceptance.py
git commit -m "feat: 서버가 기계 검증 가능한 acceptance 체크를 직접 실행"
```

---

### Task 5: Expose the new shape through the API

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` (the task payload builder)
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `RequiredVerification` and `verification_check_payload`.
- Produces: each required verification serializes as `{"name": ..., "check": {...} | null}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_team_runs.py`:

Name it `test_task_payload_exposes_verification_checks`. Copy the setup from an existing test in that file that creates a team run and reads its tasks through the API, then give the created task this acceptance:

```python
    TaskAcceptance(
        required_outputs=("draft.md",),
        required_verifications=(
            RequiredVerification("reviewed"),
            RequiredVerification(
                "marker",
                VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
            ),
        ),
    )
```

and assert on the serialized field:

```python
    assert task_payload["acceptance"]["required_verifications"] == [
        {"name": "reviewed", "check": None},
        {
            "name": "marker",
            "check": {
                "type": "file_contains",
                "path": "draft.md",
                "value": "<library_draft>",
            },
        },
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_team_runs.py -k verification_checks -v`
Expected: FAIL — the payload currently serializes `RequiredVerification` objects, which is not JSON-encodable, or emits their names only

- [ ] **Step 3: Write the implementation**

In the task payload builder, replace `list(task.acceptance.required_verifications)` with:

```python
            "required_verifications": [
                {
                    "name": item.name,
                    "check": (
                        None if item.check is None else verification_check_payload(item.check)
                    ),
                }
                for item in task.acceptance.required_verifications
            ],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_team_runs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py
git commit -m "feat: team runs API에 검증 체크 노출"
```

**Note for the follow-up plan:** this changes a response field's shape. `frontend/src/components/organisms/TeamRunDetail/index.jsx` maps `acceptance.required_verifications` as strings (`.map((name) => ...)`) and will render `[object Object]` until the follow-up plan updates it. That plan covers the prompts and the UI together; this is the one visible seam this plan leaves behind, and it is why this plan is not merged on its own without the follow-up close behind it.

---

### Task 6: Full verification

- [ ] **Step 1: Run the backend suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures against the baseline in Global Constraints. Report the counts and name any failure outside `tests/test_runtime_factory_headless.py` and `tests/test_team_cycle_recovery.py` — those are yours to fix.

- [ ] **Step 2: Run the touched files directly**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_verification_checks.py tests/test_teams.py tests/test_team_acceptance.py tests/test_team_runtime.py tests/test_team_model_effects.py tests/test_team_model_operations.py tests/test_api_team_runs.py tests/test_hook_runner.py -q`
Expected: PASS, no failures.

- [ ] **Step 3: Lint the changed files**

Run ruff over the files you changed and confirm no new findings; also confirm `ruff check .` still reports 227.

- [ ] **Step 4: Commit any fixes**

Skip if nothing needed fixing.
