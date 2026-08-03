from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_gateway.source_staging import (
    InputSnapshotModified,
    SourceStager,
    StagedInputs,
)
from personal_agent_gateway.team_outcomes import TaskOutcome
from personal_agent_gateway.team_verification_checks import (
    run_verification_check,
    safe_workspace_file,
)
from personal_agent_gateway.teams import TeamTask


RECOVERABLE_ACCEPTANCE_REASONS = frozenset(
    {
        "undeclared_deliverable",
        "required_output_missing",
        "unsafe_deliverable",
        "required_verification_failed",
        "task_not_completed",
        "invalid_task_outcome",
    }
)


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    status: Literal["completed", "blocked", "failed"]
    reason_code: str | None
    evidence: dict[str, object]


def is_recoverable_acceptance_failure(reason_code: str | None) -> bool:
    return reason_code in RECOVERABLE_ACCEPTANCE_REASONS


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
        recorded: dict[str, dict[str, str]] = {}
        verified_count = 0
        for required in task.acceptance.required_verifications:
            reported = verification_by_name.get(required.name)
            if required.check is not None:
                outcome_result = run_verification_check(required.check, workspace)
                recorded[required.name] = {
                    "mode": "verified",
                    "status": "passed" if outcome_result.passed else "failed",
                    "evidence": outcome_result.evidence,
                }
                if not outcome_result.passed:
                    return _rejected("failed", "required_verification_failed")
                verified_count += 1
                continue
            if reported is None or reported.status != "passed":
                return _rejected("failed", "required_verification_failed")
            recorded[required.name] = {
                "mode": "attested",
                "status": reported.status,
                "evidence": reported.evidence,
            }

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
                "verifications": recorded,
                "attested_only": verified_count == 0,
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
    return safe_workspace_file(workspace, relative_path) is not None
