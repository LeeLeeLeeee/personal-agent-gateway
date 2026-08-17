# Stage 0 Evaluation Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it possible to compare collaboration modes — fixture format, rubric, record schema and an aggregation that refuses to be read as a verdict when the evidence is too thin.

**Architecture:** Two small modules under `evaluation/agent_radio/`, outside the shipped package and outside gitignored `data/`. Everything is pure: definitions and records are parsed and validated in memory, and the only I/O is reading files and asking git whether a commit exists. No model is ever called.

**Tech Stack:** Python 3.13, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-13-agent-radio-stage-0-evaluation-fixture-design.md`
**Parent decision:** `docs/adr/2026-08-13-agent-radio-team-collaboration.md`

## Global Constraints

- **No model call, ever.** Nothing in this tree may import a provider client, open a socket, or read a credential. A test that needs a network is a design error here, not a fixture problem.
- **This is not the runner.** No task is executed, no baseline number is produced. The value of this stage is making the tooling fail *before* anyone spends on provider calls.
- `type` is exactly `understanding` · `architecture_impact` · `bounded_implementation`. `execution_profile` is exactly `read_only` · `bounded_write`. `mode` is exactly `single_agent` · `legacy` · `radio_lite` · `passive`. Adding a value means editing the spec first.
- **Records are evidence and live in git.** Never write them under `data/` — it is gitignored, and the ADR gate requires the fixture and baseline to be versioned.
- **A rubric item is binary. No partial credit.** So a task counts as successful only when every one of its items passed.
- Backend tests: `PYTHONPATH=src python -m pytest <files> -q -p no:randomly` from the repo root. Quote any `-k` containing spaces — unquoted, pytest runs zero tests and reports success.
- Lint: `python -m ruff check <files>` (project ruff 0.15.20; ignore `.venv/Scripts/ruff.exe`, which is 0.16.0 and not the project's linter).
- **Backend baseline is 0 failures**: `1673 passed / 2 skipped` as of 2026-08-14. Any failure is yours.
- Frontend is untouched by this plan.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | `pythonpath = ["src", "evaluation"]` — one line, so the tooling is importable without being shipped. |
| `evaluation/agent_radio/__init__.py` (new) | Empty; makes the package importable. |
| `evaluation/agent_radio/fixture.py` (new) | Parse and validate fixture definitions and run records. The only place a rule about either shape lives. |
| `evaluation/agent_radio/aggregate.py` (new) | Turn records into the comparison table, including its own refusals to be over-read. |
| `evaluation/agent_radio/tasks/*.json` (new) | Three real fixture definitions, one per type. |
| `evaluation/agent_radio/rubric.md` (new) | How to score, for the human doing it. |
| `evaluation/agent_radio/records/` (new) | Where run records land. Empty but for a `.gitkeep` until a runner exists. |
| `tests/test_agent_radio_evaluation.py` (new) | Everything above. |

---

## Task 1: Fixture definitions, and the rules that make one usable

**Files:**
- Modify: `pyproject.toml` — `pythonpath`
- Create: `evaluation/agent_radio/__init__.py`, `evaluation/agent_radio/fixture.py`
- Test: `tests/test_agent_radio_evaluation.py`

**Interfaces:**
- Produces:
  - `FIXTURE_SCHEMA = "gateway.eval-fixture/v1"`, `FIXTURE_TYPES`, `EXECUTION_PROFILES`
  - `class FixtureError(ValueError)`
  - `@dataclass(frozen=True) class RubricItem: id: str; criterion: str; check: str`
  - `@dataclass(frozen=True) class Fixture: id, type, title, goal, repo_ref, execution_profile, rubric: tuple[RubricItem, ...], sha256: str`
  - `parse_fixture(payload: dict, *, sha256: str, commit_exists: Callable[[str], bool]) -> Fixture`
  - `load_fixture(path: Path, *, commit_exists=git_commit_exists) -> Fixture`
  - `load_fixtures(directory: Path, *, commit_exists=git_commit_exists) -> dict[str, Fixture]`
  - `git_commit_exists(ref: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
import json
from pathlib import Path

import pytest

from agent_radio.fixture import (
    Fixture,
    FixtureError,
    load_fixture,
    load_fixtures,
    parse_fixture,
)

_ANY_COMMIT = lambda ref: True  # noqa: E731 - a stub, not production code


def _definition(**overrides) -> dict:
    payload = {
        "schema": "gateway.eval-fixture/v1",
        "id": "understand-acceptance-gate",
        "type": "understanding",
        "title": "수용 게이트가 무엇을 검사하는지 설명한다",
        "goal": "수용 게이트가 required_verifications를 어떻게 판정하는지 설명하라.",
        "repo_ref": "d8e9cce",
        "execution_profile": "read_only",
        "rubric": [
            {"id": f"R{n}", "criterion": f"c{n}", "check": f"how to check {n}"}
            for n in range(1, 4)
        ],
    }
    payload.update(overrides)
    return payload


def test_a_well_formed_definition_parses():
    fixture = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)

    assert fixture.id == "understand-acceptance-gate"
    assert fixture.type == "understanding"
    assert fixture.execution_profile == "read_only"
    assert [item.id for item in fixture.rubric] == ["R1", "R2", "R3"]
    assert fixture.sha256 == "abc"


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-fixture/v2"},
        {"type": "refactoring"},
        {"execution_profile": "full_access"},
        {"repo_ref": ""},
        {"id": ""},
        {"goal": "   "},
    ],
)
def test_a_definition_outside_the_vocabulary_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_fixture(_definition(**overrides), sha256="abc", commit_exists=_ANY_COMMIT)


def test_a_repo_ref_that_is_not_in_this_repository_is_refused():
    """Caught at definition time, not measurement time. A fixture pointing at a
    commit nobody has is only discovered when the run is already paid for."""
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(repo_ref="0" * 40),
            sha256="abc",
            commit_exists=lambda ref: False,
        )


@pytest.mark.parametrize(
    "goal",
    [
        "결과를 정리하고 git push 해라",
        "패키지를 npm publish 하도록 준비해라",
        "gh pr create 로 올려라",
    ],
)
def test_a_goal_that_asks_for_an_external_mutation_is_refused(goal):
    """The ADR's Stage 0 gate is that evaluation makes no real external
    mutation. A promise in a document is not enforcement."""
    with pytest.raises(FixtureError):
        parse_fixture(_definition(goal=goal), sha256="abc", commit_exists=_ANY_COMMIT)


@pytest.mark.parametrize(
    "goal",
    [
        "/etc/passwd 를 읽어라",
        "C:\\\\Users\\\\Administrator 아래를 살펴라",
        "../other-repo 의 코드를 참고해라",
    ],
)
def test_a_goal_that_reaches_outside_the_repository_is_refused(goal):
    with pytest.raises(FixtureError):
        parse_fixture(_definition(goal=goal), sha256="abc", commit_exists=_ANY_COMMIT)


def test_a_goal_naming_a_repository_path_is_allowed():
    """The guard must not reject the normal case. Almost every real goal names
    a file, and a rule that blocks those is worse than no rule -- it gets
    switched off."""
    fixture = parse_fixture(
        _definition(goal="src/personal_agent_gateway/teams.py 의 역할을 설명하라"),
        sha256="abc",
        commit_exists=_ANY_COMMIT,
    )

    assert "teams.py" in fixture.goal


@pytest.mark.parametrize("count", [0, 2, 7])
def test_a_rubric_that_cannot_be_scored_is_refused(count):
    """Under three and the pass rate is luck; over six and nobody scores it."""
    rubric = [
        {"id": f"R{n}", "criterion": "c", "check": "k"} for n in range(1, count + 1)
    ]
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(rubric=rubric), sha256="abc", commit_exists=_ANY_COMMIT
        )


def test_duplicate_rubric_ids_are_refused():
    rubric = [{"id": "R1", "criterion": "c", "check": "k"} for _ in range(3)]
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(rubric=rubric), sha256="abc", commit_exists=_ANY_COMMIT
        )


def test_loading_hashes_the_file_as_it_is_on_disk(tmp_path: Path):
    """The hash is what later detects a definition edited after the fact, so it
    must be over the bytes, not over a re-serialised payload."""
    path = tmp_path / "understand-acceptance-gate.json"
    path.write_text(json.dumps(_definition()), encoding="utf-8")

    first = load_fixture(path, commit_exists=_ANY_COMMIT)
    path.write_text(json.dumps(_definition(title="다른 제목")), encoding="utf-8")
    second = load_fixture(path, commit_exists=_ANY_COMMIT)

    assert first.sha256 != second.sha256


def test_two_definitions_claiming_the_same_id_are_refused(tmp_path: Path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(_definition()), encoding="utf-8")

    with pytest.raises(FixtureError):
        load_fixtures(tmp_path, commit_exists=_ANY_COMMIT)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_radio'`. If instead it says the test file is missing, you created it in the wrong place.

- [ ] **Step 3: Make the package importable**

In `pyproject.toml`:

```toml
pythonpath = ["src", "evaluation"]
```

Create `evaluation/agent_radio/__init__.py` empty. Nothing else goes in it — the package exists to be a namespace, and code in `__init__` gets imported by every consumer whether they want it or not.

- [ ] **Step 4: Write the module**

```python
"""Fixture definitions and run records, and the rules that make one usable.

Evaluation tooling, deliberately outside the shipped package: it is not
product code and must never be deployed with it. It also never calls a model.
Its whole value at this stage is failing before anyone pays for a provider
call.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FIXTURE_SCHEMA = "gateway.eval-fixture/v1"
FIXTURE_TYPES = frozenset(
    {"understanding", "architecture_impact", "bounded_implementation"}
)
EXECUTION_PROFILES = frozenset({"read_only", "bounded_write"})
RUBRIC_SIZE = range(3, 7)

# The ADR's Stage 0 gate is that evaluation makes no real external mutation.
# These are the commands that reach past this machine; a local commit does not.
FORBIDDEN_GOAL_COMMANDS = (
    "git push",
    "git remote add",
    "npm publish",
    "pypi upload",
    "twine upload",
    "gh pr",
    "gh release",
    "curl -x",
    "rm -rf /",
)
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[\w.]|[A-Za-z]:[\\/])")
_PARENT_ESCAPE = re.compile(r"(?:^|[\s(\"'])\.\.[\\/]")


class FixtureError(ValueError):
    """A definition or record that cannot be used as evidence."""


@dataclass(frozen=True)
class RubricItem:
    id: str
    criterion: str
    check: str


@dataclass(frozen=True)
class Fixture:
    id: str
    type: str
    title: str
    goal: str
    repo_ref: str
    execution_profile: str
    rubric: tuple[RubricItem, ...]
    sha256: str


def git_commit_exists(ref: str) -> bool:
    """Whether this repository has that commit.

    Injected everywhere it is used so the rules can be tested without a git
    repository, and so a caller working on a different checkout can say so.
    """
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def parse_fixture(
    payload: dict,
    *,
    sha256: str,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> Fixture:
    if not isinstance(payload, dict):
        raise FixtureError("fixture is not an object")
    if payload.get("schema") != FIXTURE_SCHEMA:
        raise FixtureError(f"unknown fixture schema: {payload.get('schema')!r}")
    identifier = _required_text(payload, "id")
    fixture_type = _required_text(payload, "type")
    if fixture_type not in FIXTURE_TYPES:
        raise FixtureError(f"unknown fixture type: {fixture_type!r}")
    profile = _required_text(payload, "execution_profile")
    if profile not in EXECUTION_PROFILES:
        raise FixtureError(f"unknown execution profile: {profile!r}")
    goal = _required_text(payload, "goal")
    _refuse_unsafe_goal(goal)
    repo_ref = _required_text(payload, "repo_ref")
    if not commit_exists(repo_ref):
        raise FixtureError(f"repo_ref is not a commit in this repository: {repo_ref!r}")
    return Fixture(
        id=identifier,
        type=fixture_type,
        title=_required_text(payload, "title"),
        goal=goal,
        repo_ref=repo_ref,
        execution_profile=profile,
        rubric=_parse_rubric(payload.get("rubric")),
        sha256=sha256,
    )


def load_fixture(
    path: Path,
    *,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> Fixture:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path.name} is not JSON") from exc
    return parse_fixture(
        payload,
        sha256=hashlib.sha256(raw).hexdigest(),
        commit_exists=commit_exists,
    )


def load_fixtures(
    directory: Path,
    *,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> dict[str, Fixture]:
    fixtures: dict[str, Fixture] = {}
    for path in sorted(directory.glob("*.json")):
        fixture = load_fixture(path, commit_exists=commit_exists)
        if fixture.id in fixtures:
            raise FixtureError(f"two definitions claim the id {fixture.id!r}")
        fixtures[fixture.id] = fixture
    return fixtures


def _parse_rubric(value: object) -> tuple[RubricItem, ...]:
    if not isinstance(value, list) or len(value) not in RUBRIC_SIZE:
        raise FixtureError(
            f"a rubric needs {RUBRIC_SIZE.start}-{RUBRIC_SIZE.stop - 1} items"
        )
    items: list[RubricItem] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"id", "criterion", "check"}:
            raise FixtureError("malformed rubric item")
        item = RubricItem(
            _required_text(raw, "id"),
            _required_text(raw, "criterion"),
            _required_text(raw, "check"),
        )
        if item.id in seen:
            raise FixtureError(f"duplicate rubric id: {item.id!r}")
        seen.add(item.id)
        items.append(item)
    return tuple(items)


def _refuse_unsafe_goal(goal: str) -> None:
    """Stop a definition asking for something no evaluation should do.

    Matching is deliberately narrow. Almost every real goal names a file in
    this repository, so a rule that rejects those would be switched off within
    a week -- which is worse than no rule.
    """
    lowered = goal.lower()
    for command in FORBIDDEN_GOAL_COMMANDS:
        if command in lowered:
            raise FixtureError(f"goal asks for an external mutation: {command!r}")
    if _ABSOLUTE_PATH.search(goal):
        raise FixtureError("goal names an absolute path")
    if _PARENT_ESCAPE.search(goal):
        raise FixtureError("goal reaches outside the repository")


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{key} is missing or empty")
    return value.strip()
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly`
Expected: all pass. If `test_a_goal_naming_a_repository_path_is_allowed` fails, your path guards are too broad — narrow them rather than deleting the test, because that test is what keeps the rule usable.

- [ ] **Step 6: Confirm the pythonpath change broke nothing**

Run: `PYTHONPATH=src python -m pytest tests/test_teams.py tests/test_api_team_runs.py -q -p no:randomly`
Expected: all pass. A `pythonpath` entry changes import resolution for the whole suite, so this is checked now rather than at the end.

- [ ] **Step 7: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_evaluation.py
git add pyproject.toml evaluation tests/test_agent_radio_evaluation.py
git commit -m "feat(evaluation): validate agent-radio fixture definitions"
```

---

## Task 2: Run records, and detecting a definition edited after the fact

**Files:**
- Modify: `evaluation/agent_radio/fixture.py`
- Test: `tests/test_agent_radio_evaluation.py`

**Interfaces:**
- Consumes: `FixtureError`, `_required_text` from Task 1.
- Produces:
  - `RECORD_SCHEMA = "gateway.eval-record/v1"`, `MODES = frozenset({"single_agent", "legacy", "radio_lite", "passive"})`
  - `@dataclass(frozen=True) class RubricResult: id: str; passed: bool; note: str`
  - `@dataclass(frozen=True) class Record` with `fixture_id, fixture_sha256, mode, repeat, harness_version, started_at, finished_at, wall_ms, cost, rubric_results, rework_count, conflict_count, critical_defects_found, mode_metrics`
  - `Record.succeeded -> bool` — true only when every rubric result passed
  - `parse_record(payload: dict) -> Record`, `load_records(directory: Path) -> list[Record]`
  - `is_stale(record: Record, fixtures: Mapping[str, Fixture]) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
from agent_radio.fixture import Record, is_stale, load_records, parse_record


def _record(**overrides) -> dict:
    payload = {
        "schema": "gateway.eval-record/v1",
        "fixture_id": "understand-acceptance-gate",
        "fixture_sha256": "abc",
        "mode": "legacy",
        "repeat": 1,
        "harness_version": "0.1.0",
        "started_at": "2026-08-14T01:00:00Z",
        "finished_at": "2026-08-14T01:06:20Z",
        "wall_ms": 380000,
        "cost": {"provider": "codex", "input_tokens": 41200, "output_tokens": 3100},
        "rubric_results": [
            {"id": "R1", "passed": True, "note": "n"},
            {"id": "R2", "passed": True, "note": "n"},
            {"id": "R3", "passed": True, "note": "n"},
        ],
        "rework_count": 0,
        "conflict_count": 0,
        "critical_defects_found": 0,
        "mode_metrics": {},
    }
    payload.update(overrides)
    return payload


def test_a_record_with_every_item_passed_counts_as_a_success():
    assert parse_record(_record()).succeeded is True


def test_one_failed_item_is_not_a_partial_success():
    """Items are binary and there is no partial credit, so a task succeeds only
    when all of them passed."""
    results = _record()["rubric_results"]
    results[1] = {"id": "R2", "passed": False, "note": "빠짐"}

    assert parse_record(_record(rubric_results=results)).succeeded is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-record/v2"},
        {"mode": "negotiation"},
        {"rubric_results": []},
        {"wall_ms": -1},
        {"repeat": 0},
        {"critical_defects_found": -2},
        {"mode_metrics": []},
    ],
)
def test_a_record_that_cannot_be_counted_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_record(_record(**overrides))


def test_a_record_is_stale_when_its_fixture_changed_underneath_it():
    """This is what catches 'edit the definition quietly and re-measure'."""
    fixture = parse_fixture(_definition(), sha256="def", commit_exists=_ANY_COMMIT)
    record = parse_record(_record(fixture_sha256="abc"))

    assert is_stale(record, {fixture.id: fixture}) is True
    assert is_stale(parse_record(_record(fixture_sha256="def")), {fixture.id: fixture}) is False


def test_a_record_for_an_unknown_fixture_is_stale():
    assert is_stale(parse_record(_record()), {}) is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly -k "record or stale"`
Expected: FAIL — `ImportError` for the new names.

- [ ] **Step 3: Extend the module**

```python
RECORD_SCHEMA = "gateway.eval-record/v1"
MODES = frozenset({"single_agent", "legacy", "radio_lite", "passive"})


@dataclass(frozen=True)
class RubricResult:
    id: str
    passed: bool
    note: str


@dataclass(frozen=True)
class Record:
    fixture_id: str
    fixture_sha256: str
    mode: str
    repeat: int
    harness_version: str
    started_at: str
    finished_at: str
    wall_ms: int
    cost: dict[str, object]
    rubric_results: tuple[RubricResult, ...]
    rework_count: int
    conflict_count: int
    critical_defects_found: int
    mode_metrics: dict[str, object]

    @property
    def succeeded(self) -> bool:
        """Every item passed. Items are binary, so there is no middle."""
        return all(result.passed for result in self.rubric_results)


def parse_record(payload: dict) -> Record:
    if not isinstance(payload, dict):
        raise FixtureError("record is not an object")
    if payload.get("schema") != RECORD_SCHEMA:
        raise FixtureError(f"unknown record schema: {payload.get('schema')!r}")
    mode = _required_text(payload, "mode")
    if mode not in MODES:
        raise FixtureError(f"unknown mode: {mode!r}")
    results = payload.get("rubric_results")
    if not isinstance(results, list) or not results:
        raise FixtureError("a record needs at least one rubric result")
    for key in ("cost", "mode_metrics"):
        if not isinstance(payload.get(key), dict):
            raise FixtureError(f"{key} must be an object")
    return Record(
        fixture_id=_required_text(payload, "fixture_id"),
        fixture_sha256=_required_text(payload, "fixture_sha256"),
        mode=mode,
        repeat=_positive_int(payload, "repeat"),
        harness_version=_required_text(payload, "harness_version"),
        started_at=_required_text(payload, "started_at"),
        finished_at=_required_text(payload, "finished_at"),
        wall_ms=_counter(payload, "wall_ms"),
        cost=dict(payload["cost"]),
        rubric_results=tuple(_parse_result(raw) for raw in results),
        rework_count=_counter(payload, "rework_count"),
        conflict_count=_counter(payload, "conflict_count"),
        critical_defects_found=_counter(payload, "critical_defects_found"),
        mode_metrics=dict(payload["mode_metrics"]),
    )


def load_records(directory: Path) -> list[Record]:
    records: list[Record] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name} is not JSON") from exc
        records.append(parse_record(payload))
    return records


def is_stale(record: Record, fixtures: Mapping[str, Fixture]) -> bool:
    """Whether this record was measured against a definition that no longer
    exists in that form.

    An unknown fixture counts as stale rather than as an error: a definition
    can legitimately be retired, and the records it produced then describe an
    experiment nobody can reproduce.
    """
    fixture = fixtures.get(record.fixture_id)
    return fixture is None or fixture.sha256 != record.fixture_sha256


def _parse_result(raw: object) -> RubricResult:
    if not isinstance(raw, dict) or set(raw) != {"id", "passed", "note"}:
        raise FixtureError("malformed rubric result")
    if not isinstance(raw["passed"], bool):
        raise FixtureError("rubric result passed must be a boolean")
    return RubricResult(_required_text(raw, "id"), raw["passed"], str(raw["note"]))


def _counter(payload: dict, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FixtureError(f"{key} must be a non-negative integer")
    return value


def _positive_int(payload: dict, key: str) -> int:
    value = _counter(payload, key)
    if value < 1:
        raise FixtureError(f"{key} must be at least 1")
    return value
```

Add `Mapping` to the `collections.abc` import.

Note `_counter` rejects `bool` explicitly: `True` is an `int` in Python and would otherwise pass as a count of 1.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_evaluation.py
git add evaluation tests/test_agent_radio_evaluation.py
git commit -m "feat(evaluation): parse run records and detect a rewritten fixture"
```

---

## Task 3: The comparison table, and its refusals

The table's job is as much to refuse a verdict as to report one.

**Files:**
- Create: `evaluation/agent_radio/aggregate.py`
- Test: `tests/test_agent_radio_evaluation.py`

**Interfaces:**
- Consumes: `Fixture`, `Record`, `is_stale` from Tasks 1-2.
- Produces:
  - `@dataclass(frozen=True) class Cell: mode, samples, success_rate, critical_defects, rework, cost_tokens, p50_ms, p95_ms | None`
  - `@dataclass(frozen=True) class Report: rows: dict[str, tuple[Cell, ...]]; stale_dropped: int; warnings: tuple[str, ...]`
  - `build_report(fixtures, records) -> Report`
  - `render(report) -> str`
  - `MINIMUM_REPEATS_PER_TYPE = 5`, `MINIMUM_TASKS = 20`, `P95_MINIMUM_SAMPLES = 5`

- [ ] **Step 1: Write the failing tests**

```python
from agent_radio.aggregate import build_report, render


def _fixtures():
    fixture = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)
    return {fixture.id: fixture}


def test_the_baseline_column_is_always_legacy():
    report = build_report(
        _fixtures(),
        [parse_record(_record(mode="legacy")), parse_record(_record(mode="radio_lite"))],
    )

    (row,) = report.rows.values()
    assert row[0].mode == "legacy"


def test_results_are_split_by_type_rather_than_averaged():
    """Winning at understanding and losing at implementation must not hide
    behind one total."""
    understanding = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)
    building = parse_fixture(
        _definition(id="build-a-thing", type="bounded_implementation"),
        sha256="def",
        commit_exists=_ANY_COMMIT,
    )
    records = [
        parse_record(_record()),
        parse_record(_record(fixture_id="build-a-thing", fixture_sha256="def")),
    ]

    report = build_report(
        {understanding.id: understanding, building.id: building}, records
    )

    assert set(report.rows) == {"understanding", "bounded_implementation"}


def test_a_stale_record_is_dropped_and_counted_rather_than_ignored():
    report = build_report(_fixtures(), [parse_record(_record(fixture_sha256="old"))])

    assert report.stale_dropped == 1
    assert report.rows == {}


def test_p95_is_refused_below_five_samples():
    report = build_report(_fixtures(), [parse_record(_record()) for _ in range(4)])

    (row,) = report.rows.values()
    assert row[0].samples == 4
    assert row[0].p95_ms is None
    assert "n/a" in render(report)


def test_p95_is_reported_at_five_samples():
    report = build_report(_fixtures(), [parse_record(_record()) for _ in range(5)])

    (row,) = report.rows.values()
    assert row[0].p95_ms is not None


def test_a_thin_sample_says_it_cannot_decide_activation():
    """The ADR's gate is 20 tasks or five repeats per type. Leaving that to a
    reader's memory is how a thin sample becomes a decision."""
    report = build_report(_fixtures(), [parse_record(_record())])

    assert any("기본 활성화 판단 불가" in warning for warning in report.warnings)
    assert "기본 활성화 판단 불가" in render(report)


def test_success_needs_every_item_to_pass():
    failed = _record()
    failed["rubric_results"][0] = {"id": "R1", "passed": False, "note": "n"}
    report = build_report(
        _fixtures(), [parse_record(_record()), parse_record(failed)]
    )

    (row,) = report.rows.values()
    assert row[0].success_rate == 0.5
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly -k "report or baseline or p95 or thin_sample or split_by_type"`
Expected: FAIL — no `agent_radio.aggregate`.

- [ ] **Step 3: Write the aggregator**

```python
"""Turn run records into a comparison table that refuses to be over-read.

Reporting a number is the easy half. The gates the ADR set -- twenty tasks or
five repeats per type, five samples before a p95 means anything -- are stated
in the table itself, because a threshold that lives only in a document is one
a tired reader approves past.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import quantiles

from agent_radio.fixture import Fixture, Record, is_stale

BASELINE_MODE = "legacy"
MINIMUM_REPEATS_PER_TYPE = 5
MINIMUM_TASKS = 20
P95_MINIMUM_SAMPLES = 5


@dataclass(frozen=True)
class Cell:
    mode: str
    samples: int
    success_rate: float
    critical_defects: int
    rework: int
    cost_tokens: int
    p50_ms: int
    p95_ms: int | None


@dataclass(frozen=True)
class Report:
    rows: dict[str, tuple[Cell, ...]]
    stale_dropped: int
    warnings: tuple[str, ...]


def build_report(
    fixtures: Mapping[str, Fixture],
    records: Sequence[Record],
) -> Report:
    live = [record for record in records if not is_stale(record, fixtures)]
    stale_dropped = len(records) - len(live)

    by_type: dict[str, dict[str, list[Record]]] = {}
    for record in live:
        fixture_type = fixtures[record.fixture_id].type
        by_type.setdefault(fixture_type, {}).setdefault(record.mode, []).append(record)

    rows = {
        fixture_type: tuple(
            _cell(mode, group[mode]) for mode in _mode_order(group)
        )
        for fixture_type, group in sorted(by_type.items())
    }
    return Report(rows, stale_dropped, _warnings(fixtures, live, by_type))


def render(report: Report) -> str:
    lines: list[str] = []
    for fixture_type, cells in report.rows.items():
        lines.append(f"## {fixture_type}")
        lines.append(
            "| mode | n | success | critical defects | rework | tokens | p50 ms | p95 ms |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for cell in cells:
            p95 = "n/a" if cell.p95_ms is None else str(cell.p95_ms)
            lines.append(
                f"| {cell.mode} | {cell.samples} | {cell.success_rate:.0%} | "
                f"{cell.critical_defects} | {cell.rework} | {cell.cost_tokens} | "
                f"{cell.p50_ms} | {p95} |"
            )
        lines.append("")
    if report.stale_dropped:
        lines.append(
            f"stale 기록 {report.stale_dropped}건 제외됨 "
            "(fixture 정의가 측정 이후 바뀜)"
        )
    lines.extend(report.warnings)
    return "\n".join(lines)


def _cell(mode: str, records: list[Record]) -> Cell:
    durations = sorted(record.wall_ms for record in records)
    return Cell(
        mode=mode,
        samples=len(records),
        success_rate=sum(record.succeeded for record in records) / len(records),
        critical_defects=sum(record.critical_defects_found for record in records),
        rework=sum(record.rework_count for record in records),
        cost_tokens=sum(
            int(record.cost.get("input_tokens", 0) or 0)
            + int(record.cost.get("output_tokens", 0) or 0)
            for record in records
        ),
        p50_ms=_percentile(durations, 50),
        p95_ms=(
            _percentile(durations, 95)
            if len(durations) >= P95_MINIMUM_SAMPLES
            else None
        ),
    )


def _percentile(durations: list[int], percent: int) -> int:
    # `inclusive` handles a single sample -- verified on this interpreter,
    # quantiles([5], n=100, method="inclusive") is [5]*99 -- so there is no
    # small-n special case to write here.
    return int(round(quantiles(durations, n=100, method="inclusive")[percent - 1]))


def _mode_order(group: Mapping[str, list[Record]]) -> list[str]:
    """The baseline first, always: every gate is stated against legacy."""
    modes = sorted(group)
    if BASELINE_MODE in modes:
        modes.remove(BASELINE_MODE)
        return [BASELINE_MODE, *modes]
    return modes


def _warnings(
    fixtures: Mapping[str, Fixture],
    live: Sequence[Record],
    by_type: Mapping[str, Mapping[str, list[Record]]],
) -> tuple[str, ...]:
    tasks = {record.fixture_id for record in live}
    thin_types = [
        fixture_type
        for fixture_type, group in by_type.items()
        if min(len(records) for records in group.values()) < MINIMUM_REPEATS_PER_TYPE
    ]
    if len(tasks) >= MINIMUM_TASKS and not thin_types:
        return ()
    return (
        "기본 활성화 판단 불가: "
        f"태스크 {len(tasks)}/{MINIMUM_TASKS}"
        + (f", 반복 부족 {', '.join(sorted(thin_types))}" if thin_types else ""),
    )
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_evaluation.py
git add evaluation tests/test_agent_radio_evaluation.py
git commit -m "feat(evaluation): compare modes, and say when the sample cannot decide"
```

---

## Task 4: Three real fixtures and the scoring rules

The first real content. Until now the rules have only met invented definitions.

**Files:**
- Create: `evaluation/agent_radio/tasks/understand-acceptance-gate.json`, `evaluation/agent_radio/tasks/impact-of-a-new-operation-stage.json`, `evaluation/agent_radio/tasks/add-a-verification-check-kind.json`
- Create: `evaluation/agent_radio/rubric.md`, `evaluation/agent_radio/records/.gitkeep`
- Test: `tests/test_agent_radio_evaluation.py`

**Interfaces:**
- Consumes: `load_fixtures` from Task 1.

- [ ] **Step 1: Write the failing test**

```python
def test_every_shipped_definition_is_usable():
    """The rules have only met invented definitions until here. This is the
    first time they meet real ones, including the git check against this
    repository's actual history."""
    directory = Path(__file__).resolve().parents[1] / "evaluation/agent_radio/tasks"

    fixtures = load_fixtures(directory)

    assert {fixture.type for fixture in fixtures.values()} == {
        "understanding",
        "architecture_impact",
        "bounded_implementation",
    }
```

Note this one deliberately does NOT inject `commit_exists` — it uses the real git check, so a definition pointing at a commit this repository does not have fails here.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly -k "shipped_definition"`
Expected: FAIL — the directory does not exist.

- [ ] **Step 3: Write the three definitions**

Use a real commit for `repo_ref`. Get one with `git rev-parse --short HEAD` and use the full sha; do not invent one, and do not use a sha from this plan — the plan is older than your checkout.

This is the shape, complete, for the first one — write the other two the same way:

```json
{
  "schema": "gateway.eval-fixture/v1",
  "id": "understand-acceptance-gate",
  "type": "understanding",
  "title": "수용 게이트가 무엇을 실제로 검사하는지 설명한다",
  "goal": "Team Run의 수용 게이트가 required_verifications를 어떻게 판정하는지, 그리고 어떤 검사 종류가 실제로 실행되는지 설명하라.",
  "repo_ref": "<git rev-parse HEAD 의 실제 값>",
  "execution_profile": "read_only",
  "rubric": [
    {
      "id": "R1",
      "criterion": "검사 종류를 이름으로 든다",
      "check": "답변이 file_nonempty · file_contains · file_matches · json_parses 중 둘 이상을 이름으로 언급한다"
    },
    {
      "id": "R2",
      "criterion": "아무것도 컴파일하거나 실행하지 않는다고 말한다",
      "check": "답변이 검사 종류가 모두 파일 읽기임을 명시한다. '테스트를 돌린다'거나 '빌드한다'는 서술이 있으면 실패"
    },
    {
      "id": "R3",
      "criterion": "게이트가 실행한 검사와 워커가 신고한 것을 구분한다",
      "check": "답변이 verified 와 attested(또는 같은 뜻의 두 경로)를 구분해 설명한다"
    }
  ]
}
```

Each needs 3-6 rubric items whose `check` says **how to decide**, not whether it is good. One per type:

- `understand-acceptance-gate.json`, type `understanding`, `read_only`. Goal: explain how the acceptance gate decides `required_verifications` and which check kinds actually execute. Items should force the answer to name the check kinds and to say that nothing compiles or runs.
- `impact-of-a-new-operation-stage.json`, type `architecture_impact`, `read_only`. Goal: describe everything that must change to add one new `OperationStage`. Items should force naming `REPAIR_STAGE`, the grouping requirement in `team_provider_recovery.py`, and the completeness tests that fail otherwise — this is a real trap the codebase has, so it discriminates between a real answer and a plausible one.
- `add-a-verification-check-kind.json`, type `bounded_implementation`, `bounded_write`. Goal: add one new check kind end to end with tests. Items should require the parser, the runner, a test for the failing case, and no change to the existing kinds' behaviour.

- [ ] **Step 4: Write `rubric.md`**

Short and operational, for the person scoring:

- items are binary, no partial credit, and a task succeeds only when all of them pass
- score from the answer and the workspace artefacts only, never from execution logs
- when two scorers could disagree on an item, the item is at fault — fix the `check`, and say so in the record's `note`
- record the note even when the item passed; the note is what a later reader uses to decide whether the item still means what it meant

- [ ] **Step 5: Run the test and watch it pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_evaluation.py -q -p no:randomly`
Expected: all pass. A failure here is a real definition failing a real rule — read which rule before changing either.

- [ ] **Step 6: Commit**

```bash
git add evaluation
git commit -m "feat(evaluation): add the first three fixtures and the scoring rules"
```

---

## Task 5: Verify

- [ ] **Step 1: Full backend suite**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: **0 failed.** Baseline is `1673 passed / 2 skipped` plus this plan's tests. Report the numbers.

- [ ] **Step 2: Lint**

Run: `python -m ruff check src/personal_agent_gateway/ tests/ evaluation/`
Expected: `All checks passed!`

- [ ] **Step 3: Prove no model can be called from here**

The central claim of this stage. Show it rather than asserting it:

```bash
grep -rnE "lmg_client|ModelClient|api_key|openai|anthropic|requests\.|httpx|socket" evaluation/ || echo "no provider surface"
PYTHONPATH=src python -c "
import sys
import agent_radio.fixture, agent_radio.aggregate
leaked = [name for name in sys.modules if 'personal_agent_gateway' in name]
print('leaked product imports:', leaked or 'none')
"
```

Expected: no provider surface, and importing the tooling pulls in nothing from the shipped package.

- [ ] **Step 4: Drive the whole thing once, by hand**

Write two or three records into a temporary directory by hand — this stage has no runner, so hand-written records are the only input that exists — and render the table:

```bash
PYTHONPATH=src python -c "
from pathlib import Path
from agent_radio.fixture import load_fixtures, load_records
from agent_radio.aggregate import build_report, render
fixtures = load_fixtures(Path('evaluation/agent_radio/tasks'))
print({k: v.type for k, v in fixtures.items()})
print(render(build_report(fixtures, load_records(Path('evaluation/agent_radio/records')))))
"
```

With no records this must print an empty table plus the activation warning, not a crash. Record what it printed.

- [ ] **Step 5: Record and finish**

Append what you observed to the spec's verification section, commit that file alone, then use `superpowers:finishing-a-development-branch`.

---

## Deliberately not in this plan

- **The runner.** No task is executed and no model is called. The spec is explicit that execution-time isolation belongs to the runner's own spec, and that measuring must not start before it exists.
- **Automatic scoring.** A human scores against the rubric until the rules have met real answers.
- **The collaboration metrics** inside `mode_metrics`. Stage 1 decides what it counts; inventing fields now would fix a shape nobody has tested.
- **A mode for negotiation.** Stage 1's plan negotiation is orthogonal to the ADR's watcher-axis modes, and the runner's spec decides how to record it.
- **CI integration.**
