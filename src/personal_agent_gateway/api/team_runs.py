from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.teams import TeamAgent, TeamMessage, TeamRun, TeamTask


router = APIRouter(prefix="/api/team-runs", tags=["team-runs"])


class CreateTeamRunRequest(BaseModel):
    goal: str
    leader_persona_id: str
    member_persona_ids: list[str] = []
    run_mode: Literal["planning_only", "plan_and_execute", "review_only"] = "planning_only"
    max_workers: int = 3


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_team_runs(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {
        "team_runs": [
            _team_run_payload(run) for run in request.app.state.team_run_service.list_team_runs()
        ]
    }


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
    try:
        run = await request.app.state.team_runtime.start(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/cancel")
def cancel_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.set_run_status(team_run_id, "canceled")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"team_run": _team_run_payload(run)}


@router.delete("/{team_run_id}")
def delete_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        request.app.state.team_run_service.delete_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
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
