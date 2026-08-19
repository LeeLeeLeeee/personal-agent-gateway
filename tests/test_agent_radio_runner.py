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
    DEFAULT_BACKEND,
    DEFAULT_MODEL,
    EMPTY_TRACE,
    EVAL_DATA_ROOT,
    EVAL_WORKSPACE_ROOT,
    REPOSITORY_DIFF_LIMIT,
    Harness,
    RunInProgress,
    RunnerError,
    _REPO_ROOT,
    _evaluation_config,
    export_source,
    main,
    only_one_run,
    provider_trace,
    repository_is_unchanged,
    repository_status,
    run_fixture,
)
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.db import Database
from personal_agent_gateway.lmg_client import ProviderExecutionCapabilities
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
from personal_agent_gateway.team_provider_recovery import (
    TeamProviderRecovery,
    capabilities_for_cycle,
)
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


def _artifact(**overrides) -> dict:
    payload = {
        "schema": ARTIFACT_SCHEMA,
        "run_id": "run-1",
        "fixture_id": "understand-acceptance-gate",
        "fixture_sha256": "abc",
        "mode": "legacy",
        "plan_negotiation": False,
        "execution_profile": "read_only",
        "backend": "codex",
        "model": "gpt-5.6-luna",
        "effort": "xhigh",
        "source_commit": "9e711fa0c0ffee0000000000000000000000beef",
        "resolved_model": "gpt-5.6-terra",
        "resolved_effort": "high",
        "input_tokens": 18271,
        "cached_input_tokens": 14080,
        "output_tokens": 564,
        "started_at": "2026-08-14T01:00:00Z",
        "finished_at": "2026-08-14T01:06:20Z",
        "wall_ms": 380000,
        "run_status": "completed",
        "summary": "수용 게이트는 파일 읽기만 한다",
        "workspace_path": "data/workspace/run-1/workspace",
        "repository_unchanged": True,
        "repository_diff": None,
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
        {"effort": ""},
        # An unknown must be stated as null. An empty string reads as "we looked
        # and it is blank", which is a different claim.
        {"resolved_model": ""},
        {"resolved_model": 5},
        {"resolved_effort": ""},
        # Same claim, about the tree: null says there was nothing to report, an
        # empty string says the diff itself was blank -- which no changed tree
        # produces.
        {"repository_diff": ""},
        {"repository_diff": 5},
        # Zero is a measurement and null is the absence of one, but a negative
        # count is neither.
        {"input_tokens": -1},
        {"output_tokens": "many"},
        {"input_tokens": True},
        # Cache reads are a part of the input, so more cached than input at all
        # is not a small inconsistency -- it makes fresh input negative.
        {"input_tokens": 10, "cached_input_tokens": 11},
    ],
)
def test_an_artefact_that_cannot_be_trusted_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_artifact(_artifact(**overrides))


@pytest.mark.parametrize(
    "key",
    [
        "resolved_model",
        "resolved_effort",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "repository_diff",
    ],
)
def test_an_artefact_missing_a_recovered_fact_is_refused(key):
    """Absent is not the same as null. A field that can be left out silently
    turns "nobody recorded this" into "this artefact predates the question",
    and both read as no data while meaning different things."""
    payload = _artifact()
    del payload[key]

    with pytest.raises(FixtureError):
        parse_artifact(payload)


def test_facts_the_provider_did_not_keep_are_recorded_as_unknown():
    """Null is a legitimate value: some providers keep no transcript."""
    artifact = parse_artifact(
        _artifact(
            resolved_model=None,
            resolved_effort=None,
            input_tokens=None,
            output_tokens=None,
        )
    )

    assert artifact.resolved_model is None
    assert artifact.resolved_effort is None
    assert artifact.input_tokens is None
    assert artifact.output_tokens is None


def test_an_unchanged_tree_reports_no_diff():
    """Null means there is nothing to report -- the tree did not move, or the
    check could not be run at all."""
    artifact = parse_artifact(_artifact(repository_diff=None))

    assert artifact.repository_diff is None


def test_a_changed_tree_carries_what_changed():
    """A boolean says a run broke isolation and nothing more, which is exactly
    the state that cannot be diagnosed after the sweep has ended."""
    artifact = parse_artifact(
        _artifact(
            repository_unchanged=False,
            repository_diff="--- before\n+++ after\n@@ -0,0 +1 @@\n+?? scratch.txt",
        )
    )

    assert "scratch.txt" in artifact.repository_diff
    assert artifact.scoreable is False


