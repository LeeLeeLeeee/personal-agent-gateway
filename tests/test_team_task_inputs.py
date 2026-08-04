from hashlib import sha256
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.team_task_inputs import (
    TaskInputStager,
    TaskInputUnavailable,
)
from team_cycle_helpers import make_cycle_services


def test_stage_copies_frozen_artifact_under_inputs(tmp_path: Path) -> None:
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    task = teams.create_task(run.id, "Review", "Review the draft")
    artifact = ArtifactStore(db, tmp_path / "artifacts").register_bytes(
        "markdown", "draft.md", "previous/draft.md", b"source", "text/markdown"
    )
    db.execute(
        """
        insert into team_task_input_artifacts (
            task_id, artifact_id, relative_path, sha256, size_bytes, staged_path,
            created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id,
            artifact.id,
            artifact.relative_path,
            sha256(b"source").hexdigest(),
            artifact.size_bytes,
            f"inputs/{artifact.id}/draft.md",
            "2026-08-04T00:00:00+00:00",
        ),
    )

    manifest = TaskInputStager(db, teams).stage(task, tmp_path / "workspace")

    assert (tmp_path / "workspace" / "inputs" / artifact.id / "draft.md").read_text() == "source"
    assert manifest.paths == (f"inputs/{artifact.id}/draft.md",)


def test_stage_rejects_hash_changed_artifact(tmp_path: Path) -> None:
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    task = teams.create_task(run.id, "Review", "Review the draft")
    artifact = ArtifactStore(db, tmp_path / "artifacts").register_bytes(
        "markdown", "draft.md", "previous/draft.md", b"source", "text/markdown"
    )
    db.execute(
        """
        insert into team_task_input_artifacts (
            task_id, artifact_id, relative_path, sha256, size_bytes, staged_path,
            created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id,
            artifact.id,
            artifact.relative_path,
            sha256(b"source").hexdigest(),
            artifact.size_bytes,
            f"inputs/{artifact.id}/draft.md",
            "2026-08-04T00:00:00+00:00",
        ),
    )
    artifact.file_path.write_text("changed")

    with pytest.raises(TaskInputUnavailable, match="input_artifact_unavailable"):
        TaskInputStager(db, teams).stage(task, tmp_path / "workspace")
