from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Literal

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.space_policies import SpacePolicy


router = APIRouter(prefix="/api/spaces", tags=["spaces"])


class SpacePolicyRequest(BaseModel):
    read_mode: Literal["home", "selected", "all"]
    read_path: str | None = None
    write_mode: Literal["isolated", "worktree", "full_access"]
    workspace_path: str | None = None


@router.get("")
def list_space_policies(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, object]:
    service = request.app.state.space_policy_service
    service.seed_defaults()
    return {
        "precedence": ["team", "persona", "global"],
        "global": _payload(service.global_policy(), "global"),
        "personas": [
            _payload(policy, "persona")
            for policy in service.list_persona_overrides()
        ],
        "teams": [
            _payload(policy, "team")
            for policy in service.list_team_policies()
        ],
    }


@router.put("/global")
def update_global_space(
    request: Request,
    payload: SpacePolicyRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"space_policy": _save(request, "global", "", payload)}


@router.put("/personas/{persona_id}")
def update_persona_space(
    request: Request,
    persona_id: str,
    payload: SpacePolicyRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"space_policy": _save(request, "persona", persona_id, payload)}


@router.delete("/personas/{persona_id}")
def delete_persona_space(
    request: Request,
    persona_id: str,
    _session: None = session_dependency,
) -> dict[str, bool]:
    try:
        request.app.state.persona_service.get_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    request.app.state.space_policy_service.delete_persona_override(persona_id)
    return {"deleted": True}


@router.put("/teams/{team_id}")
def update_team_space(
    request: Request,
    team_id: str,
    payload: SpacePolicyRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"space_policy": _save(request, "team", team_id, payload)}


def _save(
    request: Request,
    scope: str,
    scope_id: str,
    payload: SpacePolicyRequest,
) -> dict[str, object]:
    try:
        policy = request.app.state.space_policy_service.upsert(
            scope,
            scope_id,
            **payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _payload(policy, scope)


def _payload(policy: SpacePolicy, effective_source: str) -> dict[str, object]:
    return {**policy.snapshot(), "effective_source": effective_source}
