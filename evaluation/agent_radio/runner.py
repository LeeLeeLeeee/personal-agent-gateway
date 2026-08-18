"""Drive the product for one evaluation run.

This is the only file here that imports personal_agent_gateway. It takes the
services off a real `create_app`, rather than rebuilding the wiring, because
TeamRuntime has more than ten collaborators and a second copy of that
assembly would drift from the real one without anyone noticing.
"""

import argparse
import asyncio
import io
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_radio.artifact import (
    ANSWERING_RUN_STATUSES,
    IMPLEMENTED_MODES,
    RunArtifact,
    write_artifact,
)
from agent_radio.fixture import Fixture, FixtureError, load_fixtures
from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig, load_config

# Generous on purpose. This is not a performance budget -- wall_ms is the
# measurement -- it is the bound that stops one hung run from stalling a whole
# sweep with nothing to show for it.
DEFAULT_TIMEOUT_SECONDS = 1800.0
# Named here rather than inherited from PersonaService's defaults so that the
# artefact can say what produced its answer. See run_fixture.
DEFAULT_BACKEND = "codex"
DEFAULT_MODEL = "default"
# Deliberately far below DEFAULT_TIMEOUT_SECONDS. A worker that cleanly
# declares a failure does not just fail its task -- the product opens a user
# decision and the run parks at waiting_for_user, and with no human present to
# resolve it, run_fixture then waits out its whole timeout before recording
# anything. At 1800s that is 30 minutes of nothing per such run, and a sweep
# of a handful of fixtures cannot absorb that. Five minutes is still generous
# for one fixture actually making progress, and --timeout-seconds overrides it
# for a run expected to take longer.
CLI_DEFAULT_TIMEOUT_SECONDS = 300.0


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
    personas: object
    recovery: object
    # Where pinned source exports live. Environment wiring, like the database:
    # a per-run argument would let a test that forgot it write into the real
    # sweep's export directory.
    exports: Path


def build_harness(config: AppConfig) -> Harness:
    """Take the wired services off a real app.

    No HTTP and no TestClient: /api is OTP-gated and automating that login
    would be working around authentication rather than with it. create_app
    wires team_runtime and its collaborators directly, so app.state is a
    service container that is by construction the same one the API uses.

    The lifespan is deliberately never entered. create_app wires the cycle
    dispatcher, cycle loop, job worker, hook runner and scheduler, but only
    starts them inside the lifespan context (see
    tests/test_app_lifecycle.py: alive is False right after create_app, and
    only True inside TestClient / lifespan_context). So nothing here pumps
    queued cycles -- a caller must drive TeamRuntime directly rather than
    queue work and wait for a loop that is not running. The failure mode of
    forgetting this is not an error: it is a run that sits queued forever.
    """
    try:
        app = create_app(config)
    except Exception as exc:
        raise RunnerError(f"could not create the app: {exc}") from exc
    state = app.state
    return Harness(
        app=app,
        teams=state.team_run_service,
        runtime=state.team_runtime,
        policies=state.space_policy_service,
        directory=state.team_directory_service,
        rules=state.rule_set_service,
        personas=state.persona_service,
        recovery=state.team_provider_recovery,
        exports=EVAL_SOURCE_ROOT,
    )


