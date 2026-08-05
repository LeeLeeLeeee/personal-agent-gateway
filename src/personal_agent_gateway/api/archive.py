from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.api.dependencies import (
    record_domain_audit,
    require_intake_open,
    session_dependency,
)
from personal_agent_gateway.archive import (
    ArchiveEntry,
    ArchiveRevision,
    KnowledgeRequest,
)
from personal_agent_gateway.auth_sessions import SessionPrincipal

router = APIRouter(prefix="/api/archive", tags=["archive"])


class ArchiveEntryRequest(BaseModel):
    kind: str
    title: str
    summary: str = ""
    content_markdown: str
    tags: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    persona_ids: list[str] = Field(default_factory=list)
    change_summary: str = ""
    request_id: str | None = None


class KnowledgeRequestStatusRequest(BaseModel):
    status: str


class DelegateKnowledgeRequest(BaseModel):
    team_run_id: str
    artifact_ids: list[str] = Field(default_factory=list)


@router.get("/entries")
def list_entries(
    request: Request,
    q: str = "",
    kind: str | None = None,
    status: str = "published",
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    try:
        entries = request.app.state.archive_service.list_entries(
            query=q,
            kind=kind,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"entries": [_entry_payload(entry) for entry in entries]}


@router.post("/entries")
def create_entry(
    request: Request,
    payload: ArchiveEntryRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    return _publish_entry(request, payload)


@router.get("/requests")
def list_requests(
    request: Request,
    status: str | None = None,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    try:
        requests = request.app.state.archive_service.list_requests(status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"requests": [_request_payload(item) for item in requests]}


@router.patch("/requests/{request_id}")
def update_request(
    request: Request,
    request_id: str,
    payload: KnowledgeRequestStatusRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        item = request.app.state.archive_service.update_request_status(
            request_id,
            payload.status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Knowledge request not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"request": _request_payload(item)}


@router.post("/requests/{request_id}/delegate")
async def delegate_request(
    request: Request,
    request_id: str,
    payload: DelegateKnowledgeRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        item = request.app.state.archive_service.get_request(request_id)
        if item.status not in {"open", "in_progress", "deferred"}:
            raise ValueError("Only an active Knowledge Request can be delegated")
        if (
            item.status == "in_progress"
            and item.assigned_team_run_id is not None
        ):
            if item.assigned_team_run_id != payload.team_run_id:
                raise ValueError(
                    "Knowledge Request is already assigned to another Team Run"
                )
            raise ValueError("Knowledge Request is already assigned to this Team Run")
        previous = request.app.state.team_cycle_service.latest_settled_cycle(
            payload.team_run_id
        )
        cycle_request = request.app.state.team_cycle_service.enqueue_knowledge_request(
            payload.team_run_id,
            request_id,
            "Prepare the delegated Knowledge Request as a Library review draft.",
            previous_cycle_id=previous.id if previous is not None else None,
        )
        request.app.state.team_cycle_service.set_request_input_artifacts(
            cycle_request.id,
            payload.artifact_ids,
        )
        item = request.app.state.archive_service.assign_request_team(
            request_id,
            payload.team_run_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Knowledge Request or Team Run not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish(
        {
            "type": "team.cycle_request.queued",
            "team_run_id": payload.team_run_id,
            "cycle_request_id": cycle_request.id,
            "source_type": "knowledge_request",
            "knowledge_request_id": request_id,
        }
    )
    await request.app.state.team_cycle_dispatcher.enqueue_run(payload.team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="archive.knowledge_request_delegated",
        action="archive.delegate_request",
        resource_type="knowledge_request",
        resource_id=request_id,
        team_run_id=payload.team_run_id,
        metadata={"cycle_request_id": cycle_request.id},
    )
    return {
        "request": _request_payload(item),
        "cycle_request": {
            "id": cycle_request.id,
            "team_run_id": cycle_request.team_run_id,
            "source_type": cycle_request.source_type,
            "source_id": cycle_request.source_id,
            "status": cycle_request.status,
            "created_at": cycle_request.created_at,
        },
    }


@router.get("/map")
def archive_map(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return request.app.state.archive_service.graph()


@router.get("/entries/{entry_id}")
def get_entry(
    request: Request,
    entry_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        entry = request.app.state.archive_service.get_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Archive entry not found") from exc
    return {"entry": _entry_payload(entry)}


@router.put("/entries/{entry_id}")
def update_entry(
    request: Request,
    entry_id: str,
    payload: ArchiveEntryRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    return _publish_entry(request, payload, entry_id=entry_id)


@router.post("/entries/{entry_id}/archive")
def archive_entry(
    request: Request,
    entry_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        entry = request.app.state.archive_service.archive_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Archive entry not found") from exc
    return {"entry": _entry_payload(entry)}


@router.delete("/entries/{entry_id}")
def delete_entry(
    request: Request,
    entry_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, str]:
    try:
        status = request.app.state.archive_service.delete_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Archive entry not found") from exc
    except ValueError as exc:
        # No condition raises this today. Kept so a future refusal returns 409
        # rather than a 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Draft deletion keeps its original event name so existing audit history stays
    # continuous; deleting a shared Library document is recorded separately.
    draft = status == "draft"
    record_domain_audit(
        request,
        principal,
        event_type="archive.draft_deleted" if draft else "archive.entry_deleted",
        action="archive.delete_draft" if draft else "archive.delete_entry",
        resource_type="archive_entry",
        resource_id=entry_id,
    )
    return {"deleted_id": entry_id}


@router.get("/entries/{entry_id}/revisions")
def list_revisions(
    request: Request,
    entry_id: str,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    try:
        revisions = request.app.state.archive_service.list_revisions(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Archive entry not found") from exc
    return {"revisions": [_revision_payload(revision) for revision in revisions]}


def _publish_entry(
    request: Request,
    payload: ArchiveEntryRequest,
    *,
    entry_id: str | None = None,
) -> dict[str, object]:
    try:
        entry = request.app.state.archive_service.publish_entry(
            actor_type="user",
            entry_id=entry_id,
            **payload.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"entry": _entry_payload(entry)}


def _entry_payload(entry: ArchiveEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "title": entry.title,
        "summary": entry.summary,
        "content_markdown": entry.content_markdown,
        "tags": entry.tags,
        "source_urls": entry.source_urls,
        "status": entry.status,
        "current_revision": entry.current_revision,
        "created_by": entry.created_by,
        "persona_ids": entry.persona_ids,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "origin_source_type": entry.origin_source_type,
        "origin_source_id": entry.origin_source_id,
        "origin_hook_id": entry.origin_hook_id,
        "origin_hook_run_id": entry.origin_hook_run_id,
        "origin_team_run_id": entry.origin_team_run_id,
        "origin_cycle_id": entry.origin_cycle_id,
        "origin_request_id": entry.origin_request_id,
    }


def _revision_payload(revision: ArchiveRevision) -> dict[str, object]:
    return {
        "id": revision.id,
        "entry_id": revision.entry_id,
        "revision": revision.revision,
        "kind": revision.kind,
        "title": revision.title,
        "summary": revision.summary,
        "content_markdown": revision.content_markdown,
        "tags": revision.tags,
        "source_urls": revision.source_urls,
        "change_summary": revision.change_summary,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


def _request_payload(item: KnowledgeRequest) -> dict[str, object]:
    return {
        "id": item.id,
        "title": item.title,
        "reason": item.reason,
        "suggested_outline": item.suggested_outline,
        "source_hints": item.source_hints,
        "requested_by_persona_id": item.requested_by_persona_id,
        "requested_by_persona_name": item.requested_by_persona_name,
        "session_id": item.session_id,
        "team_run_id": item.team_run_id,
        "assigned_team_run_id": item.assigned_team_run_id,
        "status": item.status,
        "fulfilled_by_entry_id": item.fulfilled_by_entry_id,
        "last_draft_error_code": item.last_draft_error_code,
        "last_draft_error_message": item.last_draft_error_message,
        "last_draft_failed_at": item.last_draft_failed_at,
        "last_draft_cycle_id": item.last_draft_cycle_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
