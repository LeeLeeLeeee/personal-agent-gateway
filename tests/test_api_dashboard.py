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


def test_dashboard_usage_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/dashboard/usage")

    assert response.status_code == 401


def test_dashboard_usage_returns_provider_usage(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(
        agents_module,
        "probe_cli",
        lambda binary: agents_module.CliProbeResult(
            binary == "codex-test",
            None if binary == "codex-test" else "not found",
        ),
    )
    monkeypatch.setattr(agents_module, "detect_local_agent_capabilities", lambda _config: None)
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", client.app.state.auth_session_service.issue().token)

    response = client.get("/api/dashboard/usage")

    assert response.status_code == 200
    payload = response.json()
    assert "detected_at" in payload
    assert [provider["provider"] for provider in payload["providers"]] == ["codex", "claude"]

    expected_keys = {
        "provider",
        "label",
        "available",
        "availability_error",
        "version",
        "model",
        "weekly_limit",
        "used",
        "remaining",
        "reset_at",
        "usage_status",
        "usage_source",
        "note",
    }
    codex, claude = payload["providers"]
    for provider in (codex, claude):
        assert set(provider) == expected_keys

    assert codex["available"] is True
    assert codex["usage_status"] == "unconfirmed"
    assert codex["weekly_limit"] is None
    assert codex["used"] is None
    assert codex["remaining"] is None
    assert codex["reset_at"] is None
    assert codex["note"] is not None

    assert claude["available"] is False
    assert claude["usage_status"] == "unavailable"
    assert claude["availability_error"] == "not found"
