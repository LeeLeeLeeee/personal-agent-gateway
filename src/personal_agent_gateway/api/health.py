from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    components = request.app.state.health_service.components()
    is_ready = all(component.ready for component in components)
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "unavailable",
            "components": [component.payload() for component in components],
        },
    )
