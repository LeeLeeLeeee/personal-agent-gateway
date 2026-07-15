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


def test_list_schedules_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/schedules")

    assert response.status_code == 401


def test_create_schedule_returns_schedule(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post(
        "/api/schedules",
        json={
            "name": "Daily inspect",
            "capability_id": "ffmpeg.inspect",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
            "input_template": {"source_file": "demo.mov"},
        },
    )

    assert response.status_code == 200
    schedule = response.json()["schedule"]
    assert schedule["name"] == "Daily inspect"
    assert schedule["capability_id"] == "ffmpeg.inspect"
    assert schedule["cron_expression"] == "0 9 * * *"
    assert schedule["timezone"] == "UTC"
    assert schedule["input_template"] == {"source_file": "demo.mov"}
    assert schedule["enabled"] is True
    assert schedule["last_run_job_id"] is None
    assert schedule["last_run_at"] is None
    assert schedule["next_run_at"]


def test_pause_and_resume_toggle_enabled(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    schedule_id = create_schedule(client)

    pause_response = client.post(f"/api/schedules/{schedule_id}/pause")
    resume_response = client.post(f"/api/schedules/{schedule_id}/resume")

    assert pause_response.status_code == 200
    assert pause_response.json()["schedule"]["enabled"] is False
    assert resume_response.status_code == 200
    assert resume_response.json()["schedule"]["enabled"] is True


def test_run_now_returns_schedule_job(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    schedule_id = create_schedule(client)

    response = client.post(f"/api/schedules/{schedule_id}/run-now")

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["source"] == "schedule"
    assert job["capability_id"] == "ffmpeg.inspect"
    events = client.app.state.audit_service.list(resource_type="schedule")
    assert any(
        event.action == "schedules.run_now" and event.resource_id == schedule_id
        for event in events
    )


def test_schedule_detail_returns_history_stats_and_preview(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    schedule_id = create_schedule(client)
    first = client.post(f"/api/schedules/{schedule_id}/run-now").json()["job"]
    client.app.state.job_service.mark_running(first["id"])
    client.app.state.job_service.mark_succeeded(first["id"])
    second = client.post(f"/api/schedules/{schedule_id}/run-now").json()["job"]
    client.app.state.job_service.mark_running(second["id"])
    client.app.state.job_service.mark_failed(second["id"], "failed")

    response = client.get(f"/api/schedules/{schedule_id}")

    assert response.status_code == 200
    detail = response.json()
    assert [job["id"] for job in detail["jobs"]] == [second["id"], first["id"]]
    assert detail["stats"] == {
        "total": 2,
        "succeeded": 1,
        "failed": 1,
        "canceled": 0,
        "success_rate": 0.5,
    }
    assert detail["last_failure"]["id"] == second["id"]
    assert len(detail["next_runs"]) == 3


def test_delete_schedule_removes_it_from_list(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    schedule_id = create_schedule(client)

    delete_response = client.delete(f"/api/schedules/{schedule_id}")
    list_response = client.get("/api/schedules")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
    assert list_response.json()["schedules"] == []


def create_schedule(client: TestClient) -> str:
    response = client.post(
        "/api/schedules",
        json={
            "name": "Inspect",
            "capability_id": "ffmpeg.inspect",
            "cron_expression": "* * * * *",
            "timezone": "UTC",
            "input_template": {"source_file": "demo.mov"},
        },
    )
    assert response.status_code == 200
    return str(response.json()["schedule"]["id"])
