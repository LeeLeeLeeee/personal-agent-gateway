import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.config import AppConfig


router = APIRouter(prefix="/api/settings", tags=["settings"])


class AccessModeRequest(BaseModel):
    mode: Literal["restricted", "full_access"]
    confirm_full_access: bool = False


@router.get("")
def get_settings(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, dict[str, object]]:
    return {"settings": _settings_payload(request.app.state.app_config, request)}


@router.put("/access-mode")
def update_access_mode(
    request: Request,
    payload: AccessModeRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, str]:
    if payload.mode == "full_access" and not payload.confirm_full_access:
        raise HTTPException(status_code=400, detail="Full Access confirmation required")
    correlation_id = getattr(request.state, "correlation_id", None)
    request.app.state.audit_service.record(
        event_type="security.access_mode.change_requested",
        action="security.access_mode.change",
        status="observed",
        actor_type="owner",
        actor_id=principal.id,
        session_id=principal.id,
        correlation_id=correlation_id,
        resource_type="gateway",
        resource_id="access_mode",
        metadata={"mode": payload.mode},
    )
    mode = request.app.state.security_settings.set_access_mode(payload.mode)
    request.app.state.audit_service.record(
        event_type="security.access_mode.changed",
        action="security.access_mode.change",
        status="succeeded",
        actor_type="owner",
        actor_id=principal.id,
        session_id=principal.id,
        correlation_id=correlation_id,
        resource_type="gateway",
        resource_id="access_mode",
        metadata={"mode": mode},
    )
    return {"access_mode": mode}


def _settings_payload(config: AppConfig, request: Request) -> dict[str, object]:
    worker = request.app.state.job_worker
    scheduler = request.app.state.scheduler_loop
    automation_reasons: list[str] = []
    if not worker.alive:
        automation_reasons.append("Worker is not running")
    if not scheduler.alive:
        automation_reasons.append("Scheduler is not running")
    agent_availability = [
        {
            "id": agent.id,
            "available": agent.available,
            "error": agent.availability_error,
        }
        for agent in request.app.state.agent_registry.catalog()
    ]
    return {
        "workspace_root": str(config.workspace_root),
        "session_dir": str(config.session_dir),
        "artifact_root": str(config.artifact_root),
        "temp_dir": str(config.temp_dir),
        "provider": config.model_provider,
        "model": config.model,
        "codex_binary": config.codex_binary,
        "codex_sandbox": config.codex_sandbox,
        "codex_approval_policy": config.codex_approval_policy,
        "codex_timeout_seconds": config.codex_timeout_seconds,
        "codex_idle_timeout_seconds": config.codex_idle_timeout_seconds,
        "ffmpeg_binary": config.ffmpeg_binary,
        "ffprobe_binary": config.ffprobe_binary,
        "capture_binary": config.capture_binary,
        "job_worker_concurrency": config.job_worker_concurrency,
        "effective_job_concurrency": 1,
        "cookie_secure": config.cookie_secure,
        "totp_configured": request.app.state.auth_store.is_totp_enabled(),
        "session_authenticated": True,
        "bind_host": config.web_host,
        "tunnel_mode": "not_reported",
        "worker_alive": worker.alive,
        "worker_last_error": worker.last_error,
        "scheduler_alive": scheduler.alive,
        "scheduler_last_error": scheduler.last_error,
        "automation_ready": not automation_reasons,
        "automation_unavailable_reason": "; ".join(automation_reasons) or None,
        "team_review_supported": False,
        "team_execution_mode": "sequential",
        "agent_availability": agent_availability,
        "access_mode": request.app.state.security_settings.access_mode,
        "workspace_writable": config.workspace_root.is_dir()
        and os.access(config.workspace_root, os.W_OK),
        "active_session_count": len(
            request.app.state.auth_session_service.list_sessions()
        ),
        "audit_enabled": config.audit_enabled,
        "audit_retention_days": config.audit_retention_days,
        "schema_version": request.app.state.database.schema_version(),
    }
