import json
from dataclasses import asdict

import pytest

from personal_agent_gateway.team_outcomes import (
    Deliverable,
    Mention,
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


_BASE = {
    "status": "completed",
    "summary": "done",
    "reason_code": None,
    "deliverables": [],
    "verifications": [],
}


def _payload(**overrides):
    return json.dumps({**_BASE, **overrides}, ensure_ascii=False)


def test_an_outcome_without_mentions_still_parses():
    """기존 형태를 깨면 모든 워커 응답이 repair 경로로 떨어진다."""
    assert parse_task_outcome(_payload()).mentions == ()


def test_mentions_are_parsed_when_present():
    outcome = parse_task_outcome(
        _payload(mentions=[{"to": "W-02", "text": "게이트는 파일만 읽는다"}])
    )

    (mention,) = outcome.mentions
    assert (mention.to, mention.text) == ("W-02", "게이트는 파일만 읽는다")


@pytest.mark.parametrize(
    "mentions",
    [
        [{"to": "W-02"}],
        [{"to": "W-02", "text": "  "}],
        [{"to": "", "text": "x"}],
        [{"to": "W-02", "text": "x", "extra": 1}],
        [{"to": ["W-02"], "text": "x"}],
        "not a list",
    ],
)
def test_a_malformed_mention_is_dropped_without_voiding_the_outcome(mentions):
    """껍데기만 두 형태를 받고 안쪽은 지금처럼 엄격하게 검사하되, 그 거부가
    워커의 결과를 무효로 만들지는 않는다.

    쪽지는 곁다리다. 여기서 raise하면 끝낸 태스크가 자기 일이 아닌 필드 하나로
    거절되고, 그 필드를 되달라고 하지도 않는 repair 라운드가 유료로 한 번 타고,
    쪽지는 아무 기록 없이 사라진다. 라벨이 틀린 쪽지는 이미 강등으로 남고
    태스크는 살아남는다 -- 본문이 틀린 쪽지도 같아야 한다.
    """
    outcome = parse_task_outcome(_payload(mentions=mentions))

    assert outcome.status == "completed"
    assert outcome.mentions == ()
    assert outcome.mention_refusals == ("malformed",)


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\r\n", "\u2028", "\u2029", "\x85", "\x0b", "\x0c"],
)
def test_every_line_break_python_knows_is_refused(separator):
    """`"\\n" in text`는 자기 근거를 강제하지 못한다: U+2028(LINE SEPARATOR),
    U+2029(PARAGRAPH SEPARATOR), U+0085, \\x0b, \\x0c는 그 검사를 통과해 렌더된
    접두사에 그대로 실린다. json은 리터럴 U+2028을 손대지 않고 통과시키므로
    모델은 escape조차 필요하지 않다. splitlines가 아는 모든 개행이 거부되어야
    한다."""
    text = f"line one{separator}line two"

    outcome = parse_task_outcome(_payload(mentions=[{"to": "W-02", "text": text}]))

    assert outcome.status == "completed"
    assert outcome.mentions == ()
    assert outcome.mention_refusals == ("line_break",)
    # And the dataclass itself stays incapable of holding one: that invariant is
    # what makes "no path can render a forged line" structural rather than an
    # audit of every asdict(outcome) site.
    with pytest.raises(TaskOutcomeError):
        Mention("W-02", text)


def test_a_trailing_break_is_a_break_too():
    """줄 수만 세면(`len(splitlines()) > 1`) 끝에 붙은 개행이 통과한다."""
    with pytest.raises(TaskOutcomeError):
        Mention("W-02", "one line\n")


def test_a_good_note_survives_a_malformed_sibling():
    outcome = parse_task_outcome(
        _payload(
            mentions=[
                {"to": "W-02", "text": "게이트는 파일만 읽는다"},
                {"to": "W-03", "text": "line one\nline two"},
            ]
        )
    )

    assert [mention.text for mention in outcome.mentions] == [
        "게이트는 파일만 읽는다"
    ]
    assert outcome.mention_refusals == ("line_break",)


def test_a_refusal_survives_the_ledger_round_trip():
    """거부는 parse에서 발견되고 기록은 apply에서 이뤄진다. 그 둘 사이에는 원장이
    있고, outcome payload만 건너간다 -- asdict → json → parse를 넘지 못하는
    거부는 끝내 어디에도 적히지 않는다."""
    outcome = parse_task_outcome(
        _payload(mentions=[{"to": "W-02", "text": "line one\nline two"}])
    )

    again = parse_task_outcome(json.dumps(asdict(outcome), ensure_ascii=False))

    assert again.mention_refusals == ("line_break",)
