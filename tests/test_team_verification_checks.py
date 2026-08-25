import json
from pathlib import Path

import pytest

from personal_agent_gateway.team_verification_checks import (
    VerificationCheck,
    parse_verification_check,
    run_verification_check,
    safe_workspace_file,
    verification_check_payload,
)


def _workspace(tmp_path: Path, name: str, content: str) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / name).write_text(content, encoding="utf-8")
    return workspace


def test_file_nonempty_passes_on_content_and_fails_on_whitespace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "# Draft\n")
    (workspace / "blank.md").write_text("   \n\t\n", encoding="utf-8")

    assert run_verification_check(
        VerificationCheck("file_nonempty", "draft.md"), workspace
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_nonempty", "blank.md"), workspace
    ).passed


def test_file_contains_matches_a_substring(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "before <library_draft>{} after")

    assert run_verification_check(
        VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
        workspace,
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_contains", "draft.md", value="</missing>"),
        workspace,
    ).passed


def test_file_matches_applies_a_regex(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "## Week 3\n")

    assert run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern=r"^## Week \d+$"),
        workspace,
    ).passed
    assert not run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern=r"^## Day \d+$"),
        workspace,
    ).passed


def test_json_parses_distinguishes_valid_from_invalid(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "ok.json", json.dumps({"a": 1}))
    (workspace / "bad.json").write_text("{not json", encoding="utf-8")

    assert run_verification_check(
        VerificationCheck("json_parses", "ok.json"), workspace
    ).passed
    assert not run_verification_check(
        VerificationCheck("json_parses", "bad.json"), workspace
    ).passed


def test_a_missing_file_fails_rather_than_raising(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")

    result = run_verification_check(
        VerificationCheck("file_nonempty", "absent.md"), workspace
    )

    assert not result.passed
    assert "absent.md" in result.evidence


def test_an_oversized_file_fails_rather_than_being_read(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "huge.md", "x" * 1_000_001)

    result = run_verification_check(
        VerificationCheck("file_contains", "huge.md", value="x"), workspace
    )

    assert not result.passed
    assert "too large" in result.evidence


def test_file_matches_truncates_the_search_text_and_says_so(tmp_path: Path) -> None:
    padding = "x" * 64_000
    workspace = _workspace(tmp_path, "draft.md", padding + "MARKER")

    result = run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern="MARKER"), workspace
    )

    assert not result.passed
    assert "truncated" in result.evidence


def test_an_uncompilable_pattern_fails_rather_than_raising(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")

    result = run_verification_check(
        VerificationCheck("file_matches", "draft.md", pattern="("), workspace
    )

    assert not result.passed


def test_safe_workspace_file_rejects_escapes_and_sensitive_names(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "draft.md", "content")
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=1", encoding="utf-8")

    assert safe_workspace_file(workspace, "draft.md") is not None
    assert safe_workspace_file(workspace, "../outside.md") is None
    assert safe_workspace_file(workspace, ".env") is None
    assert safe_workspace_file(workspace, "absent.md") is None


def test_parse_verification_check_accepts_each_type_and_rejects_the_rest() -> None:
    assert parse_verification_check(
        {"type": "file_nonempty", "path": "a.md"}
    ) == VerificationCheck("file_nonempty", "a.md")
    assert parse_verification_check(
        {"type": "file_contains", "path": "a.md", "value": "x"}
    ) == VerificationCheck("file_contains", "a.md", value="x")

    for invalid in (
        None,
        "file_nonempty",
        {"type": "shell", "path": "a.md"},
        {"type": "file_nonempty"},
        {"type": "file_nonempty", "path": ""},
        {"type": "file_contains", "path": "a.md"},
        {"type": "file_matches", "path": "a.md"},
        {"type": "file_nonempty", "path": "a.md", "value": "x"},
        {"type": "file_nonempty", "path": "../a.md"},
        {"type": "file_matches", "path": "a.md", "pattern": "a" * 201},
    ):
        with pytest.raises(ValueError):
            parse_verification_check(invalid)


def test_parse_verification_check_accepts_a_pattern_at_the_length_cap() -> None:
    check = parse_verification_check(
        {"type": "file_matches", "path": "a.md", "pattern": "a" * 200}
    )

    assert check.pattern == "a" * 200


def test_payload_round_trips_without_the_unused_field() -> None:
    check = VerificationCheck("file_contains", "a.md", value="x")

    payload = verification_check_payload(check)

    assert payload == {"type": "file_contains", "path": "a.md", "value": "x"}
    assert parse_verification_check(payload) == check


def test_unrecognized_check_type_fails_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, "ok.json", json.dumps({"a": 1}))

    result = run_verification_check(
        VerificationCheck(type="not_a_real_type", path="ok.json"), workspace
    )

    assert not result.passed
    assert "unknown" in result.evidence.lower()


