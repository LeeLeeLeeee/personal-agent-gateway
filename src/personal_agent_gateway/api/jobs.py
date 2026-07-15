from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.api.dependencies import (
    record_domain_audit,
    require_intake_open,
    session_dependency,
)
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.jobs import Job, JobEvent, JobStatusError


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    capability_id: str
    title: str
    input_json: dict[str, object] = Field(alias="input")


@router.get("")
def list_jobs(
    request: Request,
    status: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    capability_id: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        jobs, next_cursor = request.app.state.job_service.page_jobs(
            statuses=status,
            sources=source,
            capability_ids=capability_id,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "jobs": [_job_payload(job) for job in jobs],
        "next_cursor": next_cursor,
    }


@router.post("")
async def create_job(
    request: Request,
    payload: CreateJobRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    job = request.app.state.job_service.create_job(
        capability_id=payload.capability_id,
        source="manual",
        title=payload.title,
        input_json=payload.input_json,
    )
    if job.status == "queued":
        await request.app.state.job_worker.enqueue(job.id)
    record_domain_audit(
        request,
        principal,
        event_type="automation.job_created",
        action="jobs.create",
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        command_preview=job.command_preview,
        metadata={"status": job.status, "source": job.source},
    )
    return {"job": _job_payload(job)}


@router.get("/{job_id}")
def get_job(
    request: Request,
    job_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"job": _job_payload(request.app.state.job_service.get_job(job_id))}


@router.get("/{job_id}/events")
def list_job_events(
    request: Request,
    job_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        events, next_cursor = request.app.state.job_service.page_events(
            job_id, limit=limit, cursor=cursor
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "events": [_event_payload(event) for event in events],
        "next_cursor": next_cursor,
    }


@router.post("/{job_id}/approve")
async def approve_job(
    request: Request,
    job_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        job = request.app.state.job_service.approve_job(job_id)
    except JobStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.job_worker.enqueue(job.id)
    record_domain_audit(
        request,
        principal,
        event_type="automation.job_approved",
        action="jobs.approve",
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        command_preview=job.command_preview,
    )
    return {"job": _job_payload(job)}


@router.post("/{job_id}/deny")
def deny_job(
    request: Request,
    job_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        job = request.app.state.job_service.deny_job(job_id)
    except JobStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="automation.job_denied",
        action="jobs.deny",
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        command_preview=job.command_preview,
    )
    return {"job": _job_payload(job)}


@router.post("/{job_id}/retry")
async def retry_job(
    request: Request,
    job_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        job = request.app.state.job_service.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except JobStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job.status == "queued":
        await request.app.state.job_worker.enqueue(job.id)
    record_domain_audit(
        request,
        principal,
        event_type="automation.job_retried",
        action="jobs.retry",
        job_id=job.id,
        resource_type="job",
        resource_id=job.id,
        command_preview=job.command_preview,
        metadata={"source_job_id": job.source_job_id, "status": job.status},
    )
    return {"job": _job_payload(job)}


def _job_payload(job: Job) -> dict[str, object]:
    return {
        "id": job.id,
        "capability_id": job.capability_id,
        "source": job.source,
        "title": job.title,
        "status": job.status,
        "input": job.input_json,
        "command_preview": job.command_preview,
        "approval_id": job.approval_id,
        "source_session_id": job.source_session_id,
        "source_schedule_id": job.source_schedule_id,
        "source_job_id": job.source_job_id,
        "retryable": job.source != "chat" and job.status in {"failed", "canceled"},
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_message": job.error_message,
    }


def _event_payload(event: JobEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at,
    }
