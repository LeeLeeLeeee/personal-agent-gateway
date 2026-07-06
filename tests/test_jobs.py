from pathlib import Path

import pytest

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService, JobStatusError


def make_service(tmp_path: Path) -> JobService:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    return JobService(db, CapabilityRegistry.default())


def test_create_low_risk_job_queues_immediately(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect demo.mov",
        input_json={"source_file": "demo.mov"},
    )

    assert job.status == "queued"
    assert job.capability_id == "ffmpeg.inspect"
    assert job.approval_id is None


def test_create_medium_risk_job_waits_for_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    job = service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="manual",
        title="Extract audio",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )

    assert job.status == "waiting_approval"
    assert job.approval_id is not None


def test_approve_job_moves_waiting_job_to_queued(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    job = service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="manual",
        title="Extract audio",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )

    approved = service.approve_job(job.id)

    assert approved.status == "queued"
    assert approved.approval_id == job.approval_id


def test_invalid_job_transition_is_rejected(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect demo.mov",
        input_json={"source_file": "demo.mov"},
    )

    with pytest.raises(JobStatusError, match="Cannot transition"):
        service.mark_succeeded(job.id)
