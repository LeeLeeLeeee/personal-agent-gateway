import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.team_runtime import TeamRuntime

_TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "canceled"}


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


def create_persona(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/personas",
        json={
            "name": name,
            "role": f"{name} role",
            "description": f"{name} description",
            "responsibilities": ["Do assigned work"],
            "constraints": ["Report evidence"],
        },
    )
    return response.json()["persona"]["id"]


@dataclass
class GatedModel:
    """gate가 set()될 때까지 complete()에서 블로킹하는 테스트 전용 모델.

    무한정 블로킹하지 않도록 wait_for로 상한을 둔다(회귀 시 테스트가 영원히
    걸리지 않고 실패로 끝나도록).
    """

    gate: asyncio.Event
    content: str = '[{"title": "T", "description": "D"}]'

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        await asyncio.wait_for(self.gate.wait(), timeout=5)
        return ModelResponse(content=self.content, tool_calls=[])


def _inject_gated_team_runtime(app, gate: asyncio.Event) -> None:
    app.state.team_runtime = TeamRuntime(
        app.state.team_run_service,
        lambda _agent: GatedModel(gate),
        app.state.event_bus,
    )


async def _async_create_persona(client: httpx.AsyncClient, name: str) -> str:
    response = await client.post(
        "/api/personas",
        json={
            "name": name,
            "role": f"{name} role",
            "description": f"{name} description",
            "responsibilities": ["Do assigned work"],
            "constraints": ["Report evidence"],
        },
    )
    return response.json()["persona"]["id"]


async def _poll_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    elapsed = 0.0
    while not predicate():
        await asyncio.sleep(interval)
        elapsed += interval
        if elapsed >= timeout:
            raise AssertionError("timed out waiting for condition")


