from pathlib import Path

from personal_agent_gateway.file_safety import is_sensitive_file
from personal_agent_gateway.team_verification_checks import safe_workspace_file
from personal_agent_gateway.teams import TeamTask


def _is_missing(workspace: Path, relative_path: str) -> bool:
    """Absent, or unreachable for a reason that is not absence.

    safe_workspace_file also refuses .env and .env.* by name, so asking it alone
    would report a file that is plainly there as missing and the screen would be
    telling the operator something false. Sensitive names are checked separately
    against the resolved path, still inside the workspace.
    """
    if safe_workspace_file(workspace, relative_path) is not None:
        return False
    root = workspace.resolve()
    try:
        candidate = (root / relative_path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return True
    return not (candidate.is_file() and is_sensitive_file(candidate.name))


def task_build_evidence(task: TeamTask, workspace: Path) -> dict[str, object]:
    """Compare what a task's contract asked for against what came back.

    Everything here is already stored; it has simply never been shown together.
    The two directions of the difference matter separately: a promise with no
    declaration is work that may not have happened, while a declaration with no
    promise is work outside the contract -- which the gate rejects outright, so
    without this view a rejected task looks like a failure with no explanation.
    """
    outcome = task.outcome or {}
    deliverables = outcome.get("deliverables") or []
    declared_paths = {
        str(entry.get("path"))
        for entry in deliverables
        if isinstance(entry, dict) and entry.get("path")
    }
    promised = set(task.acceptance.required_outputs)
    evidence = (task.acceptance_result or {}).get("evidence") or {}
    recorded = evidence.get("verifications") or {}

    return {
        "promised": sorted(promised),
        "declared": sorted(declared_paths),
        "undeclared_promises": sorted(promised - declared_paths),
        "extra_declarations": sorted(declared_paths - promised),
        "missing_files": sorted(
            path for path in declared_paths if _is_missing(workspace, path)
        ),
        "verifications": [
            {
                "name": name,
                "mode": str(entry.get("mode")),
                "status": str(entry.get("status")),
            }
            for name, entry in sorted(recorded.items())
            if isinstance(entry, dict)
        ],
        "worker_asserted_only": bool(evidence.get("attested_only")),
    }


def run_build_evidence(
    tasks: list[TeamTask], workspace: Path
) -> dict[str, object]:
    """The two numbers worth putting at the top of a run.

    Both say how much of the run's verdict rests on the workers' own word rather
    than on anything the gate looked at.
    """
    per_task = [task_build_evidence(task, workspace) for task in tasks]
    return {
        "task_count": len(per_task),
        "worker_asserted_only_count": sum(
            1 for item in per_task if item["worker_asserted_only"]
        ),
        "missing_file_count": sum(len(item["missing_files"]) for item in per_task),
    }
