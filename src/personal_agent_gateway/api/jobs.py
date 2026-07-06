from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.jobs import Job


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    capability_id: str
    title: str
    input_json: dict[str, object] = Field(alias="input")


def require_session(
    session: Annotated[str | None, Cookie(alias="agent_session")] = None,
) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_jobs(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {"jobs": [_job_payload(job) for job in request.app.state.job_service.list_jobs()]}


@router.post("")
async def create_job(
    request: Request,
    payload: CreateJobRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    job = request.app.state.job_service.create_job(
        capability_id=payload.capability_id,
        source="manual",
        title=payload.title,
        input_json=payload.input_json,
    )
    if job.status == "queued":
        await request.app.state.job_worker.enqueue(job.id)
    return {"job": _job_payload(job)}


@router.get("/{job_id}")
def get_job(
    request: Request,
    job_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"job": _job_payload(request.app.state.job_service.get_job(job_id))}


@router.post("/{job_id}/approve")
async def approve_job(
    request: Request,
    job_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    job = request.app.state.job_service.approve_job(job_id)
    await request.app.state.job_worker.enqueue(job.id)
    return {"job": _job_payload(job)}


@router.post("/{job_id}/deny")
def deny_job(
    request: Request,
    job_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"job": _job_payload(request.app.state.job_service.deny_job(job_id))}


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
        "error_message": job.error_message,
    }
