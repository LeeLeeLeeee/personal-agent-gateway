# Evaluation Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one evaluation fixture under one mode against the real provider, and leave behind what a human needs to score it.

**Architecture:** The runner calls `create_app(config)` and takes the already-wired services off `app.state` — no HTTP, no OTP, and no second copy of the app's wiring to drift. It creates a team and a run through the same service methods the API uses, drives it with `TeamRuntime`, then writes a **run artefact**. It never writes a record: the record schema refuses an empty `rubric_results`, and a machine has judged nothing.

**Tech Stack:** Python 3.13, the existing `personal_agent_gateway` services, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-agent-radio-evaluation-runner-design.md`
**Parent decision:** `docs/adr/2026-08-13-agent-radio-team-collaboration.md`

## Global Constraints

- **The runner must have no code path that writes to `evaluation/agent_radio/records/`.** A record is evidence of a judgement. Task 4 verifies this by inspection, not by argument.
- **Never delete or skip a failed run's artefact.** How often a mode fails is itself a measurement; dropping failures inflates the success rate.
- **Never write a partial artefact.** Build it in memory and write once. A half-written file later read as "what happened then" is where a quietly wrong number starts.
- `cost` stays empty. LMG's `/v1/usage` is an account-wide snapshot, so a before/after delta is not attributable to one run. An empty value with a stated reason beats a fabricated one.
- Only `legacy` is implemented. `single_agent` has no product equivalent — `RunMode` is `planning_only | plan_and_execute | review_only` — and inventing one produces a number nobody can interpret.
- `read_only` fixtures must leave the repository unchanged, and the runner **checks this after the run** rather than asserting it. A violated run is marked failed and its answer is not scored.
- This module lives under `evaluation/`, outside the shipped package. Unlike Stage 0's modules it **may** import `personal_agent_gateway` — driving the product is its whole job — but nothing in `src/` may import it.
- Backend tests: `PYTHONPATH=src python -m pytest <files> -q -p no:randomly` from the repo root. Quote any `-k` containing spaces or pytest runs zero tests and reports success.
- Lint: `python -m ruff check evaluation tests/<file>`. Rule selection is pinned in `pyproject.toml`, so any ruff at or above the floor gives the same answer.
- **Backend baseline is 0 failures**: `1732 passed / 2 skipped` as of 2026-08-14.
- `-n auto` runs the suite in ~2 minutes but three `worktree_delivery` tests fail under xdist (see `AGENTS.md`). Use it for speed, confirm serially.

## File Structure

| File | Responsibility |
| --- | --- |
| `evaluation/agent_radio/runner.py` (new) | Build the stack, drive one run, collect the artefact. The only file that touches the product. |
| `evaluation/agent_radio/artifact.py` (new) | The run artefact's shape and its validation. Pure, like `fixture.py`. |
| `evaluation/agent_radio/runs/` (new) | Where artefacts land. `.gitkeep` until the first real run. |
| `tests/test_agent_radio_runner.py` (new) | Everything above, with a stubbed model factory. |

---

## Task 1: The run artefact's shape

The artefact before the thing that produces it, so the producer has a contract to meet.

**Files:**
- Create: `evaluation/agent_radio/artifact.py`
- Test: `tests/test_agent_radio_runner.py`

**Interfaces:**
- Produces:
  - `ARTIFACT_SCHEMA = "gateway.eval-run/v1"`
  - `@dataclass(frozen=True) class RunArtifact` with `run_id, fixture_id, fixture_sha256, mode, execution_profile, started_at, finished_at, wall_ms, run_status, summary, workspace_path, repository_unchanged, error`
  - `RunArtifact.scoreable -> bool`
  - `parse_artifact(payload: dict) -> RunArtifact`
  - `write_artifact(directory: Path, artifact: RunArtifact) -> Path`
  - `load_artifacts(directory: Path) -> list[RunArtifact]`

- [ ] **Step 1: Write the failing tests**

```python
import json
from pathlib import Path

import pytest

