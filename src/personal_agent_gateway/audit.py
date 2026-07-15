from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor
from personal_agent_gateway.redaction import redact_text, sanitize_metadata


AuditSeverity = Literal["debug", "info", "warning", "error", "critical"]


@dataclass(frozen=True)
class AuditEvent:
    id: str
    occurred_at: str
    event_type: str
    severity: str
    actor_type: str
    actor_id: str | None
    session_id: str | None
    team_run_id: str | None
    team_task_id: str | None
    job_id: str | None
    artifact_id: str | None
    correlation_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    status: str
    command_preview: str | None
    metadata: dict[str, object]
    redaction_version: int


class AuditService:
    def __init__(self, database: Database, retention_days: int = 90) -> None:
        self._database = database
        self._retention_days = retention_days

    def record(
        self,
        *,
        event_type: str,
        action: str,
        status: str,
        severity: AuditSeverity = "info",
        actor_type: str = "system",
        actor_id: str | None = None,
        session_id: str | None = None,
        team_run_id: str | None = None,
        team_task_id: str | None = None,
        job_id: str | None = None,
        artifact_id: str | None = None,
        correlation_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        command_preview: str | None = None,
        metadata: dict[str, object] | None = None,
        secrets: list[str] | None = None,
    ) -> AuditEvent:
        event_id = uuid4().hex
        occurred_at = datetime.now(timezone.utc).isoformat()
        secret_values = secrets or []
        safe_metadata = sanitize_metadata(metadata or {}, secrets=secret_values)
        safe_command = (
            redact_text(command_preview, secrets=secret_values, limit=500)
            if command_preview
            else None
        )
        self._database.execute(
            """
            insert into audit_events (
                id, occurred_at, event_type, severity, actor_type, actor_id,
                session_id, team_run_id, team_task_id, job_id, artifact_id,
                correlation_id, action, resource_type, resource_id, status,
                command_preview, metadata_json, redaction_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                occurred_at,
                event_type,
                severity,
                actor_type,
                actor_id,
                session_id,
                team_run_id,
                team_task_id,
                job_id,
                artifact_id,
                correlation_id,
                action,
                resource_type,
                resource_id,
                status,
                safe_command,
                json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
                1,
            ),
        )
        return self.get(event_id)

    def get(self, event_id: str) -> AuditEvent:
        row = self._database.fetchone("select * from audit_events where id = ?", (event_id,))
        if row is None:
            raise KeyError(f"Audit event not found: {event_id}")
        return _event_from_row(row)

    def list(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        correlation_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        events, _next_cursor = self.page(
            event_type=event_type,
            severity=severity,
            actor_id=actor_id,
            resource_type=resource_type,
            correlation_id=correlation_id,
            since=since,
            limit=limit,
        )
        return events

    def page(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        correlation_id: str | None = None,
        since: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        filters = {
            "event_type": event_type,
            "severity": severity,
            "actor_id": actor_id,
            "resource_type": resource_type,
            "correlation_id": correlation_id,
        }
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        effective_since = since or (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()
        clauses.append("occurred_at >= ?")
        parameters.append(effective_since)
        if cursor:
            occurred_at, event_id = decode_cursor(cursor, 2)
            if not isinstance(occurred_at, str) or not isinstance(event_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(occurred_at < ? or (occurred_at = ? and id < ?))")
            parameters.extend((occurred_at, occurred_at, event_id))
        where = f"where {' and '.join(clauses)}"
        bounded_limit = max(1, min(limit, 500))
        parameters.append(bounded_limit + 1)
        rows = self._database.fetchall(
            f"select * from audit_events {where} "
            "order by occurred_at desc, id desc limit ?",
            parameters,
        )
        selected = rows[:bounded_limit]
        events = [_event_from_row(row) for row in selected]
        next_cursor = None
        if len(rows) > bounded_limit and events:
            next_cursor = encode_cursor(events[-1].occurred_at, events[-1].id)
        return events, next_cursor


def _event_from_row(row: object) -> AuditEvent:
    return AuditEvent(
        id=str(row["id"]),
        occurred_at=str(row["occurred_at"]),
        event_type=str(row["event_type"]),
        severity=str(row["severity"]),
        actor_type=str(row["actor_type"]),
        actor_id=row["actor_id"],
        session_id=row["session_id"],
        team_run_id=row["team_run_id"],
        team_task_id=row["team_task_id"],
        job_id=row["job_id"],
        artifact_id=row["artifact_id"],
        correlation_id=row["correlation_id"],
        action=str(row["action"]),
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        status=str(row["status"]),
        command_preview=row["command_preview"],
        metadata=json.loads(str(row["metadata_json"])),
        redaction_version=int(row["redaction_version"]),
    )
