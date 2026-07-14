from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.agents import AgentRegistry, CliProbeResult
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
    config = make_config(tmp_path)
    app = create_app(config)
    app.state.agent_registry = AgentRegistry(
        config,
        probe=lambda _binary: CliProbeResult(True, None),
    )
    client = TestClient(app)
    client.cookies.set("agent_session", "test-session")
    return client


def test_persona_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/personas")

    assert response.status_code == 401


def test_create_and_list_personas_api(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    create_response = client.post(
        "/api/personas",
        json={
            "name": "Tech Lead",
            "role": "Planning and integration",
            "description": "Splits goals and integrates results.",
            "responsibilities": ["Plan tasks", "Review results"],
            "constraints": ["Keep scope small"],
            "default_backend": "codex",
            "default_model": "default",
        },
    )

    assert create_response.status_code == 200
    persona = create_response.json()["persona"]
    assert persona["name"] == "Tech Lead"

    list_response = client.get("/api/personas")

    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()["personas"]] == ["Tech Lead"]


def test_patch_persona_partial_update_preserves_unset_fields(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    create_response = client.post(
        "/api/personas",
        json={
            "name": "QA Engineer",
            "role": "Testing",
            "description": "Writes and runs tests.",
            "responsibilities": ["Write tests", "Run suites"],
            "constraints": ["No flaky tests"],
            "default_backend": "codex",
            "default_model": "gpt-5.5",
        },
    )
    assert create_response.status_code == 200
    persona_id = create_response.json()["persona"]["id"]

    patch_response = client.patch(
        f"/api/personas/{persona_id}",
        json={"name": "New Name"},
    )

    assert patch_response.status_code == 200
    persona = patch_response.json()["persona"]
    assert persona["name"] == "New Name"
    assert persona["responsibilities"] == ["Write tests", "Run suites"]
    assert persona["constraints"] == ["No flaky tests"]
    assert persona["default_backend"] == "codex"
    assert persona["default_model"] == "gpt-5.5"


def test_persona_api_validates_and_returns_default_options(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post(
        "/api/personas",
        json={
            "name": "Local Codex",
            "role": "local",
            "description": "uses detected capabilities",
            "default_backend": "codex",
            "default_model": "gpt-5.4",
            "default_options": {"effort": "xhigh", "sandbox": "workspace-write"},
        },
    )

    assert response.status_code == 200
    assert response.json()["persona"]["default_options"] == {
        "effort": "xhigh",
        "sandbox": "workspace-write",
    }

    invalid = client.post(
        "/api/personas",
        json={
            "name": "Invalid",
            "role": "local",
            "description": "invalid effort",
            "default_backend": "codex",
            "default_model": "gpt-5.4",
            "default_options": {"effort": "ultra"},
        },
    )
    assert invalid.status_code == 400


def test_create_persona_with_avatar_returns_it(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post(
        "/api/personas",
        json={"name": "Lead", "role": "r", "description": "d", "avatar": "dev-glasses"},
    )

    assert response.status_code == 200
    assert response.json()["persona"]["avatar"] == "dev-glasses"


def test_patch_persona_preserves_avatar(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    created = client.post(
        "/api/personas",
        json={"name": "Lead", "role": "r", "description": "d", "avatar": "owl"},
    ).json()["persona"]

    patched = client.patch(
        f"/api/personas/{created['id']}",
        json={"name": "Lead 2"},
    ).json()["persona"]

    assert patched["name"] == "Lead 2"
    assert patched["avatar"] == "owl"


def test_avatar_asset_is_served(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/static/avatars/fox.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