def repository_status(repo_root: Path) -> str:
    """git's status text for the working tree, with untracked files fingerprinted.

    Returned as text rather than a boolean because the question a run asks is
    not "is this tree clean" but "does git describe this tree the same way it
    did before the run". Two different dirty states are not the same tree, and
    collapsing them to "dirty then, dirty now" would hide exactly the escape
    this check exists to catch.

    Be precise about what that text can and cannot see, because
    `repository_unchanged=True` means only "this string is identical", which is
    weaker than "the run wrote nothing":

    - `-uall` is not optional. Default `--porcelain` collapses an untracked
      *directory* to a single `?? dir/` line, so a new file created inside an
      already-untracked directory produces byte-identical output before and
      after -- and creating a file is the escape signature this exists to
      catch. `-uall` names every untracked path individually. It costs nothing
      on this repository because every large directory in it is gitignored.
    - Each `?? ` line carries the file's size and mtime, so an untracked file
      that is *rewritten* rather than created also shows up. That is bounded by
      what git already enumerated, not a tree walk of our own.
    - It still cannot see a *tracked* file that was already modified before the
      run and modified again during it: both reads say ` M path` and nothing
      hashes the content. Nor does it see a file whose path git quotes (paths
      with characters `core.quotePath` escapes), which is left un-fingerprinted
      rather than guessed at.
    - It sees nothing gitignored at all; that is deliberate, see below.

    This reads the tracked working tree only. Anything gitignored is
    invisible to it by design, not by oversight: `data/app.sqlite` is
    written on essentially every request the product serves, so a check that
    counted ignored paths would flag every run against itself and be
    permanently useless. `data/workspace/`, where a run's own isolated
    workspace lives, is gitignored for the same reason -- and writing there
    is exactly what an isolated run is supposed to do, for both execution
    profiles, so that path being invisible here is the check working, not a
    gap in it. What this guards is the tracked source tree: a scratch file
    or a modified tracked file left behind by a run that should not have
    touched it.

    repo_root must be a repository's own root, not merely somewhere inside
    one. `git -C` walks upward to find a `.git`, so pointing this at a
    subdirectory would silently report the *enclosing* repository's whole
    status -- including changes made by something else entirely outside
    repo_root. That precondition is checked explicitly and refused loudly:
    a wrong answer about isolation is worse than no answer.
    """
    try:
        toplevel = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerError("git is not available on PATH") from exc
    if toplevel.returncode != 0:
        raise RunnerError(
            f"cannot read git status for {repo_root}: {toplevel.stderr.strip()}"
        )
    resolved_toplevel = Path(toplevel.stdout.strip()).resolve()
    if resolved_toplevel != repo_root.resolve():
        raise RunnerError(
            f"{repo_root} is not a repository root "
            f"(its repository root is {resolved_toplevel})"
        )

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerError("git is not available on PATH") from exc
    if result.returncode != 0:
        raise RunnerError(
            f"cannot read git status for {repo_root}: {result.stderr.strip()}"
        )
    return _fingerprint_untracked(repo_root, result.stdout)


def _fingerprint_untracked(repo_root: Path, status_text: str) -> str:
    """Append size and mtime to each untracked line.

    Only `?? ` lines: a tracked file's modification already shows in its status
    code, and stat()ing every path in a large repository would be the tree walk
    -uall lets us avoid. A path we cannot stat -- git quoted it, or it vanished
    between the two calls -- is left exactly as git printed it, because a
    guessed fingerprint would be worse than an honest gap.
    """
    lines = []
    for line in status_text.splitlines():
        fingerprint = ""
        if line.startswith("?? "):
            try:
                stat = (repo_root / line[3:]).stat()
            except OSError:
                fingerprint = ""
            else:
                fingerprint = f" size={stat.st_size} mtime_ns={stat.st_mtime_ns}"
        lines.append(line + fingerprint)
    return "\n".join(lines)


