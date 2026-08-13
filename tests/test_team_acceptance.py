from pathlib import Path

import pytest

from personal_agent_gateway.source_staging import SourceStager
from personal_agent_gateway.team_acceptance import (
    TeamAcceptanceService,
    is_recoverable_acceptance_failure,
    rejected_verification_names,
)
from personal_agent_gateway.team_outcomes import (
    Deliverable,
    TaskOutcome,
    VerificationEvidence,
)
from personal_agent_gateway.team_verification_checks import VerificationCheck
from personal_agent_gateway.teams import RequiredVerification, TaskAcceptance, TeamTask

_DEFAULT_DELIVERABLES = (Deliverable("outputs/report.md", "markdown"),)
_DEFAULT_VERIFICATIONS = (VerificationEvidence("pytest", "passed", "42 passed"),)
_DEFAULT_REQUIRED_VERIFICATIONS = (RequiredVerification("pytest"),)


@pytest.mark.parametrize(
    "reason_code",
    [
        "undeclared_deliverable",
        "required_output_missing",
        "unsafe_deliverable",
        "required_verification_failed",
        "task_not_completed",
        "invalid_task_outcome",
    ],
)
def test_recoverable_acceptance_reason_codes(reason_code: str) -> None:
    assert is_recoverable_acceptance_failure(reason_code)


@pytest.mark.parametrize(
    "reason_code",
    ["input_snapshot_modified", "artifact_publication_failed", "model_failed"],
)
def test_infrastructure_acceptance_failures_are_not_recoverable(
    reason_code: str,
) -> None:
    assert not is_recoverable_acceptance_failure(reason_code)


def test_worker_declared_outcome_is_recoverable_regardless_of_reason_code() -> None:
    assert is_recoverable_acceptance_failure("draft-unmodified", worker_declared=True)
    assert is_recoverable_acceptance_failure("anything-novel", worker_declared=True)


def test_server_detected_failure_still_follows_the_allowlist() -> None:
    assert not is_recoverable_acceptance_failure("artifact_publication_failed")
    assert is_recoverable_acceptance_failure("required_output_missing")


def _task(
    *,
    outputs=("outputs/report.md",),
    verifications=_DEFAULT_REQUIRED_VERIFICATIONS,
) -> TeamTask:
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


def _marker_check() -> VerificationCheck:
    return VerificationCheck("file_contains", "outputs/report.md", value="<library_draft>")


def _workspace_with_report(tmp_path: Path, content: str) -> Path:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text(content, encoding="utf-8")
    return workspace


def test_a_server_check_decides_regardless_of_the_worker_claim(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "# Report\nNo marker here.\n")
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("marker", "passed", "파일 본문 기준 단일 마커 확인"),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == "required_verification_failed"


def test_a_server_check_failure_records_the_failing_verification_and_evidence(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_report(tmp_path, "# Report\nNo marker here.\n")
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("marker", "passed", "worker claims it passed"),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == "required_verification_failed"
    assert result.evidence["verifications"]["marker"]["mode"] == "verified"
    assert result.evidence["verifications"]["marker"]["status"] == "failed"
    assert "lacks the value" in result.evidence["verifications"]["marker"]["evidence"]


def test_a_server_check_accepts_regardless_of_the_worker_claim(tmp_path: Path) -> None:
    workspace = _workspace_with_report(
        tmp_path, "prose\n<library_draft>{}</library_draft>"
    )
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("marker", "failed", "worker claims it failed"),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is True
    assert result.evidence["verifications"]["marker"]["mode"] == "verified"
    assert result.evidence["verifications"]["marker"]["status"] == "passed"


def test_a_passing_server_check_is_recorded_as_verified(tmp_path: Path) -> None:
    workspace = _workspace_with_report(
        tmp_path, "prose\n<library_draft>{}</library_draft>"
    )
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))

    result = TeamAcceptanceService().evaluate(
        task, _outcome(verifications=()), workspace
    )

    assert result.accepted is True
    assert result.evidence["verifications"]["marker"]["mode"] == "verified"
    assert result.evidence["attested_only"] is False


