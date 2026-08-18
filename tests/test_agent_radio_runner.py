import asyncio
import json
import subprocess
import types
from dataclasses import replace
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
        "backend": "codex",
        "model": "default",
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


def test_a_run_that_lost_an_optional_task_is_still_scoreable():
    """The product writes completed_with_failures when every *required* task
    completed and an optional one did not. It is terminal, it carries a real
    summary and no error, and with a real model a flaky optional task is an
    ordinary event -- discarding it would quietly eat a share of every
    measurement."""
    artifact = parse_artifact(_artifact(run_status="completed_with_failures"))

    assert artifact.scoreable is True


def test_a_run_that_lost_an_optional_task_and_produced_nothing_is_not_scoreable():
    """Terminal is not sufficient on its own: there still has to be an answer.
    The product also reaches completed_with_failures with no summary, e.g.
    when plan approval never completed."""
    artifact = parse_artifact(
        _artifact(
            run_status="completed_with_failures",
            summary=None,
            error="collaboration_plan_approval_incomplete",
        )
    )

    assert artifact.scoreable is False


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
        {"backend": ""},
        {"model": ""},
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


def test_a_file_added_to_an_already_dirty_tree_changes_the_status(tmp_path: Path):
    """The whole point of comparing status text rather than a dirty/clean
    boolean: a file added to an already-dirty tree has to be visible."""
    repo = _initialised_repo(tmp_path)
    (repo / "wip.txt").write_text("uncommitted work", encoding="utf-8")
    before = repository_status(repo)
    (repo / "scratch.txt").write_text("dropped by a run", encoding="utf-8")

    assert repository_status(repo) != before
    assert repository_is_unchanged(repo) is False


def test_a_file_added_inside_an_already_untracked_directory_changes_the_status(
    tmp_path: Path,
):
    """Default --porcelain collapses an untracked directory to one `?? dir/`
    line, so this pair of reads was byte-identical until -uall was added --
    and creating a file is the escape this check exists to catch."""
    repo = _initialised_repo(tmp_path)
    (repo / "notes").mkdir()
    (repo / "notes" / "first.txt").write_text("already here", encoding="utf-8")
    before = repository_status(repo)
    (repo / "notes" / "second.txt").write_text("dropped by a run", encoding="utf-8")

    assert repository_status(repo) != before


def test_a_file_rewritten_inside_an_untracked_directory_changes_the_status(
    tmp_path: Path,
):
    """This is the case that pins -uall specifically. Adding a file to an
    untracked directory also moves that directory's mtime, so the fingerprint
    alone would have caught it; rewriting a file inside one does not, so
    without -uall both reads are `?? notes/` with the same stat and the change
    is invisible."""
    repo = _initialised_repo(tmp_path)
    (repo / "notes").mkdir()
    (repo / "notes" / "first.txt").write_text("first draft", encoding="utf-8")
    before = repository_status(repo)
    (repo / "notes" / "first.txt").write_text(
        "a much longer second draft", encoding="utf-8"
    )

    assert repository_status(repo) != before


def test_an_untracked_file_rewritten_in_place_changes_the_status(tmp_path: Path):
    """`?? path` is identical before and after a rewrite, so the size and
    mtime appended to each untracked line are what make it visible."""
    repo = _initialised_repo(tmp_path)
    scratch = repo / "wip.txt"
    scratch.write_text("first draft", encoding="utf-8")
    before = repository_status(repo)
    scratch.write_text("a much longer second draft", encoding="utf-8")

    assert repository_status(repo) != before


@pytest.mark.xfail(
    reason="git status shows ' M path' for a tracked file whatever its "
    "content, and nothing here hashes it. Documented, not fixed: closing it "
    "means hashing every modified tracked file on both reads.",
    strict=True,
)
def test_a_tracked_file_modified_twice_is_not_visible(tmp_path: Path):
    repo = _initialised_repo(tmp_path)
    (repo / "a.txt").write_text("someone's uncommitted work", encoding="utf-8")
    before = repository_status(repo)
    (repo / "a.txt").write_text("rewritten by a run", encoding="utf-8")

    assert repository_status(repo) != before


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