from agent_radio.artifact import (
    ARTIFACT_SCHEMA,
    RunArtifact,
    load_artifacts,
    parse_artifact,
    write_artifact,
)
from agent_radio.fixture import FixtureError


def _artifact(**overrides) -> dict:
    payload = {
        "schema": ARTIFACT_SCHEMA,
        "run_id": "run-1",
        "fixture_id": "understand-acceptance-gate",
        "fixture_sha256": "abc",
        "mode": "legacy",
        "execution_profile": "read_only",
        "started_at": "2026-08-14T01:00:00Z",
        "finished_at": "2026-08-14T01:06:20Z",
        "wall_ms": 380000,
        "run_status": "completed",
        "summary": "수용 게이트는 파일 읽기만 한다",
        "workspace_path": "data/workspace/run-1/workspace",
        "repository_unchanged": True,
        "error": None,
    }
    payload.update(overrides)
    return payload


def test_a_completed_clean_run_is_scoreable():
    assert parse_artifact(_artifact()).scoreable is True


def test_a_failed_run_is_kept_but_not_scoreable():
    """How often a mode fails is itself a measurement, so the artefact stays --
    it just is not something a human should grade."""
    artifact = parse_artifact(
        _artifact(run_status="failed", summary=None, error="provider_unavailable")
    )

    assert artifact.scoreable is False


def test_a_read_only_run_that_touched_the_repository_is_not_scoreable():
    """Isolation broke, so the answer cannot be compared with anything else --
    whatever it says, it was produced under different conditions."""
    artifact = parse_artifact(
        _artifact(execution_profile="read_only", repository_unchanged=False)
    )

    assert artifact.scoreable is False


def test_a_bounded_write_run_may_change_its_own_workspace():
    """bounded_write is allowed to write; repository_unchanged still refers to
    the repository, not the isolated workspace."""
    artifact = parse_artifact(
        _artifact(execution_profile="bounded_write", repository_unchanged=True)
    )

    assert artifact.scoreable is True


def test_a_completed_run_with_no_summary_is_not_scoreable():
    """There is nothing to grade. Silently treating it as an empty answer would
    score a missing measurement as a failed one."""
    assert parse_artifact(_artifact(summary=None)).scoreable is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-run/v2"},
        {"mode": "single_agent"},
        {"execution_profile": "full_access"},
        {"wall_ms": -1},
        {"repository_unchanged": "yes"},
        {"run_id": ""},
        {"fixture_sha256": ""},
    ],
)
def test_an_artefact_that_cannot_be_trusted_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_artifact(_artifact(**overrides))


def test_writing_then_loading_round_trips(tmp_path: Path):
    artifact = parse_artifact(_artifact())

    path = write_artifact(tmp_path, artifact)

    assert path.name == "run-1.json"
    assert load_artifacts(tmp_path) == [artifact]


def test_writing_the_same_run_twice_is_refused(tmp_path: Path):
    """An artefact is a record of one execution. Overwriting one loses the
    execution it described, and nothing else records that it happened."""
    artifact = parse_artifact(_artifact())
    write_artifact(tmp_path, artifact)

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_radio.artifact'`.

- [ ] **Step 3: Write the module**