def _empty_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def test_a_command_check_needs_a_command_and_no_path():
    """이 검사만 파일을 가리키지 않는다.

    나머지 넷은 "이 파일이 어떠한가" 를 묻지만 이것은 "이것이 도는가" 를
    묻는다. path 를 강제하면 리드가 아무 파일이나 하나 적어 넣게 된다.
    """
    check = parse_verification_check(
        {"type": "command_succeeds", "command": "python -c \"pass\""}
    )

    assert check.type == "command_succeeds"
    assert check.command == 'python -c "pass"'
    assert check.path == ""


def test_a_command_check_rejects_a_blank_or_missing_command():
    with pytest.raises(ValueError):
        parse_verification_check({"type": "command_succeeds"})
    with pytest.raises(ValueError):
        parse_verification_check({"type": "command_succeeds", "command": "   "})


def test_a_command_check_rejects_extra_fields():
    with pytest.raises(ValueError):
        parse_verification_check(
            {"type": "command_succeeds", "command": "true", "path": "a.txt"}
        )


def test_a_command_that_exits_zero_passes(tmp_path: Path) -> None:
    result = run_verification_check(
        VerificationCheck(type="command_succeeds", path="", command="python -c \"pass\""),
        _empty_workspace(tmp_path),
    )

    assert result.passed is True


def test_a_failing_command_carries_its_output_as_evidence(tmp_path: Path) -> None:
    """왜 떨어졌는지가 근거에 실려야 리드가 다음 지시를 쓸 수 있다.

    종료 코드만 남기면 리드는 "실패했다" 만 알고 워커에게 무엇을 고치라고
    말할 수 없다.
    """
    result = run_verification_check(
        VerificationCheck(
            type="command_succeeds",
            path="",
            command='python -c "import sys; sys.stderr.write(\'boom detail\'); sys.exit(3)"',
        ),
        _empty_workspace(tmp_path),
    )

    assert result.passed is False
    assert "3" in result.evidence
    assert "boom detail" in result.evidence


def test_a_command_runs_in_the_workspace(tmp_path: Path) -> None:
    """작업 폴더에서 돈다. 그래야 워커가 만든 파일을 검사가 볼 수 있다."""
    workspace = _workspace(tmp_path, "made.txt", "x")

    result = run_verification_check(
        VerificationCheck(
            type="command_succeeds",
            path="",
            command='python -c "import os,sys; sys.exit(0 if os.path.exists(\'made.txt\') else 1)"',
        ),
        workspace,
    )

    assert result.passed is True


def test_a_command_that_never_ends_fails_instead_of_hanging(tmp_path: Path) -> None:
    """제한 시간이 없으면 멈춘 명령 하나가 런 전체를 영원히 붙잡는다.

    파일 검사는 즉시 끝나므로 이 위험이 없었다. 명령은 다르다.
    """
    result = run_verification_check(
        VerificationCheck(
            type="command_succeeds",
            path="",
            command='python -c "import time; time.sleep(30)"',
        ),
        _empty_workspace(tmp_path),
        timeout_seconds=1,
    )

    assert result.passed is False
    assert "시간" in result.evidence or "timed out" in result.evidence.lower()


def test_long_output_is_truncated(tmp_path: Path) -> None:
    """근거는 기록에 저장되고 프롬프트로도 간다. 통째로 실으면 둘 다 넘친다."""
    result = run_verification_check(
        VerificationCheck(
            type="command_succeeds",
            path="",
            command='python -c "print(\'x\' * 50000); raise SystemExit(1)"',
        ),
        _empty_workspace(tmp_path),
    )

    assert result.passed is False
    assert len(result.evidence) < 5000
