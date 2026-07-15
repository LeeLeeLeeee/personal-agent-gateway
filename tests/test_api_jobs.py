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


def test_capabilities_are_available_to_authenticated_console_boot(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.get("/api/capabilities")

    assert response.status_code == 200
    assert any(item["id"] == "ffmpeg.inspect" for item in response.json()["capabilities"])


def test_approved_job_payload_exposes_timestamps(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    create_response = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.extract-audio",
            "title": "Extract",
            "input": {"source_file": "demo.mov", "format": "m4a"},
        },
    )
    job_id = create_response.json()["job"]["id"]

    response = client.post(f"/api/jobs/{job_id}/approve")

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["created_at"]
    assert job["started_at"] is None
    assert job["finished_at"] is None
    assert {
        event.action
        for event in client.app.state.audit_service.list(resource_type="job")
        if event.resource_id == job_id
    } >= {"jobs.create", "jobs.approve"}


def test_chat_job_cannot_be_approved_by_worker_api(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    job = client.app.state.job_service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="chat",
        title="Chat command history",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )

    response = client.post(f"/api/jobs/{job.id}/approve")

    assert response.status_code == 409
    assert client.app.state.job_service.get_job(job.id).status == "waiting_approval"


def test_job_events_are_returned_in_lifecycle_order(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    create_response = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.extract-audio",
            "title": "Extract",
            "input": {"source_file": "demo.mov", "format": "m4a"},
        },
    )
    job_id = create_response.json()["job"]["id"]
    client.post(f"/api/jobs/{job_id}/approve")
    client.app.state.job_service.mark_running(job_id)
    client.app.state.job_service.mark_succeeded(job_id)

    response = client.get(f"/api/jobs/{job_id}/events")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["kind"] for event in events] == [
        "created",
        "approved",
        "running",
        "succeeded",
    ]
    assert all(event["id"] for event in events)
    assert all(event["created_at"] for event in events)
    assert events[0]["payload"] == {"status": "waiting_approval"}


def test_list_jobs_filters_by_status(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    response = client.get("/api/jobs", params={"status": "waiting_approval"})

    assert response.status_code == 200
    assert [job["title"] for job in response.json()["jobs"]] == ["Extract"]


def test_list_jobs_filters_by_source(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    response = client.get("/api/jobs", params={"source": "schedule"})

    assert response.status_code == 200
    assert [job["title"] for job in response.json()["jobs"]] == ["Scheduled inspect"]


def test_list_jobs_filters_by_combined_source_and_capability(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    response = client.get(
        "/api/jobs",
        params={"source": "schedule", "capability_id": "ffmpeg.inspect"},
    )

    assert response.status_code == 200
    assert [job["title"] for job in response.json()["jobs"]] == ["Scheduled inspect"]


def test_list_jobs_repeatable_status_filters_match_any(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    response = client.get(
        "/api/jobs",
        params=[("status", "queued"), ("status", "waiting_approval")],
    )

    assert response.status_code == 200
    assert {job["title"] for job in response.json()["jobs"]} == {
        "Inspect",
        "Extract",
        "Scheduled inspect",
    }


def test_list_jobs_without_filters_returns_all_jobs(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 3


def test_list_jobs_returns_cursor_for_bounded_pages(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    seed_jobs(client)

    first = client.get("/api/jobs", params={"limit": 2}).json()
    second = client.get(
        "/api/jobs", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    assert len(first["jobs"]) == 2
    assert len(second["jobs"]) == 1
    assert {job["id"] for job in first["jobs"]}.isdisjoint(
        {job["id"] for job in second["jobs"]}
    )
    assert second["next_cursor"] is None


def test_failed_job_can_be_retried_with_lineage(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    original = client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect",
        input_json={"source_file": "demo.mov"},
    )
    client.app.state.job_service.mark_failed(original.id, "failed")

    response = client.post(f"/api/jobs/{original.id}/retry")

    assert response.status_code == 200
    retried = response.json()["job"]
    assert retried["id"] != original.id
    assert retried["source_job_id"] == original.id
    assert retried["input"] == original.input_json
    assert retried["status"] == "queued"


def test_chat_history_job_cannot_be_retried(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    original = client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="chat",
        title="History",
        input_json={"source_file": "demo.mov"},
    )
    client.app.state.job_service.mark_failed(original.id, "failed")

    response = client.post(f"/api/jobs/{original.id}/retry")

    assert response.status_code == 409


def seed_jobs(client: TestClient) -> None:
    client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect",
        input_json={"source_file": "demo.mov"},
    )
    client.app.state.job_service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="manual",
        title="Extract",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )
    client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="schedule",
        title="Scheduled inspect",
        input_json={"source_file": "demo.mov"},
    )
