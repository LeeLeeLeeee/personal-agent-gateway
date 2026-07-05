from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator


class ConfigError(Exception):
    pass


class AppConfig(BaseModel):
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_token: str
    workspace_root: Path
    model_provider: str = "codex"
    model: str = "default"
    session_dir: Path
    openai_api_key: str | None = None
    codex_binary: str = "codex"
    codex_sandbox: str = "workspace-write"
    codex_approval_policy: str = "never"
    codex_timeout_seconds: int = 600

    @field_validator("web_host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        if value in {"127.0.0.1", "localhost"}:
            return value
        raise ValueError("AGENT_WEB_HOST must be 127.0.0.1 or localhost")

    @field_validator("workspace_root", "session_dir", mode="after")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @classmethod
    def from_env(cls, env: dict[str, str | None]) -> Self:
        token = env.get("AGENT_WEB_TOKEN")
        workspace_root = env.get("AGENT_WORKSPACE_ROOT")
        session_dir = env.get("AGENT_SESSION_DIR")

        if not token:
            raise ConfigError("AGENT_WEB_TOKEN is required")
        if not workspace_root:
            raise ConfigError("AGENT_WORKSPACE_ROOT is required")
        if not session_dir:
            raise ConfigError("AGENT_SESSION_DIR is required")

        try:
            return cls(
                web_host=env.get("AGENT_WEB_HOST") or "127.0.0.1",
                web_port=int(env.get("AGENT_WEB_PORT") or "8787"),
                web_token=token,
                workspace_root=Path(workspace_root),
                model_provider=env.get("AGENT_MODEL_PROVIDER") or "codex",
                model=env.get("AGENT_MODEL") or "default",
                session_dir=Path(session_dir),
                openai_api_key=env.get("OPENAI_API_KEY"),
                codex_binary=env.get("AGENT_CODEX_BIN") or "codex",
                codex_sandbox=env.get("AGENT_CODEX_SANDBOX") or "workspace-write",
                codex_approval_policy=env.get("AGENT_CODEX_APPROVAL_POLICY") or "never",
                codex_timeout_seconds=int(env.get("AGENT_CODEX_TIMEOUT_SECONDS") or "600"),
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
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "AGENT_CODEX_BIN": os.getenv("AGENT_CODEX_BIN"),
            "AGENT_CODEX_SANDBOX": os.getenv("AGENT_CODEX_SANDBOX"),
            "AGENT_CODEX_APPROVAL_POLICY": os.getenv("AGENT_CODEX_APPROVAL_POLICY"),
            "AGENT_CODEX_TIMEOUT_SECONDS": os.getenv("AGENT_CODEX_TIMEOUT_SECONDS"),
        }
    )
