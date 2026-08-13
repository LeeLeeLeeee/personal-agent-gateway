import json

import pytest

from personal_agent_gateway.team_outcomes import (
    Deliverable,
    TaskOutcome,
    TaskOutcomeError,
    VerificationEvidence,
    parse_task_outcome,
)


def test_failure_prose_is_not_completion() -> None:
    with pytest.raises(TaskOutcomeError) as error:
        parse_task_outcome("권한이 없어 작업하지 못했습니다.")

    assert error.value.code == "invalid_task_outcome"


@pytest.mark.parametrize("status", ["completed", "blocked", "failed"])
def test_parses_exact_task_outcome(status: str) -> None:
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": status,
                "summary": "Verification finished.",
                "reason_code": None if status == "completed" else "verification_failed",
                "deliverables": [
                    {"path": "outputs/report.md", "kind": "markdown"}
                ],
                "verifications": [
                    {
                        "name": "pytest",
                        "status": "passed",
                        "evidence": "42 tests passed",
                    }
                ],
            }
        )
    )

    assert outcome == TaskOutcome(
        status=status,
        summary="Verification finished.",
        reason_code=None if status == "completed" else "verification_failed",
        deliverables=(Deliverable("outputs/report.md", "markdown"),),
        verifications=(
            VerificationEvidence("pytest", "passed", "42 tests passed"),
        ),
    )


def test_parses_task_outcome_inside_one_outer_json_fence() -> None:
    payload = {
        "status": "completed",
        "summary": "Verification finished.",
        "reason_code": None,
        "deliverables": [{"path": "outputs/report.md", "kind": "markdown"}],
        "verifications": [
            {
                "name": "pytest",
                "status": "passed",
                "evidence": "42 tests passed",
            }
        ],
    }
    outcome = parse_task_outcome(f"```json\n{json.dumps(payload)}\n```")

    assert outcome.status == "completed"
    assert outcome.summary == "Verification finished."
    assert outcome.deliverables == (
        Deliverable("outputs/report.md", "markdown"),
    )


@pytest.mark.parametrize(
    "payload",
    [
        "before\n```json\n{}\n```",
        "```json\n{}\n```\nafter",
        "```JSON\n{}\n```",
        "```json\n{}\n```\n```json\n{}\n```",
        '{"status":"completed"}',
        '{"status":"unknown","summary":"x","reason_code":null,"deliverables":[],"verifications":[]}',
        '["not", "an", "object"]',
        (
            '{"status":"completed","summary":"x","reason_code":null,'
            '"deliverables":[{"path":"C:/secret.txt","kind":"text"}],'
            '"verifications":[]}'
        ),
        (
            '{"status":"completed","summary":"x","reason_code":null,'
            '"deliverables":[{"path":"outputs/../secret.txt","kind":"text"}],'
            '"verifications":[]}'
        ),
        (
            '{"status":"completed","summary":"x","reason_code":null,'
            '"deliverables":[],"verifications":['
            '{"name":"pytest","status":"passed","evidence":"ok"},'
            '{"name":"pytest","status":"passed","evidence":"ok"}]}'
        ),
        (
            '{"status":"completed","summary":"x","reason_code":null,'
            '"deliverables":[],"verifications":['
            '{"name":"pytest","status":"unknown","evidence":"ok"}]}'
        ),
    ],
)
def test_rejects_malformed_or_unsafe_task_outcome(payload: str) -> None:
    with pytest.raises(TaskOutcomeError) as error:
        parse_task_outcome(payload)

    assert error.value.code == "invalid_task_outcome"


def test_a_worker_can_report_that_it_could_not_check():
    """The motivating run's worker ran `npx --no-install tsc --version`, could not
    use it, and had nowhere to say so -- the schema allowed only passed or failed,
    so it wrote the fact into a Markdown file nothing reads."""
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "wrote the screens",
                "reason_code": None,
                "deliverables": [{"path": "a.tsx", "kind": "file"}],
                "verifications": [
                    {
                        "name": "frontend-typechecks",
                        "checked": False,
                        "status": None,
                        "evidence": "npx --no-install tsc: typescript-unavailable",
                    }
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is False
    assert verification.status is None
    assert "typescript-unavailable" in verification.evidence


def test_a_checked_verification_still_carries_its_result():
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "s",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {
                        "name": "pytest",
                        "checked": True,
                        "status": "passed",
                        "evidence": "42 passed",
                    }
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is True
    assert verification.status == "passed"


def test_an_old_shape_report_reads_as_checked():
    """Stored outcomes predate this field. At the time, a bare status *was* the
    worker's claim to have checked, so reading it as checked=True preserves the
    meaning and avoids a migration."""
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "s",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {"name": "pytest", "status": "passed", "evidence": "42 passed"}
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is True
    assert verification.status == "passed"


@pytest.mark.parametrize(
    "verification",
    [
        # checked with no result: trying to have it both ways.
        {"name": "n", "checked": True, "status": None, "evidence": "e"},
        # not checked, but claiming a result anyway.
        {"name": "n", "checked": False, "status": "passed", "evidence": "e"},
        # a status that is not one of the two allowed values.
        {"name": "n", "checked": True, "status": "skipped", "evidence": "e"},
        # checked is not a boolean.
        {"name": "n", "checked": "yes", "status": "passed", "evidence": "e"},
        # an unrelated extra key.
        {"name": "n", "checked": True, "status": "passed", "evidence": "e", "x": 1},
    ],
)
def test_incoherent_verification_reports_are_rejected(verification):
    with pytest.raises(TaskOutcomeError):
        parse_task_outcome(
            json.dumps(
                {
                    "status": "completed",
                    "summary": "s",
                    "reason_code": None,
                    "deliverables": [],
                    "verifications": [verification],
                }
            )
        )
