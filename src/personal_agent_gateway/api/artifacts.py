from uuid import uuid4

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from personal_agent_gateway.artifacts import Artifact
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
        "thumbnail_path": str(artifact.thumbnail_path) if artifact.thumbnail_path else None,
        "tags": artifact.tags,
        "metadata": artifact.metadata,
    }
