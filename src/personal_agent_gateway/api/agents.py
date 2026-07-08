from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.agents import AgentDescriptor, AgentOption, AgentRegistry
from personal_agent_gateway.api.jobs import session_dependency
from personal_agent_gateway.session_config import SessionAgentConfigService


router = APIRouter(prefix="/api/agents", tags=["agents"])
session_config_router = APIRouter(prefix="/api/sessions/active/config", tags=["agents"])


class SessionConfigRequest(BaseModel):
    agent_id: str
    model: str
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def list_agents(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    registry = AgentRegistry(request.app.state.app_config)
    return {"agents": [_public_agent_payload(agent) for agent in registry.catalog()]}


@session_config_router.get("")
def get_active_session_config(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, dict[str, object]]:
    service = SessionAgentConfigService(request.app.state.transcript_store)
    return {"config": service.effective_config().model_dump(mode="json")}


@session_config_router.put("")
def set_active_session_config(
    payload: SessionConfigRequest,
    request: Request,
    _session: None = session_dependency,
) -> dict[str, dict[str, object]]:
    registry = AgentRegistry(request.app.state.app_config)
    try:
        validated = registry.validate_config(payload.agent_id, payload.model, payload.options)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    service = SessionAgentConfigService(request.app.state.transcript_store)
    try:
        config = service.set_config(
            request.app.state.transcript_store.active_id(),
            validated["agent_id"],
            validated["model"],
            validated["options"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"config": config.model_dump(mode="json")}


def _public_agent_payload(agent: AgentDescriptor) -> dict[str, object]:
    return {
        "id": agent.id,
        "label": agent.label,
        "available": agent.available,
        "availability_error": agent.availability_error,
        "models": agent.models,
        "default_model": agent.default_model,
        "allow_custom_model": agent.allow_custom_model,
        "options_schema": [_public_option_payload(option) for option in agent.options_schema],
        "defaults": agent.defaults,
    }


def _public_option_payload(option: AgentOption) -> dict[str, object]:
    return {
        "name": option.name,
        "kind": option.kind,
        "choices": option.choices,
        "required": option.required,
    }
