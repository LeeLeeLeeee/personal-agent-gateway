from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def _client(tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        AppConfig(
            web_token="secret",
            workspace_root=workspace,
            session_dir=tmp_path / "data" / "sessions",
        )
    )
    client = TestClient(app)
    client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
    return client


def _persona(client: TestClient, name: str) -> str:
    return client.post(
        "/api/personas",
        json={"name": name, "role": "role", "description": "description"},
    ).json()["persona"]["id"]


def test_space_api_exposes_required_global_and_team_and_optional_persona(tmp_path: Path) -> None:
    client = _client(tmp_path)
    persona_id = _persona(client, "Lead")
    team = client.post(
        "/api/teams",
        json={
            "name": "Crew",
            "leader_persona_id": persona_id,
            "member_persona_ids": [],
        },
    ).json()["team"]

    initial = client.get("/api/spaces").json()
    assert initial["global"]["effective_source"] == "global"
    assert initial["personas"] == []
    assert initial["teams"][0]["scope_id"] == team["id"]

    selected = tmp_path / "selected"
    selected.mkdir()
    saved = client.put(
        f"/api/spaces/personas/{persona_id}",
        json={
            "read_mode": "selected",
            "read_path": str(selected),
            "write_mode": "isolated",
        },
    )
    assert saved.status_code == 200
    assert client.get("/api/spaces").json()["personas"][0]["scope_id"] == persona_id

    assert client.delete(f"/api/spaces/personas/{persona_id}").json() == {"deleted": True}
    assert client.get("/api/spaces").json()["personas"] == []


def test_space_api_rejects_relative_paths_and_persona_worktrees(tmp_path: Path) -> None:
    client = _client(tmp_path)
    persona_id = _persona(client, "Lead")

    relative = client.put(
        "/api/spaces/global",
        json={
            "read_mode": "selected",
            "read_path": "relative/path",
            "write_mode": "isolated",
        },
    )
    assert relative.status_code == 400

    worktree = client.put(
        f"/api/spaces/personas/{persona_id}",
        json={
            "read_mode": "home",
            "write_mode": "worktree",
            "workspace_path": str(tmp_path),
        },
    )
    assert worktree.status_code == 400

