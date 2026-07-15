from dataclasses import asdict

from fastapi import APIRouter, Request

from personal_agent_gateway.api.dependencies import session_dependency

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("")
def list_capabilities(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {
        "capabilities": [
            asdict(capability)
            for capability in request.app.state.capability_registry.list()
        ]
    }


@router.get("/{capability_id}")
def get_capability(
    request: Request,
    capability_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    return asdict(request.app.state.capability_registry.get(capability_id))
