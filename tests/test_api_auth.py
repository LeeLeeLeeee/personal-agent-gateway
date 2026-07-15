from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.auth_store import AuthStore
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        auth_dir=tmp_path / "data" / "auth",
        auth_setup_token="setup-token",
        openai_api_key="test-key",
    )


def test_auth_status_reports_totp_required_when_not_configured(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["totp_configured"] is False


def test_auth_login_with_otp_sets_session_cookie(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    auth_store = AuthStore(config.auth_dir)
    setup = auth_store.start_totp_setup(account_name="local-owner")
    auth_store.verify_totp_setup(auth_store.current_code_for_test(setup.secret))
    code = auth_store.current_login_code_for_test()
    client = TestClient(create_app(config))

    response = client.post("/api/auth/login", json={"otp": code})

    assert response.status_code == 200
    token = response.cookies.get("agent_session")
    assert token is not None
    assert client.app.state.auth_session_service.validate(token) is not None
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_auth_login_with_recovery_code_sets_session_cookie(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    auth_store = AuthStore(config.auth_dir)
    setup = auth_store.start_totp_setup(account_name="local-owner")
    result = auth_store.verify_totp_setup(auth_store.current_code_for_test(setup.secret))
    client = TestClient(create_app(config))

    response = client.post("/api/auth/login", json={"otp": result.recovery_codes[0]})

    assert response.status_code == 200
    assert response.cookies.get("agent_session") is not None


def test_auth_login_rejects_reused_recovery_code(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    auth_store = AuthStore(config.auth_dir)
    setup = auth_store.start_totp_setup(account_name="local-owner")
    result = auth_store.verify_totp_setup(auth_store.current_code_for_test(setup.secret))
    client = TestClient(create_app(config))
    code = result.recovery_codes[0]

    first_response = client.post("/api/auth/login", json={"otp": code})
    second_response = client.post("/api/auth/login", json={"otp": code})

    assert first_response.status_code == 200
    assert second_response.status_code == 401


def test_auth_logout_clears_cookie(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)
    token = app.state.auth_session_service.issue().token
    client.cookies.set("agent_session", token)

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert "agent_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    client.cookies.set("agent_session", token)
    assert client.get("/api/settings").status_code == 401


def test_owner_can_list_and_revoke_other_sessions(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    current = app.state.auth_session_service.issue()
    other = app.state.auth_session_service.issue()
    client = TestClient(app)
    client.cookies.set("agent_session", current.token)

    response = client.get("/api/auth/sessions")

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert {item["id"] for item in sessions} == {current.principal.id, other.principal.id}
    assert next(item for item in sessions if item["id"] == current.principal.id)["current"] is True
    assert all("token" not in key for item in sessions for key in item)

    revoked = client.delete(f"/api/auth/sessions/{other.principal.id}")
    assert revoked.status_code == 200
    assert app.state.auth_session_service.validate(other.token) is None
    assert app.state.auth_session_service.validate(current.token) is not None


def test_revoke_all_sessions_invalidates_current_cookie(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    current = app.state.auth_session_service.issue()
    app.state.auth_session_service.issue()
    client = TestClient(app)
    client.cookies.set("agent_session", current.token)

    response = client.post("/api/auth/sessions/revoke-all")

    assert response.status_code == 200
    assert response.json()["revoked_count"] == 2
    assert "Max-Age=0" in response.headers["set-cookie"]
    client.cookies.set("agent_session", current.token)
    assert client.get("/api/settings").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/settings",
        "/api/jobs",
        "/api/schedules",
        "/api/personas",
        "/api/team-runs",
        "/api/teams",
        "/api/rules",
        "/api/agents",
        "/api/capabilities",
        "/api/artifacts",
    ],
)
def test_protected_apis_reject_forged_session_cookie(tmp_path: Path, path: str) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "forged-session")

    response = client.get(path)

    assert response.status_code == 401


def test_auth_status_rejects_forged_session_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "forged-session")

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False


def test_state_change_rejects_cross_origin_request(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)
    client.cookies.set("agent_session", app.state.auth_session_service.issue().token)

    response = client.post(
        "/api/reset",
        headers={"Origin": "https://example.invalid"},
    )

    assert response.status_code == 403


def test_login_rate_limit_blocks_repeated_failures(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    for _ in range(5):
        assert client.post("/api/auth/login", json={"otp": "000000"}).status_code == 401

    response = client.post("/api/auth/login", json={"otp": "000000"})

    assert response.status_code == 429
    assert response.headers["retry-after"]


def test_totp_setup_start_requires_web_token_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/auth/setup/start")

    assert response.status_code == 401


def test_totp_setup_start_returns_uri_and_qr_after_token_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/auth/setup/start?token=setup-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["otpauth_uri"].startswith("otpauth://totp/")
    assert payload["secret"]
    assert "<svg" in payload["qr_svg"]


def test_totp_setup_verify_enables_login_from_browser_flow(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config))
    setup_response = client.post("/api/auth/setup/start?token=setup-token")
    setup_code = AuthStore(config.auth_dir).current_code_for_test(
        setup_response.json()["secret"],
    )

    response = client.post(
        "/api/auth/setup/verify?token=setup-token",
        json={"otp": setup_code},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert len(response.json()["recovery_codes"]) == 10
    assert response.cookies.get("agent_session") is not None

    login_response = client.post("/api/auth/login", json={"otp": setup_code})
    assert login_response.status_code == 200
