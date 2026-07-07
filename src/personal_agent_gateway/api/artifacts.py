from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from personal_agent_gateway.artifacts import Artifact
from personal_agent_gateway.artifacts import ArtifactPathError
from personal_agent_gateway.api.jobs import session_dependency


router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("")
def list_artifacts(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {"artifacts": [_artifact_payload(item) for item in request.app.state.artifact_store.list()]}


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
