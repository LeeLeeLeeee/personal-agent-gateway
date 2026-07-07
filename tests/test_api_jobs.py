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
