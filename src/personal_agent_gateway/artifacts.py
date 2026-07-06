from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.db import Database


class ArtifactPathError(Exception):
    pass


@dataclass(frozen=True)
class Artifact:
    id: str
    type: str
    title: str
    file_path: Path
    relative_path: str
    mime_type: str
    size_bytes: int
    thumbnail_path: Path | None
    source_job_id: str | None
    source_session_id: str | None
    tags: list[str]
    metadata: dict[str, object]


class ArtifactStore:
    def __init__(self, db: Database, root: Path) -> None:
        self._db = db
        self._root = root.resolve()

    def register_bytes(
        self,
        artifact_type: str,
        title: str,
        relative_path: str,
        content: bytes,
        mime_type: str,
        source_job_id: str | None = None,
        source_session_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Artifact:
        destination = self._resolve_artifact_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return self._register(
            artifact_type=artifact_type,
            title=title,
            path=destination,
            relative_path=relative_path,
            mime_type=mime_type,
            source_job_id=source_job_id,
            source_session_id=source_session_id,
            tags=tags or [],
            metadata=metadata or {},
        )

    def register_existing_file(
        self,
        artifact_type: str,
        title: str,
        source_path: Path,
        relative_path: str,
        mime_type: str,
        source_job_id: str | None = None,
        source_session_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Artifact:
        destination = self._resolve_artifact_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        return self._register(
            artifact_type=artifact_type,
            title=title,
            path=destination,
            relative_path=relative_path,
            mime_type=mime_type,
            source_job_id=source_job_id,
            source_session_id=source_session_id,
            tags=tags or [],
            metadata=metadata or {},
        )

    def get(self, artifact_id: str) -> Artifact:
        row = self._db.fetchone("select * from artifacts where id = ?", (artifact_id,))
        if row is None:
            raise KeyError(f"Artifact not found: {artifact_id}")
        return _artifact_from_row(row)

    def list(self) -> list[Artifact]:
        return [
            _artifact_from_row(row)
            for row in self._db.fetchall("select * from artifacts order by created_at desc")
        ]

    def content_path(self, artifact_id: str) -> Path:
        artifact = self.get(artifact_id)
        path = artifact.file_path.resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactPathError("Artifact path is outside artifact root") from exc
        return path

    def _register(
        self,
        artifact_type: str,
        title: str,
        path: Path,
        relative_path: str,
        mime_type: str,
        source_job_id: str | None,
        source_session_id: str | None,
        tags: list[str],
        metadata: dict[str, object],
    ) -> Artifact:
        artifact_id = uuid4().hex
        normalized_relative_path = Path(relative_path).as_posix()
        self._db.execute(
            """
            insert into artifacts (
                id, type, title, file_path, relative_path, mime_type, size_bytes,
                thumbnail_path, source_job_id, source_session_id, created_at,
                tags_json, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                artifact_type,
                title,
                str(path),
                normalized_relative_path,
                mime_type,
                path.stat().st_size,
                None,
                source_job_id,
                source_session_id,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(tags, sort_keys=True),
                json.dumps(metadata, sort_keys=True),
            ),
        )
        return self.get(artifact_id)

    def _resolve_artifact_path(self, relative_path: str) -> Path:
        path = (self._root / relative_path).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactPathError("Path is outside artifact root") from exc
        return path


def _artifact_from_row(row: object) -> Artifact:
    thumbnail_path = row["thumbnail_path"]
    return Artifact(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        file_path=Path(row["file_path"]),
        relative_path=row["relative_path"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        thumbnail_path=Path(thumbnail_path) if thumbnail_path else None,
        source_job_id=row["source_job_id"],
        source_session_id=row["source_session_id"],
        tags=json.loads(row["tags_json"]),
        metadata=json.loads(row["metadata_json"]),
    )
