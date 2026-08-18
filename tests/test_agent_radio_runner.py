import json
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_radio.artifact import (
    ARTIFACT_SCHEMA,
    load_artifacts,
    parse_artifact,
    write_artifact,
)
from agent_radio.fixture import Fixture, FixtureError, RubricItem
from agent_radio.runner import (
    Harness,
    RunnerError,
    repository_is_unchanged,
    repository_status,
    run_fixture,
)
from personal_agent_gateway.db import Database
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.space_policies import SpacePolicyService
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_directory import TeamService
from personal_agent_gateway.team_model_effects import (
    TeamModelEffectService,
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_invoker import TeamModelInvoker
from personal_agent_gateway.team_model_operations import TeamModelOperationService
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


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


@pytest.mark.parametrize("repository_unchanged", [True, False])
def test_a_bounded_write_run_is_scoreable_only_if_the_repository_is_unchanged(
    repository_unchanged,
):
    """bounded_write is allowed to write to its own isolated workspace, but
    repository_unchanged names the repository, not the workspace -- a
    bounded_write run that mutated the repository broke isolation exactly as
    badly as a read_only run would, and is not scoreable either."""
    artifact = parse_artifact(
        _artifact(
            execution_profile="bounded_write",
            repository_unchanged=repository_unchanged,
        )
    )

    assert artifact.scoreable is repository_unchanged


def test_a_completed_run_with_no_summary_is_not_scoreable():
    """There is nothing to grade. Silently treating it as an empty answer would
    score a missing measurement as a failed one."""
    assert parse_artifact(_artifact(summary=None)).scoreable is False


def test_a_completed_run_with_a_blank_summary_is_not_scoreable():
    """A whitespace-only summary is still nothing to grade -- the same
    reasoning as a missing summary applies identically."""
    assert parse_artifact(_artifact(summary="   ")).scoreable is False


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


def test_writing_refuses_a_run_id_that_would_escape_the_directory(tmp_path: Path):
    """run_id comes from a later task and cannot be trusted -- this module is
    the last place it is still a string, so it is the last chance to stop a
    traversal before it becomes a real file outside the directory it was
    given."""
    artifact = parse_artifact(_artifact(run_id="../escaped"))

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)

    assert not (tmp_path.parent / "escaped.json").exists()


def test_writing_refuses_a_run_id_with_a_path_separator(tmp_path: Path):
    artifact = parse_artifact(_artifact(run_id="sub/run"))

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)

    assert not any(tmp_path.rglob("*.json"))


def test_writing_into_a_path_that_is_a_file_raises_fixture_error(tmp_path: Path):
    """Every other failure in this module raises FixtureError; a raw OSError
    would give a caller a second exception type to handle."""
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("occupied")
    artifact = parse_artifact(_artifact())

    with pytest.raises(FixtureError):
        write_artifact(not_a_directory, artifact)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _initialised_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


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


def test_two_different_dirty_states_read_as_different_statuses(tmp_path: Path):
    """The whole point of comparing status text rather than a dirty/clean
    boolean: a file added to an already-dirty tree has to be visible."""
    repo = _initialised_repo(tmp_path)
    (repo / "wip.txt").write_text("uncommitted work", encoding="utf-8")
    before = repository_status(repo)
    (repo / "scratch.txt").write_text("dropped by a run", encoding="utf-8")

    assert repository_status(repo) != before
    assert repository_is_unchanged(repo) is False


def test_a_path_that_is_not_a_repository_is_an_error(tmp_path: Path):
    """Silently answering 'unchanged' for a non-repository would report
    isolation held when nothing was checked."""
    with pytest.raises(RunnerError):
        repository_is_unchanged(tmp_path / "nothing")


def test_a_path_inside_a_repository_but_not_its_root_is_an_error(tmp_path: Path):
    """git -C walks upward to find a .git, so pointing this at a subdirectory
    would silently report the enclosing repository's whole status --
    including changes made by something else entirely outside repo_root."""
    repo = _initialised_repo(tmp_path)
    subdir = repo / "sub"
    subdir.mkdir()

    with pytest.raises(RunnerError):
        repository_is_unchanged(subdir)


