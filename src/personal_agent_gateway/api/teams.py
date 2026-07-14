from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.team_directory import Team

router = APIRouter(prefix="/api/teams", tags=["teams"])


class TeamRequest(BaseModel):
    name: str
    description: str = ""
    leader_persona_id: str
    member_persona_ids: list[str] = []


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    leader_persona_id: str | None = None
    member_persona_ids: list[str] | None = None


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_teams(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    service = request.app.state.team_directory_service
    personas = request.app.state.persona_service
    return {"teams": [_team_payload(team, personas) for team in service.list_teams()]}


@router.post("")
def create_team(request: Request, payload: TeamRequest, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        team = service.create_team(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.get("/{team_id}")
def get_team(request: Request, team_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        team = request.app.state.team_directory_service.get_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.put("/{team_id}")
def update_team(request: Request, team_id: str, payload: TeamUpdateRequest, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        team = service.update_team(
            team_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.delete("/{team_id}")
def delete_team(request: Request, team_id: str, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        service.delete_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    request.app.state.rule_set_service.delete_team(team_id)
    return {"deleted": True}


def _persona_summary(personas, persona_id: str) -> dict[str, object]:
    try:
        persona = personas.get_persona(persona_id)
    except KeyError:
        return {"id": persona_id, "name": persona_id, "role": "", "avatar": ""}
    return {"id": persona.id, "name": persona.name, "role": persona.role, "avatar": persona.avatar}


def _team_payload(team: Team, personas) -> dict[str, object]:
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "leader_persona_id": team.leader_persona_id,
        "member_persona_ids": team.member_persona_ids,
        "leader": _persona_summary(personas, team.leader_persona_id),
        "members": [_persona_summary(personas, pid) for pid in team.member_persona_ids],
        "created_at": team.created_at,
        "updated_at": team.updated_at,
    }
