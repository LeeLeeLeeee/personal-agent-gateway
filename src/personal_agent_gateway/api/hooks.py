import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.hook_runs import HookRun
from personal_agent_gateway.hooks import Hook

router = APIRouter(prefix="/api/hooks", tags=["hooks"])


class CreateHookRequest(BaseModel):
    name: str
    source_type: str
    connection: dict[str, object] = Field(default_factory=dict)
    secret: str
    filter: dict[str, object] = Field(default_factory=dict)
    target_backend: str
    target_model: str
    target_options: dict[str, object] = Field(default_factory=dict)
    prompt_template: str
    poll_interval_seconds: int = 300


class UpdateHookRequest(BaseModel):
    enabled: bool


@router.get("")
def list_hooks(request: Request, _session: None = session_dependency) -> dict[str, object]:
    return {"hooks": [_hook_payload(h) for h in request.app.state.hook_service.list_hooks()]}


@router.post("")
def create_hook(
    request: Request, payload: CreateHookRequest, _session: None = session_dependency
) -> dict[str, object]:
    try:
        hook = request.app.state.hook_service.create_hook(
            name=payload.name,
            source_type=payload.source_type,
            connection=payload.connection,
            secret=payload.secret,
            filter=payload.filter,
            target_backend=payload.target_backend,
            target_model=payload.target_model,
            target_options=payload.target_options,
            prompt_template=payload.prompt_template,
            poll_interval_seconds=payload.poll_interval_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"hook": _hook_payload(hook)}


@router.get("/{hook_id}")
def get_hook(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, object]:
    return {"hook": _hook_payload(_require(request, hook_id))}


@router.patch("/{hook_id}")
def update_hook(
    request: Request, hook_id: str, payload: UpdateHookRequest, _session: None = session_dependency
) -> dict[str, object]:
    _require(request, hook_id)
    hook = request.app.state.hook_service.set_enabled(hook_id, payload.enabled)
    return {"hook": _hook_payload(hook)}


@router.delete("/{hook_id}")
def delete_hook(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, bool]:
    _require(request, hook_id)
    request.app.state.hook_service.delete(hook_id)
    return {"deleted": True}


@router.post("/{hook_id}/run-now")
async def run_hook_now(
    request: Request, hook_id: str, _session: None = session_dependency
) -> dict[str, int]:
    _require(request, hook_id)
    runs = await asyncio.to_thread(
        request.app.state.hook_service.poll_hook,
        hook_id,
        request.app.state.hook_run_service,
    )
    for run in runs:
        await request.app.state.hook_runner.enqueue(run.id)
    return {"created": len(runs)}


@router.get("/{hook_id}/runs")
def list_hook_runs(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, object]:
    _require(request, hook_id)
    runs = request.app.state.hook_run_service.list_runs(hook_id)
    return {"runs": [_run_payload(r) for r in runs]}


def _require(request: Request, hook_id: str) -> Hook:
    try:
        return request.app.state.hook_service.get_hook(hook_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Hook not found") from exc


def _hook_payload(hook: Hook) -> dict[str, object]:
    return {
        "id": hook.id,
        "name": hook.name,
        "source_type": hook.source_type,
        "connection": hook.connection,
        "filter": hook.filter,
        "target_backend": hook.target_backend,
        "target_model": hook.target_model,
        "target_options": hook.target_options,
        "prompt_template": hook.prompt_template,
        "poll_interval_seconds": hook.poll_interval_seconds,
        "enabled": hook.enabled,
        "last_polled_at": hook.last_polled_at,
        "last_error": hook.last_error,
        "created_at": hook.created_at,
        "updated_at": hook.updated_at,
    }


def _run_payload(run: HookRun) -> dict[str, object]:
    return {
        "id": run.id,
        "hook_id": run.hook_id,
        "trigger_summary": run.trigger_summary,
        "status": run.status,
        "result_text": run.result_text,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
