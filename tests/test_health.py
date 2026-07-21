import logging
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        model_provider="codex",
        openai_api_key="test-key",
    )


def _available_codex():
    return [SimpleNamespace(id="codex", available=True, availability_error=None)]


def test_live_is_public_and_ready_reports_component_status(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    app = create_app(config)
    app.state.agent_registry.catalog = _available_codex

    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert {item["name"] for item in ready.json()["components"]} == {
        "database",
        "worker",
        "scheduler",
        "hook_loop",
        "hook_runner",
        "team_cycle_dispatcher",
        "team_cycle_loop",
        "cli",
        "intake",
    }
    assert str(config.workspace_root) not in ready.text


def test_one_failed_component_makes_only_ready_fail(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    app.state.agent_registry.catalog = _available_codex

    with TestClient(app) as client:
        app.state.health_service._scheduler = SimpleNamespace(
            alive=False,
            last_error="scheduler stopped",
        )
        ready = client.get("/health/ready")
        live = client.get("/health/live")

    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    scheduler = next(item for item in ready.json()["components"] if item["name"] == "scheduler")
    assert scheduler == {"name": "scheduler", "ready": False, "detail": "not running"}
    assert live.status_code == 200


def test_hook_provider_error_is_degraded_but_alive_component_remains_ready(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    app.state.agent_registry.catalog = _available_codex

    with TestClient(app) as client:
        app.state.health_service._hook_loop = SimpleNamespace(
            alive=True,
            last_error="provider unavailable",
        )
        ready = client.get("/health/ready")

    assert ready.status_code == 200
    hook_loop = next(
        item for item in ready.json()["components"] if item["name"] == "hook_loop"
    )
    assert hook_loop == {
        "name": "hook_loop",
        "ready": True,
        "detail": "degraded: provider unavailable",
    }


def test_unhandled_error_returns_and_logs_redacted_correlation_id(
    tmp_path: Path,
    caplog,
) -> None:
    app = create_app(make_config(tmp_path))

    @app.get("/boom")
    def boom():
        raise RuntimeError("failed with super-secret")

    caplog.set_level(logging.ERROR)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Correlation-ID": "corr-test"})

    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"] == "corr-test"
    assert response.json() == {
        "code": "internal_error",
        "detail": "Internal Server Error",
        "retryable": True,
        "correlation_id": "corr-test",
    }
    assert "corr-test" in caplog.text
    assert "super-secret" not in caplog.text
