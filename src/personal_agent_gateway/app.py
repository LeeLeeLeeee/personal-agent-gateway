from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from personal_agent_gateway.api import (
    artifacts_router,
    auth_router,
    capabilities_router,
    jobs_router,
)
from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.auth import require_token
from personal_agent_gateway.auth_store import AuthStore
from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.config import AppConfig, ConfigError, load_config
from personal_agent_gateway.db import Database
from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import CodexModelClient, OpenAIModelClient
from personal_agent_gateway.runtime import AgentRuntime, RuntimeResult
from personal_agent_gateway.runners.capture import CaptureRunner
from personal_agent_gateway.runners.ffmpeg import FfmpegRunner
from personal_agent_gateway.runners.shell import ShellRunner
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class ChatRequest(BaseModel):
    message: str


def create_app(config: AppConfig | None = None, runtime: AgentRuntime | None = None) -> FastAPI:
    app_config = config or load_config()
    transcript = TranscriptStore(app_config.session_dir)
    running_session_id: str | None = None
    app = FastAPI()
    token_dependency = require_token(app_config.web_token or "", secure_cookie=app_config.cookie_secure)
    static_dir = Path(__file__).parent / "static"
    _attach_local_services(app, app_config)
    shared_runtime = runtime or _create_runtime(
        app_config,
        transcript,
        app.state.job_service,
    )
    app.include_router(auth_router)
    app.include_router(capabilities_router)
    app.include_router(jobs_router)
    app.include_router(artifacts_router)

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index(_token: None = token_dependency) -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/static/app.js")
    def app_script(_token: None = token_dependency) -> FileResponse:
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    @app.get("/static/styles.css")
    def styles(_token: None = token_dependency) -> FileResponse:
        return FileResponse(static_dir / "styles.css", media_type="text/css")

    @app.get("/api/history")
    def history(_token: None = token_dependency) -> dict[str, list[dict[str, object]]]:
        return {"events": [_event_payload(event) for event in transcript.load_active()]}

    @app.get("/api/status")
    def status(_token: None = token_dependency) -> dict[str, object]:
        events = transcript.load_active()
        session_id = transcript.active_id()
        return {
            "provider": app_config.model_provider,
            "model": app_config.model,
            "workspace_root": str(app_config.workspace_root),
            "session_id": session_id,
            "message_count": sum(1 for event in events if event.kind in {"user", "assistant"}),
            "pending_approval": _has_pending_shell_approval(events),
            "session_status": _session_status(events, session_id, running_session_id),
            "cookie_secure": app_config.cookie_secure,
        }

    @app.get("/api/sessions")
    def sessions(_token: None = token_dependency) -> dict[str, list[dict[str, object]]]:
        return {
            "sessions": [
                _session_payload(session, running_session_id)
                for session in transcript.list_sessions()
            ]
        }

    @app.get("/api/sessions/search")
    def search_sessions(q: str = "", _token: None = token_dependency) -> dict[str, list[dict[str, object]]]:
        return {
            "sessions": [
                _session_payload(session, running_session_id)
                for session in transcript.search_sessions(q)
            ]
        }

    @app.post("/api/sessions/{session_id}/activate")
    def activate_session(session_id: str, _token: None = token_dependency) -> dict[str, object]:
        if not transcript.activate(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "events": [_event_payload(event) for event in transcript.load_active()]}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, _token: None = token_dependency) -> dict[str, object]:
        if not transcript.delete(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True, "active_session_id": transcript.active_id()}

    @app.post("/api/chat")
    async def chat(request: ChatRequest, _token: None = token_dependency) -> dict[str, object]:
        nonlocal running_session_id
        running_session_id = transcript.active_id()
        try:
            return _runtime_response(await shared_runtime.handle_user_message(request.message))
        finally:
            running_session_id = None

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, _token: None = token_dependency) -> dict[str, object]:
        nonlocal running_session_id
        running_session_id = transcript.active_id()
        try:
            return _runtime_response(await shared_runtime.approve(approval_id))
        finally:
            running_session_id = None

    @app.post("/api/approvals/{approval_id}/deny")
    async def deny(approval_id: str, _token: None = token_dependency) -> dict[str, object]:
        nonlocal running_session_id
        running_session_id = transcript.active_id()
        try:
            return _runtime_response(await shared_runtime.deny(approval_id))
        finally:
            running_session_id = None

    @app.post("/api/reset")
    def reset(_token: None = token_dependency) -> dict[str, object]:
        nonlocal shared_runtime
        session_id = transcript.reset()
        if runtime is None:
            shared_runtime = _create_runtime(app_config, transcript, app.state.job_service)
        return {"events": [], "session_id": session_id}

    return app


