from pathlib import Path
import subprocess

import pytest

from personal_agent_gateway.agents import AgentRegistry, CliProbeResult, probe_cli
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        codex_binary="codex-test",
        claude_binary="claude-test",
    )


def test_registry_lists_codex_and_claude_with_safe_defaults(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    registry = AgentRegistry(
        config,
        probe=lambda binary: CliProbeResult(
            available=binary == "codex-test",
            error=None if binary == "codex-test" else "not found",
        ),
    )

    catalog = registry.catalog()

    assert [agent.id for agent in catalog] == ["codex", "claude"]
    codex = catalog[0]
    claude = catalog[1]
    assert codex.available is True
    assert codex.binary == "codex-test"
    assert codex.default_model == "default"
    assert codex.defaults["sandbox"] == "workspace-write"
    assert claude.available is False
    assert claude.availability_error == "not found"
    assert claude.defaults["effort"] == "medium"


def test_registry_rejects_unknown_agent_model_and_option(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    with pytest.raises(ValueError, match="Unknown agent"):
        registry.validate_config("missing", "default", {})

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("codex", "not-listed", {})

    with pytest.raises(ValueError, match="Unsupported option"):
        registry.validate_config("codex", "default", {"not_allowed": True})


def test_registry_accepts_supported_provider_options(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    assert registry.validate_config(
        "claude",
        "sonnet",
        {"effort": "high", "permission_mode": "manual"},
    ) == {
        "agent_id": "claude",
        "model": "sonnet",
        "options": {"effort": "high", "permission_mode": "manual"},
    }


def test_probe_cli_returns_timeout_result_for_hung_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex-test", "--help"], timeout=5)

    monkeypatch.setattr("personal_agent_gateway.agents.subprocess.run", fake_run)

    assert probe_cli("codex-test") == CliProbeResult(False, "probe timed out")


def test_registry_rejects_invalid_option_choice(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    with pytest.raises(ValueError, match="Unsupported option value"):
        registry.validate_config("codex", "default", {"sandbox": "invalid-choice"})
