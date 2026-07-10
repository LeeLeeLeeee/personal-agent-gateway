import os
import sys
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, model_validator


class ConfigError(Exception):
    pass


def _default_codex_binary(os_name: str | None = None) -> str:
    if (os_name or os.name) == "nt":
        return "codex.cmd"
    return "codex"


def _default_claude_binary(os_name: str | None = None) -> str:
    if (os_name or os.name) == "nt":
        return "claude.cmd"
    return "claude"


def _default_capture_binary(platform_name: str | None = None) -> str:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return "screencapture"
    if platform_name == "win32":
        return "powershell"
    return "unsupported-capture"


class AppConfig(BaseModel):
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_token: str | None = None
    workspace_root: Path
    model_provider: str = "codex"
    model: str = "default"
    session_dir: Path
    app_db_path: Path | None = None
    artifact_root: Path | None = None
    temp_dir: Path | None = None
    auth_dir: Path | None = None
    cookie_secure: bool = False
    auth_setup_token: str | None = None
    auth_require_token_and_otp: bool = False
    environment_title: str | None = None
    openai_api_key: str | None = None
    codex_binary: str = _default_codex_binary()
    claude_binary: str = _default_claude_binary()
    codex_sandbox: str = "workspace-write"
    codex_approval_policy: str = "never"
    codex_timeout_seconds: int = 600
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    capture_binary: str = _default_capture_binary()
    job_worker_concurrency: int = 1

    @field_validator("web_host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        if value in {"127.0.0.1", "localhost"}:
            return value
        raise ValueError("AGENT_WEB_HOST must be 127.0.0.1 or localhost")

    @field_validator(
        "workspace_root",
        "session_dir",
        "app_db_path",
        "artifact_root",
        "temp_dir",
        "auth_dir",
        mode="after",
    )
    @classmethod
    def resolve_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return value
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def derive_local_data_paths(self) -> Self:
        data_root = self.session_dir.parent
        if self.app_db_path is None:
            self.app_db_path = (data_root / "app.sqlite").resolve()
        if self.artifact_root is None:
            self.artifact_root = (data_root / "artifacts").resolve()
        if self.temp_dir is None:
            self.temp_dir = (data_root / "temp").resolve()
        if self.auth_dir is None:
            self.auth_dir = (data_root / "auth").resolve()
        return self

    @field_validator("cookie_secure", "auth_require_token_and_otp", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return False

    @classmethod
    def from_env(cls, env: dict[str, str | None]) -> Self:
        token = env.get("AGENT_WEB_TOKEN")
        workspace_root = env.get("AGENT_WORKSPACE_ROOT")
        session_dir = env.get("AGENT_SESSION_DIR")

        if not workspace_root:
            raise ConfigError("AGENT_WORKSPACE_ROOT is required")
        if not session_dir:
            raise ConfigError("AGENT_SESSION_DIR is required")

        session_path = Path(session_dir)
        data_root = session_path.parent
        app_db_path = env.get("AGENT_APP_DB_PATH") or str(data_root / "app.sqlite")
        artifact_root = env.get("AGENT_ARTIFACT_ROOT") or str(data_root / "artifacts")
        temp_dir = env.get("AGENT_TEMP_DIR") or str(data_root / "temp")
        auth_dir = env.get("AGENT_AUTH_DIR") or str(data_root / "auth")

        try:
            return cls(
                web_host=env.get("AGENT_WEB_HOST") or "127.0.0.1",
                web_port=int(env.get("AGENT_WEB_PORT") or "8787"),
                web_token=token,
                workspace_root=Path(workspace_root),
                model_provider=env.get("AGENT_MODEL_PROVIDER") or "codex",
                model=env.get("AGENT_MODEL") or "default",
                session_dir=session_path,
                app_db_path=Path(app_db_path),
                artifact_root=Path(artifact_root),
                temp_dir=Path(temp_dir),
                auth_dir=Path(auth_dir),
                cookie_secure=env.get("AGENT_COOKIE_SECURE") or False,
                auth_setup_token=env.get("AGENT_AUTH_SETUP_TOKEN") or None,
                auth_require_token_and_otp=env.get("AGENT_AUTH_REQUIRE_TOKEN_AND_OTP") or False,
                environment_title=env.get("AGENT_ENVIRONMENT_TITLE") or env.get("PAG_ENV_TITLE") or None,
                openai_api_key=env.get("OPENAI_API_KEY"),
                codex_binary=env.get("AGENT_CODEX_BIN") or _default_codex_binary(),
                claude_binary=env.get("AGENT_CLAUDE_BIN") or _default_claude_binary(),
                codex_sandbox=env.get("AGENT_CODEX_SANDBOX") or "workspace-write",
                codex_approval_policy=env.get("AGENT_CODEX_APPROVAL_POLICY") or "never",
                codex_timeout_seconds=int(env.get("AGENT_CODEX_TIMEOUT_SECONDS") or "600"),
                ffmpeg_binary=env.get("AGENT_FFMPEG_BIN") or "ffmpeg",
                ffprobe_binary=env.get("AGENT_FFPROBE_BIN") or "ffprobe",
                capture_binary=env.get("AGENT_CAPTURE_BIN") or _default_capture_binary(),
                job_worker_concurrency=int(env.get("AGENT_JOB_WORKER_CONCURRENCY") or "1"),
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc


def load_config() -> AppConfig:
    load_dotenv()

    import os

    return AppConfig.from_env(
        {
            "AGENT_WEB_HOST": os.getenv("AGENT_WEB_HOST"),
            "AGENT_WEB_PORT": os.getenv("AGENT_WEB_PORT"),
            "AGENT_WEB_TOKEN": os.getenv("AGENT_WEB_TOKEN"),
            "AGENT_WORKSPACE_ROOT": os.getenv("AGENT_WORKSPACE_ROOT"),
            "AGENT_MODEL_PROVIDER": os.getenv("AGENT_MODEL_PROVIDER"),
            "AGENT_MODEL": os.getenv("AGENT_MODEL"),
            "AGENT_SESSION_DIR": os.getenv("AGENT_SESSION_DIR"),
            "AGENT_APP_DB_PATH": os.getenv("AGENT_APP_DB_PATH"),
            "AGENT_ARTIFACT_ROOT": os.getenv("AGENT_ARTIFACT_ROOT"),
            "AGENT_TEMP_DIR": os.getenv("AGENT_TEMP_DIR"),
            "AGENT_AUTH_DIR": os.getenv("AGENT_AUTH_DIR"),
            "AGENT_COOKIE_SECURE": os.getenv("AGENT_COOKIE_SECURE"),
            "AGENT_AUTH_SETUP_TOKEN": os.getenv("AGENT_AUTH_SETUP_TOKEN"),
            "AGENT_AUTH_REQUIRE_TOKEN_AND_OTP": os.getenv("AGENT_AUTH_REQUIRE_TOKEN_AND_OTP"),
            "AGENT_ENVIRONMENT_TITLE": os.getenv("AGENT_ENVIRONMENT_TITLE"),
            "PAG_ENV_TITLE": os.getenv("PAG_ENV_TITLE"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "AGENT_CODEX_BIN": os.getenv("AGENT_CODEX_BIN"),
            "AGENT_CLAUDE_BIN": os.getenv("AGENT_CLAUDE_BIN"),
            "AGENT_CODEX_SANDBOX": os.getenv("AGENT_CODEX_SANDBOX"),
            "AGENT_CODEX_APPROVAL_POLICY": os.getenv("AGENT_CODEX_APPROVAL_POLICY"),
            "AGENT_CODEX_TIMEOUT_SECONDS": os.getenv("AGENT_CODEX_TIMEOUT_SECONDS"),
            "AGENT_FFMPEG_BIN": os.getenv("AGENT_FFMPEG_BIN"),
            "AGENT_FFPROBE_BIN": os.getenv("AGENT_FFPROBE_BIN"),
            "AGENT_CAPTURE_BIN": os.getenv("AGENT_CAPTURE_BIN"),
            "AGENT_JOB_WORKER_CONCURRENCY": os.getenv("AGENT_JOB_WORKER_CONCURRENCY"),
        }
    )
