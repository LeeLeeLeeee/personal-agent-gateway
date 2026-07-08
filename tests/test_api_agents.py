from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
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


def test_agents_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/agents")

    assert response.status_code == 401


def test_agents_returns_safe_catalog(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(
        agents_module,
        "probe_cli",
        lambda binary: agents_module.CliProbeResult(
            binary == "codex-test",
            None if binary == "codex-test" else "not found",
        ),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    response = client.get("/api/agents")

    assert response.status_code == 200
    payload = response.json()
    assert [agent["id"] for agent in payload["agents"]] == ["codex", "claude"]
    assert payload["agents"][0]["available"] is True
    assert payload["agents"][1]["available"] is False
    assert payload["agents"][1]["availability_error"] == "not found"
    assert "openai_api_key" not in response.text
    assert "web_token" not in response.text