```python
"""What one execution left behind.

Deliberately not a record: a record carries a human's verdict, and this
carries only what happened. Keeping them apart is what stops the aggregator's
counts sliding from "measured" to "attempted".

Pure, like fixture.py -- it imports nothing from personal_agent_gateway, so the
shape can be reasoned about without standing up a runtime.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_radio.fixture import (
    EXECUTION_PROFILES,
    MODES,
    FixtureError,
    _required_text,
)

ARTIFACT_SCHEMA = "gateway.eval-run/v1"
IMPLEMENTED_MODES = frozenset({"legacy"})


@dataclass(frozen=True)
class RunArtifact:
    run_id: str
    fixture_id: str
    fixture_sha256: str
    mode: str
    execution_profile: str
    started_at: str
    finished_at: str
    wall_ms: int
    run_status: str
    summary: str | None
    workspace_path: str
    repository_unchanged: bool
    error: str | None

    @property
    def scoreable(self) -> bool:
        """Whether a human should grade this at all.

        Three ways an execution produces nothing gradeable: it failed, it
        produced no answer, or -- for a read-only fixture -- it wrote to the
        repository, which means it ran under conditions no other run shared.
        """
        if self.run_status != "completed" or not self.summary:
            return False
        if self.execution_profile == "read_only" and not self.repository_unchanged:
            return False
        return True


def parse_artifact(payload: dict) -> RunArtifact:
    if not isinstance(payload, dict):
        raise FixtureError("artefact is not an object")
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise FixtureError(f"unknown artefact schema: {payload.get('schema')!r}")
    mode = _required_text(payload, "mode")
    if mode not in MODES:
        raise FixtureError(f"unknown mode: {mode!r}")
    if mode not in IMPLEMENTED_MODES:
        raise FixtureError(f"mode is not implemented by the runner: {mode!r}")
    profile = _required_text(payload, "execution_profile")
    if profile not in EXECUTION_PROFILES:
        raise FixtureError(f"unknown execution profile: {profile!r}")
    unchanged = payload.get("repository_unchanged")
    if not isinstance(unchanged, bool):
        raise FixtureError("repository_unchanged must be a boolean")
    wall_ms = payload.get("wall_ms")
    if not isinstance(wall_ms, int) or isinstance(wall_ms, bool) or wall_ms < 0:
        raise FixtureError("wall_ms must be a non-negative integer")
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise FixtureError("summary must be a string or null")
    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        raise FixtureError("error must be a string or null")
    return RunArtifact(
        run_id=_required_text(payload, "run_id"),
        fixture_id=_required_text(payload, "fixture_id"),
        fixture_sha256=_required_text(payload, "fixture_sha256"),
        mode=mode,
        execution_profile=profile,
        started_at=_required_text(payload, "started_at"),
        finished_at=_required_text(payload, "finished_at"),
        wall_ms=wall_ms,
        run_status=_required_text(payload, "run_status"),
        summary=summary,
        workspace_path=_required_text(payload, "workspace_path"),
        repository_unchanged=unchanged,
        error=error,
    )


def write_artifact(directory: Path, artifact: RunArtifact) -> Path:
    """Write one artefact, refusing to overwrite.

    An artefact describes one execution. Overwriting loses the execution it
    described, and nothing else in the system records that it happened.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact.run_id}.json"
    if path.exists():
        raise FixtureError(f"an artefact for {artifact.run_id} already exists")
    payload = {"schema": ARTIFACT_SCHEMA, **asdict(artifact)}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_artifacts(directory: Path) -> list[RunArtifact]:
    artifacts: list[RunArtifact] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name} is not JSON") from exc
        artifacts.append(parse_artifact(payload))
    return artifacts
```

`_required_text` is private in `fixture.py`. Importing it across modules inside the same package is acceptable here, but if you would rather not, promote it — do not copy it, or the two shapes drift.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_runner.py
git add evaluation tests/test_agent_radio_runner.py
git commit -m "feat(evaluation): define what one execution leaves behind"
```

---

## Task 2: Standing up the product, and the isolation check

**Files:**
- Create: `evaluation/agent_radio/runner.py`
- Test: `tests/test_agent_radio_runner.py`

**Interfaces:**
- Consumes: `RunArtifact`, `write_artifact` from Task 1; `Fixture`, `load_fixture` from Stage 0.
- Produces:
  - `@dataclass(frozen=True) class Harness` holding `app`, `teams`, `runtime`, `policies`, `directory`, `rules`
  - `build_harness(config) -> Harness`
  - `repository_is_unchanged(repo_root: Path) -> bool`
  - `RunnerError(RuntimeError)`

- [ ] **Step 1: Write the failing tests**

```python
from agent_radio.runner import RunnerError, repository_is_unchanged


def test_a_clean_repository_reads_as_unchanged(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "initial")

    assert repository_is_unchanged(repo) is True


def test_an_untracked_file_counts_as_changed(tmp_path: Path):
    """A read-only fixture that dropped a scratch file into the repository did
    not run under the isolation the other runs had."""
    repo = _initialised_repo(tmp_path)
    (repo / "scratch.txt").write_text("x", encoding="utf-8")

    assert repository_is_unchanged(repo) is False


