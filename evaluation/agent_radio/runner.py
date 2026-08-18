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