def test_a_run_that_really_cost_nothing_is_not_an_unknown():
    """Zero is a measurement. Collapsing it into null would lose the difference
    between a run that spent nothing and one nobody measured."""
    artifact = parse_artifact(
        _artifact(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    )

    assert artifact.input_tokens == 0
    assert artifact.output_tokens == 0


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


class _StubRegistry:
    """A provider that has been detected, shaped like the real registry's answer.

    Deliberately not a healthy-by-default stub of freeze_cycle itself: the
    snapshot it produces goes through the real TeamProviderRecovery and is read
    back by the real parser, so a runner that freezes the wrong shape fails here
    rather than in production.
    """

    def get(self, provider: str):
        return types.SimpleNamespace(
            ready=True,
            readiness_error=None,
            snapshot_status="fresh",
            detected_at="2026-08-18T00:00:00+00:00",
            execution_capabilities=ProviderExecutionCapabilities(
                resume=True,
                external_read_only_roots=False,
                network_modes=("denied",),
                sandbox_modes=("workspace-write",),
                permission_modes=(),
            ),
        )


def _stub_harness(
    tmp_path: Path,
    *,
    sessions: list | None = None,
    answer: str = "…",
    fail_with: str | None = None,
    writes: str | None = None,
    hangs: bool = False,
    optional_task_fails: bool = False,
) -> tuple[Harness, Path]:
    sessions = [] if sessions is None else sessions
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
        recovery=TeamProviderRecovery(teams, _StubRegistry(), operations),
        exports=tmp_path / "exports",
        sessions=lambda: sessions,
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


def test_the_exported_source_is_the_commit_and_nothing_else(tmp_path: Path):
    """What a run is allowed to read is a commit, not a working tree.

    Staging the working tree of this repository copies 2629 files -- nested
    checkouts under .worktrees, browser caches under data/backups -- with
    relative paths up to 189 characters, which overruns Windows' path limit
    before it overruns patience. HEAD is 679 files and 96 characters. The
    reproducibility argument is the stronger one though: a sweep that reads
    whatever happened to be uncommitted is not a measurement anyone can repeat.
    """
    repo = _initialised_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore rules")
    (repo / "uncommitted.txt").write_text("not committed", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "junk.bin").write_text("junk", encoding="utf-8")

    source, commit = export_source(repo, tmp_path / "exports", "HEAD")

    assert (source / "a.txt").read_text(encoding="utf-8") == "x"
    assert not (source / "uncommitted.txt").exists()
    assert not (source / "ignored").exists()
    assert not (source / ".git").exists()
    assert commit == _git(repo, "rev-parse", "HEAD")


def test_the_export_omits_the_evaluations_own_material(tmp_path: Path):
    """Otherwise the answer key ships with the question.

    This evaluation's design documents live in the repository the fixtures
    read. One of them says, in as many words, that the impact fixture's items
    "should force naming REPAIR_STAGE, the grouping requirement in
    team_provider_recovery.py, and the completeness tests" -- which is three of
    that fixture's five rubric items. The first sweep's answer cited that file
    by path and line. An answer derived from the rubric is indistinguishable
    from one derived from the code, and the rubric exists precisely to tell
    those apart.
    """
    repo = _initialised_repo(tmp_path)
    plans = repo / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-08-14-agent-radio-stage-0-evaluation-tooling.md").write_text(
        "items should force naming REPAIR_STAGE", encoding="utf-8"
    )
    (plans / "2026-08-11-team-run-structured-output-resilience.md").write_text(
        "an ordinary design document", encoding="utf-8"
    )
    (repo / "evaluation").mkdir()
    (repo / "evaluation" / "rubrics.md").write_text("the answers", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs and evaluation")

    source, _ = export_source(repo, tmp_path / "exports", "HEAD")

    assert not (source / "evaluation").exists()
    assert not (
        plans_out := source / "docs" / "superpowers" / "plans"
    ).joinpath("2026-08-14-agent-radio-stage-0-evaluation-tooling.md").exists()
    # Unrelated design documents stay: they are part of the codebase a real
    # question is asked about, and removing them would change the subject.
    assert plans_out.joinpath(
        "2026-08-11-team-run-structured-output-resilience.md"
    ).exists()


def test_exporting_the_same_commit_twice_reuses_the_export(tmp_path: Path):
    """Otherwise the second run of a sweep dies on its predecessor's directory."""
    repo = _initialised_repo(tmp_path)

    first, first_commit = export_source(repo, tmp_path / "exports", "HEAD")
    second, second_commit = export_source(repo, tmp_path / "exports", "HEAD")

    assert first == second
    assert first_commit == second_commit
    assert (second / "a.txt").read_text(encoding="utf-8") == "x"


async def test_the_run_reads_the_commit_the_fixture_pins(tmp_path: Path):
    """Not HEAD. The fixture's repo_ref is the tree its rubric was written
    against, so reading anything else grades an answer about one tree with a
    rubric about another -- and nothing downstream would notice, because
    is_stale compares fixture definitions, never commits."""
    harness, repo = _stub_harness(tmp_path)
    pinned = _git(repo, "rev-parse", "HEAD")
    (repo / "later.txt").write_text("after the fixture was written", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "moves HEAD past the pin")
    fixture = replace(_understanding_fixture(), repo_ref=pinned)

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    assert artifact.source_commit == pinned
    assert artifact.source_commit != _git(repo, "rev-parse", "HEAD")
    export = harness.exports / f"pag-{pinned[:7]}"
    assert not (export / "later.txt").exists()


async def test_a_bounded_write_run_gets_a_writable_copy_of_the_source(
    tmp_path: Path,
):
    """Otherwise the task it was set is impossible, and the run measures that.

    A bounded_write fixture asks for an edit to a source file. The only source
    in scope is the staged snapshot, which acceptance verifies byte for byte
    and refuses the work if it moved -- so an agent that edits it in place is
    rejected with input_snapshot_modified, whatever its ability. Nothing else
    in the workspace holds the code. The copy goes in the working root, which
    is the one place the space policy tells the agent it may write.
    """
    harness, repo = _stub_harness(tmp_path)
    fixture = replace(_understanding_fixture(), execution_profile="bounded_write")

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    copy = Path(artifact.workspace_path) / "source" / "a.txt"
    assert copy.read_text(encoding="utf-8") == "x"
    copy.write_text("edited", encoding="utf-8")


async def test_a_read_only_run_gets_no_writable_copy(tmp_path: Path):
    """A read_only fixture has nothing to edit, and a writable copy of the
    repository sitting in its workspace is an invitation to write one."""
    harness, repo = _stub_harness(tmp_path)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert not (Path(artifact.workspace_path) / "source").exists()


def _transcript(path: Path, *entries: dict) -> str:
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )
    return str(path)


