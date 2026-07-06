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


def test_create_job_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.inspect",
            "title": "Inspect",
            "input": {"source_file": "demo.mov"},
        },
    )

    assert response.status_code == 401


def test_capabilities_are_public_to_authenticated_console_boot(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/capabilities")

    assert response.status_code == 200
    assert any(item["id"] == "ffmpeg.inspect" for item in response.json()["capabilities"])
