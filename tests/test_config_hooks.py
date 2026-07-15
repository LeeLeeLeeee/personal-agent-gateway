from personal_agent_gateway.config import AppConfig


def test_hooks_dir_defaults_under_data_root() -> None:
    config = AppConfig(
        workspace_root="/tmp/ws",
        session_dir="/tmp/data/sessions",
    )
    assert config.hooks_dir.name == "hooks"
    assert config.hooks_dir.parent.name == "data"
    assert config.hook_poll_interval_seconds == 30


def test_hooks_config_from_env() -> None:
    config = AppConfig.from_env(
        {
            "AGENT_WORKSPACE_ROOT": "/tmp/ws",
            "AGENT_SESSION_DIR": "/tmp/data/sessions",
            "AGENT_HOOK_POLL_INTERVAL_SECONDS": "15",
        }
    )
    assert config.hook_poll_interval_seconds == 15
