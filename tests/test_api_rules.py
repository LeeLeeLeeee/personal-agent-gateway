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
    client.cookies.set("agent_session", "test-session")
    return client


def test_get_and_put_global_rules(tmp_path: Path):
    client = authenticated_client(tmp_path)
    body = client.get("/api/rules").json()
    assert "global" in body and "persona_baseline" in body and "teams" in body

    updated = client.put("/api/rules/global", json={
        "personality": "new voice",
        "rules": [{"level": "REQUIRED", "text": "no destructive writes"}],
    }).json()["rule_set"]
    assert updated["personality"] == "new voice"
    assert updated["rules"][0]["level"] == "REQUIRED"


def test_put_rules_rejects_bad_level(tmp_path: Path):
    client = authenticated_client(tmp_path)
    resp = client.put("/api/rules/global", json={
        "personality": "x", "rules": [{"level": "NOPE", "text": "bad"}],
    })
    assert resp.status_code == 400


def test_rules_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    assert client.get("/api/rules").status_code == 401


def test_put_team_rules_404s_for_unknown_team(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    resp = client.put("/api/teams/bad-team-id/rules", json={
        "personality": "x", "rules": [],
    })
    assert resp.status_code == 404
