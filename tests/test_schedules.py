from datetime import datetime, timezone
from pathlib import Path

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.schedules import ScheduleService


def test_schedule_computes_next_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = ScheduleService(db, CapabilityRegistry.default())

    schedule = service.create_schedule(
        name="Daily inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="0 9 * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    )

    assert schedule.next_run_at.isoformat().startswith("2026-07-06T09:00:00")


def test_due_schedule_creates_job(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    registry = CapabilityRegistry.default()
    schedule_service = ScheduleService(db, registry)
    job_service = JobService(db, registry)
    schedule_service.create_schedule(
        name="Inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="* * * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    )

    jobs = schedule_service.create_due_jobs(
        job_service,
        now=datetime(2026, 7, 6, 0, 1, tzinfo=timezone.utc),
    )

    assert len(jobs) == 1
    assert jobs[0].source == "schedule"


def test_schedule_detail_summarizes_history_and_next_three_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    registry = CapabilityRegistry.default()
    schedule_service = ScheduleService(db, registry)
    job_service = JobService(db, registry)
    schedule = schedule_service.create_schedule(
        name="Inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="0 * * * *",
        timezone_name="Asia/Seoul",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 7, 15, 0, 10, tzinfo=timezone.utc),
    )
    succeeded = schedule_service.run_now(schedule.id, job_service)
    job_service.mark_running(succeeded.id)
    job_service.mark_succeeded(succeeded.id)
    failed = schedule_service.run_now(schedule.id, job_service)
    job_service.mark_running(failed.id)
    job_service.mark_failed(failed.id, "probe failed")

    detail = schedule_service.detail(
        schedule.id,
        job_service,
        now=datetime(2026, 7, 15, 0, 10, tzinfo=timezone.utc),
    )

    assert [job.id for job in detail.jobs] == [failed.id, succeeded.id]
    assert detail.success_rate == 0.5
    assert detail.last_failure is not None
    assert detail.last_failure.id == failed.id
    assert [value.isoformat() for value in detail.next_runs] == [
        "2026-07-15T01:00:00+00:00",
        "2026-07-15T02:00:00+00:00",
        "2026-07-15T03:00:00+00:00",
    ]


def test_next_run_preview_respects_dst_timezone(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    registry = CapabilityRegistry.default()
    service = ScheduleService(db, registry)
    schedule = service.create_schedule(
        name="DST",
        capability_id="ffmpeg.inspect",
        cron_expression="30 2 * * *",
        timezone_name="America/New_York",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc),
    )

    preview = service.next_runs(
        schedule.id,
        now=datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert [value.isoformat() for value in preview] == [
        "2026-03-08T07:00:00+00:00",
        "2026-03-09T06:30:00+00:00",
        "2026-03-10T06:30:00+00:00",
    ]
