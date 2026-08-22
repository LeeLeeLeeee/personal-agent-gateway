"""Drive the product for one evaluation run.

This is the only file here that imports personal_agent_gateway. It takes the
services off a real `create_app`, rather than rebuilding the wiring, because
TeamRuntime has more than ten collaborators and a second copy of that
assembly would drift from the real one without anyone noticing.
"""

import argparse
import asyncio
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
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
from personal_agent_gateway.lmg_client import fetch_sessions_strict

# Generous on purpose. This is not a performance budget -- wall_ms is the
# measurement -- it is the bound that stops one hung run from stalling a whole
# sweep with nothing to show for it.
DEFAULT_TIMEOUT_SECONDS = 1800.0
# Named here rather than inherited from PersonaService's defaults so that the
# artefact can say what produced its answer. See run_fixture.
DEFAULT_BACKEND = "codex"
# Named exactly, not through the "default" alias. The alias resolves to whatever
# the local Codex configuration selects, so two sweeps a month apart can run
# different models with nothing in the request to show it.
DEFAULT_MODEL = "gpt-5.6-luna"
# Requested explicitly even though it matches what the gateway would substitute
# (runtime_factory.py:132), because a value that arrives by substitution cannot
# be told from one nobody chose -- and this model's own default is medium, so the
# substitution is doing real work either way.
#
# xhigh was tried first and is not affordable here: on one fixture the worker was
# still going at 71 turns when the 900s bound cut it off, against 8-15 turns for
# the same fixture at high. It was making progress the whole time, so this is a
# budget decision rather than a hang.
DEFAULT_EFFORT = "high"
# Still well below DEFAULT_TIMEOUT_SECONDS, and for the same reason: a worker
# that cleanly declares a failure does not just fail its task -- the product
# opens a user decision and the run parks at waiting_for_user, and with no human
# present to resolve it, run_fixture waits out the whole bound before recording
# anything. So this is the cost of one such run, paid per occurrence.
#
# Raised from 300s once real durations were in hand: measured runs on this
# repository land between 150s and 460s, so five minutes truncated healthy runs
# rather than only catching parked ones. 900s covers the observed range with
# room and is what every sweep was already passing explicitly.
CLI_DEFAULT_TIMEOUT_SECONDS = 900.0
# How much of an isolation diff the artefact keeps. Enough to name what escaped
# -- a couple of hundred status lines -- without letting one run that wandered
# into a build directory write a megabyte of paths into a file that is read as a
# record of the run. Anything cut is said out loud; see _status_diff.
REPOSITORY_DIFF_LIMIT = 4000


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
    # Reads the gateway's session list. Injected rather than called directly so
    # that a test can describe a session without an LMG to ask.
    sessions: object


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
        sessions=lambda: fetch_sessions_strict(config),
    )


@dataclass(frozen=True)
class ProviderTrace:
    """What the provider itself recorded about a run.

    Every field is optional because every one of them is a fact that may simply
    not have been kept. None means "not recoverable", never "zero" and never a
    default -- a run whose cost is unknown must not be averaged in as a cheap
    one, and a run whose model is unknown must not be compared as if it were the
    alias that was requested.
    """

    model: str | None
    effort: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None


EMPTY_TRACE = ProviderTrace(None, None, None, None, None)


