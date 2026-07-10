from pathlib import Path
from personal_agent_gateway.config import AppConfig


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
