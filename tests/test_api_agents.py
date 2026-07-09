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
    assert len(payload["agents"]) == 2

    expected_keys = {
        "id",
        "label",
        "available",
        "availability_error",
        "models",
        "default_model",
        "options_schema",
        "defaults",
    }
    expected_option_keys = {"name", "kind", "choices", "required"}

    codex, claude = payload["agents"]
    for agent in (codex, claude):
        assert set(agent) == expected_keys
        assert "binary" not in agent
        assert "kind" not in agent
        for option in agent["options_schema"]:
            assert set(option) == expected_option_keys
            assert "binary" not in option

    assert codex["available"] is True
    assert claude["available"] is False
    assert claude["availability_error"] == "not found"
    assert "openai_api_key" not in response.text
    assert "web_token" not in response.text
    assert "allow_custom_model" not in codex
    assert codex["models"] == ["default", "gpt-5.5", "gpt-5.4"]
    assert any(option["name"] == "effort" and option["choices"] == ["low", "medium", "high", "xhigh"] for option in codex["options_schema"])
    assert codex["defaults"]["effort"] == "high"
    assert claude["models"] == ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"]
    assert "fable" not in claude["models"]


def test_active_session_config_defaults_and_can_be_updated_while_empty(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(agents_module, "probe_cli", lambda _binary: agents_module.CliProbeResult(True, None))
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    default_response = client.get("/api/sessions/active/config")

    assert default_response.status_code == 200
    assert default_response.json()["config"]["agent_id"] == "codex"
    assert default_response.json()["config"]["editable"] is True

    update_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )

    assert update_response.status_code == 200
    assert update_response.json()["config"]["agent_id"] == "claude"
    assert update_response.json()["config"]["options"] == {"effort": "high"}

    codex_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "codex", "model": "gpt-5.5", "options": {"effort": "xhigh"}},
    )
    unsupported_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "codex", "model": "codex-5.5", "options": {"effort": "xhigh"}},
    )

    assert codex_response.status_code == 200
    assert codex_response.json()["config"]["model"] == "gpt-5.5"
    assert codex_response.json()["config"]["options"] == {"effort": "xhigh"}
    assert unsupported_response.status_code == 400


def test_active_session_config_rejects_invalid_and_locked_updates(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module
    from personal_agent_gateway.transcript import TranscriptStore

    monkeypatch.setattr(agents_module, "probe_cli", lambda _binary: agents_module.CliProbeResult(True, None))
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    store.start_new()
    store.append("user", {"content": "already started"})
    client = TestClient(create_app(config))
    client.cookies.set("agent_session", "test-session")

    invalid_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "missing", "options": {}},
    )
    locked_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {}},
    )

    assert invalid_response.status_code == 400
    assert locked_response.status_code == 409


def test_active_session_config_rejects_unavailable_agent_updates(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(
        agents_module,
        "probe_cli",
        lambda binary: agents_module.CliProbeResult(binary == "codex-test", "not found on PATH"),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Agent unavailable: claude"