def test_a_modified_tracked_file_counts_as_changed(tmp_path: Path):
    repo = _initialised_repo(tmp_path)
    (repo / "a.txt").write_text("changed", encoding="utf-8")

    assert repository_is_unchanged(repo) is False


def test_a_path_that_is_not_a_repository_is_an_error(tmp_path: Path):
    """Silently answering 'unchanged' for a non-repository would report
    isolation held when nothing was checked."""
    with pytest.raises(RunnerError):
        repository_is_unchanged(tmp_path / "nothing")
```

Write `_git(path, *args)` and `_initialised_repo(tmp_path)` in this test file; `tests/test_api_team_runs.py` has a `_git` helper worth copying the shape of.

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly -k "repository or unchanged"`
Expected: FAIL — no `agent_radio.runner`.

- [ ] **Step 3: Write the harness and the check**

```python
"""Drive the product for one evaluation run.

This is the only file here that imports personal_agent_gateway. It takes the
services off a real `create_app`, rather than rebuilding the wiring, because
TeamRuntime has more than ten collaborators and a second copy of that
assembly would drift from the real one without anyone noticing.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


class RunnerError(RuntimeError):
    """The run could not be set up or its conditions could not be verified."""


@dataclass(frozen=True)
class Harness:
    app: object
    teams: object
    runtime: object
    policies: object
    directory: object
    rules: object


def build_harness(config: AppConfig) -> Harness:
    """Take the wired services off a real app.

    No HTTP and no TestClient: /api is OTP-gated and automating that login
    would be working around authentication rather than with it. create_app
    wires team_runtime and its collaborators directly, so app.state is a
    service container that is by construction the same one the API uses.
    """
    app = create_app(config)
    state = app.state
    return Harness(
        app=app,
        teams=state.team_run_service,
        runtime=state.team_runtime,
        policies=state.space_policy_service,
        directory=state.team_directory_service,
        rules=state.rule_set_service,
    )


def repository_is_unchanged(repo_root: Path) -> bool:
    """Whether the repository has no working-tree changes.

    Asked after a read_only run, because the isolation the spec promises is
    only real if something checks it. Untracked files count: a scratch file
    dropped into the tree means that run had a different working set from
    every other run of the same fixture.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RunnerError(
            f"cannot read git status for {repo_root}: {result.stderr.strip()}"
        )
    return result.stdout.strip() == ""
```

`app.state.space_policy_service` is assigned at `app.py:603` — verified, so `state.space_policy_service` is correct as written. The other four names are assigned at `app.py:601-610` and `:241`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly`
Expected: all pass. `build_harness` is not covered by these tests — Task 3 covers it, because standing up an app needs a config.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_runner.py
git add evaluation tests/test_agent_radio_runner.py
git commit -m "feat(evaluation): stand up the product and check isolation held"
```

---

## Task 3: Running one fixture

**Files:**
- Modify: `evaluation/agent_radio/runner.py`
- Test: `tests/test_agent_radio_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: `run_fixture(harness, fixture, *, mode, repo_root, now) -> RunArtifact`

- [ ] **Step 1: Write the failing tests**

These use a harness whose model factory is stubbed, so no provider is called. Build it by constructing the services directly the way `tests/test_team_runtime.py` does and wrapping them in a `Harness` — that keeps the test honest about which part is stubbed.

```python
async def test_a_completed_run_produces_a_scoreable_artefact(tmp_path):
    harness, repo = _stub_harness(tmp_path, answer="게이트는 파일만 읽는다")
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.run_status == "completed"
    assert artifact.summary == "게이트는 파일만 읽는다"
    assert artifact.fixture_sha256 == fixture.sha256
    assert artifact.repository_unchanged is True
    assert artifact.scoreable is True


async def test_a_failed_run_still_produces_an_artefact(tmp_path):
    """Dropping failures would inflate every success rate computed later."""
    harness, repo = _stub_harness(tmp_path, fail_with="provider_unavailable")
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.run_status != "completed"
    assert artifact.error is not None
    assert artifact.scoreable is False


