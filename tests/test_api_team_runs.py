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


def test_create_team_run_api_snapshots_agents(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")

    response = client.post(
        "/api/team-runs",
        json={
            "goal": "Design Agent Teams",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "planning_only",
            "max_workers": 2,
        },
    )

    assert response.status_code == 200
    run = response.json()["team_run"]
    assert run["goal"] == "Design Agent Teams"
    assert run["status"] == "draft"

    agents = client.get(f"/api/team-runs/{run['id']}/agents").json()["agents"]
    assert [agent["name"] for agent in agents] == ["Tech Lead", "QA Tester"]


def test_delete_team_run_removes_it(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")

    run = client.post(
        "/api/team-runs",
        json={"goal": "Ship it", "leader_persona_id": leader_id},
    ).json()["team_run"]

    deleted = client.delete(f"/api/team-runs/{run['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    assert client.get(f"/api/team-runs/{run['id']}").status_code == 404
    assert client.get("/api/team-runs").json()["team_runs"] == []


def test_delete_missing_team_run_returns_404(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.delete("/api/team-runs/does-not-exist").status_code == 404


def test_team_run_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/team-runs")

    assert response.status_code == 401
