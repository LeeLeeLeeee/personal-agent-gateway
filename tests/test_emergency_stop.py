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


def test_emergency_stop_cancels_jobs_and_blocks_new_execution_intake(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    queued = client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="queued",
        input_json={"source_file": "demo.mov"},
    )

    response = client.post(
        "/api/operations/emergency-stop",
        headers={"X-Correlation-ID": "stop-test"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["intake_open"] is False
    assert result["changed"] is True
    assert result["canceled"]["jobs"] == [queued.id]
    assert client.app.state.job_service.get_job(queued.id).status == "canceled"

    blocked_job = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.inspect",
            "title": "blocked",
            "input": {"source_file": "demo.mov"},
        },
    )
    blocked_schedule = client.post(
        "/api/schedules",
        json={
            "name": "blocked",
            "capability_id": "ffmpeg.inspect",
            "cron_expression": "* * * * *",
            "timezone": "UTC",
            "input_template": {"source_file": "demo.mov"},
        },
    )
    blocked_chat = client.post("/api/chat", json={"message": "blocked"})

    assert blocked_job.status_code == 409
    assert blocked_schedule.status_code == 409
    assert blocked_chat.status_code == 409
    assert all(
        response.json()["detail"] == "Execution intake is stopped"
        for response in (blocked_job, blocked_schedule, blocked_chat)
    )

    events = client.get(
        "/api/audit/events",
        params={"correlation_id": "stop-test"},
    ).json()["events"]
    assert events[0]["action"] == "operations.emergency_stop"
    assert events[0]["actor_id"]


def test_stop_and_resume_are_idempotent(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    first_stop = client.post("/api/operations/emergency-stop")
    second_stop = client.post("/api/operations/emergency-stop")
    first_resume = client.post("/api/operations/resume-intake")
    second_resume = client.post("/api/operations/resume-intake")

    assert first_stop.json()["changed"] is True
    assert second_stop.json()["changed"] is False
    assert first_resume.json() == {"intake_open": True, "changed": True}
    assert second_resume.json() == {"intake_open": True, "changed": False}

    create_response = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.inspect",
            "title": "allowed again",
            "input": {"source_file": "demo.mov"},
        },
    )
    assert create_response.status_code == 200
