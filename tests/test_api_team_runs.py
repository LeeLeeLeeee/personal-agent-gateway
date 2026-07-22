import asyncio
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import pytest
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
    client.cookies.set("agent_session", client.app.state.auth_session_service.issue().token)
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


def create_team(client: TestClient, leader_id: str, member_ids: list[str] | None = None) -> str:
    response = client.post(
        "/api/teams",
        json={
            "name": "Team",
            "description": "",
            "leader_persona_id": leader_id,
            "member_persona_ids": member_ids or [],
        },
    )
    return response.json()["team"]["id"]


def create_standard_run(
    app,
    leader_id: str,
    member_ids: list[str] | None = None,
    *,
    goal: str = "g",
    run_mode: str = "plan_and_execute",
) -> dict[str, object]:
    run = app.state.team_run_service.create_team_run(
        goal,
        leader_id,
        member_ids or [],
        run_mode,
        1,
    )
    return asdict(run)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_worktree_delivery_commits_and_applies_to_space_repository(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")

    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    team_id = create_team(client, leader_id)
    assert client.put(
        f"/api/spaces/teams/{team_id}",
        json={
            "read_mode": "home",
            "write_mode": "worktree",
            "workspace_path": str(repository),
        },
    ).status_code == 200
    created = client.post(
        "/api/team-runs",
        json={"team_id": team_id, "execution_policy": "triggered"},
    )
    assert created.status_code == 200
    run = created.json()["team_run"]
    run_id = run["id"]
    working_root = Path(run["working_root"])
    (working_root / "feature.txt").write_text("delivered\n", encoding="utf-8")

    preview = client.get(f"/api/team-runs/{run_id}/delivery").json()["delivery"]
    assert preview["available"] is True
    assert preview["can_commit"] is True
    assert preview["can_apply"] is False
    assert preview["uncommitted_files"] == [{"status": "??", "path": "feature.txt"}]

    committed = client.post(
        f"/api/team-runs/{run_id}/delivery/commit",
        json={"message": "feat: deliver team run"},
    )
    assert committed.status_code == 200
    delivery = committed.json()["delivery"]
    assert delivery["uncommitted_files"] == []
    assert [item["subject"] for item in delivery["pending_commits"]] == [
        "feat: deliver team run"
    ]
    assert delivery["can_apply"] is True

    dirty_target = repository / "local.txt"
    dirty_target.write_text("keep\n", encoding="utf-8")
    blocked = client.post(f"/api/team-runs/{run_id}/delivery/apply")
    assert blocked.status_code == 409
    assert "uncommitted changes" in blocked.json()["detail"]
    dirty_target.unlink()

    applied = client.post(f"/api/team-runs/{run_id}/delivery/apply")
    assert applied.status_code == 200
    result = applied.json()["delivery"]
    assert result["pending_commits"] == []
    assert result["up_to_date"] is True
    assert (repository / "feature.txt").read_text(encoding="utf-8") == "delivered\n"


def test_create_auto_run_enqueues_first_cycle_and_manual_trigger_snapshots_preview(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    team_id = create_team(client, leader_id, [worker_id])

    created = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Maintain gateway",
            "execution_policy": "auto",
            "auto_repeat_count": 3,
            "auto_interval_minutes": 5,
        },
    )

    assert created.status_code == 200
    auto_run = created.json()["team_run"]
    assert auto_run["lifecycle_mode"] == "continuous"
    assert auto_run["run_mode"] == "plan_and_execute"
    assert auto_run["execution_policy"] == "auto"
    assert auto_run["configured_max_workers"] == 1
    auto_requests = client.app.state.team_cycle_service.list_requests(auto_run["id"])
    assert len(auto_requests) == 1
    assert auto_requests[0].instruction == "Maintain gateway"
    assert client.app.state.team_cycle_dispatcher._queue.qsize() == 1

    triggered = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    assert triggered["goal"] == ""
    previous = client.app.state.team_run_service.create_cycle(
        triggered["id"], "manual", "previous"
    )
    previous = client.app.state.team_run_service.set_cycle_status(
        previous.id, "completed", summary="previous"
    )
    response = client.post(
        f"/api/team-runs/{triggered['id']}/cycle-requests",
        json={
            "instruction": "next",
            "client_request_id": "ui-1",
            "previous_cycle_id": previous.id,
        },
    )

    assert response.status_code == 200
    cycle_request = response.json()["cycle_request"]
    assert cycle_request["source_type"] == "manual"
    assert cycle_request["source_id"] == "ui-1"
    assert cycle_request["previous_summary_text"] == "previous"
    assert response.json()["queue_position"] == 1


