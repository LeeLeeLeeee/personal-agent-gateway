from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_gateway.file_safety import is_sensitive_file
from personal_agent_gateway.source_staging import (
    InputSnapshotModified,
    SourceStager,
    StagedInputs,
)
from personal_agent_gateway.team_outcomes import TaskOutcome
from personal_agent_gateway.teams import TeamTask


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    status: Literal["completed", "blocked", "failed"]
    reason_code: str | None
    evidence: dict[str, object]


class TeamAcceptanceService:
    def __init__(self, stager: SourceStager | None = None) -> None:
        self._stager = stager or SourceStager()

    def evaluate(
        self,
        task: TeamTask,
        outcome: TaskOutcome,
        workspace_root: Path,
        *,
        staged_inputs: StagedInputs | None = None,
    ) -> AcceptanceResult:
        if outcome.status != "completed":
            return _rejected(outcome.status, outcome.reason_code or "task_not_completed")

        expected = set(task.acceptance.required_outputs)
        declared = {deliverable.path for deliverable in outcome.deliverables}
        if expected - declared:
            return _rejected("failed", "required_output_missing")
        if declared - expected:
            return _rejected("failed", "undeclared_deliverable")

        workspace = workspace_root.resolve()
        for deliverable in outcome.deliverables:
            if not _safe_file(workspace, deliverable.path):
                return _rejected("failed", "unsafe_deliverable")

        verification_by_name = {
            verification.name: verification for verification in outcome.verifications
        }
        for required in task.acceptance.required_verifications:
            verification = verification_by_name.get(required)
            if verification is None or verification.status != "passed":
                return _rejected("failed", "required_verification_failed")

        if staged_inputs is not None:
            try:
                self._stager.verify(staged_inputs)
            except InputSnapshotModified:
                return _rejected("blocked", "input_snapshot_modified")

        return AcceptanceResult(
            accepted=True,
            status="completed",
            reason_code=None,
            evidence={
                "deliverables": sorted(declared),
                "verifications": {
                    item.name: {
                        "status": item.status,
                        "evidence": item.evidence,
                    }
                    for item in outcome.verifications
                },
            },
        )


def _rejected(
    status: Literal["completed", "blocked", "failed"],
    reason_code: str,
) -> AcceptanceResult:
    return AcceptanceResult(
        accepted=False,
        status=status,
        reason_code=reason_code,
        evidence={},
    )


def _safe_file(workspace: Path, relative_path: str) -> bool:
    candidate = workspace / relative_path
    current = candidate
    while current != workspace:
        if current.is_symlink():
            return False
        current = current.parent
        if workspace not in current.parents and current != workspace:
            break
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        return False
    return (
        candidate.is_file()
        and not candidate.is_symlink()
        and not is_sensitive_file(candidate.name)
    )
