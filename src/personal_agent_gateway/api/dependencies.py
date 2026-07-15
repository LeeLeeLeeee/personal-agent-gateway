import logging
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, HTTPException, Request

from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.intake import IntakeClosedError


_AUDIT_LOGGER = logging.getLogger("personal_agent_gateway.audit")


def require_session(
    request: Request,
    session: Annotated[str | None, Cookie(alias="agent_session")] = None,
) -> SessionPrincipal:
    principal = request.app.state.auth_session_service.validate(session)
    if principal is None:
        raise HTTPException(status_code=401, detail="OTP login required")
    _require_same_origin(request)
    return principal


session_dependency = Depends(require_session)


def require_intake_open(request: Request) -> None:
    try:
        request.app.state.intake_gate.require_open()
    except IntakeClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _require_same_origin(request: Request) -> None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    parsed = urlsplit(origin)
    expected_host = request.headers.get("host", "").lower()
    if not parsed.scheme or parsed.netloc.lower() != expected_host:
        raise HTTPException(status_code=403, detail="Cross-origin request denied")
    if parsed.scheme.lower() != request.url.scheme.lower():
        raise HTTPException(status_code=403, detail="Cross-origin request denied")


def record_domain_audit(
    request: Request,
    principal: SessionPrincipal | None,
    *,
    event_type: str,
    action: str,
    resource_type: str,
    resource_id: str,
    status: str = "success",
    session_id: str | None = None,
    team_run_id: str | None = None,
    team_task_id: str | None = None,
    job_id: str | None = None,
    artifact_id: str | None = None,
    command_preview: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    try:
        request.app.state.audit_service.record(
            event_type=event_type,
            action=action,
            status=status,
            actor_type="owner",
            actor_id=getattr(principal, "id", None),
            session_id=session_id,
            team_run_id=team_run_id,
            team_task_id=team_task_id,
            job_id=job_id,
            artifact_id=artifact_id,
            correlation_id=getattr(request.state, "correlation_id", None),
            resource_type=resource_type,
            resource_id=resource_id,
            command_preview=command_preview,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        _AUDIT_LOGGER.exception(
            "Domain audit write failed",
            extra={"correlation_id": getattr(request.state, "correlation_id", None)},
        )
