from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.db import Database


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


def test_artifact_payload_includes_created_at_and_thumbnail_path(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
    )

    response = client.get("/api/artifacts")

    assert response.status_code == 200
    payload = response.json()["artifacts"][0]
    assert payload["id"] == artifact.id
    assert payload["created_at"]
    assert payload["thumbnail_path"] is None


def test_get_artifact_content_returns_file_bytes(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
    )

    response = client.get(f"/api/artifacts/{artifact.id}/content")

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-type"].startswith("text/plain")
    assert "run.log" in response.headers["content-disposition"]


def test_get_artifact_content_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/artifacts/missing/content")

    assert response.status_code == 401


def test_get_artifact_content_returns_404_for_unknown_id(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.get("/api/artifacts/missing/content")

    assert response.status_code == 404


def test_get_artifact_thumbnail_returns_thumbnail_when_present(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="image",
        title="capture.png",
        relative_path="images/capture.png",
        content=b"image-content",
        mime_type="image/png",
    )
    thumbnail = tmp_path / "data" / "artifacts" / "thumbs" / "capture.png"
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"thumb-content")
    Database(client.app.state.app_config.app_db_path).execute(
        "update artifacts set thumbnail_path = ? where id = ?",
        (str(thumbnail), artifact.id),
    )

    response = client.get(f"/api/artifacts/{artifact.id}/thumbnail")

    assert response.status_code == 200
    assert response.content == b"thumb-content"
    assert response.headers["content-type"].startswith("image/png")


def test_register_artifact_copies_workspace_file(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "out").mkdir()
    (workspace / "out" / "cat.png").write_bytes(b"img-bytes")

    response = client.post(
        "/api/artifacts/register",
        json={"path": "out/cat.png", "session_id": "sess-1"},
    )

    assert response.status_code == 200
    artifact = response.json()["artifact"]
    assert artifact["type"] == "image"
    assert artifact["title"] == "cat.png"
    assert artifact["source_session_id"] == "sess-1"
    # original stays in the workspace (copy, not move)
    assert (workspace / "out" / "cat.png").exists()
    # content is retrievable through the existing content endpoint
    content = client.get(f"/api/artifacts/{artifact['id']}/content")
    assert content.content == b"img-bytes"


def test_register_artifact_rejects_path_outside_workspace(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"nope")

    response = client.post(
        "/api/artifacts/register",
        json={"path": "../secret.png"},
    )

    assert response.status_code == 400


def test_register_artifact_rejects_absolute_path_outside_workspace(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"nope")

    response = client.post(
        "/api/artifacts/register",
        json={"path": str(outside)},
    )

    assert response.status_code == 400


def test_register_artifact_rejects_directory_path(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "sub").mkdir()

    response = client.post("/api/artifacts/register", json={"path": "sub"})

    assert response.status_code == 404


def test_register_artifact_rejects_unknown_extension(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "script.py").write_text("print('x')")

    response = client.post("/api/artifacts/register", json={"path": "script.py"})

    assert response.status_code == 415


def test_register_artifact_returns_404_for_missing_file(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post("/api/artifacts/register", json={"path": "gone.png"})

    assert response.status_code == 404


def test_register_artifact_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/artifacts/register", json={"path": "x.png"})

    assert response.status_code == 401