async def test_a_read_only_fixture_that_dirtied_the_repository_is_not_scoreable(tmp_path):
    harness, repo = _stub_harness(tmp_path, answer="…", dirty_repo=True)
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.repository_unchanged is False
    assert artifact.scoreable is False


async def test_the_run_is_isolated_from_the_repository(tmp_path):
    """The isolation is the space policy's, not the runner's -- so assert the
    policy the runner actually set, rather than trusting it did."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    fixture = _understanding_fixture()

    await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    policy = harness.policies.resolve(team_id=_only_team_id(harness))
    assert policy.policy.write_mode == "isolated"


async def test_an_unimplemented_mode_is_refused_before_any_run_starts(tmp_path):
    """Refusing after spending a provider call would be the expensive way to
    learn the mode does not exist."""
    harness, repo = _stub_harness(tmp_path, answer="…")

    with pytest.raises(RunnerError):
        await run_fixture(
            harness, _understanding_fixture(), mode="passive", repo_root=repo
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly -k "run_fixture or artefact or isolated or unimplemented"`
Expected: FAIL — `run_fixture` does not exist.

- [ ] **Step 3: Implement `run_fixture`**

The shape, with the parts that need real signatures marked:

```python
async def run_fixture(
    harness: Harness,
    fixture: Fixture,
    *,
    mode: str,
    repo_root: Path,
) -> RunArtifact:
    """Run one fixture once and describe what happened.

    Never raises for a failed run -- a failure is a result, and the artefact
    is how it gets counted. It raises only when the run could not be set up,
    because there is then nothing to describe.
    """
    if mode not in IMPLEMENTED_MODES:
        raise RunnerError(f"mode is not implemented: {mode!r}")
    ...
```

Fill it in as: create two personas and a team through `harness.directory`; set the team's space policy to `write_mode="isolated"` with a read path at `repo_root` via `harness.policies.upsert(scope="team", ...)`; create the run with `harness.teams.create_team_run_from_team(harness.directory, harness.rules, team_id=..., goal=fixture.goal, run_mode="plan_and_execute", max_workers=1, lifecycle_mode="continuous", execution_policy="triggered")`; drive it with `harness.runtime.start(run.id, cycle.id)`; then read the finished run back for its `status` and `summary`, check `repository_is_unchanged(repo_root)`, and build the artefact.

Read the real signatures before writing this — `create_team_run_from_team` is at `teams.py:399` and `upsert` at `space_policies.py:132`, and `tests/test_api_team_runs.py` shows a working persona/team/run creation sequence to copy the order from. Timestamps and the wall-clock come from the caller so the tests are deterministic: take `now` as a parameter defaulting to a real clock, and say in your report what you chose.

**Wrap the drive in `try/except`** so a provider failure becomes `run_status`/`error` on the artefact rather than an exception out of `run_fixture`. Build the artefact in memory and return it; the caller writes it.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check evaluation tests/test_agent_radio_runner.py
git add evaluation tests/test_agent_radio_runner.py
git commit -m "feat(evaluation): run one fixture and describe what happened"
```

---

## Task 4: The entry point, and proving the runner cannot write a record

**Files:**
- Modify: `evaluation/agent_radio/runner.py`
- Create: `evaluation/agent_radio/runs/.gitkeep`
- Test: `tests/test_agent_radio_runner.py`

**Interfaces:**
- Produces: `main(argv) -> int` — `python -m agent_radio.runner --fixture <id> --mode legacy`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_runner_cannot_build_a_record():
    """The central promise. A record carries a human's verdict; nothing here
    has judged anything, so there must be no way for this code to produce one.

    Asserted on identifiers, not on prose: an earlier draft of this test failed
    because a docstring used the word "records" in a sentence. What matters is
    that the record vocabulary is unreachable from here.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "evaluation/agent_radio/runner.py"
    ).read_text(encoding="utf-8")

    for identifier in ("parse_record", "RECORD_SCHEMA", "rubric_results", "Record("):
        assert identifier not in source, identifier


