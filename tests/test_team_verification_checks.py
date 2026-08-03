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
