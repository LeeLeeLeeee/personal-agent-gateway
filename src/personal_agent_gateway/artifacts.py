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


class ArtifactInUseError(Exception):
    pass


@dataclass(frozen=True)
class ArtifactCleanupPreview:
    artifacts: tuple[Artifact, ...]
    total_size_bytes: int
    evaluated_at: datetime


@dataclass(frozen=True)
class ArtifactCleanupResult:
    deleted_ids: tuple[str, ...]
    skipped_ids: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactBreadcrumb:
    kind: str
    id: str
    label: str


@dataclass(frozen=True)
class ArtifactBrowserItem:
    artifact: Artifact
    role: str
    source_kind: str
    breadcrumbs: tuple[ArtifactBreadcrumb, ...]
    deletion_allowed: bool


@dataclass(frozen=True)
class ArtifactBrowserPage:
    items: tuple[ArtifactBrowserItem, ...]
    counts: dict[str, int]
    next_cursor: str | None


@dataclass(frozen=True)
class ArtifactUsage:
    kind: str
    id: str
    label: str


@dataclass(frozen=True)
class ArtifactDeleteBlocked:
    artifact_id: str
    references: tuple[ArtifactUsage, ...]


@dataclass(frozen=True)
class ArtifactDeleteResult:
    deleted_ids: tuple[str, ...]
    blocked: tuple[ArtifactDeleteBlocked, ...]
    missing_ids: tuple[str, ...]


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
    origin_kind: str
    artifact_role: str
    source_chat_turn_id: str | None
    source_team_task_id: str | None
    source_team_run_id: str | None
    source_cycle_id: str | None
    origin_group_label_snapshot: str
    origin_item_label_snapshot: str
    created_at: datetime
    retention_class: str
    expires_at: datetime | None
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
        retention_class: str = "durable",
        expires_at: datetime | None = None,
        origin_kind: str = "manual_upload",
        artifact_role: str = "attachment",
        source_chat_turn_id: str | None = None,
        source_team_task_id: str | None = None,
        source_team_run_id: str | None = None,
        source_cycle_id: str | None = None,
        origin_group_label_snapshot: str = "",
        origin_item_label_snapshot: str = "",
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
            retention_class=retention_class,
            expires_at=expires_at,
            origin_kind=origin_kind,
            artifact_role=artifact_role,
            source_chat_turn_id=source_chat_turn_id,
            source_team_task_id=source_team_task_id,
            source_team_run_id=source_team_run_id,
            source_cycle_id=source_cycle_id,
            origin_group_label_snapshot=origin_group_label_snapshot,
            origin_item_label_snapshot=origin_item_label_snapshot,
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
        retention_class: str = "durable",
        expires_at: datetime | None = None,
        origin_kind: str = "manual_upload",
        artifact_role: str = "attachment",
        source_chat_turn_id: str | None = None,
        source_team_task_id: str | None = None,
        source_team_run_id: str | None = None,
        source_cycle_id: str | None = None,
        origin_group_label_snapshot: str = "",
        origin_item_label_snapshot: str = "",
    ) -> Artifact:
        destination = self._resolve_artifact_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        try:
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
                retention_class=retention_class,
                expires_at=expires_at,
                origin_kind=origin_kind,
                artifact_role=artifact_role,
                source_chat_turn_id=source_chat_turn_id,
                source_team_task_id=source_team_task_id,
                source_team_run_id=source_team_run_id,
                source_cycle_id=source_cycle_id,
                origin_group_label_snapshot=origin_group_label_snapshot,
                origin_item_label_snapshot=origin_item_label_snapshot,
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

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

    def browser_page(
        self,
        *,
        segment: str = "saved",
        query: str = "",
        file_kind: str | None = None,
        source_kind: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ArtifactBrowserPage:
        if segment not in {"saved", "recent", "cleanup"}:
            raise ValueError("Invalid artifact segment")
        normalized_limit = max(1, min(limit, 200))
        now = datetime.now(timezone.utc)
        all_artifacts = self.list()
        counts = {
            name: sum(
                1 for artifact in all_artifacts if self._in_browser_segment(artifact, name, now)
            )
            for name in ("saved", "recent", "cleanup")
        }
        terms = [term for term in query.lower().split() if term]
        filtered: list[tuple[Artifact, str, str]] = []
        for artifact in all_artifacts:
            if not self._in_browser_segment(artifact, segment, now):
                continue
            resolved_source_kind = _artifact_source_kind(artifact)
            if file_kind and artifact.type != file_kind:
                continue
            if source_kind and resolved_source_kind != source_kind:
                continue
            group_label, item_label = self._browser_labels(artifact, resolved_source_kind)
            haystack = " ".join(
                (
                    artifact.title,
                    artifact.relative_path,
                    artifact.artifact_role,
                    group_label,
                    item_label,
                    artifact.source_job_id or "",
                    artifact.source_session_id or "",
                    artifact.source_chat_turn_id or "",
                    artifact.source_team_task_id or "",
                    artifact.source_team_run_id or "",
                    " ".join(artifact.tags),
                )
            ).lower()
            if all(term in haystack for term in terms):
                filtered.append((artifact, group_label, item_label))
        if cursor:
            created_at, artifact_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(artifact_id, str):
                raise ValueError("Invalid cursor")
            filtered = [
                artifact
                for artifact, group_label, item_label in filtered
                if artifact.created_at.isoformat() < created_at
                or (artifact.created_at.isoformat() == created_at and artifact.id < artifact_id)
            ]
        selected = filtered[:normalized_limit]
        next_cursor = None
        if len(filtered) > normalized_limit:
            last = selected[-1][0]
            next_cursor = encode_cursor(last.created_at.isoformat(), last.id)
        return ArtifactBrowserPage(
            items=tuple(
                self._browser_item(artifact, group_label, item_label)
                for artifact, group_label, item_label in selected
            ),
            counts=counts,
            next_cursor=next_cursor,
        )

    def find_by_source_path(self, source_path: str) -> Artifact | None:
        for artifact in self.list():
            if artifact.metadata.get("source_path") == source_path:
                return artifact
        return None

    def delete(self, artifact_id: str) -> None:
        artifact = self.get(artifact_id)  # raises KeyError if unknown
        if self._is_referenced(artifact_id):
            raise ArtifactInUseError(f"Artifact is used by a Team input: {artifact_id}")
        for path in (artifact.file_path, artifact.thumbnail_path):
            if path is None:
                continue
            try:
                stored = self._stored_path(path)
            except ArtifactPathError:
                continue
            stored.unlink(missing_ok=True)
        self._db.execute("delete from artifacts where id = ?", (artifact_id,))

    def delete_many(self, artifact_ids: list[str]) -> ArtifactDeleteResult:
        deleted_ids: list[str] = []
        blocked: list[ArtifactDeleteBlocked] = []
        missing_ids: list[str] = []
        for artifact_id in artifact_ids:
            try:
                self.get(artifact_id)
            except KeyError:
                missing_ids.append(artifact_id)
                continue
            references = self.references(artifact_id)
            if references:
                blocked.append(ArtifactDeleteBlocked(artifact_id, references))
                continue
            self.delete(artifact_id)
            deleted_ids.append(artifact_id)
        return ArtifactDeleteResult(
            tuple(deleted_ids), tuple(blocked), tuple(missing_ids)
        )

    def references(self, artifact_id: str) -> tuple[ArtifactUsage, ...]:
        rows = self._db.fetchall(
            """
            select 'team_cycle_request_input' as kind, input.cycle_request_id as id,
                   coalesce(request.instruction, input.cycle_request_id) as label
            from team_cycle_request_input_artifacts input
            left join team_cycle_requests request on request.id = input.cycle_request_id
            where input.artifact_id = ?
            union all
            select 'team_cycle_input' as kind, input.cycle_id as id,
                   coalesce(cycle.summary, cycle.source_id, input.cycle_id) as label
            from team_cycle_input_artifacts input
            left join team_run_cycles cycle on cycle.id = input.cycle_id
            where input.artifact_id = ?
            union all
            select 'team_task_input' as kind, input.task_id as id,
                   coalesce(task.title, input.task_id) as label
            from team_task_input_artifacts input
            left join team_tasks task on task.id = input.task_id
            where input.artifact_id = ?
            """,
            (artifact_id, artifact_id, artifact_id),
        )
        return tuple(
            ArtifactUsage(kind=row["kind"], id=row["id"], label=row["label"])
            for row in rows
        )

    def set_retention(
        self,
        artifact_id: str,
        retention_class: str,
        expires_at: datetime | None,
    ) -> Artifact:
        self.get(artifact_id)
        self._validate_retention(retention_class, expires_at)
        normalized_expiry = expires_at if retention_class == "temporary" else None
        self._db.execute(
            "update artifacts set retention_class = ?, expires_at = ? where id = ?",
            (
                retention_class,
                normalized_expiry.isoformat() if normalized_expiry else None,
                artifact_id,
            ),
        )
        return self.get(artifact_id)

    def cleanup_preview(self, evaluated_at: datetime) -> ArtifactCleanupPreview:
        artifacts = tuple(
            artifact
            for artifact in self.list()
            if self._is_cleanup_eligible(artifact, evaluated_at)
        )
        return ArtifactCleanupPreview(
            artifacts=artifacts,
            total_size_bytes=sum(artifact.size_bytes for artifact in artifacts),
            evaluated_at=evaluated_at,
        )

    def cleanup(
        self, artifact_ids: list[str], evaluated_at: datetime
    ) -> ArtifactCleanupResult:
        if not artifact_ids:
            raise ValueError("Artifact cleanup requires at least one artifact ID")
        deleted_ids: list[str] = []
        skipped_ids: list[str] = []
        for artifact_id in artifact_ids:
            try:
                artifact = self.get(artifact_id)
            except KeyError:
                skipped_ids.append(artifact_id)
                continue
            if not self._is_cleanup_eligible(artifact, evaluated_at):
                skipped_ids.append(artifact_id)
                continue
            try:
                self.delete(artifact_id)
            except ArtifactInUseError:
                skipped_ids.append(artifact_id)
            else:
                deleted_ids.append(artifact_id)
        return ArtifactCleanupResult(tuple(deleted_ids), tuple(skipped_ids))

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
        retention_class: str,
        expires_at: datetime | None,
        origin_kind: str,
        artifact_role: str,
        source_chat_turn_id: str | None,
        source_team_task_id: str | None,
        source_team_run_id: str | None,
        source_cycle_id: str | None,
        origin_group_label_snapshot: str,
        origin_item_label_snapshot: str,
    ) -> Artifact:
        self._validate_retention(retention_class, expires_at)
        artifact_id = uuid4().hex
        normalized_relative_path = Path(relative_path).as_posix()
        self._db.execute(
            """
            insert into artifacts (
                id, type, title, file_path, relative_path, mime_type, size_bytes,
                thumbnail_path, source_job_id, source_session_id, created_at,
                tags_json, metadata_json, retention_class, expires_at, origin_kind,
                artifact_role, source_chat_turn_id, source_team_task_id,
                source_team_run_id, source_cycle_id, origin_group_label_snapshot,
                origin_item_label_snapshot
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                retention_class,
                expires_at.isoformat() if expires_at else None,
                origin_kind,
                artifact_role,
                source_chat_turn_id,
                source_team_task_id,
                source_team_run_id,
                source_cycle_id,
                origin_group_label_snapshot,
                origin_item_label_snapshot,
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

    def _is_cleanup_eligible(self, artifact: Artifact, evaluated_at: datetime) -> bool:
        return (
            artifact.retention_class == "temporary"
            and artifact.expires_at is not None
            and artifact.expires_at <= evaluated_at
            and not self._is_referenced(artifact.id)
        )

    def _in_browser_segment(
        self, artifact: Artifact, segment: str, evaluated_at: datetime
    ) -> bool:
        if segment == "cleanup":
            return self._is_cleanup_eligible(artifact, evaluated_at)
        if segment == "recent":
            return (
                artifact.retention_class == "temporary"
                and (artifact.expires_at is None or artifact.expires_at > evaluated_at)
            )
        return artifact.retention_class != "temporary"

    def _browser_labels(self, artifact: Artifact, source_kind: str) -> tuple[str, str]:
        group_label = artifact.origin_group_label_snapshot or _default_group_label(source_kind)
        item_label = artifact.origin_item_label_snapshot or artifact.title
        if source_kind != "team":
            return group_label, item_label
        breadcrumbs = self._team_breadcrumbs(artifact)
        if breadcrumbs:
            group_label = breadcrumbs[0].label
            item_label = breadcrumbs[-1].label
        return group_label, item_label

    def _team_breadcrumbs(self, artifact: Artifact) -> tuple[ArtifactBreadcrumb, ...]:
        if not artifact.source_team_run_id:
            return ()
        row = self._db.fetchone(
            """
            select run.id as run_id, run.goal as run_goal, team.id as team_id,
                   team.name as team_name, cycle.id as cycle_id, cycle.sequence as cycle_sequence,
                   task.id as task_id, task.title as task_title
            from team_runs run
            left join teams team on team.id = run.team_id
            left join team_run_cycles cycle on cycle.id = ? and cycle.team_run_id = run.id
            left join team_tasks task on task.id = ? and task.team_run_id = run.id
            where run.id = ?
            """,
            (artifact.source_cycle_id, artifact.source_team_task_id, artifact.source_team_run_id),
        )
        if row is None:
            return ()
        breadcrumbs: list[ArtifactBreadcrumb] = []
        if row["team_id"] and row["team_name"]:
            breadcrumbs.append(ArtifactBreadcrumb("team", row["team_id"], row["team_name"]))
        breadcrumbs.append(ArtifactBreadcrumb("team_run", row["run_id"], row["run_goal"]))
        if row["cycle_id"] and row["cycle_sequence"] is not None:
            breadcrumbs.append(
                ArtifactBreadcrumb("team_cycle", row["cycle_id"], f"Cycle {row['cycle_sequence']}")
            )
        if row["task_id"] and row["task_title"]:
            breadcrumbs.append(ArtifactBreadcrumb("team_task", row["task_id"], row["task_title"]))
        return tuple(breadcrumbs)

    def _browser_item(
        self, artifact: Artifact, group_label: str, item_label: str
    ) -> ArtifactBrowserItem:
        source_kind = _artifact_source_kind(artifact)
        if source_kind == "team":
            team_breadcrumbs = self._team_breadcrumbs(artifact)
            if team_breadcrumbs:
                return ArtifactBrowserItem(
                    artifact=artifact,
                    role=artifact.artifact_role,
                    source_kind=source_kind,
                    breadcrumbs=team_breadcrumbs,
                    deletion_allowed=not self._is_referenced(artifact.id),
                )
        group_id = (
            artifact.source_team_run_id
            or artifact.source_session_id
            or artifact.source_job_id
            or artifact.id
        )
        item_id = (
            artifact.source_team_task_id
            or artifact.source_chat_turn_id
            or artifact.source_cycle_id
            or artifact.id
        )
        group_kind = {
            "team": "team_run",
            "chat": "chat_session",
            "job": "job",
            "schedule": "schedule",
        }.get(source_kind, source_kind)
        breadcrumbs = [ArtifactBreadcrumb(group_kind, group_id, group_label)]
        if item_id != group_id or item_label != group_label:
            item_kind = "team_task" if source_kind == "team" else "chat_turn"
            breadcrumbs.append(ArtifactBreadcrumb(item_kind, item_id, item_label))
        return ArtifactBrowserItem(
            artifact=artifact,
            role=artifact.artifact_role,
            source_kind=source_kind,
            breadcrumbs=tuple(breadcrumbs),
            deletion_allowed=not self._is_referenced(artifact.id),
        )

    def _is_referenced(self, artifact_id: str) -> bool:
        return bool(self.references(artifact_id))

    @staticmethod
    def _validate_retention(
        retention_class: str, expires_at: datetime | None
    ) -> None:
        if retention_class not in {"pinned", "durable", "temporary"}:
            raise ValueError("Invalid artifact retention class")
        if retention_class == "temporary" and expires_at is None:
            raise ValueError("Temporary artifacts require an expiry")


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
        origin_kind=row["origin_kind"],
        artifact_role=row["artifact_role"],
        source_chat_turn_id=row["source_chat_turn_id"],
        source_team_task_id=row["source_team_task_id"],
        source_team_run_id=row["source_team_run_id"],
        source_cycle_id=row["source_cycle_id"],
        origin_group_label_snapshot=row["origin_group_label_snapshot"],
        origin_item_label_snapshot=row["origin_item_label_snapshot"],
        created_at=datetime.fromisoformat(row["created_at"]),
        retention_class=row["retention_class"],
        expires_at=(datetime.fromisoformat(row["expires_at"])
                    if row["expires_at"] else None),
        tags=json.loads(row["tags_json"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _artifact_source_kind(artifact: Artifact) -> str:
    if artifact.origin_kind.startswith("team_"):
        return "team"
    if artifact.origin_kind == "job_output":
        return "job"
    if artifact.origin_kind == "chat_upload":
        return "chat"
    if artifact.origin_kind == "manual_upload":
        return "manual"
    return "legacy"


def _default_group_label(source_kind: str) -> str:
    return {
        "team": "Team output",
        "chat": "Chat files",
        "job": "Job output",
        "schedule": "Scheduled output",
        "manual": "Local files",
    }.get(source_kind, "Legacy files")