def test_a_full_run_leaves_the_records_directory_untouched(tmp_path):
    """The inspection above proves the vocabulary is absent; this proves the
    behaviour, because a module can always reach a directory by string."""
    records = tmp_path / "records"
    records.mkdir()
    harness, repo = _stub_harness(tmp_path, answer="…")

    artifact = asyncio.run(
        run_fixture(harness, _understanding_fixture(), mode="legacy", repo_root=repo)
    )
    write_artifact(tmp_path / "runs", artifact)

    assert list(records.iterdir()) == []


def test_the_entry_point_refuses_an_unknown_fixture(capsys):
    exit_code = main(["--fixture", "no-such-task", "--mode", "legacy"])

    assert exit_code != 0
    assert "no-such-task" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly -k "no_path_that_writes or entry_point"`
Expected: FAIL — `main` does not exist.

- [ ] **Step 3: Write `main`**

It loads the config with `load_config()`, builds the harness, loads the fixture from `evaluation/agent_radio/tasks/`, runs it, writes the artefact into `evaluation/agent_radio/runs/`, and prints the artefact path. It returns non-zero when setup fails — **but zero for a run that failed**, because a failed run is a successful measurement and the artefact is where it gets counted.

Say that last point in the docstring; it is the kind of thing a later reader would "fix" into returning non-zero, which would make a failing mode look like a broken tool. Keep the word "record" out of this file entirely — the test above asserts on identifiers, and staying clear of the noun keeps the intent obvious to a reader too.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add evaluation tests/test_agent_radio_runner.py
git commit -m "feat(evaluation): add the runner entry point"
```

---

## Task 5: Three real runs

The first provider spend in this whole effort. Everything before this exists so that a failure here is the model's, not the tool's.

- [ ] **Step 1: Confirm the tool is green first**

Run: `PYTHONPATH=src python -m pytest tests/test_agent_radio_runner.py tests/test_agent_radio_evaluation.py -q -p no:randomly`
Run: `python -m ruff check src/personal_agent_gateway/ tests/ evaluation/`
Expected: all pass. **Do not spend a provider call while anything here is red.**

- [ ] **Step 2: Check the provider is actually reachable**

The local runtime must be up. Confirm it, and confirm which model is configured, before starting — a run that fails on an unreachable provider costs time and teaches nothing.

- [ ] **Step 3: Run the three fixtures, one at a time**

```bash
PYTHONPATH=src python -m agent_radio.runner --fixture understand-acceptance-gate --mode legacy
PYTHONPATH=src python -m agent_radio.runner --fixture impact-of-a-new-operation-stage --mode legacy
PYTHONPATH=src python -m agent_radio.runner --fixture add-a-verification-check-kind --mode legacy
```

One at a time, reading each artefact before starting the next. If the first reveals something wrong with the harness, stop — two more runs of a broken harness is just spend.

- [ ] **Step 4: Report what actually happened**

For each: wall-clock, run status, whether the repository stayed unchanged, and whether the summary is something a human could grade against that fixture's rubric. **Report the summaries honestly** — if a model produced something that would fail every rubric item, say so. That is a finding about the baseline, not a failure of this task.

Also confirm: `evaluation/agent_radio/records/` is still empty.

- [ ] **Step 5: Full suite and finish**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: baseline `1732 passed / 2 skipped` plus this plan's tests, 0 failed.

Append what you observed to the spec's verification section, commit the artefacts and the spec, then use `superpowers:finishing-a-development-branch`.

---

## Deliberately not in this plan

- **The real baseline.** Twenty tasks across two modes is separate spend and a separate decision.
- **`single_agent`, `radio_lite`, `passive`.** The first has no product meaning yet; the other two are Stage 2 and Stage 4.
- **A mode value for plan negotiation.** It is orthogonal to the ADR's watcher-axis modes and needs its own decision.
- **Per-run cost.** LMG reports account-wide usage; attributing it to one run needs work on LMG's side.
- **Automatic scoring**, CI integration, and scheduled runs.
