import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from personal_agent_gateway.api.dependencies import (
    record_domain_audit,
    require_intake_open,
    session_dependency,
)
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.pagination import decode_cursor, encode_cursor
from personal_agent_gateway.teams import TeamAgent, TeamMessage, TeamRun, TeamTask


router = APIRouter(prefix="/api/team-runs", tags=["team-runs"])

_ACTIVE = {"planning", "running", "summarizing"}
_TERMINAL = {"completed", "completed_with_failures", "failed", "canceled"}


class CreateTeamRunRequest(BaseModel):
    team_id: str
    goal: str
    run_mode: Literal["planning_only", "plan_and_execute"] = "planning_only"
    max_workers: Literal[1] = 1


class AddWorkRequest(BaseModel):
    instruction: str


@router.get("")
def list_team_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        runs, next_cursor = request.app.state.team_run_service.page_team_runs_enriched(
            limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {"team_runs": runs, "next_cursor": next_cursor}


@router.post("")
def create_team_run(
    request: Request,
    payload: CreateTeamRunRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.create_team_run_from_team(
            request.app.state.team_directory_service,
            request.app.state.rule_set_service,
            team_id=payload.team_id,
            goal=payload.goal,
            run_mode=payload.run_mode,
            max_workers=payload.max_workers,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_created",
        action="team_runs.create",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={"run_mode": run.run_mode, "team_id": run.team_id},
    )
    return {"team_run": _team_run_payload(run)}


@router.get("/{team_run_id}")
def get_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"team_run": _team_run_payload(run)}


@router.get("/{team_run_id}/detail")
def get_team_run_detail(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    _session: None = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    try:
        run = service.get_team_run(team_run_id)
        agents = service.list_agents(team_run_id)
        tasks = service.list_tasks(team_run_id)
        messages = service.list_messages(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    selected_tasks = tasks[-limit:]
    selected_messages = messages[-limit:]
    return {
        "team_run": _team_run_payload(run),
        "agents": [_agent_payload(agent) for agent in agents],
        "tasks": [_task_payload(task) for task in selected_tasks],
        "messages": [_message_payload(message) for message in selected_messages],
        "document_summary": _document_summary(_resolved_workspace(run)),
        "truncated": {
            "tasks": len(tasks) > len(selected_tasks),
            "messages": len(messages) > len(selected_messages),
        },
    }


@router.post("/{team_run_id}/start")
async def start_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id) or run.status in _ACTIVE:
        raise HTTPException(status_code=409, detail="Team run already running")
    if run.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft team runs can be started")

    async def _run_and_finish() -> None:
        try:
            await runtime.start(team_run_id)
        finally:
            registry.finish(team_run_id)

    task = asyncio.create_task(_run_and_finish())
    registry.register(team_run_id, task)
    record_domain_audit(
        request,
        principal,
        event_type="team.run_start_requested",
        action="team_runs.start",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/resume")
async def resume_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id):
        raise HTTPException(status_code=409, detail="Team run already running")
    if run.status != "interrupted":
        raise HTTPException(status_code=409, detail="Only interrupted team runs can be resumed")

    async def _resume_and_finish() -> None:
        try:
            await runtime.resume(team_run_id)
        finally:
            registry.finish(team_run_id)

    task = asyncio.create_task(_resume_and_finish())
    registry.register(team_run_id, task)
    record_domain_audit(
        request,
        principal,
        event_type="team.run_resume_requested",
        action="team_runs.resume",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/tasks/{task_id}/retry")
async def retry_team_task(
    request: Request,
    team_run_id: str,
    task_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    if registry.is_running(team_run_id):
        raise HTTPException(status_code=409, detail="Team run already running")
    try:
        run, task = service.retry_failed_task(team_run_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run or task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish(
        {
            "type": "team.task.updated",
            "team_run_id": team_run_id,
            "task_id": task_id,
            "task": _task_payload(task),
        }
    )
    record_domain_audit(
        request,
        principal,
        event_type="team.task_retry_requested",
        action="team_runs.retry_task",
        resource_type="team_task",
        resource_id=task_id,
        team_run_id=team_run_id,
        team_task_id=task_id,
        metadata={"status": task.status},
    )
    return {"team_run": _team_run_payload(run), "task": _task_payload(task)}


@router.post("/{team_run_id}/add-work")
async def add_work(
    request: Request,
    team_run_id: str,
    payload: AddWorkRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if run.run_mode != "plan_and_execute":
        raise HTTPException(status_code=409, detail="Additional work is only supported for plan_and_execute runs")
    if run.status == "draft":
        raise HTTPException(status_code=409, detail="Start the run before adding work")
    if run.status == "interrupted":
        raise HTTPException(status_code=409, detail="Resume the run before adding work")

    await runtime.add_work(team_run_id, payload.instruction)

    run = service.get_team_run(team_run_id)
    if run.status in _TERMINAL and not registry.is_running(team_run_id):
        async def _resume_and_finish() -> None:
            try:
                await runtime.resume(team_run_id)
            finally:
                registry.finish(team_run_id)

        task = asyncio.create_task(_resume_and_finish())
        registry.register(team_run_id, task)

    record_domain_audit(
        request,
        principal,
        event_type="team.additional_work_requested",
        action="team_runs.add_work",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
        metadata={"instruction_length": len(payload.instruction)},
    )
    return {"team_run": _team_run_payload(service.get_team_run(team_run_id))}


@router.post("/{team_run_id}/cancel")
async def cancel_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if await registry.cancel_and_wait(team_run_id):
        run = service.get_team_run(team_run_id)
    elif run.status not in _TERMINAL:
        run = service.set_run_status(team_run_id, "canceled")
    record_domain_audit(
        request,
        principal,
        event_type="team.run_canceled",
        action="team_runs.cancel",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run)}


@router.delete("/{team_run_id}")
def delete_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id) or run.status in _ACTIVE:
        raise HTTPException(status_code=409, detail="Running team runs cannot be deleted")
    try:
        service.delete_team_run(team_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_deleted",
        action="team_runs.delete",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
        metadata={"workspace_deleted": True},
    )
    return {"deleted": True}


@router.get("/{team_run_id}/agents")
def list_team_agents(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        agents = request.app.state.team_run_service.list_agents(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(agents, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "agents": [_agent_payload(agent) for agent in selected],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/tasks")
def list_team_tasks(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        tasks = request.app.state.team_run_service.list_tasks(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(tasks, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "tasks": [_task_payload(task) for task in selected],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/messages")
def list_team_messages(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        messages = request.app.state.team_run_service.list_messages(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(messages, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "messages": [_message_payload(message) for message in selected],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/documents")
def list_team_documents(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    documents = _document_items(root)
    if cursor:
        try:
            values = decode_cursor(cursor, 2)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        if not isinstance(values[0], str) or not isinstance(values[1], str):
            raise HTTPException(status_code=400, detail="Invalid cursor")
        documents = [
            item for item in documents
            if (str(item["modified_at"]), str(item["path"])) < (values[0], values[1])
        ]
    selected = documents[: limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    next_cursor = None
    if has_more and selected:
        next_cursor = encode_cursor(selected[-1]["modified_at"], selected[-1]["path"])
    return {"documents": selected, "next_cursor": next_cursor}


@router.get("/{team_run_id}/documents/content")
def read_team_document(request: Request, team_run_id: str, path: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    try:
        target = _safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_visible_document(root, target):
        raise HTTPException(status_code=404, detail="Document not found")
    kind = _doc_kind(target)
    size = target.stat().st_size
    if kind == "image":
        encoded_run_id = quote(team_run_id, safe="")
        encoded_path = quote(path, safe="")
        return {
            "path": path,
            "kind": kind,
            "content": None,
            "previewable": True,
            "preview_url": (
                f"/api/team-runs/{encoded_run_id}/documents/image?path={encoded_path}"
            ),
        }
    if kind == "binary" or _is_sensitive_document(target.name) or size > _MAX_PREVIEW_BYTES:
        return {"path": path, "kind": kind, "content": None, "previewable": False,
                "reason": "binary or too large"}
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": path, "kind": "binary", "content": None, "previewable": False,
                "reason": "not utf-8 text"}
    return {"path": path, "kind": kind, "content": content, "previewable": True}


@router.get("/{team_run_id}/documents/image")
def read_team_document_image(
    request: Request, team_run_id: str, path: str, _session: None = session_dependency
) -> FileResponse:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    try:
        target = _safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_visible_document(root, target):
        raise HTTPException(status_code=404, detail="Document not found")
    media_type = _IMAGE_MIME_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=415, detail="Unsupported image type")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


_TEXT_EXTS = {".txt", ".log", ".csv", ".yaml", ".yml", ".toml", ".ini"}
_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sh", ".sql"}
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_IGNORED_DOCUMENT_DIRS = {
    ".git", ".hg", ".svn", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "__pycache__", "node_modules", "dist", "build", "coverage",
}
_MAX_PREVIEW_BYTES = 1_000_000


def _doc_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
    if suffix == ".html":
        return "html"
    if suffix in _IMAGE_MIME_TYPES:
        return "image"
    if suffix in _TEXT_EXTS:
        return "text"
    if suffix in _CODE_EXTS:
        return "code"
    return "binary"


def _resolved_workspace(run) -> Path:
    return Path(run.workspace_root).resolve()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes workspace")
    return candidate


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _is_sensitive_document(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.")


def _is_visible_document(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return (
        not _is_sensitive_document(relative.name)
        and all(part.lower() not in _IGNORED_DOCUMENT_DIRS for part in relative.parts[:-1])
    )


def _document_items(root: Path) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    if not root.is_dir():
        return documents
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name.lower() not in _IGNORED_DOCUMENT_DIRS]
        for name in files:
            if _is_sensitive_document(name):
                continue
            walked_path = Path(current) / name
            relative = walked_path.relative_to(root).as_posix()
            try:
                path = _safe_child(root, relative)
            except ValueError:
                continue
            kind = _doc_kind(path)
            if kind == "binary":
                continue
            size = path.stat().st_size
            if kind != "image":
                if size > _MAX_PREVIEW_BYTES:
                    continue
                try:
                    path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
            documents.append(
                {
                    "path": relative,
                    "size": size,
                    "modified_at": _iso_mtime(path),
                    "kind": kind,
                    "previewable": True,
                }
            )
    return sorted(
        documents,
        key=lambda item: (str(item["modified_at"]), str(item["path"])),
        reverse=True,
    )


def _document_summary(root: Path) -> dict[str, object]:
    count = 0
    size_bytes = 0
    kinds: dict[str, int] = {}
    for item in _document_items(root):
        count += 1
        size_bytes += int(item["size"])
        kind = str(item["kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"count": count, "size_bytes": size_bytes, "kinds": kinds}


def _page_entities(items: list, limit: int, cursor: str | None) -> tuple[list, str | None]:
    selected_items = sorted(items, key=lambda item: (item.created_at, item.id))
    if cursor:
        created_at, entity_id = decode_cursor(cursor, 2)
        if not isinstance(created_at, str) or not isinstance(entity_id, str):
            raise ValueError("Invalid cursor")
        selected_items = [
            item
            for item in selected_items
            if (item.created_at, item.id) > (created_at, entity_id)
        ]
    selected = selected_items[: limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    next_cursor = None
    if has_more and selected:
        next_cursor = encode_cursor(selected[-1].created_at, selected[-1].id)
    return selected, next_cursor


def _team_run_payload(run: TeamRun) -> dict[str, object]:
    return {
        "id": run.id,
        "goal": run.goal,
        "status": run.status,
        "run_mode": run.run_mode,
        "leader_agent_id": run.leader_agent_id,
        "max_workers": 1,
        "configured_max_workers": run.max_workers,
        "execution_mode": "sequential",
        "workspace_root": run.workspace_root,
        "team_id": run.team_id,
        "rules_snapshot": run.rules_snapshot,
        "summary": run.summary,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _agent_payload(agent: TeamAgent) -> dict[str, object]:
    return {
        "id": agent.id,
        "team_run_id": agent.team_run_id,
        "name": agent.name,
        "role": agent.role,
        "persona_id": agent.persona_id,
        "persona_snapshot": agent.persona_snapshot,
        "backend": agent.backend,
        "model": agent.model,
        "status": agent.status,
        "workspace_path": agent.workspace_path,
        "current_task_id": agent.current_task_id,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "started_at": agent.started_at,
        "finished_at": agent.finished_at,
    }


def _task_payload(task: TeamTask) -> dict[str, object]:
    return {
        "id": task.id,
        "team_run_id": task.team_run_id,
        "title": task.title,
        "description": task.description,
        "owner_agent_id": task.owner_agent_id,
        "status": task.status,
        "result": task.result,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _message_payload(message: TeamMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "team_run_id": message.team_run_id,
        "sender_agent_id": message.sender_agent_id,
        "recipient_agent_id": message.recipient_agent_id,
        "kind": message.kind,
        "content": message.content,
        "metadata": message.metadata,
        "created_at": message.created_at,
    }