def _token_count(given: int, produced: int, cached: int = 0) -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": given,
                    "cached_input_tokens": cached,
                    "output_tokens": produced,
                }
            },
        },
    }


def test_a_runs_cost_is_summed_across_its_sessions(tmp_path: Path):
    """A run has more than one session -- the leader and the worker each get
    their own -- and `total_token_usage` is cumulative inside a session, so the
    run's cost is the last count of each file added together. Taking one
    session would report a fraction of what the run actually spent."""
    leader = _transcript(
        tmp_path / "leader.jsonl",
        {"type": "turn_context", "payload": {"model": "m", "effort": "high"}},
        _token_count(100, 10),
        _token_count(300, 30),
    )
    worker = _transcript(
        tmp_path / "worker.jsonl",
        {"type": "turn_context", "payload": {"model": "m", "effort": "high"}},
        _token_count(700, 70),
    )
    sessions = [
        {"consumer_session_id": "run-1", "storage_path": leader},
        {"consumer_session_id": "run-1", "storage_path": worker},
        {"consumer_session_id": "other", "storage_path": leader},
    ]

    trace = provider_trace(lambda: sessions, "run-1")

    assert (trace.input_tokens, trace.output_tokens) == (1000, 100)
    assert (trace.model, trace.effort) == ("m", "high")


def test_cached_input_is_recorded_apart_from_the_total(tmp_path: Path):
    """Reporting only the total turns a run that carried more context into a run
    that cost proportionally more. The Stage 1 sweep read as 1.98x legacy on
    totals and 1.15x on freshly processed input, and the ADR's cost gate sits at
    2x, so which number is reported decides the verdict."""
    first = _transcript(tmp_path / "a.jsonl", _token_count(1000, 10, cached=800))
    second = _transcript(tmp_path / "b.jsonl", _token_count(500, 5, cached=400))
    sessions = [
        {"consumer_session_id": "run-1", "storage_path": first},
        {"consumer_session_id": "run-1", "storage_path": second},
    ]

    trace = provider_trace(lambda: sessions, "run-1")

    assert trace.input_tokens == 1500
    assert trace.cached_input_tokens == 1200
    # What the run newly processed, which is the number the gate is read against.
    assert trace.input_tokens - trace.cached_input_tokens == 300


