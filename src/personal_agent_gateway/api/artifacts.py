from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from personal_agent_gateway.artifacts import Artifact
from personal_agent_gateway.artifacts import ArtifactPathError
from personal_agent_gateway.api.jobs import session_dependency
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
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {"artifacts": [_artifact_payload(item) for item in request.app.state.artifact_store.list()]}


@router.post("/register")
def register_artifact(
    request: Request,
    payload: RegisterArtifactRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    # Localhost personal tool: any readable path the agent reports may be registered.
    # Relative paths resolve against the workspace; absolute paths resolve as-is.
    workspace_root = request.app.state.app_config.workspace_root.resolve()
    candidate = (workspace_root / payload.path).resolve()
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not is_registrable(candidate.name):
        raise HTTPException(status_code=415, detail="Unsupported file type")
    artifact = request.app.state.artifact_store.register_existing_file(
        artifact_type=artifact_type_for(candidate.name),
        title=payload.title or candidate.name,
        source_path=candidate,
        relative_path=f"files/{uuid4().hex[:8]}/{candidate.name}",
        mime_type=mime_type_for(candidate.name),
        source_session_id=payload.session_id,
    )
    return {"artifact": _artifact_payload(artifact)}


@router.get("/{artifact_id}")
def get_artifact(
    request: Request,
    artifact_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"artifact": _artifact_payload(request.app.state.artifact_store.get(artifact_id))}


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
