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
    )


def repository_is_unchanged(repo_root: Path) -> bool:
    """Whether the tracked working tree has no uncommitted changes.

    This checks the tracked working tree only. Anything gitignored is
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
            ["git", "-C", str(repo_root), "status", "--porcelain"],
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
    return result.stdout.strip() == ""
