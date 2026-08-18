"""Drive the product for one evaluation run.

This is the only file here that imports personal_agent_gateway. It takes the
services off a real `create_app`, rather than rebuilding the wiring, because
TeamRuntime has more than ten collaborators and a second copy of that
assembly would drift from the real one without anyone noticing.
"""

import asyncio
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_radio.artifact import (
    ANSWERING_RUN_STATUSES,
    IMPLEMENTED_MODES,
    RunArtifact,
)
from agent_radio.fixture import Fixture
from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig

# Generous on purpose. This is not a performance budget -- wall_ms is the
# measurement -- it is the bound that stops one hung run from stalling a whole
# sweep with nothing to show for it.
DEFAULT_TIMEOUT_SECONDS = 1800.0
# Named here rather than inherited from PersonaService's defaults so that the
# artefact can say what produced its answer. See run_fixture.
DEFAULT_BACKEND = "codex"
DEFAULT_MODEL = "default"


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
        # run reads the repository and writes only to its own workspace.
        # create_team_run snapshots the policy, so a later change cannot
        # retroactively alter what this run was allowed to do.
        harness.policies.upsert(
            "team",
            team.id,
            read_mode="selected",
            read_path=str(repo_root),
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
        cycle = harness.teams.create_cycle(run.id, "manual", f"eval-{fixture.id}")
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
