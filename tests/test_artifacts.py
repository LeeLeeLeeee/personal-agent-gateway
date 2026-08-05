from datetime import datetime, timezone
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactPathError, ArtifactStore
from personal_agent_gateway.db import Database
from personal_agent_gateway.migrations import _migration_27_backfill_artifact_origins


def test_artifact_store_registers_file_under_root(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    artifact = store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
    )

    assert artifact.relative_path == "logs/run.log"
    assert artifact.type == "text"
    assert (tmp_path / "artifacts" / "logs" / "run.log").read_text() == "hello"
    assert store.get(artifact.id) == artifact


def test_artifact_store_records_explicit_temporary_retention(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    expiry = datetime(2026, 9, 5, tzinfo=timezone.utc)

    artifact = store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
        retention_class="temporary",
        expires_at=expiry,
    )

    assert artifact.retention_class == "temporary"
    assert artifact.expires_at == expiry


def test_artifact_schema_persists_explicit_origin_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    artifact = store.register_bytes(
        artifact_type="markdown",
        title="draft.md",
        relative_path="files/draft.md",
        content=b"# Draft",
        mime_type="text/markdown",
        origin_kind="manual_upload",
        artifact_role="attachment",
        origin_group_label_snapshot="Local files",
        origin_item_label_snapshot="draft.md",
    )

    assert artifact.origin_kind == "manual_upload"
    assert artifact.artifact_role == "attachment"
    assert artifact.origin_group_label_snapshot == "Local files"
    assert artifact.origin_item_label_snapshot == "draft.md"