def test_a_provider_that_reports_no_cache_reads_counts_as_none_cached(tmp_path: Path):
    """Zero, not unknown. The provider did report usage; it simply had no cache
    reads in it, and treating that as unknown would drop a real measurement."""
    path = _transcript(tmp_path / "a.jsonl", _token_count(100, 10))
    sessions = [{"consumer_session_id": "run-1", "storage_path": path}]

    assert provider_trace(lambda: sessions, "run-1").cached_input_tokens == 0


def test_a_run_whose_sessions_disagree_reports_no_single_model(tmp_path: Path):
    """Two sessions on different models have no answer to "which model
    answered", and naming either one would state something untrue. The cost is
    still a fact, so it survives."""
    first = _transcript(
        tmp_path / "a.jsonl",
        {"type": "turn_context", "payload": {"model": "m1", "effort": "high"}},
        _token_count(100, 10),
    )
    second = _transcript(
        tmp_path / "b.jsonl",
        {"type": "turn_context", "payload": {"model": "m2", "effort": "low"}},
        _token_count(200, 20),
    )
    sessions = [
        {"consumer_session_id": "run-1", "storage_path": first},
        {"consumer_session_id": "run-1", "storage_path": second},
    ]

    trace = provider_trace(lambda: sessions, "run-1")

    assert trace.model is None
    assert trace.effort is None
    assert (trace.input_tokens, trace.output_tokens) == (300, 30)


def test_an_unreadable_transcript_is_an_unknown_not_a_crash(tmp_path: Path):
    """This runs after the provider call is paid for, where nothing may raise."""
    sessions = [
        {"consumer_session_id": "run-1", "storage_path": str(tmp_path / "gone.jsonl")},
        {"consumer_session_id": "run-1", "storage_path": None},
    ]

    trace = provider_trace(lambda: sessions, "run-1")

    assert trace == EMPTY_TRACE


def test_an_unavailable_gateway_is_an_unknown_not_a_crash():
    def refuse():
        raise RuntimeError("gateway down")

    assert provider_trace(refuse, "run-1") == EMPTY_TRACE


async def test_the_run_records_which_model_actually_answered(tmp_path: Path):
    """`model` is the request; this is the answer.

    "default" means "whatever the local provider configuration selects", and
    neither the operation ledger nor the gateway's session list resolves it --
    both store the alias. The provider's own transcript does, and the session
    whose consumer id is this run is what points at it.
    """
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({"type": "session_meta", "payload": {"model": "gpt-5.6-terra"}})
        + "\n",
        encoding="utf-8",
    )
    harness, repo = _stub_harness(tmp_path)
    fixture = _understanding_fixture()

    # The session only exists once the run does, so it is described by run id
    # after the fact, the way the real gateway reports it.
    async def run_and_capture():
        return await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    sessions: list[dict] = []
    harness = replace(harness, sessions=lambda: sessions)
    original = harness.teams.create_team_run_from_team

    def remember(*args, **kwargs):
        run = original(*args, **kwargs)
        sessions.append(
            {
                "consumer_session_id": run.id,
                "storage_path": str(transcript),
            }
        )
        return run

    harness.teams.create_team_run_from_team = remember
    artifact = await run_and_capture()

    # The request and the answer are separate facts: the transcript says terra
    # ran, while the run asked for whatever DEFAULT_MODEL names.
    assert artifact.model == DEFAULT_MODEL
    assert artifact.resolved_model == "gpt-5.6-terra"


async def test_the_requested_model_and_effort_reach_the_personas(tmp_path: Path):
    """Recorded and delivered, because either alone misleads.

    The gateway fills in "high" for a persona with no effort option
    (runtime_factory.py:132), which overrides both the model's own default and
    the alias default without anything in the request showing it. So an artefact
    that names an effort has to be an effort the personas actually carry.
    """
    harness, repo = _stub_harness(tmp_path)

    artifact = await run_fixture(
        harness,
        _understanding_fixture(),
        mode="legacy",
        repo_root=repo,
        model="gpt-5.6-luna",
        effort="xhigh",
    )

    assert (artifact.model, artifact.effort) == ("gpt-5.6-luna", "xhigh")
    personas = harness.personas.list_personas()
    assert personas
    for persona in personas:
        assert persona.default_model == "gpt-5.6-luna"
        assert persona.default_options.get("effort") == "xhigh"