def provider_trace(sessions: object, run_id: str) -> ProviderTrace:
    """Read back what actually ran, from the provider's own transcripts.

    The artefact's `model` is what was *requested*, and for codex that is
    normally the alias "default" -- "use whatever the local configuration
    selects". Nothing in the gateway resolves it: the operation ledger and the
    session list both store the alias verbatim. Reasoning effort is worse than
    unresolved, it is never requested at all, so the only record of it is the
    provider's. Token usage is the same story from the other end: LMG reports
    it per account, which cannot be attributed to one run.

    All three live in the transcript, so all three are read here, through the
    sessions whose consumer id is this run. A run has more than one session --
    the leader and the worker each get their own -- so tokens are summed across
    them and `total_token_usage` is cumulative within a session, meaning the
    last count in each file is that session's total.

    Model and effort are collected as sets and reported only when the run agrees
    with itself. Two sessions that ran different models have no single answer to
    "which model answered", and picking either one would state something untrue
    rather than admit the ambiguity.

    Never raises. Every failure here -- no session yet, no transcript, a file
    already rotated away, a line that is not JSON -- is a missing fact, and this
    runs after the provider call has been paid for, where nothing is allowed to
    destroy the artefact.
    """
    try:
        rows = sessions()
    except Exception:  # noqa: BLE001 - an unavailable gateway is an unknown
        return EMPTY_TRACE
    paths = [
        row.get("storage_path")
        for row in rows or ()
        if isinstance(row, dict) and row.get("consumer_session_id") == run_id
    ]
    models: set[str] = set()
    efforts: set[str] = set()
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    for path in paths:
        if not isinstance(path, str) or not path:
            continue
        session_input: int | None = None
        session_cached: int | None = None
        session_output: int | None = None
        try:
            with Path(path).open(encoding="utf-8") as stream:
                for line in stream:
                    entry = _transcript_entry(line)
                    if entry is None:
                        continue
                    models.update(_texts(entry, ("model",)))
                    efforts.update(_texts(entry, ("effort", "reasoning_effort")))
                    usage = _token_usage(entry)
                    if usage is not None:
                        session_input, session_cached, session_output = usage
        except OSError:
            continue
        if session_input is not None:
            input_tokens = (input_tokens or 0) + session_input
        if session_cached is not None:
            cached_tokens = (cached_tokens or 0) + session_cached
        if session_output is not None:
            output_tokens = (output_tokens or 0) + session_output
    return ProviderTrace(
        model=_only(models),
        effort=_only(efforts),
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
    )


def _only(values: set[str]) -> str | None:
    """The single value, or None when the run does not agree with itself."""
    return next(iter(values)) if len(values) == 1 else None