def test_cancel_continuous_run_cancels_queued_hook_lineage(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    team_id = create_team(client, leader_id, [worker_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Process hook",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    hook = client.post(
        "/api/hooks",
        json={
            "name": "Inbox",
            "source_type": "email",
            "connection": {"host": "imap.test", "port": 993, "username": "me@test"},
            "secret": "secret",
            "filter": {},
            "target_kind": "team_run",
            "target_team_run_id": run["id"],
            "prompt_template": "summarize",
        },
    ).json()["hook"]
    hook_run = client.app.state.hook_run_service.create_run(
        hook["id"], "message-1", "message", {"subject": "hello"}
    )
    request = client.app.state.team_cycle_service.enqueue_request(
        run["id"], "hook", hook_run.id, "work", previous_cycle_id=None
    )
    client.app.state.hook_run_service.link_cycle_request(hook_run.id, request.id)

    first = client.post(f"/api/team-runs/{run['id']}/cancel")
    second = client.post(f"/api/team-runs/{run['id']}/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["team_run"]["status"] == "canceled"
    assert client.app.state.team_cycle_service.get_request(request.id).status == "canceled"
    assert client.app.state.hook_run_service.get_run(hook_run.id).status == "canceled"


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_policy", ["auto", "triggered"])
async def test_cancel_during_add_work_cannot_resurrect_continuous_lineage(
    tmp_path: Path,
    execution_policy: str,
) -> None:
    app = create_app(make_config(tmp_path))
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    worker = app.state.persona_service.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "race",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy=execution_policy,
        auto_repeat_count=2 if execution_policy == "auto" else None,
        auto_interval_seconds=60 if execution_policy == "auto" else None,
    )
    hook_run = None
    if execution_policy == "auto":
        request = cycles.list_requests(run.id)[0]
    else:
        hook = app.state.hook_service.create_hook(
            name="Inbox",
            source_type="email",
            connection={"host": "imap.test", "port": 993, "username": "me@test"},
            secret="secret",
            filter={},
            target_backend="",
            target_model="",
            target_options={},
            prompt_template="summarize",
            poll_interval_seconds=300,
            target_kind="team_run",
            target_team_run_id=run.id,
        )
        hook_run = app.state.hook_run_service.create_run(
            hook.id, "message-1", "message", {"subject": "hello"}
        )
        request = cycles.enqueue_request(
            run.id, "hook", hook_run.id, "work", previous_cycle_id=None
        )
        app.state.hook_run_service.link_cycle_request(hook_run.id, request.id)

    entered_add_work = asyncio.Event()
    release_add_work = asyncio.Event()
    resume_calls: list[str] = []

    class BlockingRuntime:
        async def add_work(self, team_run_id, _instruction, cycle_id=None):
            entered_add_work.set()
            await release_add_work.wait()
            teams.create_task(
                team_run_id,
                "late task",
                "must not survive cancellation",
                cycle_id=cycle_id,
            )
            return []

        async def resume(self, team_run_id, cycle_id=None):
            resume_calls.append(team_run_id)
            teams.set_run_status(team_run_id, "completed")
            teams.set_cycle_status(cycle_id, "completed", summary="resurrected")
            return teams.get_team_run(team_run_id)

    app.state.team_runtime = BlockingRuntime()
    transport = httpx.ASGITransport(app=app)
    await app.state.team_cycle_dispatcher.start()
    try:
        await app.state.team_cycle_dispatcher.enqueue_run(run.id)
        await asyncio.wait_for(entered_add_work.wait(), timeout=1)
        assert app.state.team_run_registry.is_running(run.id) is True

        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            client.cookies.set(
                "agent_session", app.state.auth_session_service.issue().token
            )
            canceled = await client.post(f"/api/team-runs/{run.id}/cancel")

        assert canceled.status_code == 200
        release_add_work.set()
        await asyncio.wait_for(app.state.team_cycle_dispatcher._queue.join(), timeout=1)

        cycle = teams.get_cycle_for_request(request.id)
        assert cycle is not None
        assert teams.get_team_run(run.id).status == "canceled"
        assert cycle.status == "canceled"
        assert cycles.get_request(request.id).status == "canceled"
        assert resume_calls == []
        assert teams.list_tasks(run.id, cycle.id) == []
        assert app.state.team_cycle_dispatcher.alive is True
        if execution_policy == "auto":
            assert cycles.get_active_series(run.id) is None
        else:
            assert hook_run is not None
            assert app.state.hook_run_service.get_run(hook_run.id).status == "canceled"
    finally:
        await app.state.team_cycle_dispatcher.stop()

@pytest.mark.parametrize(
    "payload",
    [
        {
            "team_id": "team",
            "goal": "g",
            "execution_policy": "auto",
            "auto_repeat_count": 2,
        },
        {
            "team_id": "team",
            "goal": "  ",
            "execution_policy": "auto",
            "auto_repeat_count": 2,
            "auto_interval_minutes": 5,
        },
        {
            "team_id": "team",
            "goal": "g",
            "execution_policy": "triggered",
            "auto_repeat_count": 2,
            "auto_interval_minutes": 5,
        },
        {"team_id": "team", "goal": "g"},
    ],
)
def test_create_run_rejects_incomplete_or_mixed_policy_settings(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    client = authenticated_client(tmp_path)

    response = client.post("/api/team-runs", json=payload)

    assert response.status_code == 422


def test_auto_actions_and_detail_read_model(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    team_id = create_team(client, leader_id, [worker_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "AUTO maintenance",
            "execution_policy": "auto",
            "auto_repeat_count": 2,
            "auto_interval_minutes": 5,
        },
    ).json()["team_run"]
    cycle_service = client.app.state.team_cycle_service
    team_service = client.app.state.team_run_service
    series = cycle_service.get_active_series(run["id"])

    queued_detail = client.get(f"/api/team-runs/{run['id']}/detail").json()
    assert queued_detail["policy_status"] == "queued"
    assert queued_detail["queue_count"] == 1
    assert queued_detail["active_request"] is None

    first = cycle_service.claim_next(run["id"])
    failed_cycle = team_service.create_cycle(
        run["id"], "auto", first.source_id, request_id=first.id
    )
    active_detail = client.get(f"/api/team-runs/{run['id']}/detail").json()
    assert active_detail["policy_status"] == "running"
    assert active_detail["active_request"]["id"] == first.id
    team_service.set_cycle_status(failed_cycle.id, "failed", error_message="boom")
    cycle_service.settle_cycle(failed_cycle.id)

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    assert detail["policy_status"] == "paused_failure"
    assert detail["active_auto_series"]["settled_slots"] == 0
    assert detail["queue_count"] == 0
    assert detail["active_request"] is None

    retried = client.post(
        f"/api/team-runs/{run['id']}/auto-series/{series.id}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["cycle_request"]["retry_of_request_id"] == first.id

    retry_request = cycle_service.claim_next(run["id"])
    retry_cycle = team_service.create_cycle(
        run["id"], "retry", retry_request.source_id, request_id=retry_request.id
    )
    team_service.set_cycle_status(retry_cycle.id, "failed", error_message="again")
    cycle_service.settle_cycle(retry_cycle.id)

    continued = client.post(
        f"/api/team-runs/{run['id']}/auto-series/{series.id}/continue"
    )
    assert continued.status_code == 200
    assert continued.json()["auto_series"]["settled_slots"] == 1
    assert continued.json()["auto_series"]["status"] == "waiting_interval"


def test_restart_completed_auto_series_enqueues_first_cycle(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "AUTO once",
            "execution_policy": "auto",
            "auto_repeat_count": 1,
            "auto_interval_minutes": 1,
        },
    ).json()["team_run"]
    cycles = client.app.state.team_cycle_service
    teams = client.app.state.team_run_service
    first = cycles.claim_next(run["id"])
    cycle = teams.create_cycle(run["id"], "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(cycle.id, "completed", summary="done")
    cycles.settle_cycle(cycle.id)

    restarted = client.post(f"/api/team-runs/{run['id']}/auto-series/restart")

    assert restarted.status_code == 200
    assert restarted.json()["auto_series"]["series_number"] == 2
    assert restarted.json()["cycle_request"]["slot_ordinal"] == 1


def test_continuous_run_rejects_legacy_start_add_work_and_wrong_policy_actions(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    team_id = create_team(client, leader_id)
    triggered = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Triggered",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    auto = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Auto",
            "execution_policy": "auto",
            "auto_repeat_count": 2,
            "auto_interval_minutes": 5,
        },
    ).json()["team_run"]

    assert client.post(f"/api/team-runs/{triggered['id']}/start").status_code == 409
    assert (
        client.post(
            f"/api/team-runs/{triggered['id']}/add-work",
            json={"instruction": "bypass"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/team-runs/{auto['id']}/cycle-requests",
            json={"instruction": "wrong", "client_request_id": "ui-wrong"},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/team-runs/{triggered['id']}/auto-series/restart").status_code
        == 409
    )


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
    team_id = create_team(client, leader_id, [member_id])

    response = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Design Agent Teams",
            "execution_policy": "triggered",
        },
    )

    assert response.status_code == 200
    run = response.json()["team_run"]
    assert run["goal"] == "Design Agent Teams"
    assert run["status"] == "draft"
    events = client.app.state.audit_service.list(resource_type="team_run")
    assert any(
        event.action == "team_runs.create" and event.resource_id == run["id"]
        for event in events
    )

    agents = client.get(f"/api/team-runs/{run['id']}/agents").json()["agents"]
    assert [agent["name"] for agent in agents] == ["Tech Lead", "QA Tester"]
    stored_agent = client.app.state.team_run_service.get_agent(agents[0]["id"])
    model = client.app.state.team_runtime._model_factory(stored_agent)
    assert model._workspace_root == Path(run["working_root"]).resolve()


def test_create_team_run_is_continuous_and_standard_record_remains_readable(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)

    continuous = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Watch inbox",
            "execution_policy": "triggered",
        },
    )
    standard = create_standard_run(
        client.app,
        leader_id,
        goal="One-off task",
        run_mode="planning_only",
    )

    assert continuous.status_code == 200
    assert continuous.json()["team_run"]["lifecycle_mode"] == "continuous"
    assert client.get(
        f"/api/team-runs/{continuous.json()['team_run']['id']}"
    ).json()["team_run"]["lifecycle_mode"] == "continuous"
    standard_read = client.get(f"/api/team-runs/{standard['id']}")
    assert standard_read.status_code == 200
    assert standard_read.json()["team_run"]["lifecycle_mode"] == "standard"


def test_list_team_runs_returns_enriched_fields(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")
    team_id = create_team(client, leader_id, [member_id])
    run_id = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "execution_policy": "triggered",
        },
    ).json()["team_run"]["id"]
    cycle_service = client.app.state.team_cycle_service
    team_service = client.app.state.team_run_service
    request = cycle_service.enqueue_request(
        run_id,
        "manual",
        "list-read-model",
        "Design Agent Teams",
        previous_cycle_id=None,
    )
    claimed = cycle_service.claim_next(run_id)
    assert claimed is not None and claimed.id == request.id
    cycle = team_service.create_cycle(
        run_id,
        "manual",
        request.source_id,
        request_id=request.id,
    )
    team_service.set_cycle_status(cycle.id, "running")
    team_service.create_task(
        run_id,
        "Design list",
        "Expose current Cycle",
        cycle_id=cycle.id,
    )

    body = client.get("/api/team-runs").json()
    run = next(r for r in body["team_runs"] if r["id"] == run_id)

    assert run["leader_name"] == "Tech Lead"
    assert run["leader"] == {"name": "Tech Lead", "avatar": "", "initials": "TL"}
    assert run["team_name"] == "Team"
    assert run["goal"] == ""
    assert run["display_status"] == "active"
    assert run["current_objective"] == "Design Agent Teams"
    assert run["cycle_count"] == 1
    assert run["latest_cycle"]["sequence"] == 1
    assert run["task_total"] == 1
    assert "members" in run
    assert "task_counts" in run
    assert "elapsed_seconds" in run


def test_documents_only_list_previewable_files_newest_first(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Preview documents",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    (workspace / "docs").mkdir()
    (workspace / "node_modules" / "pkg").mkdir(parents=True)
    (workspace / "old.md").write_text("old", encoding="utf-8")
    (workspace / "docs" / "page.html").write_text("<h1>preview</h1>", encoding="utf-8")
    (workspace / "new.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (workspace / "archive.zip").write_bytes(b"PK\x03\x04")
    (workspace / "node_modules" / "pkg" / "README.md").write_text("dependency", encoding="utf-8")
    os.utime(workspace / "old.md", (100, 100))
    os.utime(workspace / "docs" / "page.html", (200, 200))
    os.utime(workspace / "new.png", (300, 300))

    response = client.get(f"/api/team-runs/{run['id']}/documents")

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert [(item["path"], item["kind"]) for item in documents] == [
        ("new.png", "image"),
        ("docs/page.html", "html"),
        ("old.md", "md"),
    ]
    assert all(item["previewable"] for item in documents)
    assert client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": ".env"}
    ).status_code == 404
    assert client.get(
        f"/api/team-runs/{run['id']}/documents/content",
        params={"path": "node_modules/pkg/README.md"},
    ).status_code == 404

    first_page = client.get(
        f"/api/team-runs/{run['id']}/documents", params={"limit": 2}
    ).json()
    second_page = client.get(
        f"/api/team-runs/{run['id']}/documents",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    ).json()
    assert [item["path"] for item in first_page["documents"] + second_page["documents"]] == [
        "new.png", "docs/page.html", "old.md"
    ]


def test_html_and_image_documents_return_safe_preview_payloads(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Preview content",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    (workspace / "page.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    (workspace / "image.webp").write_bytes(b"RIFFxxxxWEBP")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    html = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "page.html"}
    ).json()
    image = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "image.webp"}
    ).json()
    image_response = client.get(
        f"/api/team-runs/{run['id']}/documents/image", params={"path": "image.webp"}
    )
    text_response = client.get(
        f"/api/team-runs/{run['id']}/documents/image", params={"path": "notes.txt"}
    )

    assert html == {
        "path": "page.html", "kind": "html", "content": "<h1>Hello</h1>", "previewable": True
    }
    assert image["kind"] == "image"
    assert image["content"] is None
    assert image["previewable"] is True
    assert image["preview_url"].endswith("/documents/image?path=image.webp")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/webp"
    assert image_response.headers["x-content-type-options"] == "nosniff"
    assert text_response.status_code == 415


