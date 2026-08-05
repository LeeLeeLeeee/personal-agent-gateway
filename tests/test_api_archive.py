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
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
    return client


def test_archive_api_requires_authentication(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/archive/entries")

    assert response.status_code == 401


def test_delete_draft_removes_only_private_team_draft(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    draft = client.app.state.archive_service.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-1",
        kind="reference",
        title="Discard me",
        summary="",
        content_markdown="# Draft",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )

    response = client.delete(f"/api/archive/entries/{draft.id}")

    assert response.status_code == 200
    assert response.json() == {"deleted_id": draft.id}
    assert client.get("/api/archive/entries", params={"status": "draft"}).json()["entries"] == []


def test_library_publish_search_revision_and_map_api(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    persona = client.post(
        "/api/personas",
        json={"name": "Researcher", "role": "Sources", "description": ""},
    ).json()["persona"]

    created_response = client.post(
        "/api/archive/entries",
        json={
            "kind": "reference",
            "title": "SQLite FTS reference",
            "summary": "How full-text search is configured.",
            "content_markdown": "Use the unicode61 tokenizer.",
            "tags": ["sqlite", "search"],
            "source_urls": ["https://sqlite.org/fts5.html"],
            "persona_ids": [persona["id"]],
        },
    )

    assert created_response.status_code == 200
    created = created_response.json()["entry"]
    assert created["current_revision"] == 1

    searched = client.get("/api/archive/entries", params={"q": "unicode61"}).json()
    assert [entry["id"] for entry in searched["entries"]] == [created["id"]]

    updated_response = client.put(
        f"/api/archive/entries/{created['id']}",
        json={
            "kind": "reference",
            "title": created["title"],
            "summary": created["summary"],
            "content_markdown": "Use the unicode61 tokenizer and prefix queries.",
            "tags": created["tags"],
            "source_urls": created["source_urls"],
            "persona_ids": [persona["id"]],
            "change_summary": "Add prefix query note",
        },
    )
    assert updated_response.json()["entry"]["current_revision"] == 2
    revisions = client.get(
        f"/api/archive/entries/{created['id']}/revisions"
    ).json()["revisions"]
    assert [revision["revision"] for revision in revisions] == [2, 1]

    graph = client.get("/api/archive/map").json()
    assert any(node["id"] == f"entry:{created['id']}" for node in graph["nodes"])
    assert any(
        edge["source"] == f"persona:{persona['id']}"
        and edge["target"] == f"entry:{created['id']}"
        for edge in graph["edges"]
    )


def test_request_workflow_is_user_fulfilled_from_library(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    persona = client.post(
        "/api/personas",
        json={"name": "Builder", "role": "Implementation", "description": ""},
    ).json()["persona"]
    request = client.app.state.archive_service.create_knowledge_request(
        title="Release checklist",
        reason="No reusable release procedure exists.",
        suggested_outline=["Prepare", "Deploy", "Verify"],
        source_hints=["CI configuration"],
        requested_by_persona_id=persona["id"],
    )

    listed = client.get("/api/archive/requests").json()["requests"]
    assert [item["id"] for item in listed] == [request.id]

    deferred = client.patch(
        f"/api/archive/requests/{request.id}",
        json={"status": "deferred"},
    )
    assert deferred.status_code == 200
    assert deferred.json()["request"]["status"] == "deferred"

    published = client.post(
        "/api/archive/entries",
        json={
            "kind": "checklist",
            "title": request.title,
            "summary": "User-reviewed release steps.",
            "content_markdown": "- Prepare\n- Deploy\n- Verify",
            "tags": ["release"],
            "source_urls": [],
            "persona_ids": [persona["id"]],
            "request_id": request.id,
        },
    )
    assert published.status_code == 200
    resolved = client.get("/api/archive/requests", params={"status": "fulfilled"}).json()
    assert resolved["requests"][0]["fulfilled_by_entry_id"] == published.json()["entry"]["id"]


def test_knowledge_request_can_be_delegated_to_documentation_team(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = authenticated_client(tmp_path)
    personas = client.app.state.persona_service
    leader = personas.create_persona("Docs Lead", "Research", "", [], [])
    team_run = client.app.state.team_run_service.create_team_run(
        "Research and draft reusable Library documents",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    knowledge_request = client.app.state.archive_service.create_knowledge_request(
        title="Rollback checklist",
        reason="A reusable rollback procedure is missing.",
        suggested_outline=["Trigger", "Rollback", "Verify"],
        source_hints=["Runbook", "Deployment scripts"],
        requested_by_persona_id=leader.id,
    )
    enqueued: list[str] = []

    async def record_enqueue(team_run_id: str) -> None:
        enqueued.append(team_run_id)

    monkeypatch.setattr(
        client.app.state.team_cycle_dispatcher,
        "enqueue_run",
        record_enqueue,
    )

    response = client.post(
        f"/api/archive/requests/{knowledge_request.id}/delegate",
        json={"team_run_id": team_run.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request"]["status"] == "in_progress"
    assert payload["request"]["assigned_team_run_id"] == team_run.id
    cycle_request = client.app.state.team_cycle_service.get_request(
        payload["cycle_request"]["id"]
    )
    assert cycle_request.source_type == "knowledge_request"
    assert cycle_request.source_id == knowledge_request.id
    assert enqueued == [team_run.id]

    client.app.state.team_cycle_service.mark_request_settled(cycle_request.id)
    client.app.state.archive_service.update_request_status(
        knowledge_request.id,
        "open",
    )
    retry_response = client.post(
        f"/api/archive/requests/{knowledge_request.id}/delegate",
        json={"team_run_id": team_run.id},
    )
    retry_request = client.app.state.team_cycle_service.get_request(
        retry_response.json()["cycle_request"]["id"]
    )

    assert retry_response.status_code == 200
    assert retry_request.id != cycle_request.id
    assert retry_request.source_id == f"{knowledge_request.id}#attempt-2"


def test_knowledge_request_delegation_snapshots_selected_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = authenticated_client(tmp_path)
    personas = client.app.state.persona_service
    leader = personas.create_persona("Docs Lead", "Research", "", [], [])
    team_run = client.app.state.team_run_service.create_team_run(
        "Research and draft reusable Library documents",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    knowledge_request = client.app.state.archive_service.create_knowledge_request(
        title="D3 conventions",
        reason="A reusable D3 guide is missing.",
        suggested_outline=["Scope"],
        source_hints=[],
        requested_by_persona_id=leader.id,
    )
    artifact = client.app.state.artifact_store.register_bytes(
        artifact_type="markdown",
        title="d3-curriculum-draft.md",
        relative_path="team-runs/previous/d3-curriculum-draft.md",
        content=b"draft",
        mime_type="text/markdown",
    )

    async def record_enqueue(_team_run_id: str) -> None:
        return None

    monkeypatch.setattr(
        client.app.state.team_cycle_dispatcher,
        "enqueue_run",
        record_enqueue,
    )

    response = client.post(
        f"/api/archive/requests/{knowledge_request.id}/delegate",
        json={"team_run_id": team_run.id, "artifact_ids": [artifact.id]},
    )

    assert response.status_code == 200
    request_id = response.json()["cycle_request"]["id"]
    inputs = client.app.state.team_cycle_service.list_request_input_artifacts(request_id)
    assert [item.artifact_id for item in inputs] == [artifact.id]


def test_requests_api_exposes_the_last_draft_failure(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    archive = client.app.state.archive_service
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=["Signals"],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="Team response must contain exactly one Library Draft marker",
        cycle_id="cycle-1",
    )

    listed = client.get("/api/archive/requests").json()["requests"]

    item = next(entry for entry in listed if entry["id"] == request.id)
    assert item["last_draft_error_code"] == "draft_contract_violation"
    assert item["last_draft_error_message"] == (
        "Team response must contain exactly one Library Draft marker"
    )
    assert item["last_draft_cycle_id"] == "cycle-1"
    assert item["last_draft_failed_at"]
