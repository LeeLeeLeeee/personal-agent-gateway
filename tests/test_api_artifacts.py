from datetime import datetime, timezone
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
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
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


def test_artifact_browser_returns_resolved_breadcrumbs(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="markdown",
        title="draft.md",
        relative_path="files/draft.md",
        content=b"# Draft",
        mime_type="text/markdown",
        origin_kind="chat_upload",
        artifact_role="attachment",
        origin_group_label_snapshot="Release discussion",
        origin_item_label_snapshot="Please draft release notes",
    )

    response = client.get("/api/artifacts/browser", params={"q": "release notes"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["artifact"]["id"] == artifact.id
    assert item["breadcrumbs"][0]["label"] == "Release discussion"


def test_batch_delete_rejects_duplicate_artifact_ids(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post("/api/artifacts/delete", json={"artifact_ids": ["a1", "a1"]})

    assert response.status_code == 422


def test_cleanup_preview_reports_expired_temporary_artifact_without_deleting_it(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
        retention_class="temporary",
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    response = client.get("/api/artifacts/cleanup-preview")

    assert response.status_code == 200
    assert response.json()["artifact_ids"] == [artifact.id]
    assert response.json()["total_size_bytes"] == artifact.size_bytes
    assert client.app.state.artifact_store.get(artifact.id).id == artifact.id


def test_cleanup_and_retention_update_return_safe_results(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    store = client.app.state.artifact_store
    expired = store.register_bytes(
        "text", "expired.log", "logs/expired.log", b"expired", "text/plain",
        retention_class="temporary", expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    durable = store.register_bytes(
        "text", "saved.log", "logs/saved.log", b"saved", "text/plain",
    )

    cleaned = client.post(
        "/api/artifacts/cleanup", json={"artifact_ids": [expired.id, durable.id]}
    )
    updated = client.patch(
        f"/api/artifacts/{durable.id}/retention", json={"retention_class": "pinned"}
    )

    assert cleaned.status_code == 200
    assert cleaned.json() == {"deleted_ids": [expired.id], "skipped_ids": [durable.id]}
    assert updated.status_code == 200
    assert updated.json()["artifact"]["retention_class"] == "pinned"
    assert updated.json()["artifact"]["expires_at"] is None
    events = client.app.state.audit_service.list(resource_type="artifact_cleanup")
    assert any(event.action == "artifacts.cleanup" for event in events)


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


def test_register_artifact_rejects_absolute_path_outside_workspace_in_restricted_mode(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    outside = tmp_path / "capture.png"
    outside.write_bytes(b"img")

    response = client.post(
        "/api/artifacts/register",
        json={"path": str(outside)},
    )

    assert response.status_code == 403


def test_register_artifact_audits_absolute_path_in_full_access_mode(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    outside = tmp_path / "capture.png"
    outside.write_bytes(b"img")
    client.app.state.security_settings.set_access_mode("full_access")

    response = client.post(
        "/api/artifacts/register",
        json={"path": str(outside)},
        headers={"X-Correlation-ID": "corr-artifact"},
    )

    assert response.status_code == 200
    assert response.json()["artifact"]["title"] == "capture.png"
    events = client.app.state.audit_service.list(correlation_id="corr-artifact")
    assert any(event.event_type == "artifact.external_path.registered" for event in events)


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


def test_register_artifact_rejects_duplicate_source_path(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "dup.png").write_bytes(b"img")

    first = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert first.status_code == 200
    first_id = first.json()["artifact"]["id"]

    second = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert second.status_code == 409
    assert second.json()["detail"]["artifact"]["id"] == first_id


def test_delete_artifact_removes_it_and_frees_reregistration(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "dup.png").write_bytes(b"img")

    created = client.post("/api/artifacts/register", json={"path": "dup.png"}).json()["artifact"]

    deleted = client.delete(f"/api/artifacts/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/api/artifacts/{created['id']}").status_code == 404

    # source path is free again
    again = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert again.status_code == 200


def test_delete_artifact_returns_404_for_unknown_id(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.delete("/api/artifacts/missing").status_code == 404
