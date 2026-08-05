from uuid import uuid4
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from personal_agent_gateway.artifacts import Artifact
from personal_agent_gateway.artifacts import ArtifactInUseError
from personal_agent_gateway.artifacts import ArtifactPathError
from personal_agent_gateway.api.dependencies import record_domain_audit, session_dependency
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.artifact_types import (
    artifact_type_for,
    is_registrable,
    mime_type_for,
)


router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class RegisterArtifactRequest(BaseModel):
    path: str
    session_id: str | None = None
    title: str | None = None


class CleanupArtifactsRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1)


class DeleteArtifactsRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=200)


class UpdateArtifactRetentionRequest(BaseModel):
    retention_class: str
    expires_at: str | None = None


@router.get("")
def list_artifacts(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        artifacts, next_cursor = request.app.state.artifact_store.page(
            limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "artifacts": [_artifact_payload(item) for item in artifacts],
        "next_cursor": next_cursor,
    }


@router.get("/cleanup-preview")
def cleanup_preview(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, object]:
    preview = request.app.state.artifact_store.cleanup_preview(datetime.now(timezone.utc))
    return {
        "artifacts": [_artifact_payload(item) for item in preview.artifacts],
        "artifact_ids": [item.id for item in preview.artifacts],
        "total_size_bytes": preview.total_size_bytes,
        "evaluated_at": preview.evaluated_at.isoformat(),
    }


@router.get("/browser")
def browser_artifacts(
    request: Request,
    segment: str = "saved",
    q: str = "",
    file_kind: str | None = None,
    source_kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        page = request.app.state.artifact_store.browser_page(
            segment=segment,
            query=q,
            file_kind=file_kind,
            source_kind=source_kind,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": [
            {
                "artifact": _artifact_payload(item.artifact),
                "role": {
                    "code": item.role,
                    "label": item.role.replace("_", " ").title(),
                },
                "source_kind": item.source_kind,
                "breadcrumbs": [
                    {"kind": crumb.kind, "id": crumb.id, "label": crumb.label}
                    for crumb in item.breadcrumbs
                ],
                "deletion": {"allowed": item.deletion_allowed},
            }
            for item in page.items
        ],
        "counts": page.counts,
        "next_cursor": page.next_cursor,
    }


@router.post("/delete")
def delete_artifacts(
    request: Request,
    payload: DeleteArtifactsRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    if len(set(payload.artifact_ids)) != len(payload.artifact_ids):
        raise HTTPException(status_code=422, detail="Artifact IDs must be unique")
    result = request.app.state.artifact_store.delete_many(payload.artifact_ids)
    for artifact_id in result.deleted_ids:
        record_domain_audit(
            request,
            principal,
            event_type="artifact.deleted",
            action="artifact.delete",
            resource_type="artifact",
            resource_id=artifact_id,
            artifact_id=artifact_id,
        )
    return {
        "deleted_ids": list(result.deleted_ids),
        "blocked": [
            {
                "artifact_id": item.artifact_id,
                "code": "artifact_in_use",
                "references": [
                    {"kind": reference.kind, "id": reference.id, "label": reference.label}
                    for reference in item.references
                ],
            }
            for item in result.blocked
        ],
        "missing_ids": list(result.missing_ids),
    }


@router.post("/cleanup")
def cleanup_artifacts(
    request: Request,
    payload: CleanupArtifactsRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    result = request.app.state.artifact_store.cleanup(
        payload.artifact_ids, datetime.now(timezone.utc)
    )
    record_domain_audit(
        request,
        principal,
        event_type="artifact.cleanup_executed",
        action="artifacts.cleanup",
        resource_type="artifact_cleanup",
        resource_id="manual",
        metadata={
            "requested_ids": payload.artifact_ids,
            "deleted_ids": list(result.deleted_ids),
            "skipped_ids": list(result.skipped_ids),
        },
    )
    return {"deleted_ids": list(result.deleted_ids), "skipped_ids": list(result.skipped_ids)}


@router.post("/register")
def register_artifact(
    request: Request,
    payload: RegisterArtifactRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    workspace_root = request.app.state.app_config.workspace_root.resolve()
    candidate = (workspace_root / payload.path).resolve()
    outside_workspace = not candidate.is_relative_to(workspace_root)
    if outside_workspace and request.app.state.security_settings.access_mode == "restricted":
        raise HTTPException(
            status_code=403,
            detail="Restricted mode blocks files outside the workspace",
        )
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not is_registrable(candidate.name):
        raise HTTPException(status_code=415, detail="Unsupported file type")
    source_path = str(candidate)
    # Dedup is find-then-register (not atomic); acceptable for this single-user localhost tool.
    existing = request.app.state.artifact_store.find_by_source_path(source_path)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Already registered", "artifact": _artifact_payload(existing)},
        )
    artifact = request.app.state.artifact_store.register_existing_file(
        artifact_type=artifact_type_for(candidate.name),
        title=payload.title or candidate.name,
        source_path=candidate,
        relative_path=f"files/{uuid4().hex[:8]}/{candidate.name}",
        mime_type=mime_type_for(candidate.name),
        source_session_id=payload.session_id,
        metadata={"source_path": source_path, "original_path": payload.path},
        origin_kind="chat_upload" if payload.session_id else "manual_upload",
        artifact_role="attachment",
        origin_group_label_snapshot="Chat files" if payload.session_id else "Local files",
        origin_item_label_snapshot=payload.title or candidate.name,
    )
    if outside_workspace:
        request.app.state.audit_service.record(
            event_type="artifact.external_path.registered",
            action="artifact.register",
            status="succeeded",
            actor_type="owner",
            actor_id=principal.id,
            session_id=principal.id,
            artifact_id=artifact.id,
            correlation_id=getattr(request.state, "correlation_id", None),
            resource_type="artifact",
            resource_id=artifact.id,
            metadata={"outside_workspace": True},
        )
    return {"artifact": _artifact_payload(artifact)}


@router.get("/{artifact_id}")
def get_artifact(
    request: Request,
    artifact_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        return {"artifact": _artifact_payload(request.app.state.artifact_store.get(artifact_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc


@router.delete("/{artifact_id}")
def delete_artifact(
    request: Request,
    artifact_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        request.app.state.artifact_store.delete(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except ArtifactInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="artifact.deleted",
        action="artifact.delete",
        resource_type="artifact",
        resource_id=artifact_id,
        artifact_id=artifact_id,
    )
    return {"deleted": True}


@router.patch("/{artifact_id}/retention")
def update_artifact_retention(
    request: Request,
    artifact_id: str,
    payload: UpdateArtifactRetentionRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        expires_at = (
            datetime.fromisoformat(payload.expires_at) if payload.expires_at else None
        )
        artifact = request.app.state.artifact_store.set_retention(
            artifact_id, payload.retention_class, expires_at
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="artifact.retention_updated",
        action="artifacts.retention_update",
        resource_type="artifact",
        resource_id=artifact.id,
        artifact_id=artifact.id,
        metadata={
            "retention_class": artifact.retention_class,
            "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
        },
    )
    return {"artifact": _artifact_payload(artifact)}


@router.get("/{artifact_id}/content")
def get_artifact_content(
    request: Request,
    artifact_id: str,
    _session: None = session_dependency,
) -> FileResponse:
    try:
        artifact = request.app.state.artifact_store.get(artifact_id)
        path = request.app.state.artifact_store.content_path(artifact_id)
    except (ArtifactPathError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.title)


@router.get("/{artifact_id}/thumbnail")
def get_artifact_thumbnail(
    request: Request,
    artifact_id: str,
    _session: None = session_dependency,
) -> FileResponse:
    try:
        artifact = request.app.state.artifact_store.get(artifact_id)
        path = request.app.state.artifact_store.thumbnail_path(artifact_id)
        if path is None:
            if not artifact.mime_type.startswith("image/"):
                raise HTTPException(status_code=404, detail="Thumbnail not found")
            path = request.app.state.artifact_store.content_path(artifact_id)
    except (ArtifactPathError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.title)


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": artifact.id,
        "type": artifact.type,
        "title": artifact.title,
        "relative_path": artifact.relative_path,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "source_job_id": artifact.source_job_id,
        "source_session_id": artifact.source_session_id,
        "created_at": artifact.created_at.isoformat(),
        "retention_class": artifact.retention_class,
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
        "thumbnail_path": str(artifact.thumbnail_path) if artifact.thumbnail_path else None,
        "tags": artifact.tags,
        "metadata": artifact.metadata,
    }
