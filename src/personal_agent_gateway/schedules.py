import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from croniter import croniter

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import Job, JobService


@dataclass(frozen=True)
class Schedule:
    id: str
    name: str
    capability_id: str
    cron_expression: str
    timezone: str
    input_template_json: dict[str, object]
    enabled: bool
    last_run_job_id: str | None
    last_run_at: datetime | None
    next_run_at: datetime


class ScheduleService:
    def __init__(self, db: Database, capabilities: CapabilityRegistry) -> None:
        self._db = db
        self._capabilities = capabilities

    def create_schedule(
        self,
        name: str,
        capability_id: str,
        cron_expression: str,
        timezone_name: str,
        input_template_json: dict[str, object],
        now: datetime | None = None,
    ) -> Schedule:
        self._capabilities.validate_input(capability_id, input_template_json)
        now = _normalize_now(now)
        next_run_at = _next_run(cron_expression, now)
        schedule_id = uuid4().hex
        self._db.execute(
            """
            insert into schedules (
                id, name, capability_id, cron_expression, timezone,
                input_template_json, enabled, next_run_at, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_id,
                name,
                capability_id,
                cron_expression,
                timezone_name,
                json.dumps(input_template_json, sort_keys=True),
                1,
                next_run_at.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: str) -> Schedule:
        row = self._db.fetchone("select * from schedules where id = ?", (schedule_id,))
        if row is None:
            raise KeyError(f"Schedule not found: {schedule_id}")
        return _schedule_from_row(row)

    def list_schedules(self) -> list[Schedule]:
        return [
            _schedule_from_row(row)
            for row in self._db.fetchall("select * from schedules order by created_at desc")
        ]

    def delete(self, schedule_id: str) -> None:
        self.get_schedule(schedule_id)
        self._db.execute("delete from schedules where id = ?", (schedule_id,))

    def pause(self, schedule_id: str) -> Schedule:
        self._set_enabled(schedule_id, enabled=False)
        return self.get_schedule(schedule_id)

    def resume(self, schedule_id: str, now: datetime | None = None) -> Schedule:
        schedule = self.get_schedule(schedule_id)
        now = _normalize_now(now)
        self._db.execute(
            """
            update schedules
            set enabled = 1, next_run_at = ?, updated_at = ?
            where id = ?
            """,
            (_next_run(schedule.cron_expression, now).isoformat(), now.isoformat(), schedule_id),
        )
        return self.get_schedule(schedule_id)

    def run_now(self, schedule_id: str, job_service: JobService) -> Job:
        schedule = self.get_schedule(schedule_id)
        job = self._create_job_for_schedule(schedule, job_service)
        now = _now()
        self._db.execute(
            """
            update schedules
            set last_run_job_id = ?, last_run_at = ?, updated_at = ?
            where id = ?
            """,
            (job.id, now.isoformat(), now.isoformat(), schedule.id),
        )
        return job

    def create_due_jobs(
        self,
        job_service: JobService,
        now: datetime | None = None,
    ) -> list[Job]:
        now = _normalize_now(now)
        jobs: list[Job] = []
        for schedule in self._due_schedules(now):
            job = self._create_job_for_schedule(schedule, job_service)
            next_run_at = _next_run(schedule.cron_expression, now)
            self._db.execute(
                """
                update schedules
                set last_run_job_id = ?, last_run_at = ?, next_run_at = ?, updated_at = ?
                where id = ?
                """,
                (
                    job.id,
                    now.isoformat(),
                    next_run_at.isoformat(),
                    now.isoformat(),
                    schedule.id,
                ),
            )
            jobs.append(job)
        return jobs

    def _due_schedules(self, now: datetime) -> list[Schedule]:
        rows = self._db.fetchall(
            """
            select *
            from schedules
            where enabled = 1 and next_run_at <= ?
            order by next_run_at asc
            """,
            (now.isoformat(),),
        )
        return [_schedule_from_row(row) for row in rows]

    def _create_job_for_schedule(self, schedule: Schedule, job_service: JobService) -> Job:
        return job_service.create_job(
            capability_id=schedule.capability_id,
            source="schedule",
            title=schedule.name,
            input_json=schedule.input_template_json,
            source_schedule_id=schedule.id,
        )

    def _set_enabled(self, schedule_id: str, enabled: bool) -> None:
        self._db.execute(
            "update schedules set enabled = ?, updated_at = ? where id = ?",
            (1 if enabled else 0, _now().isoformat(), schedule_id),
        )


def _schedule_from_row(row: object) -> Schedule:
    return Schedule(
        id=row["id"],
        name=row["name"],
        capability_id=row["capability_id"],
        cron_expression=row["cron_expression"],
        timezone=row["timezone"],
        input_template_json=json.loads(row["input_template_json"]),
        enabled=bool(row["enabled"]),
        last_run_job_id=row["last_run_job_id"],
        last_run_at=_parse_datetime(row["last_run_at"]),
        next_run_at=_parse_datetime(row["next_run_at"]) or _now(),
    )


def _next_run(cron_expression: str, base: datetime) -> datetime:
    return croniter(cron_expression, base).get_next(datetime)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_now(value: datetime | None) -> datetime:
    value = value or _now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
