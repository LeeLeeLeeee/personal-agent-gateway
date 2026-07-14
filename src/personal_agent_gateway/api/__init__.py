from personal_agent_gateway.api.agents import router as agents_router
from personal_agent_gateway.api.agents import session_config_router
from personal_agent_gateway.api.artifacts import router as artifacts_router
from personal_agent_gateway.api.auth import router as auth_router
from personal_agent_gateway.api.capabilities import router as capabilities_router
from personal_agent_gateway.api.jobs import router as jobs_router
from personal_agent_gateway.api.personas import router as personas_router
from personal_agent_gateway.api.rules import router as rules_router
from personal_agent_gateway.api.schedules import router as schedules_router
from personal_agent_gateway.api.settings import router as settings_router
from personal_agent_gateway.api.team_runs import router as team_runs_router
from personal_agent_gateway.api.teams import router as teams_router

__all__ = [
    "agents_router",
    "artifacts_router",
    "auth_router",
    "capabilities_router",
    "jobs_router",
    "personas_router",
    "rules_router",
    "schedules_router",
    "session_config_router",
    "settings_router",
    "team_runs_router",
    "teams_router",
]