def test_an_attested_verification_keeps_the_self_reported_rule(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "report")
    task = _task(verifications=(RequiredVerification("reviewed"),))
    outcome = _outcome(
        verifications=(VerificationEvidence("reviewed", "passed", "read it"),)
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is True
    assert result.evidence["verifications"]["reviewed"]["mode"] == "attested"
    assert result.evidence["attested_only"] is True


def test_an_attested_verification_the_worker_omitted_still_fails(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "report")
    task = _task(verifications=(RequiredVerification("reviewed"),))

    result = TeamAcceptanceService().evaluate(
        task, _outcome(verifications=()), workspace
    )

    assert result.accepted is False
    assert result.reason_code == "required_verification_failed"


def test_a_deliverable_rejection_does_not_blame_unevaluated_checks(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_report(
        tmp_path, "prose\n<library_draft>{}</library_draft>"
    )
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))
    outcome = _outcome(deliverables=())

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted is False
    assert result.reason_code == "required_output_missing"

    verification_status = {item.name: item.status for item in outcome.verifications}
    required_verifications = tuple(
        (required.name, required.check is not None)
        for required in task.acceptance.required_verifications
    )
    assert (
        rejected_verification_names(
            required_verifications, verification_status, result.evidence
        )
        == []
    )


def test_a_checked_verification_after_an_earlier_failure_is_not_blamed() -> None:
    required_verifications = (("reviewed", False), ("marker", True))

    assert rejected_verification_names(
        required_verifications, {}, {"verifications": {}}
    ) == ["reviewed"]

    required_verifications = (("a", True), ("b", True))

    assert rejected_verification_names(
        required_verifications,
        {},
        {"verifications": {"a": {"status": "failed"}}},
    ) == ["a"]


def test_an_unchecked_required_verification_is_accepted_and_recorded(tmp_path: Path) -> None:
    """The whole point: the task is not refused, but the admission survives.
    Refusing would kill every run in an environment without the dependencies --
    which is the environment run 699c1915 was in -- and the goal is to stop
    unverified work passing *silently*."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence(
                "frontend-typechecks",
                None,
                "npx --no-install tsc: typescript-unavailable",
                checked=False,
            ),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted
    assert result.evidence["unverified"] == ["frontend-typechecks"]
    assert result.evidence["verifications"]["frontend-typechecks"]["mode"] == "unverified"


def test_an_unchecked_verification_is_never_recorded_as_passed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("frontend-typechecks", None, "unavailable", checked=False),
        )
    )

    recorded = TeamAcceptanceService().evaluate(task, outcome, workspace).evidence

    assert recorded["verifications"]["frontend-typechecks"] == {
        "mode": "unverified",
        "status": "unknown",
        "evidence": "unavailable",
    }


def test_a_missing_verification_still_fails_the_task(tmp_path: Path) -> None:
    """The new field must not turn an omitted report into a tolerated one."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(verifications=())

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert not result.accepted
    assert result.reason_code == "required_verification_failed"


def test_a_reported_failure_still_fails_the_task(tmp_path: Path) -> None:
    """checked=True with status failed is a real negative result, not an excuse."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("frontend-typechecks", "failed", "3 errors", checked=True),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert not result.accepted
    assert result.reason_code == "required_verification_failed"


def test_a_gate_run_check_ignores_the_worker_claim_entirely(tmp_path: Path) -> None:
    """A required verification carrying a check is decided by the gate. A worker
    saying checked=False must not turn that into unverified."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(
        verifications=(
            RequiredVerification(
                "report-written",
                check=VerificationCheck(type="file_contains", path="outputs/report.md", value="report"),
            ),
        )
    )
    outcome = _outcome(
        verifications=(
            VerificationEvidence("report-written", None, "did not run it", checked=False),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted
    assert result.evidence["verifications"]["report-written"]["mode"] == "verified"
    assert result.evidence["unverified"] == []


def test_an_unchecked_verification_counts_as_not_confirmed_passed() -> None:
    """This list is what the worker is told to go fix. A verification nobody
    confirmed belongs on it -- the review it appears in was triggered by some
    other failure, so this adds a name rather than causing a rejection."""
    names = rejected_verification_names(
        [("typecheck", False)], {"typecheck": None}, {"verifications": {}}
    )

    assert names == ["typecheck"]