def _transcript_entry(line: str) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _texts(entry: dict, keys: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    payload = entry.get("payload")
    for holder in (payload, entry):
        if not isinstance(holder, dict):
            continue
        for key in keys:
            value = holder.get(key)
            if isinstance(value, str) and value.strip():
                found.add(value)
    return found


def _token_usage(entry: dict) -> tuple[int, int, int] | None:
    """This entry's cumulative session totals, if it carries them.

    Cached input comes back as its own number rather than folded into the total,
    so a later reader can tell context carried from context newly processed.
    Absent cache reporting counts as zero cached, not as unknown: the provider
    reported a usage record and simply had no cache reads in it.
    """
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    totals = info.get("total_token_usage")
    if not isinstance(totals, dict):
        return None
    given = totals.get("input_tokens")
    produced = totals.get("output_tokens")
    if not isinstance(given, int) or isinstance(given, bool):
        return None
    if not isinstance(produced, int) or isinstance(produced, bool):
        return None
    cached = totals.get("cached_input_tokens")
    if not isinstance(cached, int) or isinstance(cached, bool) or cached < 0:
        cached = 0
    return given, min(cached, given), produced


class RunInProgress(RunnerError):
    """Another run holds the lock."""


def _lock_path(config: AppConfig) -> Path:
    return Path(config.app_db_path).parent / "run.lock"


def _lock_detail(path: Path) -> str:
    """Who holds the lock and for how long, best effort and never raising."""
    try:
        holder = path.read_text(encoding="utf-8").strip() or "unknown pid"
    except OSError:
        holder = "unreadable"
    try:
        age = int(time.time() - path.stat().st_mtime)
        return f"pid {holder}, held {age}s"
    except OSError:
        return f"pid {holder}"


@contextmanager
def only_one_run(config: AppConfig):
    """Hold the evaluation lock, or refuse.

    Concurrent runs do not just contend for the provider, they spoil each other's
    measurement: artefacts are written inside the repository, so one run's
    artefact lands between another's before and after snapshots and the isolation
    check reports a changed tree that no run caused. Both runs then look
    unscoreable for a reason belonging to neither.

    Exclusive create, and no staleness heuristic on purpose. A leftover lock
    means a run died without releasing it, and quietly stealing the lock is how a
    genuinely concurrent second run gets started anyway.
    """
    path = _lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        # The message carries what a person needs to decide, because the lock is
        # deliberately not self-clearing and a stale one blocks everything. A run
        # killed abruptly -- not raising, killed -- never reaches the release, and
        # that is exactly the case where the holder's pid and the lock's age tell
        # you in one glance whether to delete it.
        raise RunInProgress(
            f"another evaluation run holds {path} "
            f"({_lock_detail(path)}). If no run is active, delete it."
        ) from exc
    try:
        handle.write(f"{os.getpid()}\n")
        handle.close()
        yield path
    finally:
        path.unlink(missing_ok=True)


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
    plan_negotiation: bool = False,
    backend: str = DEFAULT_BACKEND,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    workers: int = 1,
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
    # The mode names a wiring the harness was built with, and the artefact
    # records the name rather than the wiring. A harness built for one arm and
    # labelled the other produces a record that reads as a controlled
    # comparison and is not one -- which no downstream check can catch, because
    # both arms are legal on their own.
    expects_notes = mode == "radio_lite"
    has_notes = getattr(harness.runtime, "_collaboration", None) is not None
    if has_notes is not expects_notes:
        raise RunnerError(
            f"mode {mode!r} needs peer messages "
            f"{'on' if expects_notes else 'off'}, but the harness was built "
            f"with them {'on' if has_notes else 'off'}"
        )
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
            default_options={"effort": effort},
        )
        # One persona per worker. `workers` many members give the peer-message
        # channel a peer to write to; without a second worker the feature is
        # unmeasurable. create_team_run_from_team below is given
        # max_workers=workers, but that is only the configured value the run
        # records (teams.py reports execution_mode as "sequential"
        # unconditionally) -- tasks still execute one at a time, never
        # concurrently. The ADR forbids parallel execute; this is sequential
        # execution across a bigger roster, not a way around that.
        members = [
            harness.personas.create_persona(
                f"Eval Worker {index + 1} ({fixture.id})",
                "worker",
                "Carries out the evaluation task.",
                [],
                [],
                default_backend=backend,
                default_model=model,
                default_options={"effort": effort},
            )
            for index in range(workers)
        ]
        team = harness.directory.create_team(
            f"Eval {fixture.id}",
            fixture.title,
            leader.id,
            [m.id for m in members],
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
            max_workers=workers,
            lifecycle_mode="continuous",
            execution_policy="triggered",
            plan_negotiation=plan_negotiation,
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
    repository_diff: str | None = None
    try:
        status_after = repository_status(repo_root)
        repository_unchanged = status_after == status_before
        if not repository_unchanged:
            repository_diff = _status_diff(status_before, status_after)
    except Exception as exc:  # noqa: BLE001 - see the note above: nothing here
        # may raise. Unverifiable is not the same as verified-clean, and only
        # one of the two is safe to assume; a diff that could not be built is
        # reported as no diff rather than as an unchanged tree.
        repository_unchanged = False
        repository_diff = None
        raised = _joined(raised, f"could not verify isolation: {exc}")

    # Cannot raise: provider_trace swallows its own failures and answers None
    # for anything it could not recover.
    trace = provider_trace(harness.sessions, run.id)

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
        plan_negotiation=plan_negotiation,
        execution_profile=fixture.execution_profile,
        backend=backend,
        model=model,
        effort=effort,
        source_commit=source_commit,
        resolved_model=trace.model,
        resolved_effort=trace.effort,
        input_tokens=trace.input_tokens,
        cached_input_tokens=trace.cached_input_tokens,
        output_tokens=trace.output_tokens,
        started_at=_isoformat(started),
        finished_at=_isoformat(finished),
        wall_ms=max(int((finished - started).total_seconds() * 1000), 0),
        run_status=final.status,
        summary=final.summary,
        workspace_path=final.working_root or final.workspace_root,
        workers=workers,
        repository_unchanged=repository_unchanged,
        repository_diff=repository_diff,
        error=error,
    )


