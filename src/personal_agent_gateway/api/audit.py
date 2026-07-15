from fastapi import APIRouter, HTTPException, Query, Request

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.audit import AuditEvent


router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events")
def list_audit_events(
    request: Request,
    event_type: str | None = None,
    severity: str | None = None,
    actor_id: str | None = None,
    resource_type: str | None = None,
    correlation_id: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        events, next_cursor = request.app.state.audit_service.page(
            event_type=event_type,
            severity=severity,
            actor_id=actor_id,
            resource_type=resource_type,
            correlation_id=correlation_id,
            since=since,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "events": [_payload(event) for event in events],
        "next_cursor": next_cursor,
    }


def _payload(event: AuditEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "occurred_at": event.occurred_at,
        "event_type": event.event_type,
        "severity": event.severity,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "session_id": event.session_id,
        "team_run_id": event.team_run_id,
        "team_task_id": event.team_task_id,
        "job_id": event.job_id,
        "artifact_id": event.artifact_id,
        "correlation_id": event.correlation_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "status": event.status,
        "command_preview": event.command_preview,
        "metadata": event.metadata,
        "redaction_version": event.redaction_version,
    }