def _worker_failure() -> str:
    return json.dumps(
        {
            "status": "failed",
            "summary": "교차 확인은 하지 못했다",
            "reason_code": "tool_unavailable",
            "deliverables": [],
            "verifications": [
                {
                    "name": "worker-result",
                    "status": "failed",
                    "evidence": "the cross-check tool was not available",
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

    def __init__(
        self,
        agent,
        teams,
        repo,
        *,
        answer,
        fail_with,
        writes,
        hangs,
        optional_task_fails,
    ):
        self._agent = agent
        self._teams = teams
        self._repo = repo
        self._answer = answer
        self._fail_with = fail_with
        self._writes = writes
        self._hangs = hangs
        self._optional_task_fails = optional_task_fails
        self._calls = 0

    async def complete_operation(self, messages, *, consumer_run_id):
        if self._writes:
            escaped = self._repo / self._writes
            escaped.parent.mkdir(parents=True, exist_ok=True)
            escaped.write_text("escaped", encoding="utf-8")
        if self._hangs:
            await asyncio.Event().wait()
        if self._fail_with:
            raise RuntimeError(self._fail_with)
        self._calls += 1
        if self._agent.role != "leader":
            if self._optional_task_fails and self._calls == 2:
                # A declared failure, not a raised one. A raw exception leaves
                # the model operation open and the whole run fails on the next
                # stage -- which is the product's behaviour, not the case this
                # test is about.
                return ModelResponse(content=_worker_failure(), tool_calls=[])
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
        plan = [
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
        if self._optional_task_fails:
            plan.append(
                {
                    "title": "Nice to have",
                    "description": "An optional cross-check.",
                    "owner_agent_id": worker.id,
                    "required": False,
                    "plan_task_id": "cross-check",
                    "depends_on_task_ids": [],
                    "acceptance": {
                        "required_outputs": [],
                        "required_verifications": ["worker-result"],
                    },
                }
            )
        return json.dumps(plan)


def _stub_harness(
    tmp_path: Path,
    *,
    answer: str = "…",
    fail_with: str | None = None,
    writes: str | None = None,
    hangs: bool = False,
    optional_task_fails: bool = False,
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
                writes=writes,
                hangs=hangs,
                optional_task_fails=optional_task_fails,
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
    harness, repo = _stub_harness(tmp_path, answer="…", writes="scratch.txt")
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
    harness, repo = _stub_harness(tmp_path, answer="…", writes="scratch.txt")
    (repo / "wip.txt").write_text("someone's uncommitted work", encoding="utf-8")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is False
    assert artifact.scoreable is False


async def test_a_run_that_wrote_inside_an_already_untracked_directory_is_caught(
    tmp_path: Path,
):
    """The hole -uall closes, driven end to end: default --porcelain reports
    `?? notes/` before and after, so a run that wrote a real file was recorded
    as having changed nothing and was graded as if isolation had held."""
    harness, repo = _stub_harness(tmp_path, answer="…", writes="notes/second.txt")
    (repo / "notes").mkdir()
    (repo / "notes" / "first.txt").write_text("already here", encoding="utf-8")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert (repo / "notes" / "second.txt").exists()
    assert artifact.repository_unchanged is False
    assert artifact.scoreable is False


async def test_no_error_is_invented_for_a_status_that_carries_an_answer(
    tmp_path: Path, monkeypatch
):
    """completed_with_failures is terminal, carries a real summary and has no
    error of its own: the product writes it when every required task completed
    and an optional one did not. Fabricating an error for it would make it
    unscoreable, and with a real model a flaky optional task is ordinary.

    The status is substituted on the way back rather than provoked, because
    the product's own route to it in a cycle run needs a scripted
    acceptance-review dance that would test the script, not the runner. The
    run underneath is real and so is the TeamRun this replaces a field on.
    """
    harness, repo = _stub_harness(tmp_path, answer="게이트는 파일만 읽는다")
    real_get = harness.teams.get_team_run

    def as_completed_with_failures(run_id):
        return replace(real_get(run_id), status="completed_with_failures")

    monkeypatch.setattr(harness.teams, "get_team_run", as_completed_with_failures)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.run_status == "completed_with_failures"
    assert artifact.summary == "게이트는 파일만 읽는다"
    assert artifact.error is None
    assert artifact.scoreable is True


async def test_a_worker_declared_failure_parks_the_run_and_is_recorded(
    tmp_path: Path,
):
    """Worth knowing before any money is spent: a worker that *declares* a
    failure does not simply fail its task -- the product opens a user decision
    and parks the run at waiting_for_user. The runner records that as a
    non-scoreable result instead of hanging on it or raising."""
    harness, repo = _stub_harness(
        tmp_path,
        answer="게이트는 파일만 읽는다",
        optional_task_fails=True,
    )

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.run_status == "waiting_for_user"
    assert artifact.error is not None
    assert artifact.scoreable is False


async def test_a_hung_run_becomes_a_recorded_failure(tmp_path: Path):
    """Neither TeamRuntime.start nor a provider call has a wall-clock bound of
    its own, so without one here a single hang stalls the sweep and produces
    no artefact at all."""
    harness, repo = _stub_harness(tmp_path, hangs=True)

    artifact = await run_fixture(
        harness,
        _understanding_fixture(),
        mode="legacy",
        repo_root=repo,
        timeout_seconds=0.2,
    )

    assert artifact.run_status != "completed"
    assert artifact.error is not None and "wall-clock" in artifact.error
    assert artifact.scoreable is False


async def test_an_unverifiable_repository_still_produces_an_artefact(
    tmp_path: Path, monkeypatch
):
    """The provider call is already paid for by the time isolation is read.
    git going missing, or the repository moving, must not destroy the run --
    a non-scoreable artefact beats no artefact."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    real = repository_status
    calls = []

    def fail_after_the_run(path: Path) -> str:
        calls.append(path)
        if len(calls) > 1:
            raise RunnerError("git is not available on PATH")
        return real(path)

    monkeypatch.setattr("agent_radio.runner.repository_status", fail_after_the_run)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.run_status == "completed"
    assert artifact.summary == "…"
    # Unverifiable is not verified-clean, and only one of the two is safe.
    assert artifact.repository_unchanged is False
    assert "could not verify isolation" in artifact.error
    assert artifact.scoreable is False


async def test_the_artefact_names_what_produced_the_answer(tmp_path: Path):
    """Once the money is spent, an artefact that cannot say which provider and
    model produced its answer is not comparable with anything."""
    harness, repo = _stub_harness(tmp_path, answer="…")

    artifact = await run_fixture(
        harness,
        _understanding_fixture(),
        mode="legacy",
        repo_root=repo,
        backend="claude",
        model="a-named-model",
    )

    assert (artifact.backend, artifact.model) == ("claude", "a-named-model")
    # And it is what the run actually used, not a label attached afterwards.
    assert all(
        (persona.default_backend, persona.default_model)
        == ("claude", "a-named-model")
        for persona in harness.personas.list_personas()
    )


async def test_a_relative_repo_root_is_resolved(tmp_path: Path, monkeypatch):
    """A relative path passes the repository-root check and then fails deep
    inside the space policy, which requires an absolute directory."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    monkeypatch.chdir(tmp_path)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=Path("repo")
    )

    assert artifact.run_status == "completed"
    policy = harness.policies.resolve(team_id=_only_team_id(harness))
    assert policy.policy.read_path == str(repo.resolve())


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
