import pytest
from pydantic import ValidationError

from personal_agent_gateway.config import AppConfig, ConfigError


def test_claude_permission_mode_default(tmp_path):
    config = AppConfig(workspace_root=tmp_path, session_dir=tmp_path / "sessions")
    assert config.claude_permission_mode == "acceptEdits"


def test_claude_permission_mode_from_env(tmp_path):
    config = AppConfig.from_env({
        "AGENT_WORKSPACE_ROOT": str(tmp_path),
        "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
        "AGENT_CLAUDE_PERMISSION_MODE": "plan",
    })
    assert config.claude_permission_mode == "plan"


def test_load_config_forwards_claude_permission_mode(tmp_path, monkeypatch):
    from personal_agent_gateway.config import load_config
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_CLAUDE_PERMISSION_MODE", "bypassPermissions")
    assert load_config().claude_permission_mode == "bypassPermissions"


def test_codex_timeout_defaults_and_idle_timeout_from_env(tmp_path):
    defaults = AppConfig(workspace_root=tmp_path, session_dir=tmp_path / "sessions")
    assert defaults.codex_timeout_seconds == 3600
    assert defaults.codex_idle_timeout_seconds == 600

    configured = AppConfig.from_env({
        "AGENT_WORKSPACE_ROOT": str(tmp_path),
        "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
        "AGENT_CODEX_TIMEOUT_SECONDS": "7200",
        "AGENT_CODEX_IDLE_TIMEOUT_SECONDS": "900",
    })
    assert configured.codex_timeout_seconds == 7200
    assert configured.codex_idle_timeout_seconds == 900


def test_job_worker_concurrency_rejects_unsupported_values(tmp_path):
    with pytest.raises(ValidationError, match="currently supports only 1"):
        AppConfig(
            workspace_root=tmp_path,
            session_dir=tmp_path / "sessions",
            job_worker_concurrency=2,
        )

    with pytest.raises(ConfigError, match="currently supports only 1"):
        AppConfig.from_env({
            "AGENT_WORKSPACE_ROOT": str(tmp_path),
            "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
            "AGENT_JOB_WORKER_CONCURRENCY": "2",
        })
