from collections.abc import Iterable
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


def is_recoverable_acceptance_failure(
    reason_code: str | None,
    *,
    worker_declared: bool = False,
) -> bool:
    return worker_declared or reason_code in RECOVERABLE_ACCEPTANCE_REASONS


def is_worker_declared_outcome(outcome: TaskOutcome) -> bool:
    """Did the Worker itself declare this non-completed outcome?

    `TeamRuntime._task_outcome` synthesizes ``status="blocked"`` with
    ``reason_code="invalid_task_outcome"`` when a Worker response cannot be
    parsed. That is a server-detected failure, not something the Worker
    declared, so it must not earn the worker-declared recovery path.
    """
    return (
        outcome.status != "completed"
        and outcome.reason_code != "invalid_task_outcome"
    )


def terminal_rejected_status(
    status: str,
    *,
    worker_declared: bool,
) -> Literal["blocked", "failed"]:
    """Terminal task status for a rejected outcome that cannot recover further.

    A `blocked` the Worker declared stays `blocked`; anything else ends `failed`.
    """
    return "blocked" if status == "blocked" and worker_declared else "failed"


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
        unverified: list[str] = []
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
                    return _rejected(
                        "failed",
                        "required_verification_failed",
                        evidence={"verifications": recorded},
                    )
                verified_count += 1
                continue
            if reported is None:
                return _rejected(
                    "failed",
                    "required_verification_failed",
                    evidence={"verifications": recorded},
                )
            if not reported.checked:
                # Accepted, but recorded rather than counted. The worker told us
                # it could not confirm this; the run should carry that forward
                # instead of reading it as a pass.
                unverified.append(required.name)
                recorded[required.name] = {
                    "mode": "unverified",
                    "status": "unknown",
                    "evidence": reported.evidence,
                }
                continue
            if reported.status != "passed":
                return _rejected(
                    "failed",
                    "required_verification_failed",
                    evidence={"verifications": recorded},
                )
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
                "unverified": unverified,
            },
        )


def _rejected(
    status: Literal["completed", "blocked", "failed"],
    reason_code: str,
    *,
    evidence: dict[str, object] | None = None,
) -> AcceptanceResult:
    return AcceptanceResult(
        accepted=False,
        status=status,
        reason_code=reason_code,
        evidence=evidence or {},
    )


def rejected_verification_names(
    required_verifications: Iterable[tuple[str, bool]],
    verification_status: dict[str, str | None],
    acceptance_evidence: dict[str, object],
) -> list[str]:
    """Names of required verifications not confirmed passed.

    For a checked verification, the server's own verdict (carried in
    ``acceptance_evidence["verifications"]``, populated by `evaluate`) decides
    it — never the worker's self-report. `evaluate` returns on the first
    failure, so a checked verification that the loop never reached (because
    an earlier deliverable or verification check failed, or the loop never
    started at all) has no entry in ``acceptance_evidence["verifications"]``.
    A checked verification is therefore blamed only when the server recorded
    it as failed; one with no entry is never blamed. For an attested
    (check-less) verification, the worker's self-reported status is the only
    signal available, so it is used as before.
    """
    verified = acceptance_evidence.get("verifications")
    verified_status = (
        {name: entry.get("status") for name, entry in verified.items()}
        if isinstance(verified, dict)
        else {}
    )
    return [
        name
        for name, has_check in required_verifications
        if (
            verified_status.get(name) == "failed"
            if has_check
            else verification_status.get(name) != "passed"
        )
    ]


def _safe_file(workspace: Path, relative_path: str) -> bool:
    return safe_workspace_file(workspace, relative_path) is not None
