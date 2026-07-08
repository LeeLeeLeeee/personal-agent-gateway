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
            "default_backend": "openai",
            "default_model": "gpt-5",
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
    assert persona["default_backend"] == "openai"
    assert persona["default_model"] == "gpt-5"
