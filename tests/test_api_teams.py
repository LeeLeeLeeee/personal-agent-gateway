from pathlib import Path

import pytest
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


def create_persona(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/personas",
        json={
            "name": name,
            "role": f"{name} role",
            "description": f"{name} description",
            "responsibilities": ["Do assigned work"],
            "constraints": ["Report evidence"],
        },
    )
    return response.json()["persona"]["id"]


@pytest.fixture
def authed_client_with_personas(tmp_path: Path):
    client = authenticated_client(tmp_path)
    lead_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")
    return client, lead_id, member_id


def test_team_crud(authed_client_with_personas):
    client, lead_id, member_id = authed_client_with_personas
    created = client.post("/api/teams", json={
        "name": "Release Crew", "description": "ships",
        "leader_persona_id": lead_id, "member_persona_ids": [member_id],
    }).json()["team"]
    assert created["name"] == "Release Crew"

    listed = client.get("/api/teams").json()["teams"]
    assert any(t["id"] == created["id"] for t in listed)

    updated = client.put(f"/api/teams/{created['id']}", json={
        "name": "Release", "member_persona_ids": [],
    }).json()["team"]
    assert updated["name"] == "Release" and updated["member_persona_ids"] == []

    assert client.delete(f"/api/teams/{created['id']}").json()["deleted"] is True


def test_create_team_rejects_unknown_persona(authed_client_with_personas):
    client, lead_id, _ = authed_client_with_personas
    resp = client.post("/api/teams", json={
        "name": "X", "description": "", "leader_persona_id": lead_id,
        "member_persona_ids": ["nope"],
    })
    assert resp.status_code == 400


def test_teams_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    assert client.get("/api/teams").status_code == 401
