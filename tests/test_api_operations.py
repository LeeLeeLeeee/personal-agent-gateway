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


def test_operations_requires_authentication(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    assert client.get("/api/operations").status_code == 401


def test_operations_projects_domain_states_with_stable_deep_links(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    transcript = client.app.state.transcript_store
    session_id = transcript.start_new()
    transcript.append_to(session_id, "runtime_error", {"message": "failed"})
    job = client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect",
        input_json={"source_file": "demo.mov"},
    )
    client.app.state.job_service.mark_failed(job.id, "failed")
    schedule = client.app.state.schedule_service.create_schedule(
        name="Daily inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="0 9 * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
    )

    response = client.get("/api/operations")

    assert response.status_code == 200
    body = response.json()
    assert body["intake_open"] is True
    assert body["access_mode"] == "restricted"
    assert body["diagnostics"] == {
        "bind_host": "127.0.0.1",
        "cookie_secure": False,
        "tunnel_mode": "not_reported",
        "workspace_writable": True,
    }
    assert {component["name"] for component in body["health"]} == {
        "database",
            "worker",
            "scheduler",
            "hook_loop",
            "hook_runner",
            "team_cycle_dispatcher",
            "team_cycle_loop",
            "cli",
            "intake",
    }
    items = {(item["domain"], item["id"]): item for item in body["items"]}
    assert items[("session", session_id)]["target"] == {
        "screen": "chat",
        "session_id": session_id,
    }
    assert items[("session", session_id)]["status"] == "failed"
    assert items[("job", job.id)]["retryable"] is True
    assert items[("job", job.id)]["target"] == {
        "screen": "jobs",
        "job_id": job.id,
    }
    assert items[("schedule", schedule.id)]["target"] == {
        "screen": "schedules",
        "schedule_id": schedule.id,
    }


def test_operations_includes_canceled_retryable_job(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    job = client.app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Canceled inspect",
        input_json={"source_file": "demo.mov"},
    )
    client.app.state.job_service.cancel_job(job.id, "stopped")

    body = client.get("/api/operations").json()

    item = next(entry for entry in body["items"] if entry["id"] == job.id)
    assert item["status"] == "canceled"
    assert item["retryable"] is True


def test_operations_reports_interrupted_team_run(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    persona = client.app.state.persona_service.create_persona(
        "Lead",
        "lead",
        "Plans",
        [],
        [],
    )
    run = client.app.state.team_run_service.create_team_run(
        goal="Recover this run",
        leader_persona_id=persona.id,
        member_persona_ids=[],
        run_mode="plan_only",
        max_workers=1,
    )
    client.app.state.team_run_service.set_run_status(run.id, "running")
    client.app.state.team_run_service.interrupt_run(run.id)

    body = client.get("/api/operations").json()

    item = next(entry for entry in body["items"] if entry["id"] == run.id)
    assert item["domain"] == "team_run"
    assert item["status"] == "interrupted"
    assert item["resumable"] is True
    assert item["target"] == {"screen": "teams", "team_run_id": run.id}


def test_operations_reports_policy_active_terminal_team_run(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    persona = client.app.state.persona_service.create_persona(
        "Lead", "lead", "Plans", [], []
    )
    run = client.app.state.team_run_service.create_team_run(
        goal="Continue this run",
        leader_persona_id=persona.id,
        member_persona_ids=[],
        run_mode="plan_and_execute",
        max_workers=1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=2,
        auto_interval_seconds=300,
    )
    request = client.app.state.team_cycle_service.claim_next(run.id)
    cycle = client.app.state.team_run_service.create_cycle(
        run.id, "auto", request.source_id, request_id=request.id
    )
    client.app.state.team_run_service.set_cycle_status(
        cycle.id, "completed", summary="first"
    )
    client.app.state.team_run_service.set_run_status(run.id, "completed")
    client.app.state.team_cycle_service.settle_cycle(cycle.id)

    body = client.get("/api/operations").json()

    item = next(entry for entry in body["items"] if entry["id"] == run.id)
    assert item["execution_policy"] == "auto"
    assert item["policy_status"] == "waiting_interval"
    assert item["queue_count"] == 0
    assert item["next_run_at"] is not None
    assert item["pause_reason"] is None
    assert item["active_cycle_id"] is None
