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


@pytest.mark.parametrize(
    "payload",
    [
        "```json\n{}\n```",
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
