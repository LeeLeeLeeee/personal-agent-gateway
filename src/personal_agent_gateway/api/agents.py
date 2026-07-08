from fastapi import APIRouter, Request

from personal_agent_gateway.agents import AgentDescriptor, AgentOption, AgentRegistry
from personal_agent_gateway.api.jobs import session_dependency


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    registry = AgentRegistry(request.app.state.app_config)
    return {"agents": [_public_agent_payload(agent) for agent in registry.catalog()]}


def _public_agent_payload(agent: AgentDescriptor) -> dict[str, object]:
    return {
        "id": agent.id,
        "label": agent.label,
        "available": agent.available,
        "availability_error": agent.availability_error,
        "models": agent.models,
        "default_model": agent.default_model,
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
