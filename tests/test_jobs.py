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


def test_retry_creates_new_job_from_failed_input_and_rechecks_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    original = service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="manual",
        title="Extract audio",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )
    service.approve_job(original.id)
    service.mark_failed(original.id, "failed")

    retried = service.retry_job(original.id)

    assert retried.id != original.id
    assert retried.source_job_id == original.id
    assert retried.input_json == original.input_json
    assert retried.input_json is not original.input_json
    assert retried.status == "waiting_approval"
    assert retried.approval_id != original.approval_id


def test_retry_rejects_non_terminal_and_chat_history_jobs(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    queued = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect",
        input_json={"source_file": "demo.mov"},
    )
    chat = service.create_job(
        capability_id="ffmpeg.inspect",
        source="chat",
        title="Chat history",
        input_json={"source_file": "chat.mov"},
    )
    service.mark_failed(chat.id, "failed")

    with pytest.raises(JobStatusError, match="failed or canceled"):
        service.retry_job(queued.id)
    with pytest.raises(JobStatusError, match="Chat jobs"):
        service.retry_job(chat.id)


def test_jobs_cursor_pages_are_stable_and_do_not_duplicate(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = [
        service.create_job(
            capability_id="ffmpeg.inspect",
            source="manual",
            title=f"Inspect {index}",
            input_json={"source_file": f"{index}.mov"},
        )
        for index in range(5)
    ]

    first, cursor = service.page_jobs(limit=2)
    second, next_cursor = service.page_jobs(limit=2, cursor=cursor)
    third, final_cursor = service.page_jobs(limit=2, cursor=next_cursor)

    ids = [job.id for job in first + second + third]
    assert set(ids) == {job.id for job in created}
    assert len(ids) == len(set(ids))
    assert final_cursor is None