def test_artifact_schema_creates_durable_chat_turn_table(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    tables = {
        row["name"]
        for row in db.fetchall("select name from sqlite_master where type = 'table'")
    }

    assert "chat_turns" in tables


def test_origin_backfill_recovers_team_references_from_legacy_metadata(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    artifact = store.register_bytes(
        "markdown", "legacy.md", "files/legacy.md", b"# Legacy", "text/markdown",
        metadata={"team_run_id": "run-1", "task_id": "task-1", "cycle_id": "cycle-1"},
    )
    with db.connection() as connection:
        connection.execute(
            """
            update artifacts set origin_kind = 'legacy', source_team_run_id = null,
                                 source_team_task_id = null, source_cycle_id = null
            where id = ?
            """,
            (artifact.id,),
        )
        _migration_27_backfill_artifact_origins(connection)

    restored = store.get(artifact.id)

    assert restored.origin_kind == "team_task_output"
    assert (restored.source_team_run_id, restored.source_team_task_id, restored.source_cycle_id) == (
        "run-1", "task-1", "cycle-1"
    )


def test_cleanup_preview_excludes_referenced_temporary_artifacts(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    eligible = store.register_bytes(
        "text", "eligible.log", "logs/eligible.log", b"eligible", "text/plain",
        retention_class="temporary", expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    referenced = store.register_bytes(
        "text", "referenced.log", "logs/referenced.log", b"referenced", "text/plain",
        retention_class="temporary", expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    with db.connection() as connection:
        connection.execute("pragma foreign_keys = off")
        connection.execute(
            "insert into team_task_input_artifacts "
            "(task_id, artifact_id, relative_path, sha256, size_bytes, staged_path, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            ("task-1", referenced.id, "referenced.log", "digest", referenced.size_bytes,
             "inputs/referenced.log", now.isoformat()),
        )

    preview = store.cleanup_preview(now)

    assert preview.artifacts == (eligible,)
    assert preview.total_size_bytes == eligible.size_bytes


def test_artifact_store_rejects_path_escape(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    with pytest.raises(ArtifactPathError, match="outside artifact root"):
        store.register_bytes(
            artifact_type="text",
            title="bad",
            relative_path="../bad.txt",
            content=b"bad",
            mime_type="text/plain",
        )


def test_artifact_store_registers_existing_file(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    root = tmp_path / "artifacts"
    source = tmp_path / "temp" / "capture.png"
    source.parent.mkdir()
    source.write_bytes(b"png")
    store = ArtifactStore(db, root)

    artifact = store.register_existing_file(
        artifact_type="image",
        title="capture.png",
        source_path=source,
        relative_path="images/capture.png",
        mime_type="image/png",
    )

    assert artifact.relative_path == "images/capture.png"
    assert store.content_path(artifact.id) == root / "images" / "capture.png"
    assert (root / "images" / "capture.png").read_bytes() == b"png"


def test_register_existing_file_removes_copy_when_registration_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    root = tmp_path / "artifacts"
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    store = ArtifactStore(db, root)

    def fail_register(**_kwargs):
        raise OSError("database failed")

    monkeypatch.setattr(store, "_register", fail_register)

    with pytest.raises(OSError, match="database failed"):
        store.register_existing_file(
            artifact_type="text",
            title="source.txt",
            source_path=source,
            relative_path="files/source.txt",
            mime_type="text/plain",
        )

    assert not (root / "files" / "source.txt").exists()


def test_find_by_source_path_and_delete(tmp_path: Path) -> None:
    from personal_agent_gateway.artifacts import ArtifactStore
    from personal_agent_gateway.db import Database

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    src = tmp_path / "cat.png"
    src.write_bytes(b"img")

    created = store.register_existing_file(
        artifact_type="image",
        title="cat.png",
        source_path=src,
        relative_path="files/aa/cat.png",
        mime_type="image/png",
        metadata={"source_path": str(src.resolve()), "original_path": "cat.png"},
    )

    assert store.find_by_source_path(str(src.resolve())).id == created.id
    assert store.find_by_source_path(str(tmp_path / "other.png")) is None

    stored = store.content_path(created.id)
    assert stored.exists()
    store.delete(created.id)
    assert not stored.exists()
    import pytest
    with pytest.raises(KeyError):
        store.get(created.id)


def test_artifacts_cursor_pages_are_stable(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    created = [
        store.register_bytes(
            artifact_type="text",
            title=f"{index}.txt",
            relative_path=f"files/{index}.txt",
            content=str(index).encode(),
            mime_type="text/plain",
        )
        for index in range(5)
    ]

    first, cursor = store.page(limit=2)
    second, next_cursor = store.page(limit=2, cursor=cursor)
    third, final_cursor = store.page(limit=2, cursor=next_cursor)

    ids = [artifact.id for artifact in first + second + third]
    assert set(ids) == {artifact.id for artifact in created}
    assert len(ids) == len(set(ids))
    assert final_cursor is None


def test_browser_search_matches_resolved_origin_label(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    artifact = store.register_bytes(
        artifact_type="markdown",
        title="chart-notes.md",
        relative_path="files/chart-notes.md",
        content=b"# Notes",
        mime_type="text/markdown",
        origin_kind="team_task_output",
        artifact_role="deliverable",
        origin_group_label_snapshot="D3 guide research",
        origin_item_label_snapshot="Verify chart examples",
    )

    page = store.browser_page(segment="saved", query="verify chart", limit=20)

    assert [item.artifact.id for item in page.items] == [artifact.id]
    assert [crumb.label for crumb in page.items[0].breadcrumbs] == [
        "D3 guide research",
        "Verify chart examples",
    ]


def test_browser_prefers_live_team_goal_and_task_title_for_breadcrumbs(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    with db.connection() as connection:
        connection.execute(
            """
            insert into team_runs (id, goal, status, run_mode, lifecycle_mode, max_workers,
                                   rounds_budget, workspace_root, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "Design system review", "completed", "manual", "standard", 1,
             1, "workspace", "now", "now"),
        )
        connection.execute(
            """
            insert into team_tasks (id, team_run_id, title, description, status, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("task-1", "run-1", "Verify chart examples", "description", "completed", "now", "now"),
        )
    artifact = store.register_bytes(
        "markdown", "chart-notes.md", "files/chart-notes.md", b"# Notes", "text/markdown",
        origin_kind="team_task_output", source_team_run_id="run-1", source_team_task_id="task-1",
    )

    item = store.browser_page(segment="saved").items[0]

    assert item.artifact.id == artifact.id
    assert [crumb.label for crumb in item.breadcrumbs] == [
        "Design system review", "Verify chart examples"
    ]


def test_browser_search_uses_complete_catalog_before_pagination(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    for index in range(205):
        store.register_bytes(
            artifact_type="text",
            title="needle.txt" if index == 204 else f"file-{index}.txt",
            relative_path=f"files/{index}.txt",
            content=b"content",
            mime_type="text/plain",
        )

    page = store.browser_page(segment="saved", query="needle", limit=20)

    assert [item.artifact.title for item in page.items] == ["needle.txt"]


def test_batch_delete_keeps_referenced_artifact_and_reports_usage(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")
    free = store.register_bytes("text", "free.txt", "files/free.txt", b"free", "text/plain")
    used = store.register_bytes("text", "used.txt", "files/used.txt", b"used", "text/plain")
    with db.connection() as connection:
        connection.execute("pragma foreign_keys = off")
        connection.execute(
            """
            insert into team_task_input_artifacts (
                task_id, artifact_id, relative_path, sha256, size_bytes, staged_path, created_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("task-1", used.id, "used.txt", "digest", used.size_bytes, "inputs/used.txt", "now"),
        )

    result = store.delete_many([free.id, used.id])

    assert result.deleted_ids == (free.id,)
    assert result.blocked[0].artifact_id == used.id
    assert result.blocked[0].references[0].kind == "team_task_input"
    assert store.get(used.id).id == used.id
