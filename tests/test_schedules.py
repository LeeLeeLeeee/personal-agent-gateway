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
