from pathlib import Path
import subprocess

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
    assert initial["global"]["read_mode"] == "none"
    assert initial["global"]["read_path"] is None
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


def test_space_api_accepts_explicit_no_source(tmp_path: Path) -> None:
    client = _client(tmp_path)

    saved = client.put(
        "/api/spaces/global",
        json={
            "read_mode": "none",
            "read_path": str(tmp_path),
            "write_mode": "isolated",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["space_policy"]["read_path"] is None


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


def test_space_api_rejects_workspace_combinations_that_cannot_run(tmp_path: Path) -> None:
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

    unbounded_isolated = client.put(
        f"/api/spaces/teams/{team['id']}",
        json={
            "read_mode": "home",
            "write_mode": "isolated",
        },
    )
    assert unbounded_isolated.status_code == 400
    assert "bounded source directory" in unbounded_isolated.json()["detail"]

    plain_directory = tmp_path / "plain"
    plain_directory.mkdir()
    non_git_worktree = client.put(
        f"/api/spaces/teams/{team['id']}",
        json={
            "read_mode": "none",
            "write_mode": "worktree",
            "workspace_path": str(plain_directory),
        },
    )
    assert non_git_worktree.status_code == 400
    assert "Git repository" in non_git_worktree.json()["detail"]


def test_space_api_reports_user_facing_workspace_capabilities(tmp_path: Path) -> None:
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
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "README.md").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "init"], check=True, capture_output=True)

    saved = client.put(
        f"/api/spaces/teams/{team['id']}",
        json={
            "read_mode": "none",
            "write_mode": "worktree",
            "workspace_path": str(repository),
        },
    )

    capability = saved.json()["space_policy"]["capability"]
    assert capability == {
        "ready": True,
        "read_summary": "Reads the selected Git repository",
        "write_summary": "Changes stay on a new Team Run branch",
        "changes_originals": False,
        "issues": [],
    }
