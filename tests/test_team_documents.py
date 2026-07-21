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
def authed_client_with_run(tmp_path: Path):
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = client.post(
        "/api/teams",
        json={
            "name": "Team",
            "description": "",
            "leader_persona_id": leader_id,
            "member_persona_ids": [],
        },
    ).json()["team"]["id"]
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship it",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    return client, run, workspace


def test_documents_list_and_content(authed_client_with_run):
    client, run, workspace = authed_client_with_run
    (Path(workspace) / "notes.md").write_text("# Title\nhello", encoding="utf-8")
    (Path(workspace) / "data.json").write_text('{"a":1}', encoding="utf-8")

    listing = client.get(f"/api/team-runs/{run['id']}/documents").json()["documents"]
    paths = {d["path"] for d in listing}
    assert "notes.md" in paths and "data.json" in paths
    md = next(d for d in listing if d["path"] == "notes.md")
    assert md["kind"] == "md" and md["previewable"] is True

    content = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "notes.md"}
    ).json()
    assert content["content"] == "# Title\nhello"
    assert content["kind"] == "md"


def test_documents_content_rejects_traversal(authed_client_with_run):
    client, run, _ = authed_client_with_run
    resp = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "../../etc/passwd"}
    )
    assert resp.status_code == 400