async def test_a_provider_that_kept_no_transcript_leaves_the_model_unknown(
    tmp_path: Path,
):
    """Null, not the alias. Recording "default" would turn a missing fact into
    a claim about which model ran."""
    harness, repo = _stub_harness(tmp_path, sessions=[])

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.resolved_model is None


@pytest.mark.parametrize("negotiation", [True, False])
async def test_the_negotiation_axis_reaches_the_run_and_the_artefact(
    tmp_path: Path, negotiation
):
    """Both halves, because either alone is a silent lie.

    An artefact that records the axis while the run ignored it labels a
    negotiated arm on a run that never negotiated -- and the comparison the axis
    exists for would then be legacy against itself. This project has already
    shipped that exact bug once, where plan_negotiation was accepted by the API
    and dropped before it reached the run.
    """
    harness, repo = _stub_harness(tmp_path)

    artifact = await run_fixture(
        harness,
        _understanding_fixture(),
        mode="legacy",
        repo_root=repo,
        plan_negotiation=negotiation,
    )

    assert artifact.plan_negotiation is negotiation
    run = harness.teams.get_team_run(artifact.run_id)
    assert run.plan_negotiation_enabled is negotiation


async def test_the_run_records_the_commit_it_read(tmp_path: Path):
    """An artefact that cannot say which source produced its answer is not
    comparable with anything, and the source is half of what a run is."""
    harness, repo = _stub_harness(tmp_path)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.source_commit == _git(repo, "rev-parse", "HEAD")
    policy = harness.policies.resolve(team_id=_only_team_id(harness)).policy
    read_path = Path(policy.read_path).resolve()
    assert read_path != repo.resolve()
    assert harness.exports.resolve() in read_path.parents


async def test_the_run_freezes_provider_capabilities_onto_its_cycle(tmp_path: Path):
    """The half of the dispatcher's prologue this runner had dropped.

    TeamCycleDispatcher.run_one creates the cycle and then freezes the provider
    snapshot onto it, and the model factory reads that snapshot back through
    capabilities_for_cycle (app.py). Every other test in this file substitutes
    the model factory, so none of them exercise that read -- which is how a
    runner that froze nothing passed this whole suite and then failed every real
    run at preflight with "codex: capabilities_unavailable", a message that
    names a provider the gateway was reporting as ready.
    """
    harness, repo = _stub_harness(tmp_path)
    fixture = _understanding_fixture()

    artifact = await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    cycle = harness.teams.get_cycle(
        harness.teams.list_cycles(artifact.run_id)[0].id
    )
    capabilities = capabilities_for_cycle(cycle, DEFAULT_BACKEND)
    assert capabilities.sandbox_modes == ("workspace-write",)


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


async def test_a_run_that_dirtied_the_repository_records_what_changed(
    tmp_path: Path,
):
    """A boolean says isolation broke and nothing else. That has already
    happened once on a real sweep and the cause is still unknown, because the
    artefact recorded that something changed without recording what."""
    harness, repo = _stub_harness(tmp_path, answer="…", writes="scratch.txt")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is False
    assert artifact.repository_diff is not None
    assert "scratch.txt" in artifact.repository_diff