def test_missing_git_executable_is_a_runner_error(tmp_path: Path, monkeypatch):
    """subprocess.run raises FileNotFoundError when git is not on PATH, which
    a returncode check alone cannot catch."""

    def _raise(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(
        "agent_radio.runner.subprocess", types.SimpleNamespace(run=_raise)
    )

    with pytest.raises(RunnerError):
        repository_is_unchanged(tmp_path)


def _understanding_fixture() -> Fixture:
    """Built directly rather than parsed: parse_fixture verifies repo_ref
    against a real commit, and these tests are about the runner, not the
    definition rules."""
    return Fixture(
        id="understand-acceptance-gate",
        type="understanding",
        title="수용 게이트가 무엇을 읽는지 설명하라",
        goal="수용 게이트가 어떤 파일을 읽는지 설명하라",
        repo_ref="HEAD",
        execution_profile="read_only",
        rubric=(
            RubricItem("names-the-gate", "게이트를 지목한다", "이름이 등장한다"),
            RubricItem("names-the-inputs", "입력을 지목한다", "파일이 등장한다"),
            RubricItem("no-invention", "없는 동작을 만들지 않는다", "근거가 있다"),
        ),
        sha256="0" * 64,
    )


def _only_team_id(harness: Harness) -> str:
    teams = harness.directory.list_teams()
    assert len(teams) == 1
    return teams[0].id


async def _no_sleep(_delay):
    return None


def _worker_outcome() -> str:
    return json.dumps(
        {
            "status": "completed",
            "summary": "읽은 파일을 정리했다",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "worker-result",
                    "status": "passed",
                    "evidence": "checked",
                }
            ],
        }
    )


class _StubModel:
    """Stands in for the provider, and for nothing else.

    Every other collaborator in the harness is the real service, so a test
    that passes here says the runner drove the real runtime -- only the
    model call was replaced.
    """

    def __init__(self, agent, teams, repo, *, answer, fail_with, dirty_repo):
        self._agent = agent
        self._teams = teams
        self._repo = repo
        self._answer = answer
        self._fail_with = fail_with
        self._dirty_repo = dirty_repo
        self._calls = 0

    async def complete_operation(self, messages, *, consumer_run_id):
        if self._dirty_repo:
            (self._repo / "scratch.txt").write_text("escaped", encoding="utf-8")
        if self._fail_with:
            raise RuntimeError(self._fail_with)
        self._calls += 1
        if self._agent.role != "leader":
            return ModelResponse(content=_worker_outcome(), tool_calls=[])
        if self._calls == 1:
            return ModelResponse(content=self._plan(), tool_calls=[])
        return ModelResponse(content=self._answer, tool_calls=[])

    async def complete(self, messages):
        return await self.complete_operation(messages, consumer_run_id="direct")

    def _plan(self) -> str:
        """Named at call time because the worker agent only exists once the
        runner has created the run."""
        worker = next(
            agent
            for agent in self._teams.list_agents(self._agent.team_run_id)
            if agent.role == "member"
        )
        return json.dumps(
            [
                {
                    "title": "Read the gate",
                    "description": "Read the acceptance gate and report on it.",
                    "owner_agent_id": worker.id,
                    "required": True,
                    "plan_task_id": "read",
                    "depends_on_task_ids": [],
                    "acceptance": {
                        "required_outputs": [],
                        "required_verifications": ["worker-result"],
                    },
                }
            ]
        )


def _stub_harness(
    tmp_path: Path,
    *,
    answer: str = "…",
    fail_with: str | None = None,
    dirty_repo: bool = False,
) -> tuple[Harness, Path]:
    repo = _initialised_repo(tmp_path)
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    policies = SpacePolicyService(db)
    policies.seed_defaults()
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        tmp_path / "workspace",
        cycle_service=cycles,
        space_policies=policies,
    )
    directory = TeamService(db, personas, policies)
    rules = RuleSetService(db)
    rules.seed_defaults()
    operations = TeamModelOperationService(
        db,
        result_validators=team_model_effect_result_validators(),
    )
    models: dict[str, _StubModel] = {}

    def model_factory(agent, _cycle_id=None):
        if agent.id not in models:
            models[agent.id] = _StubModel(
                agent,
                teams,
                repo,
                answer=answer,
                fail_with=fail_with,
                dirty_repo=dirty_repo,
            )
        return models[agent.id]

    runtime = TeamRuntime(
        teams,
        model_factory,
        operations=operations,
        model_invoker=TeamModelInvoker(operations, sleep=_no_sleep),
        model_effects=TeamModelEffectService(db, teams, operations),
    )
    harness = Harness(
        app=None,
        teams=teams,
        runtime=runtime,
        policies=policies,
        directory=directory,
        rules=rules,
        personas=personas,
    )
    return harness, repo


async def test_a_completed_run_produces_a_scoreable_artefact(tmp_path: Path):
    harness, repo = _stub_harness(tmp_path, answer="게이트는 파일만 읽는다")
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.run_status == "completed"
    assert artifact.summary == "게이트는 파일만 읽는다"
    assert artifact.fixture_id == fixture.id
    assert artifact.fixture_sha256 == fixture.sha256
    assert artifact.mode == "legacy"
    assert artifact.execution_profile == fixture.execution_profile
    assert artifact.repository_unchanged is True
    assert artifact.error is None
    assert artifact.scoreable is True


