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
        "LMG_LOCAL_TOKEN": "local-secret",
    })
    assert config.claude_permission_mode == "plan"


def test_load_config_forwards_claude_permission_mode(tmp_path, monkeypatch):
    from personal_agent_gateway.config import load_config
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_CLAUDE_PERMISSION_MODE", "bypassPermissions")
    monkeypatch.setenv("LMG_LOCAL_TOKEN", "local-secret")
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
        "LMG_LOCAL_TOKEN": "local-secret",
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
            "LMG_LOCAL_TOKEN": "local-secret",
        })


def test_team_run_concurrency_defaults_and_accepts_env_override(tmp_path):
    config = AppConfig(
        workspace_root=tmp_path,
        session_dir=tmp_path / "sessions",
    )
    assert config.team_run_concurrency == 2

    configured = AppConfig.from_env(
        {
            "AGENT_WORKSPACE_ROOT": str(tmp_path),
            "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
            "AGENT_TEAM_RUN_CONCURRENCY": "4",
            "LMG_LOCAL_TOKEN": "local-secret",
        }
    )
    assert configured.team_run_concurrency == 4


@pytest.mark.parametrize("value", [0, 17])
def test_team_run_concurrency_rejects_values_outside_limit(tmp_path, value):
    with pytest.raises(ValidationError, match="between 1 and 16"):
        AppConfig(
            workspace_root=tmp_path,
            session_dir=tmp_path / "sessions",
            team_run_concurrency=value,
        )

    with pytest.raises(ConfigError, match="between 1 and 16"):
        AppConfig.from_env(
            {
                "AGENT_WORKSPACE_ROOT": str(tmp_path),
                "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
                "AGENT_TEAM_RUN_CONCURRENCY": str(value),
                "LMG_LOCAL_TOKEN": "local-secret",
            }
        )
