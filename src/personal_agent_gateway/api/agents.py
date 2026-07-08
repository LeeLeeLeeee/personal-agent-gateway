from fastapi import APIRouter, Request

from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.api.jobs import session_dependency


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    registry = AgentRegistry(request.app.state.app_config)
    return {"agents": [agent.model_dump(mode="json") for agent in registry.catalog()]}
