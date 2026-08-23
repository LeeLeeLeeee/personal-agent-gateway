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


def test_peer_messages_are_on_unless_asked_otherwise(tmp_path):
    """The feature shipped enabled. A default of off would turn it off for
    every existing installation on upgrade, silently."""
    config = AppConfig(workspace_root=tmp_path, session_dir=tmp_path / "sessions")

    assert config.team_peer_messages_enabled is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", False), ("false", False), ("off", False), ("1", True), ("on", True)],
)
def test_peer_messages_read_the_environment(tmp_path, value, expected):
    config = AppConfig.from_env({
        "AGENT_WORKSPACE_ROOT": str(tmp_path),
        "AGENT_SESSION_DIR": str(tmp_path / "sessions"),
        "AGENT_TEAM_PEER_MESSAGES": value,
        "LMG_LOCAL_TOKEN": "local-secret",
    })

    assert config.team_peer_messages_enabled is expected



@pytest.mark.parametrize(
    ("variable", "attribute", "value", "expected"),
    [
        ("AGENT_TEAM_PEER_MESSAGES", "team_peer_messages_enabled", "off", False),
        ("AGENT_TEAM_PEER_MESSAGES", "team_peer_messages_enabled", "on", True),
        (
            "AGENT_TEAM_CONCURRENT_WORKERS",
            "team_concurrent_workers_enabled",
            "true",
            True,
        ),
        (
            "AGENT_TEAM_CONCURRENT_WORKERS",
            "team_concurrent_workers_enabled",
            "0",
            False,
        ),
    ],
)
def test_team_switches_survive_load_config(
    tmp_path, monkeypatch, variable, attribute, value, expected
):
    """from_env reading a key is not the same as load_config passing it.

    load_config hands from_env an explicit dict, so a key missing from that
    dict is unreachable from the environment no matter what from_env does with
    it -- and both team switches were missing. The existing tests called
    from_env directly, which is exactly the layer that cannot catch this, so
    this one goes through load_config.
    """
    from personal_agent_gateway.config import load_config

    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("LMG_LOCAL_TOKEN", "local-secret")
    monkeypatch.setenv(variable, value)

    assert getattr(load_config(), attribute) is expected