def test_team_run_detail_aggregate_includes_documents_summary(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    team_id = create_team(client, leader_id, [member_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Aggregate detail",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["workspace_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text("hello", encoding="utf-8")

    response = client.get(f"/api/team-runs/{run['id']}/detail")

    assert response.status_code == 200
    detail = response.json()
    assert detail["team_run"]["id"] == run["id"]
    assert len(detail["agents"]) == 2
    assert detail["tasks"] == []
    assert detail["messages"] == []
    assert detail["cycles"] == []
    assert detail["document_summary"] == {
        "count": 1,
        "size_bytes": 5,
        "kinds": {"md": 1},
    }


def test_team_run_detail_includes_complete_cycle_payload(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Mail Lead")
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Process inbox",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    service = client.app.state.team_run_service
    created = service.create_cycle(run["id"], "hook", "hook-run-1", rounds_budget=3)
    service.set_cycle_status(created.id, "running")
    service.increment_cycle_rounds_used(created.id)
    cycle = service.set_cycle_status(created.id, "completed", summary="Mail handled")

    response = client.get(f"/api/team-runs/{run['id']}/detail")

    assert response.status_code == 200
    assert response.json()["cycles"] == [
        {
            "id": cycle.id,
            "team_run_id": run["id"],
            "sequence": 1,
            "source_type": "hook",
            "source_id": "hook-run-1",
            "status": "completed",
            "rounds_budget": 3,
            "rounds_used": 1,
            "summary": "Mail handled",
            "error_message": None,
            "created_at": cycle.created_at,
            "started_at": cycle.started_at,
            "finished_at": cycle.finished_at,
            "updated_at": cycle.updated_at,
        }
    ]


async def test_answer_decision_request_rejects_stale_and_registers_one_resume(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        run = create_standard_run(app, leader_id, [member_id])
        service = app.state.team_run_service
        worker = service.list_agents(run["id"])[1]
        task = service.create_task(run["id"], "Deploy", "choose target")
        service.set_run_status(run["id"], "running")
        service.start_task(task.id, worker.id)
        service.defer_task_for_user_decision(
            task.id,
            worker.id,
            {
                "topic": "target",
                "question": "Where?",
                "why_needed": "Changes config.",
                "options": [],
                "recommended_option_id": None,
                "blocking_scope": "task",
            },
        )
        request = service.publish_decision_request(run["id"])

        detail = (await client.get(f"/api/team-runs/{run['id']}/detail")).json()
        assert detail["decision_request"]["id"] == request.id
        assert detail["decision_request"]["items"][0]["id"] == "Q-001"

        stale = await client.post(
            f"/api/team-runs/{run['id']}/decision-request/answer",
            json={
                "request_id": request.id,
                "revision": request.revision - 1,
                "answers": {"Q-001": "staging"},
            },
        )
        assert stale.status_code == 409

        answered = await client.post(
            f"/api/team-runs/{run['id']}/decision-request/answer",
            json={
                "request_id": request.id,
                "revision": request.revision,
                "answers": {"Q-001": "staging"},
            },
        )

        assert answered.status_code == 200
        assert answered.json()["decision_request"]["status"] == "resolved"
        assert app.state.team_run_registry.is_running(run["id"]) is True
        duplicate = await client.post(
            f"/api/team-runs/{run['id']}/decision-request/answer",
            json={
                "request_id": request.id,
                "revision": request.revision,
                "answers": {"Q-001": "production"},
            },
        )
        assert duplicate.status_code == 409

        gate.set()
        await _poll_until(lambda: not app.state.team_run_registry.is_running(run["id"]))


def test_waiting_decision_survives_restart_and_cancel_settles_request(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    app = create_app(config)
    service = app.state.team_run_service
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    member = app.state.persona_service.create_persona("Worker", "work", "d", [], [])
    run = service.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = service.list_agents(run.id)[1]
    task = service.create_task(run.id, "Deploy", "choose target")
    service.set_run_status(run.id, "running")
    service.start_task(task.id, worker.id)
    service.defer_task_for_user_decision(
        task.id,
        worker.id,
        {
            "topic": "target",
            "question": "Where?",
            "why_needed": "Changes config.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
    )
    request = service.publish_decision_request(run.id)

    restarted_app = create_app(config)
    with TestClient(restarted_app) as client:
        client.cookies.set(
            "agent_session", restarted_app.state.auth_session_service.issue().token
        )
        assert restarted_app.state.team_run_service.get_team_run(run.id).status == "waiting_for_user"
        assert client.post(
            f"/api/team-runs/{run.id}/add-work", json={"instruction": "more"}
        ).status_code == 409
        canceled = client.post(f"/api/team-runs/{run.id}/cancel")

    assert canceled.status_code == 200
    assert canceled.json()["team_run"]["status"] == "canceled"
    assert restarted_app.state.team_run_service.list_tasks(run.id)[0].status == "canceled"
    resolved = restarted_app.state.team_run_service.list_decision_requests(run.id)[0]
    assert resolved.id == request.id
    assert resolved.status == "canceled"


def test_team_run_list_uses_stable_cursor_pages(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)
    created_ids = {
        client.post(
            "/api/team-runs",
            json={
                "team_id": team_id,
                "goal": f"Run {index}",
                "execution_policy": "triggered",
            },
        ).json()["team_run"]["id"]
        for index in range(3)
    }

    first = client.get("/api/team-runs", params={"limit": 2}).json()
    second = client.get(
        "/api/team-runs",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()

    returned_ids = {
        run["id"] for run in first["team_runs"] + second["team_runs"]
    }
    assert returned_ids == created_ids
    assert second["next_cursor"] is None


def test_create_team_run_rejects_unimplemented_review_mode(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)

    response = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Review the workspace",
            "execution_policy": "triggered",
            "run_mode": "review_only",
            "max_workers": 1,
        },
    )

    assert response.status_code == 422


def test_create_team_run_rejects_concurrency_above_effective_limit(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)

    response = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship sequentially",
            "execution_policy": "triggered",
            "run_mode": "plan_and_execute",
            "max_workers": 2,
        },
    )

    assert response.status_code == 422


def test_delete_team_run_removes_it(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)

    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship it",
            "execution_policy": "triggered",
        },
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
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Temporary test run",
            "execution_policy": "triggered",
        },
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


def test_retry_failed_team_task_api_creates_new_cycle_and_preserves_history(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    team_id = create_team(client, leader_id, [member_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship it",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    service = client.app.state.team_run_service
    cycle = service.create_cycle(run["id"], "manual", "original-cycle")
    task = service.create_task(run["id"], "QA", "Run checks", cycle_id=cycle.id)
    service.set_task_status(task.id, "failed", error_message="timed out")
    service.set_cycle_status(cycle.id, "completed_with_failures", summary="old cycle")
    service.set_run_status(run["id"], "completed_with_failures", summary="old")
    rules = [{"level": "REQUIRED", "text": "Use a dedicated Git worktree."}]
    assert client.put(
        f"/api/teams/{team_id}/rules",
        json={"personality": "", "rules": rules},
    ).status_code == 200

    response = client.post(f"/api/team-runs/{run['id']}/tasks/{task.id}/retry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["team_run"]["status"] == "interrupted"
    assert payload["task"]["id"] != task.id
    assert payload["task"]["status"] == "pending"
    assert payload["task"]["retry_of_task_id"] == task.id
    assert payload["cycle"]["id"] != cycle.id
    assert payload["cycle"]["source_type"] == "task_retry"
    assert payload["cycle"]["source_id"] == task.id
    assert payload["cycle"]["status"] == "interrupted"
    assert payload["cycle"]["rules_snapshot"]["team"]["rules"] == rules

    original_task = next(
        item for item in service.list_tasks(run["id"], cycle.id) if item.id == task.id
    )
    original_cycle = service.get_cycle(cycle.id)
    assert original_task.status == "failed"
    assert original_task.error_message == "timed out"
    assert original_cycle.status == "completed_with_failures"
    assert original_cycle.summary == "old cycle"
    assert client.post(f"/api/team-runs/{run['id']}/tasks/{task.id}/retry").status_code == 409


def test_retry_team_task_api_rejects_missing_task(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship it",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    client.app.state.team_run_service.set_run_status(run["id"], "failed")

    response = client.post(f"/api/team-runs/{run['id']}/tasks/missing/retry")

    assert response.status_code == 404


def test_start_returns_immediately_without_blocking(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")
    created = create_standard_run(
        client.app,
        leader_id,
        [member_id],
        run_mode="planning_only",
    )

    resp = client.post(f"/api/team-runs/{created['id']}/start")

    assert resp.status_code == 200
    # 즉시 반환된 payload는 team_run을 포함
    assert resp.json()["team_run"]["id"] == created["id"]


def test_double_start_conflicts(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    created = create_standard_run(client.app, leader_id, run_mode="planning_only")
    client.post(f"/api/team-runs/{created['id']}/start")
    second = client.post(f"/api/team-runs/{created['id']}/start")
    assert second.status_code in (200, 409)  # 이미 끝났으면 finished 409, 실행중이면 409


def test_cancel_does_not_overwrite_already_terminal_run(tmp_path: Path) -> None:
    """registry에 없는(이미 끝난) 팀런을 /cancel해도 실제 종료 상태가 덮어써지지 않아야 함."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    created = create_standard_run(client.app, leader_id, run_mode="planning_only")
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
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        leader_id = await _async_create_persona(client, "Tech Lead")
        created = create_standard_run(app, leader_id, run_mode="planning_only")
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
    run = create_standard_run(client.app, leader_id, run_mode="planning_only")

    resp = client.post(f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"})
    assert resp.status_code == 409


def test_add_work_rejects_draft_run(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    member_id = create_persona(client, "Worker")
    run = create_standard_run(client.app, leader_id, [member_id])

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

    with TestClient(restarted_app):
        recovered = restarted_app.state.team_run_service.get_team_run(run.id)
    assert recovered.status == "interrupted"
    assert restarted_app.state.team_run_service.list_tasks(run.id)[0].status == "pending"


def test_interrupted_run_rejects_start_and_add_work(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    member_id = create_persona(client, "Worker")
    run = create_standard_run(client.app, leader_id, [member_id])
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
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        run = create_standard_run(app, leader_id, [member_id])
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
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
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
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        created = create_standard_run(app, leader_id, [member_id])
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
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        leader_id = await _async_create_persona(client, "Tech Lead")
        created = create_standard_run(app, leader_id, run_mode="planning_only")
        run_id = created["id"]
        registry = app.state.team_run_registry

        start_resp = await client.post(f"/api/team-runs/{run_id}/start")
        assert start_resp.status_code == 200
        assert registry.is_running(run_id) is True

        cancel_resp = await client.post(f"/api/team-runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["team_run"]["status"] == "canceled"
        assert registry.is_running(run_id) is False

        await _poll_until(lambda: not registry.is_running(run_id))
        assert registry.is_running(run_id) is False

        final = (await client.get(f"/api/team-runs/{run_id}")).json()["team_run"]
        assert final["status"] == "canceled"
