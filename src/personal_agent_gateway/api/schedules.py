from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.api.jobs import _job_payload, session_dependency
from personal_agent_gateway.schedules import Schedule


router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class CreateScheduleRequest(BaseModel):
    name: str
    capability_id: str
    cron_expression: str
    timezone: str
    input_template: dict[str, object] = Field(default_factory=dict)


@router.get("")
def list_schedules(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {
        "schedules": [
            _schedule_payload(schedule)
            for schedule in request.app.state.schedule_service.list_schedules()
        ]
    }


@router.post("")
def create_schedule(
    request: Request,
    payload: CreateScheduleRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    schedule = request.app.state.schedule_service.create_schedule(
        name=payload.name,
        capability_id=payload.capability_id,
        cron_expression=payload.cron_expression,
        timezone_name=payload.timezone,
        input_template_json=payload.input_template,
    )
    return {"schedule": _schedule_payload(schedule)}


@router.post("/{schedule_id}/pause")
def pause_schedule(
    request: Request,
    schedule_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        schedule = request.app.state.schedule_service.pause(schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    return {"schedule": _schedule_payload(schedule)}


@router.post("/{schedule_id}/resume")
def resume_schedule(
    request: Request,
    schedule_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        schedule = request.app.state.schedule_service.resume(schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    return {"schedule": _schedule_payload(schedule)}


@router.post("/{schedule_id}/run-now")
async def run_schedule_now(
    request: Request,
    schedule_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        job = request.app.state.schedule_service.run_now(
            schedule_id,
            request.app.state.job_service,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    if job.status == "queued":
        await request.app.state.job_worker.enqueue(job.id)
    return {"job": _job_payload(job)}


@router.delete("/{schedule_id}")
def delete_schedule(
    request: Request,
    schedule_id: str,
    _session: None = session_dependency,
) -> dict[str, bool]:
    try:
        request.app.state.schedule_service.delete(schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    return {"deleted": True}


def _schedule_payload(schedule: Schedule) -> dict[str, object]:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "capability_id": schedule.capability_id,
        "cron_expression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "input_template": schedule.input_template_json,
        "enabled": schedule.enabled,
        "last_run_job_id": schedule.last_run_job_id,
        "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "next_run_at": schedule.next_run_at.isoformat(),
    }
