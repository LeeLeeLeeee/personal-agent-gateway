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


def test_access_mode_defaults_restricted_and_requires_confirmation_for_full_access(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)
    client.cookies.set("agent_session", app.state.auth_session_service.issue().token)

    settings = client.get("/api/settings").json()["settings"]
    assert settings["access_mode"] == "restricted"
    assert settings["workspace_writable"] is True

    rejected = client.put(
        "/api/settings/access-mode",
        json={"mode": "full_access", "confirm_full_access": False},
    )
    assert rejected.status_code == 400

    changed = client.put(
        "/api/settings/access-mode",
        json={"mode": "full_access", "confirm_full_access": True},
        headers={"X-Correlation-ID": "corr-mode"},
    )
    assert changed.status_code == 200
    assert changed.json()["access_mode"] == "full_access"
    assert app.state.security_settings.access_mode == "full_access"
    events = app.state.audit_service.list(correlation_id="corr-mode")
    assert any(event.event_type == "security.access_mode.changed" for event in events)


def test_settings_returns_non_secret_config_snapshot(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
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
        "codex_idle_timeout_seconds",
        "ffmpeg_binary",
        "ffprobe_binary",
        "capture_binary",
        "job_worker_concurrency",
        "effective_job_concurrency",
        "cookie_secure",
        "totp_configured",
        "session_authenticated",
        "bind_host",
        "tunnel_mode",
        "worker_alive",
        "worker_last_error",
        "scheduler_alive",
        "scheduler_last_error",
        "automation_ready",
        "automation_unavailable_reason",
        "team_review_supported",
            "team_execution_mode",
            "agent_availability",
            "access_mode",
            "workspace_writable",
            "active_session_count",
            "audit_enabled",
            "audit_retention_days",
            "schema_version",
        }
    assert settings["provider"] == "codex"
    assert settings["totp_configured"] is True
    assert settings["session_authenticated"] is True
    assert settings["tunnel_mode"] == "not_reported"
    assert settings["worker_alive"] is False
    assert settings["scheduler_alive"] is False
    assert settings["automation_ready"] is False
    assert settings["team_review_supported"] is False
    assert settings["team_execution_mode"] == "sequential"
    assert settings["job_worker_concurrency"] == 1
    assert settings["effective_job_concurrency"] == 1
    assert "web_token" not in settings
    assert "openai_api_key" not in settings
    assert "auth_setup_token" not in settings
    assert "totp_secret" not in settings


def test_settings_reports_live_automation_inside_app_lifespan(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))

    with TestClient(app) as client:
        client.cookies.set(
            "agent_session",
            app.state.auth_session_service.issue().token,
        )
        settings = client.get("/api/settings").json()["settings"]

        assert settings["worker_alive"] is True
        assert settings["scheduler_alive"] is True
        assert settings["automation_ready"] is True
        assert settings["automation_unavailable_reason"] is None
