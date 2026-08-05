from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import LmgQueryResult


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
    monkeypatch.setattr(agents_module, "fetch_capabilities", lambda _config: None)
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
        "rate_limits",
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


def test_dashboard_usage_reads_lmg_limits_with_app_config(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module
    import personal_agent_gateway.api.dashboard as dash

    monkeypatch.setattr(
        agents_module,
        "probe_cli",
        lambda _binary: agents_module.CliProbeResult(True, None),
    )
    monkeypatch.setattr(agents_module, "fetch_capabilities", lambda _config: None)
    monkeypatch.setattr(
        dash,
        "fetch_usage",
        lambda config: LmgQueryResult(
            data={
                "collected_at": "2026-07-27T00:00:00Z",
                "providers": [
                    {
                        "provider": "codex",
                        "status": "ok",
                        "rate_limits": [
                            {
                                "window_minutes": 300,
                                "used_percent": 25,
                                "resets_at": "2026-07-27T04:00:00Z",
                            }
                        ],
                    }
                ],
            },
            status="ready",
        ),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", client.app.state.auth_session_service.issue().token)

    response = client.get("/api/dashboard/usage")

    assert response.status_code == 200
    codex = response.json()["providers"][0]
    assert codex["rate_limits"] == [
        {
            "window_minutes": 300,
            "used_percent": 25.0,
            "resets_at": "2026-07-27T04:00:00Z",
            # Empty for an account-wide window; a per-model window names its model.
            "scope": "",
        }
    ]


def test_dashboard_sessions_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/dashboard/sessions")

    assert response.status_code == 401


def test_dashboard_sessions_proxies_lmg(tmp_path: Path, monkeypatch) -> None:
    import personal_agent_gateway.api.dashboard as dash

    monkeypatch.setattr(
        dash,
        "fetch_sessions",
        lambda _config: LmgQueryResult(
            data=[{"upstream_id": "s1", "provider": "codex"}],
            status="ready",
        ),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", client.app.state.auth_session_service.issue().token)

    response = client.get("/api/dashboard/sessions")

    assert response.status_code == 200
    assert response.json() == {
        "sessions": [{"upstream_id": "s1", "provider": "codex"}],
        "lmg": {"status": "ready", "message": None},
    }


def test_dashboard_sessions_exposes_lmg_failure_without_fake_empty_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import personal_agent_gateway.api.dashboard as dash

    monkeypatch.setattr(
        dash,
        "fetch_sessions",
        lambda _config: LmgQueryResult(
            data=None,
            status="not_ready",
            message="로컬 모델 게이트웨이가 준비되지 않았습니다.",
        ),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", client.app.state.auth_session_service.issue().token)

    response = client.get("/api/dashboard/sessions")

    assert response.status_code == 200
    assert response.json() == {
        "sessions": [],
        "lmg": {
            "status": "not_ready",
            "message": "로컬 모델 게이트웨이가 준비되지 않았습니다.",
        },
    }
