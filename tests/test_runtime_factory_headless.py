from pathlib import Path

import pytest

from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
from personal_agent_gateway.transcript import TranscriptStore


def _factory(tmp_path: Path) -> AgentRuntimeFactory:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
    )
    return AgentRuntimeFactory(config, TranscriptStore(config.session_dir))


def test_headless_codex_runtime_uses_codex_client(tmp_path: Path) -> None:
    runtime = _factory(tmp_path).create_headless_runtime("codex", "gpt-x", {})
    assert isinstance(runtime._model, CodexModelClient)


def test_headless_claude_runtime_uses_claude_client(tmp_path: Path) -> None:
    runtime = _factory(tmp_path).create_headless_runtime("claude", "sonnet", {})
    assert isinstance(runtime._model, ClaudeModelClient)


def test_headless_unsupported_backend_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _factory(tmp_path).create_headless_runtime("bogus", "x", {})
