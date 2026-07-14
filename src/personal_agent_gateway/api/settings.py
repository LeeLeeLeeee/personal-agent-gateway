from fastapi import APIRouter, Request

from personal_agent_gateway.api.jobs import session_dependency
from personal_agent_gateway.config import AppConfig


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(
    request: Request,
    _session: None = session_dependency,
) -> dict[str, dict[str, object]]:
    return {"settings": _settings_payload(request.app.state.app_config, request)}


def _settings_payload(config: AppConfig, request: Request) -> dict[str, object]:
    return {
        "workspace_root": str(config.workspace_root),
        "session_dir": str(config.session_dir),
        "artifact_root": str(config.artifact_root),
        "temp_dir": str(config.temp_dir),
        "provider": config.model_provider,
        "model": config.model,
        "codex_binary": config.codex_binary,
        "codex_sandbox": config.codex_sandbox,
        "codex_approval_policy": config.codex_approval_policy,
        "codex_timeout_seconds": config.codex_timeout_seconds,
        "codex_idle_timeout_seconds": config.codex_idle_timeout_seconds,
        "ffmpeg_binary": config.ffmpeg_binary,
        "ffprobe_binary": config.ffprobe_binary,
        "capture_binary": config.capture_binary,
        "job_worker_concurrency": config.job_worker_concurrency,
        "cookie_secure": config.cookie_secure,
        "totp_configured": request.app.state.auth_store.is_totp_enabled(),
    }
