from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor


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
    created_at: datetime
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

    def page(
        self, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[Artifact], str | None]:
        parameters: list[object] = []
        where = ""
        if cursor:
            created_at, artifact_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(artifact_id, str):
                raise ValueError("Invalid cursor")
            where = "where created_at < ? or (created_at = ? and id < ?)"
            parameters.extend((created_at, created_at, artifact_id))
        normalized_limit = max(1, min(limit, 200))
        rows = self._db.fetchall(
            f"select * from artifacts {where} "
            "order by created_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        artifacts = [_artifact_from_row(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return artifacts, next_cursor

    def find_by_source_path(self, source_path: str) -> Artifact | None:
        for artifact in self.list():
            if artifact.metadata.get("source_path") == source_path:
                return artifact
        return None

    def delete(self, artifact_id: str) -> None:
        artifact = self.get(artifact_id)  # raises KeyError if unknown
        for path in (artifact.file_path, artifact.thumbnail_path):
            if path is None:
                continue
            try:
                stored = self._stored_path(path)
            except ArtifactPathError:
                continue
            stored.unlink(missing_ok=True)
        self._db.execute("delete from artifacts where id = ?", (artifact_id,))

    def content_path(self, artifact_id: str) -> Path:
        artifact = self.get(artifact_id)
        return self._stored_path(artifact.file_path)

    def thumbnail_path(self, artifact_id: str) -> Path | None:
        artifact = self.get(artifact_id)
        if artifact.thumbnail_path is None:
            return None
        return self._stored_path(artifact.thumbnail_path)

    def _stored_path(self, stored_path: Path) -> Path:
        path = stored_path.resolve()
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
        created_at=datetime.fromisoformat(row["created_at"]),
        tags=json.loads(row["tags_json"]),
        metadata=json.loads(row["metadata_json"]),
    )
