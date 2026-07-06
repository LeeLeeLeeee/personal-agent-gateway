from fastapi import APIRouter, Request

from personal_agent_gateway.artifacts import Artifact
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
        "tags": artifact.tags,
        "metadata": artifact.metadata,
    }
