from pathlib import Path, PurePosixPath, PureWindowsPath

from personal_agent_gateway.file_safety import is_sensitive_file
from personal_agent_gateway.team_verification_checks import safe_workspace_file
from personal_agent_gateway.teams import TeamTask


def _is_anchored(relative_path: str) -> bool:
    """A drive, a UNC share, or a leading separator, in either path flavour."""
    return any(
        bool(flavour(relative_path).anchor)
        for flavour in (PurePosixPath, PureWindowsPath)
    )


def _is_missing(workspace: Path, relative_path: str) -> bool:
    """Absent, or unreachable for a reason that is not absence.

    safe_workspace_file already walks the path, resolves it, and stats it once;
    redoing all of that in full would double the filesystem work for every
    declared path a run reports on -- but the resolve-and-contain check here
    is not that duplication, it is load-bearing on its own. pathlib's `/`
    discards the workspace root entirely when the declared path is itself
    absolute, so without re-resolving and checking containment, an absolute
    or `..`-escaping path that happens to match a real file on the host would
    read as present while safe_workspace_file refuses it. The symlink check
    that runs first must not follow the link to its target for the same
    reason: safe_workspace_file refuses a symlink outright, and the report
    must agree with the gate about what is reachable.

    An anchored declared path is refused before anything touches the disk. The
    path comes from model output, and `Path('C:/ws') / '//10.0.0.1/share/x'` is
    just `//10.0.0.1/share/x` -- pathlib drops the root for any absolute path,
    UNC included -- so statting first let a worker-declared deliverable turn
    every /detail poll into an outbound SMB connection to a host the model
    named, blocking a threadpool worker until the mount timed out. The
    containment check that would have caught it ran after the stat. Both path
    flavours are tested, not just the running platform's: the drive letter or
    UNC prefix that makes this dangerous on Windows is what a POSIX pathlib
    reads as an ordinary relative name.
    """
    if _is_anchored(relative_path):
        return True
    if safe_workspace_file(workspace, relative_path) is not None:
        return False
    root = workspace.resolve()
    candidate = root / relative_path
    try:
        if candidate.is_symlink():
            return True
        resolved = candidate.resolve()
        resolved.relative_to(root)
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
    per_task: list[dict[str, object]],
) -> dict[str, object]:
    """The two numbers worth putting at the top of a run.

    Both say how much of the run's verdict rests on the workers' own word rather
    than on anything the gate looked at.

    Takes the already-computed per-task reports rather than the tasks: every
    caller renders those too, and recomputing them here doubled the filesystem
    work for every declared path on a polled endpoint.
    """
    return {
        "task_count": len(per_task),
        "worker_asserted_only_count": sum(
            1 for item in per_task if item["worker_asserted_only"]
        ),
        "missing_file_count": sum(len(item["missing_files"]) for item in per_task),
    }
