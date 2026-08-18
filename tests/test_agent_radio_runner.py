from pathlib import Path

import pytest

from agent_radio.artifact import (
    ARTIFACT_SCHEMA,
    load_artifacts,
    parse_artifact,
    write_artifact,
)
from agent_radio.fixture import FixtureError


def _artifact(**overrides) -> dict:
    payload = {
        "schema": ARTIFACT_SCHEMA,
        "run_id": "run-1",
        "fixture_id": "understand-acceptance-gate",
        "fixture_sha256": "abc",
        "mode": "legacy",
        "execution_profile": "read_only",
        "started_at": "2026-08-14T01:00:00Z",
        "finished_at": "2026-08-14T01:06:20Z",
        "wall_ms": 380000,
        "run_status": "completed",
        "summary": "수용 게이트는 파일 읽기만 한다",
        "workspace_path": "data/workspace/run-1/workspace",
        "repository_unchanged": True,
        "error": None,
    }
    payload.update(overrides)
    return payload


def test_a_completed_clean_run_is_scoreable():
    assert parse_artifact(_artifact()).scoreable is True


def test_a_failed_run_is_kept_but_not_scoreable():
    """How often a mode fails is itself a measurement, so the artefact stays --
    it just is not something a human should grade."""
    artifact = parse_artifact(
        _artifact(run_status="failed", summary=None, error="provider_unavailable")
    )

    assert artifact.scoreable is False


def test_a_read_only_run_that_touched_the_repository_is_not_scoreable():
    """Isolation broke, so the answer cannot be compared with anything else --
    whatever it says, it was produced under different conditions."""
    artifact = parse_artifact(
        _artifact(execution_profile="read_only", repository_unchanged=False)
    )

    assert artifact.scoreable is False


@pytest.mark.parametrize("repository_unchanged", [True, False])
def test_a_bounded_write_run_is_scoreable_only_if_the_repository_is_unchanged(
    repository_unchanged,
):
    """bounded_write is allowed to write to its own isolated workspace, but
    repository_unchanged names the repository, not the workspace -- a
    bounded_write run that mutated the repository broke isolation exactly as
    badly as a read_only run would, and is not scoreable either."""
    artifact = parse_artifact(
        _artifact(
            execution_profile="bounded_write",
            repository_unchanged=repository_unchanged,
        )
    )

    assert artifact.scoreable is repository_unchanged


def test_a_completed_run_with_no_summary_is_not_scoreable():
    """There is nothing to grade. Silently treating it as an empty answer would
    score a missing measurement as a failed one."""
    assert parse_artifact(_artifact(summary=None)).scoreable is False


def test_a_completed_run_with_a_blank_summary_is_not_scoreable():
    """A whitespace-only summary is still nothing to grade -- the same
    reasoning as a missing summary applies identically."""
    assert parse_artifact(_artifact(summary="   ")).scoreable is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-run/v2"},
        {"mode": "single_agent"},
        {"execution_profile": "full_access"},
        {"wall_ms": -1},
        {"repository_unchanged": "yes"},
        {"run_id": ""},
        {"fixture_sha256": ""},
    ],
)
def test_an_artefact_that_cannot_be_trusted_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_artifact(_artifact(**overrides))


def test_writing_then_loading_round_trips(tmp_path: Path):
    artifact = parse_artifact(_artifact())

    path = write_artifact(tmp_path, artifact)

    assert path.name == "run-1.json"
    assert load_artifacts(tmp_path) == [artifact]


def test_writing_the_same_run_twice_is_refused(tmp_path: Path):
    """An artefact is a record of one execution. Overwriting one loses the
    execution it described, and nothing else records that it happened."""
    artifact = parse_artifact(_artifact())
    write_artifact(tmp_path, artifact)

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)


def test_writing_refuses_a_run_id_that_would_escape_the_directory(tmp_path: Path):
    """run_id comes from a later task and cannot be trusted -- this module is
    the last place it is still a string, so it is the last chance to stop a
    traversal before it becomes a real file outside the directory it was
    given."""
    artifact = parse_artifact(_artifact(run_id="../escaped"))

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)

    assert not (tmp_path.parent / "escaped.json").exists()


def test_writing_refuses_a_run_id_with_a_path_separator(tmp_path: Path):
    artifact = parse_artifact(_artifact(run_id="sub/run"))

    with pytest.raises(FixtureError):
        write_artifact(tmp_path, artifact)

    assert not any(tmp_path.rglob("*.json"))


def test_writing_into_a_path_that_is_a_file_raises_fixture_error(tmp_path: Path):
    """Every other failure in this module raises FixtureError; a raw OSError
    would give a caller a second exception type to handle."""
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("occupied")
    artifact = parse_artifact(_artifact())

    with pytest.raises(FixtureError):
        write_artifact(not_a_directory, artifact)
