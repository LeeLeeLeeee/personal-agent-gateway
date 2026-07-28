from pathlib import Path

import pytest

from personal_agent_gateway.source_staging import SourceStager
from personal_agent_gateway.team_acceptance import TeamAcceptanceService
from personal_agent_gateway.team_outcomes import (
    Deliverable,
    TaskOutcome,
    VerificationEvidence,
)
from personal_agent_gateway.teams import TaskAcceptance, TeamTask

_DEFAULT_DELIVERABLES = (Deliverable("outputs/report.md", "markdown"),)
_DEFAULT_VERIFICATIONS = (VerificationEvidence("pytest", "passed", "42 passed"),)


def _task(*, outputs=("outputs/report.md",), verifications=("pytest",)) -> TeamTask:
    return TeamTask(
        id="task-1",
        team_run_id="run-1",
        title="Report",
        description="Write report",
        owner_agent_id="worker-1",
        status="in_progress",
        required=True,
        acceptance=TaskAcceptance(outputs, verifications),
        outcome=None,
        acceptance_result=None,
        result=None,
        error_message=None,
        created_at="t",
        updated_at="t",
    )


def _outcome(
    *,
    deliverables=_DEFAULT_DELIVERABLES,
    verifications=_DEFAULT_VERIFICATIONS,
) -> TaskOutcome:
    return TaskOutcome(
        status="completed",
        summary="done",
        reason_code=None,
        deliverables=deliverables,
        verifications=verifications,
    )


def test_accepts_exact_outputs_and_passed_verifications(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")

    result = TeamAcceptanceService().evaluate(_task(), _outcome(), workspace)

    assert result.accepted is True
    assert result.status == "completed"
    assert result.reason_code is None


@pytest.mark.parametrize(
    ("task", "outcome", "reason"),
    [
        (_task(), _outcome(deliverables=()), "required_output_missing"),
        (
            _task(outputs=()),
            _outcome(deliverables=(Deliverable("outputs/report.md", "markdown"),)),
            "undeclared_deliverable",
        ),
        (
            _task(),
            _outcome(
                verifications=(
                    VerificationEvidence("pytest", "failed", "1 failed"),
                )
            ),
            "required_verification_failed",
        ),
    ],
)
def test_rejects_missing_undeclared_or_failed_evidence(
    tmp_path: Path,
    task: TeamTask,
    outcome: TaskOutcome,
    reason: str,
) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == reason


@pytest.mark.parametrize("unsafe_kind", ["outside", "directory", "sensitive", "symlink"])
def test_rejects_unsafe_deliverable_files(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "outputs" / "report.md"
    if unsafe_kind == "outside":
        path = tmp_path / "outside.md"
        path.write_text("outside", encoding="utf-8")
        outcome = _outcome(deliverables=(Deliverable("../outside.md", "markdown"),))
        task = _task(outputs=("../outside.md",))
    elif unsafe_kind == "directory":
        path.mkdir(parents=True)
        outcome = _outcome()
        task = _task()
    elif unsafe_kind == "sensitive":
        path = workspace / ".env"
        path.write_text("SECRET=x", encoding="utf-8")
        outcome = _outcome(deliverables=(Deliverable(".env", "text"),))
        task = _task(outputs=(".env",))
    else:
        target = workspace / "target.md"
        target.write_text("target", encoding="utf-8")
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlinks are unavailable for this Windows account")
        outcome = _outcome()
        task = _task()

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == "unsafe_deliverable"


def test_modified_staged_inputs_block_acceptance(tmp_path: Path) -> None:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    (source / "evidence.txt").write_text("original", encoding="utf-8")
    staged = SourceStager(home=tmp_path / "home").stage((source,), workspace)
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir()
    output.write_text("report", encoding="utf-8")
    (workspace / "_inputs" / "01-source" / "evidence.txt").write_text(
        "changed",
        encoding="utf-8",
    )

    result = TeamAcceptanceService().evaluate(
        _task(),
        _outcome(),
        workspace,
        staged_inputs=staged,
    )

    assert result.accepted is False
    assert result.status == "blocked"
    assert result.reason_code == "input_snapshot_modified"