async def test_a_failed_run_still_produces_an_artefact(tmp_path: Path):
    """Dropping failures would inflate every success rate computed later."""
    harness, repo = _stub_harness(tmp_path, fail_with="provider_unavailable")
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.run_status != "completed"
    assert artifact.error is not None
    assert artifact.scoreable is False


async def test_a_read_only_fixture_that_dirtied_the_repository_is_not_scoreable(
    tmp_path: Path,
):
    harness, repo = _stub_harness(tmp_path, answer="…", dirty_repo=True)
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.repository_unchanged is False
    assert artifact.scoreable is False


async def test_a_run_that_changes_nothing_in_an_already_dirty_tree_is_scoreable(
    tmp_path: Path,
):
    """repository_unchanged asks whether *this run* changed the tree, not
    whether the tree is clean. A checkout with uncommitted work in it is the
    normal state of this repository, and grading against "clean" would mark
    every run of a mid-development sweep unscoreable for something no run
    did."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    (repo / "wip.txt").write_text("someone's uncommitted work", encoding="utf-8")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is True
    assert artifact.scoreable is True


async def test_a_run_that_adds_to_an_already_dirty_tree_is_not_scoreable(
    tmp_path: Path,
):
    """The comparison is on the status text, not on a dirty/clean boolean:
    two different dirty states are not the same tree, and collapsing them
    would hide exactly the escape this check exists to catch."""
    harness, repo = _stub_harness(tmp_path, answer="…", dirty_repo=True)
    (repo / "wip.txt").write_text("someone's uncommitted work", encoding="utf-8")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is False
    assert artifact.scoreable is False


async def test_the_run_is_isolated_from_the_repository(tmp_path: Path):
    """The isolation is the space policy's, not the runner's -- so assert the
    policy the runner actually set, rather than trusting it did."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    fixture = _understanding_fixture()

    await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    policy = harness.policies.resolve(team_id=_only_team_id(harness))
    assert policy.policy.write_mode == "isolated"
    assert policy.policy.read_path == str(repo)


async def test_the_run_writes_into_its_own_workspace_not_the_repository(
    tmp_path: Path,
):
    """The artefact has to name where the run actually worked, or a later
    reader cannot tell an isolated run from one that used the repository."""
    harness, repo = _stub_harness(tmp_path, answer="…")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    workspace = Path(artifact.workspace_path)
    assert workspace.exists()
    with pytest.raises(ValueError):
        workspace.relative_to(repo)


async def test_an_unimplemented_mode_is_refused_before_any_run_starts(tmp_path: Path):
    """Refusing after spending a provider call would be the expensive way to
    learn the mode does not exist."""
    harness, repo = _stub_harness(tmp_path, answer="…")

    with pytest.raises(RunnerError):
        await run_fixture(
            harness, _understanding_fixture(), mode="passive", repo_root=repo
        )

    assert harness.directory.list_teams() == []


async def test_a_repo_root_that_is_not_a_repository_root_is_refused_up_front(
    tmp_path: Path,
):
    """repository_is_unchanged refuses a subdirectory, and learning that after
    the run would throw the run away. Nothing has happened yet, so this is a
    setup failure and raises rather than returning an artefact."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    subdir = repo / "sub"
    subdir.mkdir()

    with pytest.raises(RunnerError):
        await run_fixture(
            harness, _understanding_fixture(), mode="legacy", repo_root=subdir
        )

    assert harness.directory.list_teams() == []


async def test_the_clock_is_the_callers(tmp_path: Path):
    """Wall time is a measurement, so the tests have to be able to pin it."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    start = datetime(2026, 8, 14, 1, 0, 0, tzinfo=timezone.utc)
    ticks = iter([start, start + timedelta(milliseconds=1500)])

    artifact = await run_fixture(
        harness,
        _understanding_fixture(),
        mode="legacy",
        repo_root=repo,
        now=lambda: next(ticks),
    )

    assert artifact.started_at == "2026-08-14T01:00:00Z"
    assert artifact.finished_at == "2026-08-14T01:00:01.500000Z"
    assert artifact.wall_ms == 1500


async def test_the_artefact_a_run_produces_survives_a_round_trip(tmp_path: Path):
    """The artefact is only useful if the aggregator can read it back, and
    parse_artifact is stricter than the dataclass constructor."""
    harness, repo = _stub_harness(tmp_path, answer="게이트는 파일만 읽는다")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    write_artifact(tmp_path / "artefacts", artifact)
    assert load_artifacts(tmp_path / "artefacts") == [artifact]
