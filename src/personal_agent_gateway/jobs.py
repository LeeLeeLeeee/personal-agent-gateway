import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor


JobSource = Literal["chat", "manual", "schedule", "api"]
JobStatus = Literal[
    "draft",
    "waiting_approval",
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
]


class JobStatusError(Exception):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    capability_id: str
    source: JobSource
    title: str
    status: JobStatus
    input_json: dict[str, object]
    command_preview: str | None
    approval_id: str | None
    source_session_id: str | None = None
    source_schedule_id: str | None = None
    source_job_id: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class JobEvent:
    id: str
    kind: str
    payload: dict[str, object]
    created_at: str


class JobService:
    def __init__(self, db: Database, capabilities: CapabilityRegistry) -> None:
        self._db = db
        self._capabilities = capabilities

    def create_job(
        self,
        capability_id: str,
        source: JobSource,
        title: str,
        input_json: dict[str, object],
        source_session_id: str | None = None,
        source_schedule_id: str | None = None,
        source_job_id: str | None = None,
        command_preview: str | None = None,
    ) -> Job:
        self._capabilities.validate_input(capability_id, input_json)
        capability = self._capabilities.get(capability_id)
        job_id = uuid4().hex
        approval_id = uuid4().hex if capability.requires_approval else None
        status: JobStatus = "waiting_approval" if capability.requires_approval else "queued"
        now = _now()
        preview = command_preview or _default_command_preview(capability.title, input_json)
        self._db.execute(
            """
            insert into jobs (
                id, capability_id, source, source_session_id, source_schedule_id,
                source_job_id,
                title, status, input_json, command_preview, approval_id,
                created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                capability_id,
                source,
                source_session_id,
                source_schedule_id,
                source_job_id,
                title,
                status,
                json.dumps(input_json, sort_keys=True),
                preview,
                approval_id,
                now,
                now,
            ),
        )
        if approval_id is not None:
            self._db.execute(
                """
                insert into approvals (
                    id, job_id, risk_level, command_preview, status, created_at
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (approval_id, job_id, capability.risk_level, preview or title, "pending", now),
            )
        self.append_event(job_id, "created", {"status": status})
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Job:
        row = self._db.fetchone("select * from jobs where id = ?", (job_id,))
        if row is None:
            raise KeyError(f"Job not found: {job_id}")
        return _job_from_row(row)

    def list_jobs(
        self,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        capability_ids: list[str] | None = None,
        source_schedule_id: str | None = None,
    ) -> list[Job]:
        filters = {
            "status": statuses or [],
            "source": sources or [],
            "capability_id": capability_ids or [],
        }
        clauses: list[str] = []
        parameters: list[object] = []
        for column, values in filters.items():
            if not values:
                continue
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{column} in ({placeholders})")
            parameters.extend(values)
        if source_schedule_id is not None:
            clauses.append("source_schedule_id = ?")
            parameters.append(source_schedule_id)
        where_clause = f"where {' and '.join(clauses)}" if clauses else ""
        return [
            _job_from_row(row)
            for row in self._db.fetchall(
                f"select * from jobs {where_clause} order by created_at desc",
                parameters,
            )
        ]

    def page_jobs(
        self,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        capability_ids: list[str] | None = None,
        source_schedule_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Job], str | None]:
        filters = {
            "status": statuses or [],
            "source": sources or [],
            "capability_id": capability_ids or [],
        }
        clauses: list[str] = []
        parameters: list[object] = []
        for column, values in filters.items():
            if not values:
                continue
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{column} in ({placeholders})")
            parameters.extend(values)
        if source_schedule_id is not None:
            clauses.append("source_schedule_id = ?")
            parameters.append(source_schedule_id)
        if cursor:
            created_at, job_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(job_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(created_at < ? or (created_at = ? and id < ?))")
            parameters.extend((created_at, created_at, job_id))
        where_clause = f"where {' and '.join(clauses)}" if clauses else ""
        normalized_limit = max(1, min(limit, 200))
        rows = self._db.fetchall(
            f"select * from jobs {where_clause} "
            "order by created_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        jobs = [_job_from_row(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return jobs, next_cursor

    def list_events(self, job_id: str) -> list[JobEvent]:
        self.get_job(job_id)
        return [
            _job_event_from_row(row)
            for row in self._db.fetchall(
                "select * from job_events where job_id = ? order by created_at asc",
                (job_id,),
            )
        ]

    def page_events(
        self, job_id: str, limit: int = 200, cursor: str | None = None
    ) -> tuple[list[JobEvent], str | None]:
        self.get_job(job_id)
        clauses = ["job_id = ?"]
        parameters: list[object] = [job_id]
        if cursor:
            created_at, event_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(event_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(created_at < ? or (created_at = ? and id < ?))")
            parameters.extend((created_at, created_at, event_id))
        normalized_limit = max(1, min(limit, 500))
        rows = self._db.fetchall(
            f"select * from job_events where {' and '.join(clauses)} "
            "order by created_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        events = [_job_event_from_row(row) for row in reversed(selected)]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return events, next_cursor

    def runner_type_for(self, job: Job) -> str:
        return self._capabilities.get(job.capability_id).runner_type

    def approve_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.source == "chat":
            raise JobStatusError("Chat jobs are history records and cannot be worker-approved")
        if job.status != "waiting_approval":
            raise JobStatusError(f"Cannot transition {job.status} to queued")
        now = _now()
        self._db.execute(
            "update approvals set status = ?, decided_at = ? where id = ?",
            ("approved", now, job.approval_id),
        )
        self._set_status(job.id, "queued")
        self.append_event(job.id, "approved", {"approval_id": job.approval_id})
        return self.get_job(job.id)

    def deny_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.source == "chat":
            raise JobStatusError("Chat jobs are history records and cannot be worker-denied")
        if job.status != "waiting_approval":
            raise JobStatusError(f"Cannot transition {job.status} to canceled")
        now = _now()
        self._db.execute(
            "update approvals set status = ?, decided_at = ? where id = ?",
            ("denied", now, job.approval_id),
        )
        self._set_status(job.id, "canceled")
        self.append_event(job.id, "denied", {"approval_id": job.approval_id})
        return self.get_job(job.id)

    def retry_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.source == "chat":
            raise JobStatusError("Chat jobs are history records and cannot be retried")
        if job.status not in {"failed", "canceled"}:
            raise JobStatusError("Only failed or canceled jobs can be retried")
        retried = self.create_job(
            capability_id=job.capability_id,
            source=job.source,
            title=job.title,
            input_json=json.loads(json.dumps(job.input_json)),
            source_session_id=job.source_session_id,
            source_schedule_id=job.source_schedule_id,
            source_job_id=job.source_job_id or job.id,
            command_preview=job.command_preview,
        )
        self.append_event(
            retried.id,
            "retried",
            {"source_job_id": retried.source_job_id},
        )
        self.append_event(job.id, "retry_created", {"job_id": retried.id})
        return retried

    def cancel_job(self, job_id: str, message: str) -> Job:
        job = self.get_job(job_id)
        if job.status not in {"queued", "running"}:
            raise JobStatusError(f"Cannot transition {job.status} to canceled")
        self._set_status(
            job.id,
            "canceled",
            finished_at=_now(),
            error_message=message,
        )
        self.append_event(job.id, "canceled", {"message": message})
        return self.get_job(job.id)

    def cancel_active(self, message: str) -> list[str]:
        canceled: list[str] = []
        for job in self.list_jobs(statuses=["queued", "running"]):
            try:
                self.cancel_job(job.id, message)
            except JobStatusError:
                continue
            canceled.append(job.id)
        return canceled

    def mark_running(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status != "queued":
            raise JobStatusError(f"Cannot transition {job.status} to running")
        self._set_status(job.id, "running", started_at=_now())
        self.append_event(job.id, "running", {})
        return self.get_job(job.id)

    def mark_succeeded(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status != "running":
            raise JobStatusError(f"Cannot transition {job.status} to succeeded")
        self._set_status(job.id, "succeeded", finished_at=_now())
        self.append_event(job.id, "succeeded", {})
        return self.get_job(job.id)

    def mark_failed(self, job_id: str, message: str) -> Job:
        job = self.get_job(job_id)
        if job.status not in {"queued", "running"}:
            raise JobStatusError(f"Cannot transition {job.status} to failed")
        self._set_status(job.id, "failed", finished_at=_now(), error_message=message)
        self.append_event(job.id, "failed", {"message": message})
        return self.get_job(job.id)

    def recover_interrupted_jobs(self) -> None:
        for job in self.list_jobs():
            if job.status == "running":
                self._set_status(
                    job.id,
                    "failed",
                    finished_at=_now(),
                    error_message="Gateway restarted while job was running",
                )
                self.append_event(
                    job.id,
                    "failed",
                    {"message": "Gateway restarted while job was running"},
                )

    def append_event(self, job_id: str, kind: str, payload: dict[str, object]) -> None:
        self._db.execute(
            """
            insert into job_events (id, job_id, kind, payload_json, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (uuid4().hex, job_id, kind, json.dumps(payload, sort_keys=True), _now()),
        )

    def _set_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._db.execute(
            """
            update jobs
            set status = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                error_message = ?,
                updated_at = ?
            where id = ?
            """,
            (status, started_at, finished_at, error_message, _now(), job_id),
        )


def _job_from_row(row: object) -> Job:
    return Job(
        id=row["id"],
        capability_id=row["capability_id"],
        source=row["source"],
        source_session_id=row["source_session_id"],
        source_schedule_id=row["source_schedule_id"],
        source_job_id=row["source_job_id"],
        title=row["title"],
        status=row["status"],
        input_json=json.loads(row["input_json"]),
        command_preview=row["command_preview"],
        approval_id=row["approval_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_message=row["error_message"],
    )


def _job_event_from_row(row: object) -> JobEvent:
    return JobEvent(
        id=row["id"],
        kind=row["kind"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
    )


def _default_command_preview(title: str, input_json: dict[str, object]) -> str:
    return f"{title}: {json.dumps(input_json, sort_keys=True)}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
