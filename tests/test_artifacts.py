from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactPathError, ArtifactStore
from personal_agent_gateway.db import Database


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
