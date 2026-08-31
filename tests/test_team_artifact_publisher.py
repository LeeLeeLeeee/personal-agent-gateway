from datetime import timedelta
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.db import Database
from personal_agent_gateway.team_artifact_publisher import (
    ArtifactPublicationError,
    TeamArtifactPublisher,
)
from personal_agent_gateway.team_outcomes import Deliverable, TaskOutcome
from personal_agent_gateway.teams import RequiredVerification, TaskAcceptance, TeamTask


def _task() -> TeamTask:
    return TeamTask(
        id="task-1",
        team_run_id="run-1",
        title="Report",
        description="Write report",
        owner_agent_id="worker-1",
        status="in_progress",
        required=True,
        acceptance=TaskAcceptance(("outputs/report.md",), (RequiredVerification("pytest"),)),
        outcome=None,
        acceptance_result=None,
        result=None,
        error_message=None,
        created_at="t",
        updated_at="t",
    )


def _outcome(*paths: str) -> TaskOutcome:
    return TaskOutcome(
        status="completed",
        summary="done",
        reason_code=None,
        deliverables=tuple(Deliverable(path, "markdown") for path in paths),
        verifications=(),
    )


def test_publishes_only_declared_files_with_integrity_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    undeclared = workspace / "%SystemDrive%" / "cache.db"
    undeclared.parent.mkdir()
    undeclared.write_text("cache", encoding="utf-8")
    db = Database(tmp_path / "app.db")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    artifacts = TeamArtifactPublisher(store).publish(
        "run-1",
        "cycle-1",
        _task(),
        _outcome("outputs/report.md"),
        workspace,
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.relative_path == (
        "team-runs/run-1/cycle-1/deliverables/task-1/report.md"
    )
    assert artifact.metadata["source_path"] == "outputs/report.md"
    assert artifact.metadata["sha256"] == (
        "845e91831319e89c4d656bdb80c278ac09a7230d61e5dfd2e1b1fbb436ac8917"
    )
    assert artifact.metadata["task_id"] == "task-1"
    assert artifact.metadata["cycle_id"] == "cycle-1"
    assert artifact.metadata["team_run_id"] == "run-1"
    assert artifact.retention_class == "temporary"
    assert artifact.expires_at is not None
    assert timedelta(days=29) < artifact.expires_at - artifact.created_at <= timedelta(days=30)
    assert all("%SystemDrive%" not in item.relative_path for item in store.list())


def test_successful_publication_indexes_declared_outputs_for_team_memory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Report", encoding="utf-8")
    db = Database(tmp_path / "app.db")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    class RecordingMemory:
        def __init__(self) -> None:
            self.calls = []

        def index_markdown_outputs(self, **kwargs) -> None:
            self.calls.append(kwargs)

    memory = RecordingMemory()

    artifacts = TeamArtifactPublisher(store, memory).publish(
        "run-1",
        "cycle-1",
        _task(),
        _outcome("outputs/report.md"),
        workspace,
    )

    assert len(artifacts) == 1
    assert memory.calls == [
        {
            "team_run_id": "run-1",
            "cycle_id": "cycle-1",
            "task_id": "task-1",
            "task_title": "Report",
            "relative_paths": ["outputs/report.md"],
            "workspace_root": workspace.resolve(),
        }
    ]


def test_memory_index_failure_does_not_undo_published_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("# Report", encoding="utf-8")
    db = Database(tmp_path / "app.db")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    class FailingMemory:
        def index_markdown_outputs(self, **_kwargs) -> None:
            raise OSError("index unavailable")

    artifacts = TeamArtifactPublisher(store, FailingMemory()).publish(
        "run-1",
        None,
        _task(),
        _outcome("outputs/report.md"),
        workspace,
    )

    assert len(artifacts) == 1
    assert len(store.list()) == 1


def test_publication_failure_rolls_back_current_attempt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    for name in ("one.md", "two.md"):
        path = workspace / "outputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    db = Database(tmp_path / "app.db")
    db.initialize()
    real_store = ArtifactStore(db, tmp_path / "artifacts")

    class FailingStore:
        def __init__(self):
            self.calls = 0

        def register_existing_file(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise OSError("storage failed")
            return real_store.register_existing_file(**kwargs)

        def delete(self, artifact_id):
            real_store.delete(artifact_id)

    with pytest.raises(ArtifactPublicationError):
        TeamArtifactPublisher(FailingStore()).publish(
            "run-1",
            None,
            _task(),
            _outcome("outputs/one.md", "outputs/two.md"),
            workspace,
        )

    assert real_store.list() == []
