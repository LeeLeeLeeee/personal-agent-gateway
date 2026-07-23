from fastapi import APIRouter, Request

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.lmg_client import fetch_sessions
from personal_agent_gateway.local_usage import collect_local_agent_usage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/usage")
def dashboard_usage(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, object]:
    report = collect_local_agent_usage(request.app.state.agent_registry)
    return report.model_dump(mode="json")


@router.get("/sessions")
def dashboard_sessions(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, object]:
    return {"sessions": fetch_sessions(request.app.state.app_config)}
