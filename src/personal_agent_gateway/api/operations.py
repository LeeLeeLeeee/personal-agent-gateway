import os

from fastapi import APIRouter, HTTPException, Request

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.backup import BackupError, BackupRecord


router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("")
def operations_status(
    request: Request,
    _session: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    items = [
        *_session_items(request),
        *_team_run_items(request),
        *_job_items(request),
        *_schedule_items(request),
    ]
    return {
        "intake_open": request.app.state.intake_gate.is_open,
        "access_mode": request.app.state.security_settings.access_mode,
        "diagnostics": {
            "bind_host": request.app.state.app_config.web_host,
            "cookie_secure": request.app.state.app_config.cookie_secure,
            "tunnel_mode": "not_reported",
            "workspace_writable": request.app.state.app_config.workspace_root.is_dir()
            and os.access(request.app.state.app_config.workspace_root, os.W_OK),
        },
        "health": [
            component.payload()
            for component in request.app.state.health_service.components()
        ],
        "items": items,
        "counts": {
            status: sum(item["status"] == status for item in items)
            for status in {
                "running",
                "waiting_approval",
                "interrupted",
                "failed",
                "canceled",
                "queued",
                "scheduled",
                "paused",
            }
        },
        "backups": [
            _backup_payload(backup)
            for backup in request.app.state.backup_service.list_backups()
        ],
    }


@router.post("/emergency-stop")
async def emergency_stop(
    request: Request,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    result = await request.app.state.emergency_stop_service.stop(
        actor_id=principal.id,
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    return {
        "stopped_at": result.stopped_at,
        "intake_open": False,
        "changed": result.changed,
        "canceled": {
            "sessions": result.session_ids,
            "team_runs": result.team_run_ids,
            "jobs": result.job_ids,
            "hook_runs": result.hook_run_ids,
        },
        "failures": result.failures,
    }


@router.post("/resume-intake")
def resume_intake(
    request: Request,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, bool]:
    changed = request.app.state.emergency_stop_service.resume(
        actor_id=principal.id,
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    return {"intake_open": True, "changed": changed}


@router.get("/backups")
def list_backups(
    request: Request,
    _session: SessionPrincipal = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {
        "backups": [
            _backup_payload(backup)
            for backup in request.app.state.backup_service.list_backups()
        ]
    }


@router.post("/backups")
def create_backup(
    request: Request,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        backup = request.app.state.backup_service.create_backup()
    except (BackupError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Backup creation failed") from exc
    request.app.state.audit_service.record(
        event_type="operations.backup_created",
        action="operations.backup_create",
        status="success",
        actor_type="owner",
        actor_id=principal.id,
        correlation_id=getattr(request.state, "correlation_id", None),
        resource_type="backup",
        resource_id=backup.id,
        metadata={
            "schema_version": backup.schema_version,
            "database_size_bytes": backup.database_size_bytes,
        },
    )
    return {"backup": _backup_payload(backup)}


@router.post("/backups/{backup_id}/dry-run")
def verify_backup(
    request: Request,
    backup_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        result = request.app.state.backup_service.dry_run(backup_id)
    except BackupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request.app.state.audit_service.record(
        event_type="operations.backup_verified",
        action="operations.backup_dry_run",
        status="success",
        actor_type="owner",
        actor_id=principal.id,
        correlation_id=getattr(request.state, "correlation_id", None),
        resource_type="backup",
        resource_id=backup_id,
        metadata={"schema_version": result.schema_version},
    )
    return {
        "backup_id": result.backup_id,
        "valid": result.valid,
        "schema_version": result.schema_version,
        "database_sha256": result.database_sha256,
        "profile": result.profile,
        "recoverability": result.recoverability,
        "warnings": result.warnings,
        "missing_hook_connection_refs": result.missing_hook_connection_refs,
    }


def _backup_payload(backup: BackupRecord) -> dict[str, object]:
    return {
        "id": backup.id,
        "created_at": backup.created_at,
        "schema_version": backup.schema_version,
        "database_sha256": backup.database_sha256,
        "database_size_bytes": backup.database_size_bytes,
        "profile": backup.profile,
        "recoverability": backup.recoverability,
    }


def _session_items(request: Request) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for session in request.app.state.transcript_store.list_sessions(origin="chat"):
        status = (
            "running"
            if request.app.state.run_registry.is_running(session.id)
            else session.status
        )
        if status == "idle":
            continue
        items.append(
            {
                "domain": "session",
                "id": session.id,
                "title": session.title,
                "status": status,
                "updated_at": session.updated_at.isoformat(),
                "retryable": False,
                "resumable": False,
                "target": {"screen": "chat", "session_id": session.id},
            }
        )
    return items


def _team_run_items(request: Request) -> list[dict[str, object]]:
    visible = {"planning", "running", "summarizing", "interrupted", "failed"}
    items: list[dict[str, object]] = []
    for run in request.app.state.team_run_service.list_team_runs_enriched():
        status = str(run["status"])
        if status not in visible:
            continue
        items.append(
            {
                "domain": "team_run",
                "id": run["id"],
                "title": run["goal"],
                "status": status,
                "updated_at": run["updated_at"],
                "retryable": False,
                "resumable": status == "interrupted",
                "target": {"screen": "teams", "team_run_id": run["id"]},
            }
        )
    return items


def _job_items(request: Request) -> list[dict[str, object]]:
    visible = {"waiting_approval", "queued", "running", "failed", "canceled"}
    return [
        {
            "domain": "job",
            "id": job.id,
            "title": job.title,
            "status": job.status,
            "updated_at": job.finished_at or job.started_at or job.created_at,
            "retryable": job.source != "chat" and job.status in {"failed", "canceled"},
            "resumable": False,
            "target": {"screen": "jobs", "job_id": job.id},
        }
        for job in request.app.state.job_service.list_jobs()
        if job.status in visible
    ]


def _schedule_items(request: Request) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for schedule in request.app.state.schedule_service.list_schedules():
        last_job = None
        if schedule.last_run_job_id is not None:
            try:
                last_job = request.app.state.job_service.get_job(schedule.last_run_job_id)
            except KeyError:
                last_job = None
        status = (
            "failed"
            if last_job is not None and last_job.status == "failed"
            else "scheduled" if schedule.enabled else "paused"
        )
        items.append(
            {
                "domain": "schedule",
                "id": schedule.id,
                "title": schedule.name,
                "status": status,
                "updated_at": (
                    schedule.last_run_at or schedule.next_run_at
                ).isoformat(),
                "retryable": False,
                "resumable": not schedule.enabled,
                "target": {
                    "screen": "schedules",
                    "schedule_id": schedule.id,
                },
            }
        )
    return items
