import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.teams import TeamAgent, TeamMessage, TeamRun, TeamTask


router = APIRouter(prefix="/api/team-runs", tags=["team-runs"])

_ACTIVE = {"planning", "running", "summarizing"}
_TERMINAL = {"completed", "completed_with_failures", "failed", "canceled"}


class CreateTeamRunRequest(BaseModel):
    goal: str
    leader_persona_id: str
    member_persona_ids: list[str] = []
    run_mode: Literal["planning_only", "plan_and_execute", "review_only"] = "planning_only"
    max_workers: int = 3


class AddWorkRequest(BaseModel):
    instruction: str


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_team_runs(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {"team_runs": request.app.state.team_run_service.list_team_runs_enriched()}


@router.post("")
def create_team_run(
    request: Request, payload: CreateTeamRunRequest, _session: None = session_dependency
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.create_team_run(**payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"team_run": _team_run_payload(run)}


@router.get("/{team_run_id}")
def get_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/start")
async def start_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
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
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/resume")
async def resume_team_run(
    request: Request, team_run_id: str, _session: None = session_dependency
) -> dict[str, object]:
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
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/tasks/{task_id}/retry")
async def retry_team_task(
    request: Request, team_run_id: str, task_id: str, _session: None = session_dependency
) -> dict[str, object]:
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
        {"type": "team.task.updated", "team_run_id": team_run_id, "task_id": task_id}
    )
    return {"team_run": _team_run_payload(run), "task": _task_payload(task)}


@router.post("/{team_run_id}/add-work")
async def add_work(
    request: Request, team_run_id: str, payload: AddWorkRequest, _session: None = session_dependency
) -> dict[str, object]:
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

    return {"team_run": _team_run_payload(service.get_team_run(team_run_id))}


@router.post("/{team_run_id}/cancel")
def cancel_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.cancel(team_run_id):
        return {"team_run": _team_run_payload(service.get_team_run(team_run_id))}
    if run.status not in _TERMINAL:
        run = service.set_run_status(team_run_id, "canceled")
    return {"team_run": _team_run_payload(run)}


@router.delete("/{team_run_id}")
def delete_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
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
    return {"deleted": True}


@router.get("/{team_run_id}/agents")
def list_team_agents(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    try:
        agents = request.app.state.team_run_service.list_agents(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"agents": [_agent_payload(agent) for agent in agents]}


@router.get("/{team_run_id}/tasks")
def list_team_tasks(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    try:
        tasks = request.app.state.team_run_service.list_tasks(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"tasks": [_task_payload(task) for task in tasks]}


@router.get("/{team_run_id}/messages")
def list_team_messages(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    try:
        messages = request.app.state.team_run_service.list_messages(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"messages": [_message_payload(message) for message in messages]}


@router.get("/{team_run_id}/documents")
def list_team_documents(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    documents: list[dict[str, object]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            kind = _doc_kind(path)
            size = path.stat().st_size
            documents.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": size,
                    "modified_at": _iso_mtime(path),
                    "kind": kind,
                    "previewable": kind != "binary" and size <= _MAX_PREVIEW_BYTES,
                }
            )
    return {"documents": documents}


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
    kind = _doc_kind(target)
    size = target.stat().st_size
    if kind == "binary" or size > _MAX_PREVIEW_BYTES:
        return {"path": path, "kind": kind, "content": None, "previewable": False,
                "reason": "binary or too large"}
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": path, "kind": "binary", "content": None, "previewable": False,
                "reason": "not utf-8 text"}
    return {"path": path, "kind": kind, "content": content, "previewable": True}


_TEXT_EXTS = {".txt", ".log", ".csv", ".yaml", ".yml", ".toml", ".ini", ".env"}
_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh", ".sql"}
_MAX_PREVIEW_BYTES = 1_000_000


def _doc_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
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


def _team_run_payload(run: TeamRun) -> dict[str, object]:
    return {
        "id": run.id,
        "goal": run.goal,
        "status": run.status,
        "run_mode": run.run_mode,
        "leader_agent_id": run.leader_agent_id,
        "max_workers": run.max_workers,
        "workspace_root": run.workspace_root,
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
