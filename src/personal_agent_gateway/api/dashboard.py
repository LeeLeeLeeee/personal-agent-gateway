from fastapi import APIRouter, Request

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.local_usage import collect_local_agent_usage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/usage")
def dashboard_usage(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, object]:
    report = collect_local_agent_usage(request.app.state.agent_registry)
    return report.model_dump(mode="json")
