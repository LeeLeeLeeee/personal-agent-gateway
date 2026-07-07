from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-openai-key",
        auth_setup_token="setup-secret",
    )


def test_settings_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/settings")

    assert response.status_code == 401


def test_settings_returns_non_secret_config_snapshot(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")
    setup = client.app.state.auth_store.start_totp_setup("local-owner")
    client.app.state.auth_store.verify_totp_setup(
        client.app.state.auth_store.current_code_for_test(setup.secret),
    )

    response = client.get("/api/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert set(settings) == {
        "workspace_root",
        "session_dir",
        "artifact_root",
        "temp_dir",
        "provider",
        "model",
        "codex_binary",
        "codex_sandbox",
        "codex_approval_policy",
        "codex_timeout_seconds",
        "ffmpeg_binary",
        "ffprobe_binary",
        "capture_binary",
        "job_worker_concurrency",
        "cookie_secure",
        "totp_configured",
    }
    assert settings["provider"] == "codex"
    assert settings["totp_configured"] is True
    assert "web_token" not in settings
    assert "openai_api_key" not in settings
    assert "auth_setup_token" not in settings
    assert "totp_secret" not in settings
