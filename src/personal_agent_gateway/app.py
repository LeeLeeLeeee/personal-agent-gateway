from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.auth import require_token
from personal_agent_gateway.config import AppConfig, ConfigError, load_config
from personal_agent_gateway.model_client import OpenAIModelClient
from personal_agent_gateway.runtime import AgentRuntime, RuntimeResult
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class ChatRequest(BaseModel):
    message: str


def create_app(config: AppConfig | None = None, runtime: AgentRuntime | None = None) -> FastAPI:
    app_config = config or load_config()
    transcript = TranscriptStore(app_config.session_dir)
    shared_runtime = runtime or _create_runtime(app_config, transcript)
    app = FastAPI()
    token_dependency = require_token(app_config.web_token)
    static_dir = Path(__file__).parent / "static"

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

    @app.post("/api/chat")
    async def chat(request: ChatRequest, _token: None = token_dependency) -> dict[str, object]:
        return _runtime_response(await shared_runtime.handle_user_message(request.message))

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, _token: None = token_dependency) -> dict[str, object]:
        return _runtime_response(await shared_runtime.approve(approval_id))

    @app.post("/api/approvals/{approval_id}/deny")
    async def deny(approval_id: str, _token: None = token_dependency) -> dict[str, object]:
        return _runtime_response(await shared_runtime.deny(approval_id))

    @app.post("/api/reset")
    def reset(_token: None = token_dependency) -> dict[str, list[object]]:
        nonlocal shared_runtime
        transcript.reset()
        if runtime is None:
            shared_runtime = _create_runtime(app_config, transcript)
        return {"events": []}

    return app


def main() -> None:
    config = load_config()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


def _create_runtime(config: AppConfig, transcript: TranscriptStore) -> AgentRuntime:
    if config.model_provider != "openai":
        raise ConfigError(f"Unsupported model provider: {config.model_provider}")

    return AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(config.workspace_root, ApprovalStore()),
        model=OpenAIModelClient(api_key=config.openai_api_key or "", model=config.model),
    )


def _event_payload(event: BaseModel) -> dict[str, object]:
    payload = event.model_dump(mode="json")
    return {str(key): value for key, value in payload.items()}


def _runtime_response(result: RuntimeResult) -> dict[str, object]:
    return {
        "messages": result.messages,
        "pending_approval": result.pending_approval,
    }


__all__ = ["create_app", "main"]
