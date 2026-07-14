from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.rule_sets import RuleSet

router = APIRouter(tags=["rules"])


class RuleItem(BaseModel):
    level: str
    text: str


class RuleSetRequest(BaseModel):
    personality: str = ""
    rules: list[RuleItem] = []


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("/api/rules")
def get_rules(request: Request, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.rule_set_service
    return {
        "global": _payload(service.get_global()),
        "persona_baseline": _payload(service.get_persona_baseline()),
        "teams": [_payload(rs) for rs in service.list_team_rule_sets()],
    }


@router.put("/api/rules/global")
def put_global(request: Request, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "global", None, payload)


@router.put("/api/rules/persona-baseline")
def put_persona_baseline(request: Request, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "persona_baseline", None, payload)


@router.put("/api/teams/{team_id}/rules")
def put_team_rules(request: Request, team_id: str, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "team", team_id, payload)


def _upsert(request: Request, scope: str, team_id: str | None, payload: RuleSetRequest) -> dict[str, object]:
    service = request.app.state.rule_set_service
    try:
        rule_set = service.upsert(
            scope, team_id, payload.personality,
            [rule.model_dump() for rule in payload.rules],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rule_set": _payload(rule_set)}


def _payload(rule_set: RuleSet) -> dict[str, object]:
    return {
        "scope": rule_set.scope,
        "team_id": rule_set.team_id,
        "personality": rule_set.personality,
        "rules": rule_set.rules,
        "updated_at": rule_set.updated_at,
    }
