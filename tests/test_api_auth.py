from pathlib import Path

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
    assert response.cookies.get("agent_session") is not None


def test_auth_logout_clears_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert "agent_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_totp_setup_start_requires_web_token_cookie(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/auth/setup/start")

    assert response.status_code == 401


def test_totp_setup_start_returns_uri_and_qr_after_token_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.get("/?token=secret-token")

    response = client.post("/api/auth/setup/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["otpauth_uri"].startswith("otpauth://totp/")
    assert payload["secret"]
    assert "<svg" in payload["qr_svg"]


def test_totp_setup_verify_enables_login_from_browser_flow(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config))
    client.get("/?token=secret-token")
    setup_response = client.post("/api/auth/setup/start")
    setup_code = AuthStore(config.auth_dir).current_code_for_test(
        setup_response.json()["secret"],
    )

    response = client.post("/api/auth/setup/verify", json={"otp": setup_code})

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert len(response.json()["recovery_codes"]) == 10

    login_response = client.post("/api/auth/login", json={"otp": setup_code})
    assert login_response.status_code == 200