def repository_is_unchanged(repo_root: Path) -> bool:
    """Whether the tracked working tree has no uncommitted changes at all.

    An absolute question, and the one a caller wants when it needs to know the
    tree is pristine. A *run* asks the relative question instead -- see
    repository_status.
    """
    return repository_status(repo_root).strip() == ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def run_fixture(
    harness: Harness,
    fixture: Fixture,
    *,
    mode: str,
    repo_root: Path,
    now: Callable[[], datetime] = utc_now,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    backend: str = DEFAULT_BACKEND,
    model: str = DEFAULT_MODEL,
) -> RunArtifact:
    """Run one fixture once and describe what happened.

    Never raises for a failed run -- a failure is a result, and the artefact is
    how it gets counted. Dropping failures would inflate every success rate
    computed later, so a run that failed comes back as an artefact carrying its
    status and error, not as an exception. It raises only when the run could
    not be *set up*, because there is then nothing to describe. Once the
    provider call has been paid for, nothing after it is allowed to destroy the
    artefact: a hang, a vanished git, a moved repository all become a recorded
    non-scoreable result.

    `now` is the caller's clock, called exactly twice -- once before the run
    and once after -- so both timestamps and the wall time derived from them
    come from the same source and a test can pin all three.

    `backend` and `model` are named explicitly rather than left to
    PersonaService's defaults, and land on the artefact. An artefact that
    cannot say what produced its answer is not comparable with anything, and
    that is not a thing to discover after the invoice. What is recorded is what
    was *requested*: a model alias like "default" is resolved upstream, and the
    runner never sees what it resolved to.
    """
    if mode not in IMPLEMENTED_MODES:
        raise RunnerError(f"mode is not implemented: {mode!r}")
    # Resolved once, here: a relative path passes the repository-root check
    # (git resolves it) and then fails deep inside the space policy, which
    # requires an absolute directory. Normalizing at the entrance means one
    # failure mode instead of two.
    repo_root = repo_root.resolve()
    # Read before anything is created and before any provider call is spent.
    # Two jobs: it refuses a repo_root that is not a repository root -- the one
    # condition that would make the isolation check silently answer about a
    # different tree -- and it is the baseline the run is judged against. The
    # question `repository_unchanged` answers is "did this run change the
    # tree", not "is the tree clean": a checkout with uncommitted work in it is
    # the normal state of this repository, and grading against "clean" would
    # mark every run of a mid-development sweep unscoreable for something no
    # run did.
    status_before = repository_status(repo_root)

    try:
        # What the run reads: an export of the commit this fixture pins, not
        # the working tree it was launched from. See export_source.
        source, source_commit = export_source(
            repo_root, Path(harness.exports), fixture.repo_ref
        )
        leader = harness.personas.create_persona(
            f"Eval Lead ({fixture.id})",
            "lead",
            "Plans the evaluation task and reports the answer.",
            [],
            [],
            default_backend=backend,
            default_model=model,
        )
        member = harness.personas.create_persona(
            f"Eval Worker ({fixture.id})",
            "worker",
            "Carries out the evaluation task.",
            [],
            [],
            default_backend=backend,
            default_model=model,
        )
        team = harness.directory.create_team(
            f"Eval {fixture.id}",
            fixture.title,
            leader.id,
            [member.id],
        )
        # The isolation the ADR requires, set before the run is created: the
        # run reads the pinned export and writes only to its own workspace.
        # create_team_run snapshots the policy, so a later change cannot
        # retroactively alter what this run was allowed to do.
        harness.policies.upsert(
            "team",
            team.id,
            read_mode="selected",
            read_path=str(source),
            write_mode="isolated",
            workspace_path=None,
        )
        run = harness.teams.create_team_run_from_team(
            harness.directory,
            harness.rules,
            team_id=team.id,
            goal=fixture.goal,
            run_mode="plan_and_execute",
            max_workers=1,
            lifecycle_mode="continuous",
            execution_policy="triggered",
        )
        # A task that asks for an edit needs something it is allowed to edit.
        # The staged snapshot is not it: acceptance verifies it byte for byte
        # and refuses the work if it moved, so an agent that edits the source in
        # place is rejected with input_snapshot_modified however good its patch
        # was. The copy goes in the working root because that is the one place
        # the space policy tells the agent it may write. read_only fixtures get
        # nothing -- a writable copy of the repository in the workspace is an
        # invitation to write one.
        if fixture.execution_profile == "bounded_write":
            shutil.copytree(
                source,
                Path(run.working_root or run.workspace_root) / "source",
            )
        cycle = harness.teams.create_cycle(run.id, "manual", f"eval-{fixture.id}")
        # The rest of the dispatcher's prologue. TeamCycleDispatcher.run_one
        # creates the cycle and then freezes the provider snapshot onto it,
        # because the model factory reads that snapshot back out of the cycle
        # rather than off the registry -- pinning what the run was allowed to do
        # to the moment it started. Creating a cycle without freezing it leaves
        # the run unable to build a single model client, and the refusal names
        # the provider ("codex: capabilities_unavailable") for a gateway that is
        # reporting it as ready, which reads as an outage rather than a missing
        # step here.
        #
        # A freeze failure belongs to setup: no provider call has been paid for,
        # so there is no run to describe, and a sweep that turned this into a
        # recorded "failed run" would bill a broken provider as evidence about
        # the modes under test.
        cycle = harness.recovery.freeze_cycle(cycle.id)
    except Exception as exc:
        raise RunnerError(f"could not set up a run for {fixture.id}: {exc}") from exc

    started = now()
    raised: str | None = None
    try:
        # Driven directly, not queued. build_harness never enters the app's
        # lifespan, so the cycle dispatcher and cycle loop are wired but not
        # running: a queued cycle request would sit queued forever and nothing
        # would raise.
        #
        # Bounded, because neither TeamRuntime.start nor a provider call has a
        # wall-clock limit of its own: without this a single hung run stalls a
        # whole sweep and produces no artefact at all. Cancellation reaches
        # TeamRuntime, which settles the run as canceled, so the timeout is
        # recorded rather than merely escaped.
        await asyncio.wait_for(
            harness.runtime.start(run.id, cycle.id),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        raised = f"run exceeded its {timeout_seconds:g}s wall-clock bound"
    except Exception as exc:  # noqa: BLE001 - a failed run is a result
        raised = str(exc) or type(exc).__name__
    finished = now()

    # Everything from here is bookkeeping over a run that has already been paid
    # for, so nothing in it may raise. A lost artefact costs the provider call
    # twice: once to make it, once to make it again.
    try:
        final = harness.teams.get_team_run(run.id)
    except Exception as exc:  # noqa: BLE001
        final = run
        raised = _joined(raised, f"could not read the finished run back: {exc}")
    try:
        repository_unchanged = repository_status(repo_root) == status_before
    except RunnerError as exc:
        # Unverifiable is not the same as verified-clean, and only one of the
        # two is safe to assume.
        repository_unchanged = False
        raised = _joined(raised, f"could not verify isolation: {exc}")

    error = _joined(final.error_message, raised)
    if not error and final.status not in ANSWERING_RUN_STATUSES:
        # Only for a status that carries no answer. `completed_with_failures`
        # is a real answer with a real summary and no error of its own --
        # inventing one for it would discard a gradeable run because some
        # optional task failed.
        error = f"run ended as {final.status}"
    return RunArtifact(
        run_id=run.id,
        fixture_id=fixture.id,
        fixture_sha256=fixture.sha256,
        mode=mode,
        execution_profile=fixture.execution_profile,
        backend=backend,
        model=model,
        source_commit=source_commit,
        started_at=_isoformat(started),
        finished_at=_isoformat(finished),
        wall_ms=max(int((finished - started).total_seconds() * 1000), 0),
        run_status=final.status,
        summary=final.summary,
        workspace_path=final.working_root or final.workspace_root,
        repository_unchanged=repository_unchanged,
        error=error,
    )


def _joined(first: str | None, second: str | None) -> str | None:
    """Both reasons or the one there is. A run can fail and then also refuse to
    be verified, and the second must not overwrite the first."""
    return "; ".join(part for part in (first, second) if part) or None


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# evaluation/agent_radio/runner.py -> repo root is two levels up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASKS_DIR = Path(__file__).resolve().parent / "tasks"
_RUNS_DIR = Path(__file__).resolve().parent / "runs"
# Isolated from the product's real data on purpose. build_harness stands up
# personas, teams, runs and workspaces through the same services the running
# product uses, so pointing that at .env's AGENT_APP_DB_PATH /
# AGENT_WORKSPACE_ROOT would create evaluation clutter inside the user's
# actual database, visible in the UI next to their real work -- and it would
# also make a sweep's outcome depend on whatever that database already
# happens to hold, so two sweeps a week apart would not be measuring the same
# thing. `data/` is already gitignored, and repository_status deliberately
# never looks there (see its docstring), so `data/eval/` is invisible to the
# isolation check for the same reason `data/workspace/` already is.
EVAL_DATA_ROOT = _REPO_ROOT / "data" / "eval"
# Outside the repository, unlike the database, and not by preference: source
# staging refuses a source root that contains the execution workspace, because
# staging it would copy the workspace into itself. This repository is the source
# every fixture reads, so a workspace anywhere beneath it fails every read_only
# run with "The selected source could not be staged" before a model is called.
# Adjacent rather than in a temporary directory so that a staged snapshot is
# still there to inspect when an answer needs explaining, and so that deleting
# it is one obvious `rm -rf`.
# Short, and on the drive the user's home is on. Not cosmetic: a staged file's
# destination is <workspace>/<run id>/workspace/._inputs-<32 hex>/01-<root
# name>/<path in the repository>, which spends about 110 characters before the
# repository's own paths start, and Windows stops at 260. The obvious home for
# this -- beside the repository, under `playground/` -- overruns that limit for
# the deepest documents in `docs/`.
_EVAL_ROOT = Path(Path.home().anchor) / "pag-eval"
EVAL_WORKSPACE_ROOT = _EVAL_ROOT / "workspace"
EVAL_SOURCE_ROOT = _EVAL_ROOT / "source"


def export_source(repo_root: Path, exports_root: Path, ref: str) -> tuple[Path, str]:
    """A pristine checkout of `ref`, and the commit it resolved to.

    The caller passes the fixture's `repo_ref`, never HEAD: a rubric is written
    against a particular tree, and grading an answer about today's code with
    yesterday's rubric compares two different things while looking entirely
    valid. Nothing downstream would catch it -- `is_stale` compares fixture
    definitions, not commits -- so the pin has to be honoured here.

    What a run reads has to be a commit rather than a working tree, for two
    reasons of unequal weight. The lesser one is mechanical: staging this
    repository's working tree copies 2629 files, including nested checkouts
    under `.worktrees` and browser caches under `data/backups`, with relative
    paths up to 189 characters -- which no workspace location can fit inside
    Windows' path limit. A commit is 679 files and 96 characters.

    The greater one is that a sweep reading uncommitted work measures something
    nobody can repeat. `git archive` gives exactly the tracked files at that
    commit, with no `.git` directory for a model to go spelunking in.

    Reused across runs of the same commit, and the completion marker sits beside
    the export rather than inside it: a directory left half-written by a killed
    run must not be mistaken for a source tree, and the export the model reads
    must contain nothing this harness put there.
    """
    commit = _git_output(repo_root, "rev-parse", f"{ref}^{{commit}}")
    destination = exports_root / f"pag-{commit[:7]}"
    marker = exports_root / f"pag-{commit[:7]}.complete"
    if marker.exists():
        return destination, commit
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    archive = _git_bytes(repo_root, "archive", "--format=tar", commit)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    _remove_evaluation_material(destination)
    marker.write_text(f"{commit}\n", encoding="utf-8")
    return destination, commit


def _remove_evaluation_material(export: Path) -> None:
    """Take this evaluation's own design material out of what a run may read.

    It lives in the repository the fixtures ask questions about. One plan
    document states that the impact fixture's items "should force naming
    REPAIR_STAGE, the grouping requirement in team_provider_recovery.py, and the
    completeness tests" -- three of that fixture's five rubric items, in the
    tree the model is reading. The first sweep's answer cited it by path and
    line, so this is not hypothetical.

    Removed after extraction rather than filtered during it: `git archive` does
    not honour exclude pathspecs, and a filter that silently matched nothing
    would be indistinguishable from one that worked.

    Only material about the evaluation goes. Unrelated design documents stay --
    they are part of the codebase the questions are about, and dropping them
    would change the subject rather than close a leak.
    """
    removed = 0
    evaluation = export / "evaluation"
    if evaluation.is_dir():
        shutil.rmtree(evaluation)
        removed += 1
    for path in export.glob("docs/**/*agent-radio*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    if not removed:
        # Not an error: a commit predating the evaluation has nothing to leak.
        # Said out loud so that a future rename cannot turn this into a silent
        # no-op that reads like a clean export.
        print(f"note: no evaluation material found in {export.name}")


def _git_output(repo_root: Path, *args: str) -> str:
    return _git_bytes(repo_root, *args).decode("utf-8").strip()


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerError("git is not available on PATH") from exc
    if result.returncode != 0:
        raise RunnerError(
            f"git {' '.join(args)} failed in {repo_root}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def _evaluation_config(config: AppConfig) -> AppConfig:
    """Point storage at an evaluation-only database and workspace root.

    Everything else -- the LMG base URL, provider settings, and the rest of
    what `.env` configures -- passes through from `config` unchanged.
    Isolating storage is the whole point; building a second configuration is
    not.
    """
    return config.model_copy(
        update={
            "app_db_path": EVAL_DATA_ROOT / "app.sqlite",
            "workspace_root": EVAL_WORKSPACE_ROOT,
        }
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fixture once against the real product and write its artefact.

    Returns non-zero only when the run could not be *set up*: an unknown
    fixture, a fixture directory that fails to load, or anything run_fixture
    itself raises for. Returns zero for a run that ran and failed -- a failed
    run is a successful measurement, and the artefact this writes is where
    that failure gets counted. Do not "fix" this to return non-zero whenever
    the artefact's run_status is not a success: that would make a failing
    mode look like a broken tool, which inverts exactly the distinction this
    harness exists to draw.
    """
    parser = argparse.ArgumentParser(prog="python -m agent_radio.runner")
    parser.add_argument("--fixture", required=True, help="fixture id to run")
    parser.add_argument("--mode", required=True, help="mode to run it under")
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=CLI_DEFAULT_TIMEOUT_SECONDS,
        help="wall-clock bound for the run, in seconds "
        f"(default: {CLI_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    args = parser.parse_args(argv)

    try:
        fixtures = load_fixtures(_TASKS_DIR)
    except FixtureError as exc:
        print(f"error: could not load fixtures: {exc}", file=sys.stderr)
        return 1

    fixture = fixtures.get(args.fixture)
    if fixture is None:
        print(f"error: no such fixture: {args.fixture!r}", file=sys.stderr)
        return 1

    config = _evaluation_config(load_config())
    print(f"database: {config.app_db_path}")
    print(f"workspace: {config.workspace_root}")

    try:
        harness = build_harness(config)
        artifact = asyncio.run(
            run_fixture(
                harness,
                fixture,
                mode=args.mode,
                repo_root=_REPO_ROOT,
                timeout_seconds=args.timeout_seconds,
                backend=args.backend,
                model=args.model,
            )
        )
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        path = write_artifact(_RUNS_DIR, artifact)
    except FixtureError as exc:
        print(f"error: could not write the artefact: {exc}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