async def test_a_run_that_changed_nothing_records_no_diff(tmp_path: Path):
    """Null is "nothing to report", and an unchanged tree has nothing to
    report. Writing an empty diff instead would put a blank answer where there
    is no question."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    (repo / "wip.txt").write_text("someone's uncommitted work", encoding="utf-8")

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is True
    assert artifact.repository_diff is None


async def test_an_enormous_difference_is_truncated_and_says_so(
    tmp_path: Path, monkeypatch
):
    """A run that escaped into a build directory can move thousands of paths.
    The artefact keeps enough to diagnose it and says out loud that it kept
    only that much -- a silently cut diff reads as a complete one.

    The flood is substituted rather than written, because what is under test is
    the cap, not git's ability to enumerate several thousand files.
    """
    harness, repo = _stub_harness(tmp_path, answer="…")
    real = repository_status
    calls = []

    def flood_after_the_run(path: Path) -> str:
        calls.append(path)
        if len(calls) > 1:
            return "\n".join(f"?? escaped/file-{index:05d}.txt" for index in range(900))
        return real(path)

    monkeypatch.setattr("agent_radio.runner.repository_status", flood_after_the_run)

    artifact = await run_fixture(
        harness, _understanding_fixture(), mode="legacy", repo_root=repo
    )

    assert artifact.repository_unchanged is False
    assert "escaped/file-00000.txt" in artifact.repository_diff
    assert "truncated" in artifact.repository_diff
    assert len(artifact.repository_diff) < REPOSITORY_DIFF_LIMIT + 200


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
    # And there is genuinely nothing to report: the check never ran, so there
    # are no two states to compare.
    assert artifact.repository_diff is None
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
    # Absolute, and pointing at the export of the repository the relative path
    # named -- a relative read_path fails deep inside the space policy.
    assert policy.policy.read_path == str(
        harness.exports / f"pag-{artifact.source_commit[:7]}"
    )


async def test_the_run_is_isolated_from_the_repository(tmp_path: Path):
    """The isolation is the space policy's, not the runner's -- so assert the
    policy the runner actually set, rather than trusting it did."""
    harness, repo = _stub_harness(tmp_path, answer="…")
    fixture = _understanding_fixture()

    await run_fixture(harness, fixture, mode="legacy", repo_root=repo)

    policy = harness.policies.resolve(team_id=_only_team_id(harness))
    assert policy.policy.write_mode == "isolated"
    # Not the repository itself: the run reads a pinned export of it, so the
    # working tree it was launched from is not even in scope.
    assert Path(policy.policy.read_path) != repo
    assert harness.exports in Path(policy.policy.read_path).parents


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


def test_the_evaluation_config_does_not_point_at_the_products_data():
    """A measurement run must not create personas, teams, runs or workspaces
    inside the user's real database, and must not depend on whatever that
    database already holds -- two sweeps a week apart would otherwise start
    from different states with nobody the wiser."""
    product_config = AppConfig(
        workspace_root=Path("/product/data/workspace"),
        session_dir=Path("/product/data/sessions"),
        app_db_path=Path("/product/data/app.sqlite"),
        lmg_base_url="http://127.0.0.1:9999",
    )

    eval_config = _evaluation_config(product_config)

    assert eval_config.app_db_path != product_config.app_db_path
    assert eval_config.workspace_root != product_config.workspace_root
    assert EVAL_DATA_ROOT in eval_config.app_db_path.parents
    assert eval_config.workspace_root == EVAL_WORKSPACE_ROOT
    # Isolating storage is the whole point -- everything else must still come
    # from the caller's real configuration.
    assert eval_config.lmg_base_url == product_config.lmg_base_url
    assert eval_config.session_dir == product_config.session_dir


def test_a_second_run_is_refused_while_one_holds_the_lock(tmp_path: Path):
    """Overlapping runs invalidate each other rather than merely competing.

    Artefacts are written inside the repository, so one run's artefact lands
    between another's before and after snapshots and the isolation check reports
    a changed tree that no run caused. A sweep lost fourteen of sixteen runs to
    exactly this before the lock existed.
    """
    config = AppConfig(
        workspace_root=tmp_path / "workspace",
        session_dir=tmp_path / "sessions",
        app_db_path=tmp_path / "data" / "app.sqlite",
        lmg_base_url="http://127.0.0.1:9999",
    )

    with only_one_run(config) as held:
        assert held.exists()
        with pytest.raises(RunInProgress):
            with only_one_run(config):
                raise AssertionError("the second run should not have started")

    # Released on the way out, so the next run is not blocked by the last one.
    assert not held.exists()


def test_the_lock_is_released_even_when_the_run_raises(tmp_path: Path):
    """A crashed run must not wedge every later one."""
    config = AppConfig(
        workspace_root=tmp_path / "workspace",
        session_dir=tmp_path / "sessions",
        app_db_path=tmp_path / "data" / "app.sqlite",
        lmg_base_url="http://127.0.0.1:9999",
    )

    with pytest.raises(RuntimeError):
        with only_one_run(config) as held:
            raise RuntimeError("the run died")

    assert not held.exists()
    with only_one_run(config):
        pass


def test_the_evaluation_workspace_lives_outside_the_repository():
    """Because the repository is the source the fixtures read.

    Source staging refuses a source root that contains the execution workspace
    -- staging it would copy the workspace into itself -- so a workspace under
    this repository makes every read_only fixture fail with "The selected source
    could not be staged" before a single model call is made. The database is free
    to stay inside the repository: it is never staged.
    """
    eval_config = _evaluation_config(
        AppConfig(
            workspace_root=Path("/product/data/workspace"),
            session_dir=Path("/product/data/sessions"),
            app_db_path=Path("/product/data/app.sqlite"),
            lmg_base_url="http://127.0.0.1:9999",
        )
    )

    workspace = eval_config.workspace_root.resolve()
    with pytest.raises(ValueError):
        workspace.relative_to(_REPO_ROOT)