def _attach_local_services(app: FastAPI, config: AppConfig) -> None:
    assert config.app_db_path is not None
    assert config.artifact_root is not None
    assert config.temp_dir is not None
    assert config.auth_dir is not None
    db = Database(config.app_db_path)
    db.initialize()
    registry = CapabilityRegistry.default()
    job_service = JobService(db, registry)
    artifact_store = ArtifactStore(db, config.artifact_root)
    job_worker = JobWorker(
        job_service,
        artifact_store,
        {
            "ffmpeg": FfmpegRunner(
                ffmpeg_binary=config.ffmpeg_binary,
                ffprobe_binary=config.ffprobe_binary,
                workspace_root=config.workspace_root,
                temp_dir=config.temp_dir,
            ),
            "capture": CaptureRunner(
                capture_binary=config.capture_binary,
                temp_dir=config.temp_dir,
            ),
            "shell": ShellRunner(config.workspace_root),
        },
    )
    app.state.app_config = config
    app.state.auth_store = AuthStore(config.auth_dir)
    app.state.capability_registry = registry
    app.state.job_service = job_service
    app.state.artifact_store = artifact_store
    app.state.job_worker = job_worker


def main() -> None:
    config = load_config()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


def _create_runtime(
    config: AppConfig,
    transcript: TranscriptStore,
    job_service: JobService | None = None,
) -> AgentRuntime:
    if config.model_provider == "codex":
        return AgentRuntime(
            transcript=transcript,
            tools=WorkspaceTools(config.workspace_root, ApprovalStore()),
            model=CodexModelClient(
                binary=config.codex_binary,
                model=config.model,
                workspace_root=config.workspace_root,
                sandbox=config.codex_sandbox,
                approval_policy=config.codex_approval_policy,
                timeout_seconds=config.codex_timeout_seconds,
            ),
            job_service=job_service,
        )

    if config.model_provider != "openai":
        raise ConfigError(f"Unsupported model provider: {config.model_provider}")
    if not config.openai_api_key:
        raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

    return AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(config.workspace_root, ApprovalStore()),
        model=OpenAIModelClient(api_key=config.openai_api_key or "", model=config.model),
        job_service=job_service,
    )


def _event_payload(event: BaseModel) -> dict[str, object]:
    payload = event.model_dump(mode="json")
    return {str(key): value for key, value in payload.items()}


def _runtime_response(result: RuntimeResult) -> dict[str, object]:
    return {
        "messages": result.messages,
        "pending_approval": result.pending_approval,
    }


def _session_payload(session: BaseModel, running_session_id: str | None) -> dict[str, object]:
    payload = session.model_dump(mode="json")
    if payload["id"] == running_session_id:
        payload["status"] = "running"
    return {str(key): value for key, value in payload.items()}


def _session_status(events: list[object], session_id: str | None, running_session_id: str | None) -> str:
    if session_id is not None and session_id == running_session_id:
        return "running"
    if events and getattr(events[-1], "kind", "") == "runtime_error":
        return "failed"
    if _has_pending_shell_approval(events):
        return "waiting_approval"
    return "idle"


def _has_pending_shell_approval(events: list[object]) -> bool:
    pending_by_tool_id: set[str] = set()
    for event in events:
        kind = getattr(event, "kind", "")
        payload = getattr(event, "payload", {})
        if kind == "tool_request" and payload.get("name") == "shell.run":
            pending_by_tool_id.add(str(payload.get("id", "")))
        elif kind in {"tool_result", "tool_denial"}:
            pending_by_tool_id.discard(str(payload.get("id", "")))
    return bool(pending_by_tool_id)


__all__ = ["create_app", "main"]