def test_create_team_run_api_snapshots_agents(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")

    response = client.post(
        "/api/team-runs",
        json={
            "goal": "Design Agent Teams",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "planning_only",
            "max_workers": 2,
        },
    )

    assert response.status_code == 200
    run = response.json()["team_run"]
    assert run["goal"] == "Design Agent Teams"
    assert run["status"] == "draft"

    agents = client.get(f"/api/team-runs/{run['id']}/agents").json()["agents"]
    assert [agent["name"] for agent in agents] == ["Tech Lead", "QA Tester"]
    stored_agent = client.app.state.team_run_service.get_agent(agents[0]["id"])
    model = client.app.state.team_runtime._model_factory(stored_agent)
    assert model._workspace_root == Path(run["workspace_root"]).resolve()


def test_list_team_runs_returns_enriched_fields(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")
    run_id = client.post(
        "/api/team-runs",
        json={
            "goal": "Design Agent Teams",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "planning_only",
            "max_workers": 2,
        },
    ).json()["team_run"]["id"]

    body = client.get("/api/team-runs").json()
    run = next(r for r in body["team_runs"] if r["id"] == run_id)

    assert run["leader_name"] == "Tech Lead"
    assert "members" in run
    assert "task_counts" in run
    assert "elapsed_seconds" in run


def test_delete_team_run_removes_it(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")

    run = client.post(
        "/api/team-runs",
        json={"goal": "Ship it", "leader_persona_id": leader_id},
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "temporary.txt").write_text("test only", encoding="utf-8")

    deleted = client.delete(f"/api/team-runs/{run['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    assert client.get(f"/api/team-runs/{run['id']}").status_code == 404
    assert client.get("/api/team-runs").json()["team_runs"] == []
    assert not workspace.exists()


def test_delete_running_team_run_keeps_workspace_and_record(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    run = client.post(
        "/api/team-runs",
        json={"goal": "Temporary test run", "leader_persona_id": leader_id},
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    sentinel = workspace / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    client.app.state.team_run_service.set_run_status(run["id"], "running")

    response = client.delete(f"/api/team-runs/{run['id']}")

    assert response.status_code == 409
    assert sentinel.exists()
    assert client.get(f"/api/team-runs/{run['id']}").status_code == 200


def test_delete_missing_team_run_returns_404(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.delete("/api/team-runs/does-not-exist").status_code == 404


def test_team_run_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/team-runs")

    assert response.status_code == 401


def test_retry_failed_team_task_api_requeues_task_for_manual_resume(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "Ship it",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "plan_and_execute",
        },
    ).json()["team_run"]
    service = client.app.state.team_run_service
    task = service.create_task(run["id"], "QA", "Run checks")
    service.set_task_status(task.id, "failed", error_message="timed out")
    service.set_run_status(run["id"], "completed_with_failures", summary="old")

    response = client.post(f"/api/team-runs/{run['id']}/tasks/{task.id}/retry")

    assert response.status_code == 200
    assert response.json()["team_run"]["status"] == "interrupted"
    assert response.json()["task"]["status"] == "pending"
    assert client.post(f"/api/team-runs/{run['id']}/tasks/{task.id}/retry").status_code == 409


def test_retry_team_task_api_rejects_missing_task(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    run = client.post(
        "/api/team-runs",
        json={"goal": "Ship it", "leader_persona_id": leader_id},
    ).json()["team_run"]
    client.app.state.team_run_service.set_run_status(run["id"], "failed")

    response = client.post(f"/api/team-runs/{run['id']}/tasks/missing/retry")

    assert response.status_code == 404


def test_start_returns_immediately_without_blocking(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")
    created = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "planning_only",
            "max_workers": 1,
        },
    ).json()["team_run"]

    resp = client.post(f"/api/team-runs/{created['id']}/start")

    assert resp.status_code == 200
    # 즉시 반환된 payload는 team_run을 포함
    assert resp.json()["team_run"]["id"] == created["id"]


def test_double_start_conflicts(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    created = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [],
            "run_mode": "planning_only",
            "max_workers": 1,
        },
    ).json()["team_run"]
    client.post(f"/api/team-runs/{created['id']}/start")
    second = client.post(f"/api/team-runs/{created['id']}/start")
    assert second.status_code in (200, 409)  # 이미 끝났으면 finished 409, 실행중이면 409


def test_cancel_does_not_overwrite_already_terminal_run(tmp_path: Path) -> None:
    """registry에 없는(이미 끝난) 팀런을 /cancel해도 실제 종료 상태가 덮어써지지 않아야 함."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    created = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [],
            "run_mode": "planning_only",
            "max_workers": 1,
        },
    ).json()["team_run"]
    run_id = created["id"]

    service = client.app.state.team_run_service
    service.set_run_status(run_id, "completed", summary="real result")

    cancel_resp = client.post(f"/api/team-runs/{run_id}/cancel")
    assert cancel_resp.status_code == 200

    final = client.get(f"/api/team-runs/{run_id}").json()["team_run"]
    assert final["status"] == "completed"
    assert final["summary"] == "real result"


async def test_start_returns_before_orchestration_completes(tmp_path: Path) -> None:
    """오케스트레이션이 아직 끝나지 않았을 때도 /start가 즉시 반환되는지 확인.

    모델 호출을 gate로 블로킹시켜, start가 인라인으로 await하도록 회귀하면
    이 테스트가 (오케스트레이션이 끝난 뒤에야 응답이 오므로) 실패한다.
    """
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", "test-session")
        leader_id = await _async_create_persona(client, "Tech Lead")
        created = (
            await client.post(
                "/api/team-runs",
                json={
                    "goal": "g",
                    "leader_persona_id": leader_id,
                    "member_persona_ids": [],
                    "run_mode": "planning_only",
                    "max_workers": 1,
                },
            )
        ).json()["team_run"]
        run_id = created["id"]
        registry = app.state.team_run_registry

        start_resp = await client.post(f"/api/team-runs/{run_id}/start")

        assert start_resp.status_code == 200
        # 핸들러가 오케스트레이션 완료를 기다리지 않고 반환했어야 함
        assert registry.is_running(run_id) is True
        status_after_start = (
            await client.get(f"/api/team-runs/{run_id}")
        ).json()["team_run"]["status"]
        assert status_after_start not in _TERMINAL_STATUSES

        # 정리: 블로킹을 풀어 백그라운드 태스크가 정상적으로 끝나도록 함
        gate.set()
        await _poll_until(lambda: not registry.is_running(run_id))
        final = (await client.get(f"/api/team-runs/{run_id}")).json()["team_run"]
        assert final["status"] == "completed"


def test_add_work_rejects_non_execute_mode(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [],
            "run_mode": "planning_only",
            "max_workers": 1,
        },
    ).json()["team_run"]

    resp = client.post(f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"})
    assert resp.status_code == 409


def test_add_work_rejects_draft_run(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    member_id = create_persona(client, "Worker")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "plan_and_execute",
            "max_workers": 1,
        },
    ).json()["team_run"]

    resp = client.post(f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"})
    assert resp.status_code == 409  # draft: run not started yet


def test_create_app_marks_stale_active_run_interrupted(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first_app = create_app(config)
    service = first_app.state.team_run_service
    leader = first_app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    member = first_app.state.persona_service.create_persona("Worker", "work", "d", [], [])
    run = service.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = service.list_agents(run.id)[1]
    task = service.create_task(run.id, "current", "d", worker.id)
    service.set_task_status(task.id, "in_progress")
    service.set_agent_status(worker.id, "running")
    service.set_run_status(run.id, "running")

    restarted_app = create_app(config)

    recovered = restarted_app.state.team_run_service.get_team_run(run.id)
    assert recovered.status == "interrupted"
    assert restarted_app.state.team_run_service.list_tasks(run.id)[0].status == "pending"


def test_interrupted_run_rejects_start_and_add_work(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    member_id = create_persona(client, "Worker")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "plan_and_execute",
            "max_workers": 1,
        },
    ).json()["team_run"]
    service = client.app.state.team_run_service
    service.set_run_status(run["id"], "running")
    service.interrupt_active_runs()

    assert client.post(f"/api/team-runs/{run['id']}/start").status_code == 409
    assert client.post(
        f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"}
    ).status_code == 409


async def test_resume_interrupted_run_registers_background_task_and_blocks_duplicate(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", "test-session")
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        run = (
            await client.post(
                "/api/team-runs",
                json={
                    "goal": "g",
                    "leader_persona_id": leader_id,
                    "member_persona_ids": [member_id],
                    "run_mode": "plan_and_execute",
                    "max_workers": 1,
                },
            )
        ).json()["team_run"]
        service = app.state.team_run_service
        worker = service.list_agents(run["id"])[1]
        task = service.create_task(run["id"], "current", "d", worker.id)
        service.set_task_status(task.id, "in_progress")
        service.set_agent_status(worker.id, "running")
        service.set_run_status(run["id"], "running")
        service.interrupt_active_runs()

        response = await client.post(f"/api/team-runs/{run['id']}/resume")

        assert response.status_code == 200
        assert app.state.team_run_registry.is_running(run["id"]) is True
        assert (await client.post(f"/api/team-runs/{run['id']}/resume")).status_code == 409

        gate.set()
        await _poll_until(lambda: not app.state.team_run_registry.is_running(run["id"]))
        final = service.get_team_run(run["id"])
        assert final.status == "completed"
        assert service.list_tasks(run["id"])[0].status == "completed"


def test_shutdown_marks_registered_run_interrupted(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    service = app.state.team_run_service
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    member = app.state.persona_service.create_persona("Worker", "work", "d", [], [])
    run = service.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = service.list_agents(run.id)[1]
    task = service.create_task(run.id, "current", "d", worker.id)
    service.set_task_status(task.id, "in_progress")
    service.set_agent_status(worker.id, "running")
    service.set_run_status(run.id, "running")
    service.interrupt_active_runs()
    gate = asyncio.Event()
    _inject_gated_team_runtime(app, gate)

    with TestClient(app) as client:
        client.cookies.set("agent_session", "test-session")
        response = client.post(f"/api/team-runs/{run.id}/resume")
        assert response.status_code == 200
        assert app.state.team_run_registry.is_running(run.id) is True

    assert service.get_team_run(run.id).status == "interrupted"
    assert service.list_tasks(run.id)[0].status == "pending"


async def test_add_work_reopens_terminal_run(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    gate.set()  # never block
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", "test-session")
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        created = (
            await client.post(
                "/api/team-runs",
                json={
                    "goal": "g",
                    "leader_persona_id": leader_id,
                    "member_persona_ids": [member_id],
                    "run_mode": "plan_and_execute",
                    "max_workers": 1,
                },
            )
        ).json()["team_run"]
        run_id = created["id"]
        registry = app.state.team_run_registry

        await client.post(f"/api/team-runs/{run_id}/start")
        await _poll_until(lambda: not registry.is_running(run_id))
        before = len((await client.get(f"/api/team-runs/{run_id}/tasks")).json()["tasks"])

        resp = await client.post(f"/api/team-runs/{run_id}/add-work", json={"instruction": "also do Y"})
        assert resp.status_code == 200

        await _poll_until(lambda: not registry.is_running(run_id))
        after = (await client.get(f"/api/team-runs/{run_id}/tasks")).json()["tasks"]
        assert len(after) == before + 1
        final = (await client.get(f"/api/team-runs/{run_id}")).json()["team_run"]
        assert final["status"] in {"completed", "completed_with_failures"}
        assert all(task["status"] in {"completed", "failed"} for task in after)


async def test_cancel_endpoint_settles_blocked_run_as_canceled(tmp_path: Path) -> None:
    """실행 중인 팀런을 /cancel로 실제 취소했을 때 canceled로 정착하는지 확인."""
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", "test-session")
        leader_id = await _async_create_persona(client, "Tech Lead")
        created = (
            await client.post(
                "/api/team-runs",
                json={
                    "goal": "g",
                    "leader_persona_id": leader_id,
                    "member_persona_ids": [],
                    "run_mode": "planning_only",
                    "max_workers": 1,
                },
            )
        ).json()["team_run"]
        run_id = created["id"]
        registry = app.state.team_run_registry

        start_resp = await client.post(f"/api/team-runs/{run_id}/start")
        assert start_resp.status_code == 200
        assert registry.is_running(run_id) is True

        cancel_resp = await client.post(f"/api/team-runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200

        await _poll_until(lambda: not registry.is_running(run_id))
        assert registry.is_running(run_id) is False

        final = (await client.get(f"/api/team-runs/{run_id}")).json()["team_run"]
        assert final["status"] == "canceled"
