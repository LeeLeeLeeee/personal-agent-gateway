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
        openai_api_key="test-key",
    )


def authenticated_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
    return client


def _create_body() -> dict:
    return {
        "name": "Inbox watcher",
        "source_type": "email",
        "connection": {"host": "imap.test", "port": 993, "username": "me@test"},
        "secret": "app-password",
        "filter": {"from_contains": "boss"},
        "target_backend": "codex",
        "target_model": "default",
        "target_options": {},
        "prompt_template": "요약: {{subject}}",
        "poll_interval_seconds": 300,
    }


def test_list_hooks_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    assert client.get("/api/hooks").status_code == 401


def test_create_hook_hides_secret(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    response = client.post("/api/hooks", json=_create_body())
    assert response.status_code == 200
    hook = response.json()["hook"]
    assert hook["name"] == "Inbox watcher"
    assert hook["enabled"] is True
    assert hook["target_backend"] == "codex"
    serialized = response.text
    assert "app-password" not in serialized
    assert "connection_ref" not in hook


def test_get_and_list_and_delete(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]

    assert client.get(f"/api/hooks/{hook_id}").status_code == 200
    assert len(client.get("/api/hooks").json()["hooks"]) == 1

    assert client.delete(f"/api/hooks/{hook_id}").json() == {"deleted": True}
    assert client.get("/api/hooks").json()["hooks"] == []


def test_patch_toggles_enabled(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]

    response = client.patch(f"/api/hooks/{hook_id}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["hook"]["enabled"] is False


def test_get_missing_hook_returns_404(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.get("/api/hooks/nope").status_code == 404


def test_run_now_returns_created_count(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]
    # 실제 IMAP 접속은 실패하므로(호스트 없음) poll_hook은 오류를 삼키고 0건 생성.
    response = client.post(f"/api/hooks/{hook_id}/run-now")
    assert response.status_code == 200
    assert response.json() == {"created": 0}
    runs = client.get(f"/api/hooks/{hook_id}/runs")
    assert runs.status_code == 200
    assert runs.json()["runs"] == []


def test_test_connection_unreachable_returns_ok_false(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    response = client.post(
        "/api/hooks/test-connection",
        json={
            "connection": {"host": "imap.invalid.test", "port": 993, "username": "me@test"},
            "secret": "app-password",
            "filter": {"folder": "INBOX"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "app-password" not in response.text


def test_test_connection_unsupported_source_returns_400(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    response = client.post(
        "/api/hooks/test-connection",
        json={"source_type": "slack", "connection": {}, "secret": "x"},
    )
    assert response.status_code == 400


def test_test_connection_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    response = client.post(
        "/api/hooks/test-connection",
        json={"connection": {}, "secret": "x"},
    )
    assert response.status_code == 401
