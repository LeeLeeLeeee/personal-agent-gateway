from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

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
    hook = client.post(
        "/api/hooks",
        json={
            "name": "Inbox",
            "source_type": "email",
            "connection": {"host": "imap.test", "port": 993, "username": "me@test"},
            "secret": "app-password",
            "filter": {},
            "target_backend": "codex",
            "target_model": "default",
            "prompt_template": "summarize",
        },
    ).json()["hook"]
    hook_run = client.app.state.hook_run_service.create_run(
        hook["id"],
        "message-1",
        "message",
        {"subject": "hello"},
    )
    assert hook_run is not None

    response = client.post(
        "/api/operations/emergency-stop",
        headers={"X-Correlation-ID": "stop-test"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["intake_open"] is False
    assert result["changed"] is True
    assert result["canceled"]["jobs"] == [queued.id]
    assert result["canceled"]["hook_runs"] == [hook_run.id]
    assert client.app.state.job_service.get_job(queued.id).status == "canceled"
    assert client.app.state.hook_run_service.get_run(hook_run.id).status == "interrupted"

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
    blocked_hook = client.post(f"/api/hooks/{hook['id']}/run-now")

    assert blocked_job.status_code == 409
    assert blocked_schedule.status_code == 409
    assert blocked_chat.status_code == 409
    assert all(
        response.json()["detail"] == "Execution intake is stopped"
        for response in (blocked_job, blocked_schedule, blocked_chat, blocked_hook)
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


def test_emergency_stop_cancels_cycle_queue_and_resume_runs_only_new_work(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    with TestClient(app) as client:
        client.cookies.set(
            "agent_session", app.state.auth_session_service.issue().token
        )
        leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
        worker = app.state.persona_service.create_persona("Worker", "worker", "d", [], [])
        auto_run = app.state.team_run_service.create_team_run(
            "auto",
            leader.id,
            [worker.id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy="auto",
            auto_repeat_count=2,
            auto_interval_seconds=60,
        )
        queued = app.state.team_cycle_service.list_requests(auto_run.id)[0]
        due_run = app.state.team_run_service.create_team_run(
            "due",
            leader.id,
            [worker.id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy="auto",
            auto_repeat_count=2,
            auto_interval_seconds=60,
        )
        due_request = app.state.team_cycle_service.claim_next(due_run.id)
        due_cycle = app.state.team_run_service.create_cycle(
            due_run.id,
            due_request.source_type,
            due_request.source_id,
            request_id=due_request.id,
        )
        app.state.team_run_service.set_cycle_status(
            due_cycle.id, "completed", summary="slot one"
        )
        app.state.team_cycle_service.settle_cycle(
            due_cycle.id,
            now=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        assert app.state.team_cycle_service.get_active_series(
            due_run.id
        ).status == "waiting_interval"

        stopped = client.post("/api/operations/emergency-stop")

        assert stopped.status_code == 200
        assert app.state.team_cycle_dispatcher.alive is False
        assert app.state.team_cycle_loop.alive is False
        assert app.state.team_cycle_service.get_request(queued.id).status == "canceled"
        assert app.state.team_cycle_service.get_active_series(auto_run.id) is None
        assert app.state.team_cycle_service.get_active_series(due_run.id) is None
        assert app.state.team_cycle_service.enqueue_due_auto_requests(
            now=datetime.now(timezone.utc) + timedelta(days=1)
        ) == []

        resumed = client.post("/api/operations/resume-intake")
        assert resumed.status_code == 200
        assert app.state.team_cycle_dispatcher.alive is True
        assert app.state.team_cycle_loop.alive is True
        assert app.state.team_cycle_service.get_request(queued.id).status == "canceled"

        triggered = app.state.team_run_service.create_team_run(
            "new",
            leader.id,
            [worker.id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy="triggered",
        )
        new_request = app.state.team_cycle_service.enqueue_request(
            triggered.id, "manual", "new", "work", previous_cycle_id=None
        )

        async def complete(team_run_id, cycle_id, _instruction):
            app.state.team_run_service.set_cycle_status(
                cycle_id, "completed", summary="done"
            )
            run = app.state.team_run_service.get_team_run(team_run_id)
            await app.state.team_cycle_dispatcher.on_team_run_settled(
                run, cycle_id
            )
            return run

        app.state.team_run_orchestrator.run_cycle = complete
        client.portal.call(app.state.team_cycle_dispatcher.enqueue_run, triggered.id)
        client.portal.call(app.state.team_cycle_dispatcher._queue.join)
        assert app.state.team_cycle_service.get_request(new_request.id).status == "settled"


def test_resume_failure_keeps_intake_closed_and_cleans_started_dispatcher(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    with TestClient(app) as client:
        client.cookies.set(
            "agent_session", app.state.auth_session_service.issue().token
        )
        assert client.post("/api/operations/emergency-stop").status_code == 200
        app.state.team_cycle_loop.start = AsyncMock(
            side_effect=RuntimeError("loop start failed")
        )

        response = client.post("/api/operations/resume-intake")

        assert response.status_code == 500
        assert app.state.intake_gate.is_open is False
        assert app.state.team_cycle_dispatcher.alive is False