def _status_diff(before: str, after: str) -> str | None:
    """What moved between the two status readings, as a unified diff.

    The two texts are git's own status output, so their diff names the paths
    that appeared, vanished or changed state -- which is the whole question a
    later reader has. Recording only that the two strings differed leaves that
    reader with a boolean and no way back to the cause.

    Truncation is announced rather than silent: a diff cut at the limit and
    presented as complete would read as "these are the four files it touched"
    when it touched four hundred, and that is a worse artefact than a short one.

    None when the diff is empty, which two texts differing only in trailing
    whitespace can produce. An empty string would claim a blank answer where
    null claims no answer, and the artefact refuses it for that reason.
    """
    text = "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="before the run",
            tofile="after the run",
            lineterm="",
        )
    )
    if not text.strip():
        return None
    if len(text) > REPOSITORY_DIFF_LIMIT:
        dropped = len(text) - REPOSITORY_DIFF_LIMIT
        return text[:REPOSITORY_DIFF_LIMIT] + (
            f"\n[diff truncated: {dropped} more characters]"
        )
    return text


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

    Safe against concurrent callers, because a sweep's slots share this
    directory: each extracts into its own temporary directory and publishes it
    with one atomic rename, so no caller can ever read a half-written export.
    Losing the rename race means someone else published a complete export
    first -- the loser discards its copy and uses the winner's.
    """
    commit = _git_output(repo_root, "rev-parse", f"{ref}^{{commit}}")
    destination = exports_root / f"pag-{commit[:7]}"
    marker = exports_root / f"pag-{commit[:7]}.complete"
    if marker.exists():
        return destination, commit
    staging = exports_root / f"pag-{commit[:7]}.staging-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    archive = _git_bytes(repo_root, "archive", "--format=tar", commit)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(staging, filter="data")
    _remove_evaluation_material(staging)
    if destination.exists():
        # No marker, so this is a leftover from a run killed mid-extract --
        # unless a concurrent caller published it between our marker check and
        # now, in which case the marker says so and the leftover is not one.
        if marker.exists():
            shutil.rmtree(staging)
            return destination, commit
        shutil.rmtree(destination)
    try:
        staging.rename(destination)
    except OSError:
        # A concurrent caller renamed its complete copy in first.
        shutil.rmtree(staging)
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


def _evaluation_config(
    config: AppConfig, *, mode: str, data_root: Path = EVAL_DATA_ROOT
) -> AppConfig:
    """Point storage at an evaluation-only database and workspace root.

    Everything else -- the LMG base URL, provider settings, and the rest of
    what `.env` configures -- passes through from `config` unchanged.
    Isolating storage is the whole point; building a second configuration is
    not.

    `mode` is here rather than at run_fixture because the peer-message channel
    is decided when the app is wired, and the harness is built from this
    config. Whatever `.env` says about the flag is overridden: a sweep's arms
    must differ by the arm and not by the machine it ran on.

    `data_root` exists for concurrent sweeps: two runs sharing one SQLite file
    would contend on it, and the run lock lives beside the database, so giving
    each slot its own root gives it its own database *and* its own lock in one
    move. The default keeps a plain run exactly where it always was.
    """
    return config.model_copy(
        update={
            "app_db_path": data_root / "app.sqlite",
            "workspace_root": EVAL_WORKSPACE_ROOT,
            "team_peer_messages_enabled": mode == "radio_lite",
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
    parser.add_argument(
        "--negotiation",
        action="store_true",
        help="negotiate the plan before executing it (its own axis, not a mode)",
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        help="reasoning effort to request (the gateway substitutes high if unset)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="how many workers to run the team with (default: 1)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=CLI_DEFAULT_TIMEOUT_SECONDS,
        help="wall-clock bound for the run, in seconds "
        f"(default: {CLI_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--slot",
        default=None,
        help="run in an isolated slot so runs can execute concurrently: the "
        "slot gets its own database, its own run lock, and writes its "
        "artefact under data/eval/slots/<slot>/runs instead of the tracked "
        "runs directory (a concurrent artefact landing in the tracked tree "
        "would spoil every other run's isolation snapshot). A sweep collects "
        "slot artefacts into the tracked directory after it finishes.",
    )
    args = parser.parse_args(argv)

    if args.slot is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", args.slot):
        print(f"error: slot is not a safe directory name: {args.slot!r}",
              file=sys.stderr)
        return 1

    try:
        fixtures = load_fixtures(_TASKS_DIR)
    except FixtureError as exc:
        print(f"error: could not load fixtures: {exc}", file=sys.stderr)
        return 1

    fixture = fixtures.get(args.fixture)
    if fixture is None:
        print(f"error: no such fixture: {args.fixture!r}", file=sys.stderr)
        return 1

    if args.slot is None:
        data_root = EVAL_DATA_ROOT
        runs_dir = _RUNS_DIR
    else:
        data_root = EVAL_DATA_ROOT / "slots" / args.slot
        runs_dir = data_root / "runs"
    config = _evaluation_config(load_config(), mode=args.mode, data_root=data_root)
    print(f"database: {config.app_db_path}")
    print(f"workspace: {config.workspace_root}")

    try:
        with only_one_run(config):
            harness = build_harness(config)
            artifact = asyncio.run(
                run_fixture(
                    harness,
                    fixture,
                    mode=args.mode,
                    repo_root=_REPO_ROOT,
                    timeout_seconds=args.timeout_seconds,
                    plan_negotiation=args.negotiation,
                    backend=args.backend,
                    model=args.model,
                    effort=args.effort,
                    workers=args.workers,
                )
            )
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        path = write_artifact(runs_dir, artifact)
    except FixtureError as exc:
        print(f"error: could not write the artefact: {exc}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
