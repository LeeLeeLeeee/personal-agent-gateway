import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import pytest
from execution_helpers import ready_agent_registry
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.team_lifecycle import MAX_CONCURRENT_WORKERS
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.remote_model_client import RemoteRunAbortedError
from personal_agent_gateway.team_model_operations import (
    OperationSpec,
    StaleOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from personal_agent_gateway.team_model_invoker import AmbiguousModelOperation
from personal_agent_gateway.team_provider_recovery import TeamProviderRecovery
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.team_verification_checks import VerificationCheck
from personal_agent_gateway.teams import RequiredVerification, TaskAcceptance

_TERMINAL_STATUSES = {
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
}


def _make_ambiguous_api_operation(
    client,
    sessions,
    *,
    stage="worker_execution",
    stage_ordinal=0,
    preplanning=False,
    interrupt=True,
):
    app = client.app
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    leader = app.state.persona_service.create_persona(
        "Ambiguous Lead",
        "lead",
        "d",
        [],
        [],
    )
    worker = app.state.persona_service.create_persona(
        "Ambiguous Worker",
        "worker",
        "d",
        [],
        [],
    )
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "ambiguous-api",
        "work",
        previous_cycle_id=None,
    )
    cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    worker_agent = next(
        agent
        for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    if preplanning:
        task = None
        actor = teams.get_agent(run.leader_agent_id)
    else:
        teams.set_cycle_status(cycle.id, "running")
        teams.set_run_status(run.id, "running")
        task = teams.create_task(
            run.id,
            "work",
            "work",
            owner_agent_id=worker_agent.id,
            cycle_id=cycle.id,
        )
        teams.start_task(task.id, worker_agent.id)
        actor = worker_agent
    operations = TeamModelOperationService(app.state.database)
    operation = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:{stage}:{stage_ordinal}",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=task.id if task is not None else None,
            agent_id=actor.id,
            provider=actor.backend,
            stage=stage,
            stage_ordinal=stage_ordinal,
            request_digest=hashlib.sha256(stage.encode()).hexdigest(),
        )
    )
    operation = operations.begin_attempt(operation.id, "consumer-1")
    recovery = TeamProviderRecovery(
        teams,
        app.state.agent_registry,
        operations,
        session_loader=lambda: sessions(operation, run),
    )
    if interrupt:
        asyncio.run(
            recovery.interrupt_ambiguous_operation(
                operation.id,
                consumer_run_id="consumer-1",
                upstream_session_id=None,
            )
        )
    app.state.team_provider_recovery = recovery

    class RecordingOrchestrator:
        def __init__(self):
            self.resume_calls = []
            self.continue_calls = []

        def resume(self, team_run_id, cycle_id=None):
            self.resume_calls.append((team_run_id, cycle_id))

        def continue_cycle(self, team_run_id, cycle_id, instruction):
            self.continue_calls.append((team_run_id, cycle_id, instruction))

    orchestrator = RecordingOrchestrator()
    app.state.team_run_orchestrator = orchestrator
    app.state.team_cycle_dispatcher._orchestrator = orchestrator
    return run, cycle, task, worker_agent, operations, operation, orchestrator


@pytest.mark.parametrize(
    "session_mode",
    [
        "exact",
        "none",
        "duplicate",
        "provider_mismatch",
        "consumer_mismatch",
        "loader_failure",
    ],
)
def test_ambiguous_resume_requires_strict_reconciliation(
    tmp_path: Path,
    session_mode: str,
) -> None:
    client = authenticated_client(tmp_path)

    def sessions(operation, run):
        exact = {
            "provider": operation.provider,
            "upstream_id": "strict-session",
            "consumer": "personal-agent-gateway",
            "consumer_session_id": run.id,
            "consumer_run_id": operation.consumer_run_id,
        }
        if session_mode == "loader_failure":
            raise RuntimeError("LMG unavailable")
        if session_mode == "none":
            return []
        if session_mode == "duplicate":
            return [
                exact,
                {**exact, "upstream_id": "strict-session-2"},
            ]
        if session_mode == "provider_mismatch":
            return [{**exact, "provider": "claude"}]
        if session_mode == "consumer_mismatch":
            return [{**exact, "consumer_run_id": "other-run"}]
        return [exact]

    run, cycle, _task, _worker, operations, operation, orchestrator = (
        _make_ambiguous_api_operation(client, sessions)
    )

    response = client.post(f"/api/team-runs/{run.id}/resume")

    if session_mode == "exact":
        assert response.status_code == 200
        assert orchestrator.resume_calls == [(run.id, cycle.id)]
        restored = operations.get(operation.id)
        assert restored.status == "prepared"
        assert restored.upstream_session_id == "strict-session"
    else:
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "ambiguous_operation_not_reconcilable"
        )
        assert orchestrator.resume_calls == []
        assert operations.get(operation.id).status == "ambiguous"
        assert client.app.state.team_run_service.get_team_run(
            run.id
        ).status == "interrupted"


@pytest.mark.parametrize(
    ("stage", "stage_ordinal"),
    [
        ("cycle_add_work", 0),
        ("cycle_planning_repair", 2),
    ],
)
def test_ambiguous_add_work_resume_reuses_exact_operation_path(
    tmp_path: Path,
    stage: str,
    stage_ordinal: int,
) -> None:
    client = authenticated_client(tmp_path)

    def sessions(operation, run):
        return [
            {
                "provider": operation.provider,
                "upstream_id": "strict-add-work-session",
                "consumer": "personal-agent-gateway",
                "consumer_session_id": run.id,
                "consumer_run_id": operation.consumer_run_id,
            }
        ]

    run, cycle, _task, _worker, operations, operation, orchestrator = (
        _make_ambiguous_api_operation(
            client,
            sessions,
            stage=stage,
            stage_ordinal=stage_ordinal,
            preplanning=True,
        )
    )

    response = client.post(f"/api/team-runs/{run.id}/resume")

    assert response.status_code == 200
    assert orchestrator.resume_calls == []
    assert orchestrator.continue_calls == [(run.id, cycle.id, "work")]
    assert operations.get(operation.id).status == "prepared"
    rows = client.app.state.database.fetchall(
        "select id from team_model_operations where cycle_id = ?",
        (cycle.id,),
    )
    assert [row["id"] for row in rows] == [operation.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_responses", "expected_stage", "expected_ordinal"),
    [
        (
            [RemoteRunAbortedError("run_timeout", "timed out")],
            "cycle_add_work",
            0,
        ),
        (
            [
                ModelResponse(
                    "invalid plan",
                    [],
                    upstream_session_id="repair-session",
                ),
                RemoteRunAbortedError("run_timeout", "timed out"),
            ],
            "cycle_planning_repair",
            2,
        ),
    ],
)
async def test_explicit_add_work_resume_runs_production_path_without_collision(
    tmp_path: Path,
    initial_responses,
    expected_stage: str,
    expected_ordinal: int,
) -> None:
    config = make_config(tmp_path)
    app = create_app(
        config,
        agent_registry=ready_agent_registry(config),
    )
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    worker = app.state.persona_service.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    request = cycles.enqueue_request(
        run.id,
        "manual",
        f"explicit-{expected_stage}",
        "work",
        previous_cycle_id=None,
    )
    cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_effective_instruction(cycle.id, "work")
    worker_agent = next(
        agent for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )

    class OperationModel:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = 0

        async def complete_operation(self, _messages, *, consumer_run_id):
            self.calls += 1
            value = self.responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

    lead_model = OperationModel(initial_responses)
    worker_model = OperationModel(
        [
            ModelResponse(
                json.dumps(
                    {
                        "status": "completed",
                        "summary": "done",
                        "reason_code": None,
                        "deliverables": [],
                        "verifications": [
                            {
                                "name": "worker-result",
                                "status": "passed",
                                "evidence": "checked",
                            }
                        ],
                    }
                ),
                [],
                upstream_session_id="worker-session",
            )
        ]
    )
    runtime = TeamRuntime(
        teams,
        lambda agent, _cycle_id=None: (
            lead_model if agent.id == run.leader_agent_id else worker_model
        ),
        operations=app.state.team_model_operation_service,
        model_invoker=app.state.team_model_invoker,
        model_effects=app.state.team_model_effect_service,
        provider_recovery=app.state.team_provider_recovery,
    )
    app.state.team_runtime = runtime

    with pytest.raises(AmbiguousModelOperation):
        await runtime.add_work(run.id, "work", cycle.id)

    operation = app.state.team_model_operation_service.get_open_for_cycle(
        cycle.id
    )
    assert operation is not None
    assert (operation.stage, operation.stage_ordinal, operation.status) == (
        expected_stage,
        expected_ordinal,
        "ambiguous",
    )
    strict_session = operation.upstream_session_id or "strict-resume-session"
    recovery = TeamProviderRecovery(
        teams,
        app.state.agent_registry,
        app.state.team_model_operation_service,
        session_loader=lambda: [
            {
                "provider": operation.provider,
                "upstream_id": strict_session,
                "consumer": "personal-agent-gateway",
                "consumer_session_id": run.id,
                "consumer_run_id": operation.consumer_run_id,
            }
        ],
    )
    runtime._provider_recovery = recovery
    app.state.team_provider_recovery = recovery
    app.state.team_cycle_dispatcher._provider_recovery = recovery
    lead_model.responses.extend(
        [
            ModelResponse(
                json.dumps(
                    [
                        {
                            "plan_task_id": "research",
                            "title": "Research",
                            "description": "Research the request.",
                            "owner_agent_id": worker_agent.id,
                            "required": True,
                            "acceptance": {
                                "required_outputs": [],
                                "required_verifications": ["worker-result"],
                            },
                        }
                    ]
                ),
                [],
                upstream_session_id=strict_session,
            ),
            ModelResponse(
                "summary",
                [],
                upstream_session_id=strict_session,
            ),
        ]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        client.cookies.set(
            "agent_session",
            app.state.auth_session_service.issue().token,
        )
        response = await client.post(f"/api/team-runs/{run.id}/resume")

    assert response.status_code == 200
    await _poll_until(
        lambda: not app.state.team_run_registry.is_running(run.id)
    )
    assert app.state.team_model_operation_service.get(
        operation.id
    ).status == "applied"
    assert teams.get_team_run(run.id).status != "failed"
    assert teams.get_cycle(cycle.id).status != "failed"
    assert cycles.get_request(request.id).status == "settled"


@pytest.mark.parametrize("operation_state", ["waiting", "invoking"])
def test_cancel_api_closes_open_operation_and_complete_continuous_lineage(
    tmp_path: Path,
    operation_state: str,
) -> None:
    client = authenticated_client(tmp_path)
    run, cycle, task, worker, operations, operation, _orchestrator = (
        _make_ambiguous_api_operation(
            client,
            lambda _operation, _run: [],
            interrupt=False,
        )
    )
    recovery = client.app.state.team_provider_recovery
    if operation_state == "waiting":
        recovery.wait_for_operation(
            operation.id,
            reason_code="provider_unavailable",
        )

    response = client.post(f"/api/team-runs/{run.id}/cancel")

    assert response.status_code == 200
    assert response.json()["team_run"]["status"] == "canceled"
    assert operations.get(operation.id).status == "canceled"
    assert client.app.state.team_run_service.get_cycle(cycle.id).status == "canceled"
    assert client.app.state.team_cycle_service.get_request(
        cycle.request_id
    ).status == "canceled"
    assert client.app.state.team_run_service.get_task(task.id).status == "canceled"
    assert client.app.state.team_run_service.get_agent(worker.id).status == "canceled"
    assert recovery.reconcile_startup().interrupted_cycle_ids == ()
    with pytest.raises(StaleOperation, match="Expected operation status invoking"):
        operations.complete(
            operation.id,
            operation.version,
            ValidatedOperationResult("task_outcome", {"status": "completed"}),
        )


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
    config = make_config(tmp_path)
    client = TestClient(
        create_app(
            config,
            agent_registry=ready_agent_registry(config),
        )
    )
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


def test_requesting_negotiation_at_creation_actually_enables_it(tmp_path: Path) -> None:
    """The flag was accepted by the endpoint and then dropped, because
    create_team_run_from_team had no parameter to forward it through. A feature
    that cannot be switched on from the only surface that creates runs is
    unreachable -- which is exactly how a shipped feature goes unnoticed."""
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
            "plan_negotiation": True,
        },
    )

    assert response.status_code == 200
    run_id = response.json()["team_run"]["id"]
    stored = client.app.state.team_run_service.get_team_run(run_id)
    assert stored.plan_negotiation_enabled is True


def test_a_run_created_without_asking_leaves_negotiation_off(tmp_path: Path) -> None:
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

    run_id = response.json()["team_run"]["id"]
    stored = client.app.state.team_run_service.get_team_run(run_id)
    assert stored.plan_negotiation_enabled is False


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


def test_worktree_delivery_resolves_conflict_before_applying(
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
    run = client.post(
        "/api/team-runs",
        json={"team_id": team_id, "execution_policy": "triggered"},
    ).json()["team_run"]
    run_id = run["id"]
    working_root = Path(run["working_root"])
    (working_root / "README.md").write_text("team\n", encoding="utf-8")
    assert client.post(
        f"/api/team-runs/{run_id}/delivery/commit",
        json={"message": "feat: change from team"},
    ).status_code == 200

    (repository / "README.md").write_text("target\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "feat: change target")

    conflicted = client.post(f"/api/team-runs/{run_id}/delivery/apply")
    assert conflicted.status_code == 200
    session = conflicted.json()["delivery"]["conflict_session"]
    assert session["total_count"] == 1
    assert session["files"][0]["path"] == "README.md"
    assert session["files"][0]["target_content"] == "target\n"
    assert session["files"][0]["team_content"] == "team\n"
    assert (repository / "README.md").read_text(encoding="utf-8") == "target\n"

    conflict_id = session["files"][0]["id"]
    resolved = client.post(
        f"/api/team-runs/{run_id}/delivery/conflicts/{conflict_id}/resolve",
        json={"mode": "manual", "content": "target + team\n"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["delivery"]["conflict_session"]["can_continue"] is True

    continued = client.post(f"/api/team-runs/{run_id}/delivery/continue")
    assert continued.status_code == 200
    delivery = continued.json()["delivery"]
    assert delivery["conflict_session"] is None
    assert delivery["up_to_date"] is True
    assert (repository / "README.md").read_text(encoding="utf-8") == "target + team\n"
    assert not (Path(run["workspace_root"]) / ".delivery-session.json").exists()
    assert ".delivery-integration-" not in _git(repository, "worktree", "list")


def test_worktree_delivery_auto_resolves_generated_doc_indexes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    inspector = repository / "docs" / "component-inspector"
    inspector.mkdir(parents=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    base_index = "# Component Inspector Reports\n\n- [Base — 2026-07-20 10:00](./base.md)\n"
    (inspector / "index.md").write_text(base_index, encoding="utf-8")
    base_registry = {
        "schema_version": 1,
        "document_count": 1,
        "documents": [{
            "path": "docs/component-inspector/index.md",
            "title": "Component Inspector Reports",
            "excerpt": "base",
        }],
    }
    (repository / "docs" / "registry.json").write_text(
        json.dumps(base_registry, indent=2) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", "docs")
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
    run = client.post(
        "/api/team-runs",
        json={"team_id": team_id, "execution_policy": "triggered"},
    ).json()["team_run"]
    run_id = run["id"]
    working_root = Path(run["working_root"])
    team_index = (
        "# Component Inspector Reports\n\n"
        "- [Team — 2026-07-22 09:00](./team.md)\n"
        "- [Base — 2026-07-20 10:00](./base.md)\n"
    )
    (working_root / "docs" / "component-inspector" / "index.md").write_text(
        team_index,
        encoding="utf-8",
    )
    team_registry = {
        **base_registry,
        "document_count": 2,
        "documents": [
            *base_registry["documents"],
            {"path": "docs/team.md", "title": "Team"},
        ],
    }
    (working_root / "docs" / "registry.json").write_text(
        json.dumps(team_registry, indent=2) + "\n",
        encoding="utf-8",
    )
    assert client.post(
        f"/api/team-runs/{run_id}/delivery/commit",
        json={"message": "docs: add team report"},
    ).status_code == 200

    target_index = (
        "# Component Inspector Reports\n\n"
        "- [Target — 2026-07-22 10:00](./target.md)\n"
        "- [Base — 2026-07-20 10:00](./base.md)\n"
    )
    (inspector / "index.md").write_text(target_index, encoding="utf-8")
    target_registry = {
        **base_registry,
        "document_count": 2,
        "documents": [
            *base_registry["documents"],
            {"path": "docs/target.md", "title": "Target"},
        ],
    }
    (repository / "docs" / "registry.json").write_text(
        json.dumps(target_registry, indent=2) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", "docs")
    _git(repository, "commit", "-m", "docs: add target report")

    applied = client.post(f"/api/team-runs/{run_id}/delivery/apply")
    assert applied.status_code == 200
    delivery = applied.json()["delivery"]
    assert delivery["conflict_session"] is None
    assert delivery["up_to_date"] is True
    assert set(delivery["auto_resolved_files"]) == {
        "docs/component-inspector/index.md",
        "docs/registry.json",
    }
    merged_index = (inspector / "index.md").read_text(encoding="utf-8")
    assert "[Target — 2026-07-22 10:00]" in merged_index
    assert "[Team — 2026-07-22 09:00]" in merged_index
    merged_registry = json.loads(
        (repository / "docs" / "registry.json").read_text(encoding="utf-8")
    )
    assert merged_registry["document_count"] == 3
    assert [document["path"] for document in merged_registry["documents"]] == [
        "docs/component-inspector/index.md",
        "docs/target.md",
        "docs/team.md",
    ]
    index_entry = merged_registry["documents"][0]
    assert "[Target — 2026-07-22 10:00]" in index_entry["excerpt"]
    assert "[Team — 2026-07-22 09:00]" in index_entry["excerpt"]


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
    assert cycle_request["previous_summary_text"] == (
        "STATUS: COMPLETED\n\nSUMMARY\nprevious"
    )
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


def test_canceling_a_run_drops_a_pending_pause_request(tmp_path: Path) -> None:
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
    run_id = run["id"]

    # 정지 요청은 서비스로 직접 건다. /pause 엔드포인트는 Task 5 에서 생기고,
    # 이 작업은 엔드포인트가 아니라 요청의 수명을 다룬다.
    client.app.state.team_run_service.request_pause(run_id)
    assert client.get(f"/api/team-runs/{run_id}").json()["team_run"]["pause_requested_at"]

    client.post(f"/api/team-runs/{run_id}/cancel")

    run = client.get(f"/api/team-runs/{run_id}").json()["team_run"]
    assert run["pause_requested_at"] is None


@dataclass
class AnsweringModel:
    """고정된 답을 돌려주는 테스트 전용 모델.

    answer_question 은 operation 원장을 쓰지 않고 model.complete() 를 바로
    부르므로, 다른 팀런 테스트가 쓰는 OperationModel(complete_operation)이
    아니라 이 모델이 필요하다.
    """

    answer: str

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        return ModelResponse(self.answer, [])


def test_pausing_an_idle_run_marks_it_paused_immediately(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    created = create_standard_run(app, leader.id)
    run_id = created["id"]

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        response = client.post(f"/api/team-runs/{run_id}/pause")

    assert response.status_code == 200
    assert response.json()["team_run"]["status"] == "paused"


def test_a_question_returns_an_answer_and_creates_no_tasks(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    created = create_standard_run(app, leader.id)
    run_id = created["id"]
    app.state.team_runtime = TeamRuntime(
        app.state.team_run_service,
        lambda _agent: AnsweringModel("설정 파일에서 정해집니다."),
        app.state.event_bus,
    )

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        before = client.get(f"/api/team-runs/{run_id}/tasks").json()["tasks"]

        response = client.post(
            f"/api/team-runs/{run_id}/questions",
            json={"question": "이 값은 어디서 정해지나요?"},
        )
        assert response.status_code == 200
        assert response.json()["answer"]

        after = client.get(f"/api/team-runs/{run_id}/tasks").json()["tasks"]

    assert len(after) == len(before)


def test_the_question_log_holds_both_sides(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    created = create_standard_run(app, leader.id)
    run_id = created["id"]
    app.state.team_runtime = TeamRuntime(
        app.state.team_run_service,
        lambda _agent: AnsweringModel("왜냐하면요."),
        app.state.event_bus,
    )

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        client.post(f"/api/team-runs/{run_id}/questions", json={"question": "왜죠"})

        messages = client.get(f"/api/team-runs/{run_id}/questions").json()["messages"]

    kinds = [message["kind"] for message in messages]
    assert kinds == ["user_question", "lead_answer"]


def test_a_blank_question_is_refused(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    created = create_standard_run(app, leader.id)
    run_id = created["id"]

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        response = client.post(
            f"/api/team-runs/{run_id}/questions", json={"question": "   "}
        )

    assert response.status_code == 422


def test_a_paused_run_can_be_resumed(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    created = create_standard_run(app, leader.id)
    run_id = created["id"]

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        client.post(f"/api/team-runs/{run_id}/pause")
        response = client.post(f"/api/team-runs/{run_id}/resume")

    assert response.status_code != 409


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_policy", ["auto", "triggered"])
async def test_cancel_during_add_work_cannot_resurrect_continuous_lineage(
    tmp_path: Path,
    execution_policy: str,
) -> None:
    config = make_config(tmp_path)
    app = create_app(
        config,
        agent_registry=ready_agent_registry(config),
    )
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


def test_team_task_payload_exposes_acceptance_outcome_and_result(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    run = create_standard_run(
        client.app,
        leader_id,
        [worker_id],
        run_mode="planning_only",
    )
    service = client.app.state.team_run_service
    task = service.create_task(
        run["id"],
        "Verify guide",
        "Check the guide.",
        required=True,
        acceptance=TaskAcceptance(
            ("outputs/guide.md",),
            (RequiredVerification("link-check"),),
        ),
    )
    prerequisite = service.create_task(
        run["id"],
        "Prepare guide",
        "Write the guide.",
    )
    service.add_task_dependencies(task.id, [prerequisite.id])
    service.record_task_outcome(
        task.id,
        {
            "status": "blocked",
            "summary": "Link checker was unavailable.",
            "reason_code": "tool_unavailable",
            "deliverables": [],
            "verifications": [],
        },
        {
            "accepted": False,
            "status": "blocked",
            "reason_code": "required_verification_failed",
            "evidence": {},
        },
    )
    service.set_task_status(
        task.id,
        "blocked",
        error_message="required_verification_failed",
    )

    response = client.get(f"/api/team-runs/{run['id']}/tasks")

    assert response.status_code == 200
    payload = response.json()["tasks"][0]
    assert payload["required"] is True
    assert payload["depends_on_task_ids"] == [prerequisite.id]
    assert payload["acceptance"] == {
        "required_outputs": ["outputs/guide.md"],
        "required_verifications": [{"name": "link-check", "check": None}],
    }
    assert payload["outcome"]["reason_code"] == "tool_unavailable"
    assert payload["acceptance_result"]["reason_code"] == (
        "required_verification_failed"
    )
    assert payload["acceptance_recovery_attempts"] == 0

    detail = client.get(f"/api/team-runs/{run['id']}/detail")

    assert detail.status_code == 200
    detail_tasks = {item["id"]: item for item in detail.json()["tasks"]}
    assert detail_tasks[task.id]["depends_on_task_ids"] == [prerequisite.id]
    assert detail_tasks[prerequisite.id]["depends_on_task_ids"] == []

    leader, worker = service.list_agents(run["id"])
    service.start_task(task.id, worker.id)
    service.record_acceptance_review(
        task.id,
        leader.id,
        worker.id,
        action="retry_worker",
        reason_code="undeclared_deliverable",
        reason="The contract declares no output.",
        instruction="Resubmit without the undeclared file.",
        acceptance_after=None,
        rejected_deliverables=("outputs/guide.md",),
        rejected_verifications=(),
    )

    refreshed = client.get(f"/api/team-runs/{run['id']}/tasks")

    assert refreshed.status_code == 200
    assert refreshed.json()["tasks"][0]["acceptance_recovery_attempts"] == 1


def test_task_payload_exposes_verification_checks(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    run = create_standard_run(
        client.app,
        leader_id,
        [worker_id],
        run_mode="planning_only",
    )
    service = client.app.state.team_run_service
    service.create_task(
        run["id"],
        "Draft the library guide",
        "Write the draft.",
        required=True,
        acceptance=TaskAcceptance(
            required_outputs=("draft.md",),
            required_verifications=(
                RequiredVerification("reviewed"),
                RequiredVerification(
                    "marker",
                    VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
                ),
            ),
        ),
    )

    response = client.get(f"/api/team-runs/{run['id']}/tasks")

    assert response.status_code == 200
    task_payload = response.json()["tasks"][0]
    assert task_payload["acceptance"]["required_verifications"] == [
        {"name": "reviewed", "check": None},
        {
            "name": "marker",
            "check": {
                "type": "file_contains",
                "path": "draft.md",
                "value": "<library_draft>",
            },
        },
    ]


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
    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        await asyncio.wait_for(self.gate.wait(), timeout=5)
        prompt = str(messages[-1].get("content", ""))
        if "CONCRETE ASSIGNMENT" in prompt:
            content = json.dumps(
                {
                    "status": "completed",
                    "summary": "Done",
                    "reason_code": None,
                    "deliverables": [],
                    "verifications": [
                        {
                            "name": "worker-result",
                            "status": "passed",
                            "evidence": "test fixture response",
                        }
                    ],
                }
            )
        elif "Task results:" in prompt:
            content = "Completed."
        else:
            content = json.dumps(
                [
                    {
                        "plan_task_id": "t",
                        "title": "T",
                        "description": "D",
                        "owner_agent_id": None,
                        "required": True,
                        "acceptance": {
                            "required_outputs": [],
                            "required_verifications": ["worker-result"],
                        },
                    }
                ]
            )
        return ModelResponse(content=content, tool_calls=[])


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
    assert Path(model._execution["workspace_root"]).resolve() == Path(run["working_root"]).resolve()


def test_create_team_run_api_inherits_parent_workspace(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)
    parent = client.post(
        "/api/team-runs",
        json={"team_id": team_id, "execution_policy": "triggered"},
    ).json()["team_run"]
    parent_file = Path(parent["working_root"]) / "README.md"
    parent_file.write_text("SNS studio", encoding="utf-8")
    client.app.state.team_run_service.set_run_status(parent["id"], "completed")

    response = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "execution_policy": "triggered",
            "parent_team_run_id": parent["id"],
        },
    )

    assert response.status_code == 200
    child = response.json()["team_run"]
    assert child["parent_team_run_id"] == parent["id"]
    assert (Path(child["working_root"]) / "README.md").read_text(
        encoding="utf-8"
    ) == "SNS studio"


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
    workspace = Path(run["working_root"])
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
    workspace = Path(run["working_root"])
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
    workspace = Path(run["working_root"])
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


def test_cycle_space_policy_is_included_in_cycle_detail(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Mail Lead")
    team_id = create_team(client, leader_id)
    assert client.put(
        f"/api/spaces/teams/{team_id}",
        json={"read_mode": "all", "write_mode": "isolated"},
    ).status_code == 200
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
    service.set_cycle_effective_instruction(
        created.id,
        "Read the inbox and summarize actionable messages.",
    )
    service.set_cycle_status(created.id, "running")
    service.increment_cycle_rounds_used(created.id)
    cycle = service.set_cycle_status(created.id, "completed", summary="Mail handled")

    response = client.get(f"/api/team-runs/{run['id']}/detail")

    assert response.status_code == 200
    detail = response.json()
    assert detail["cycles"] == [
        {
            "id": cycle.id,
            "team_run_id": run["id"],
            "sequence": 1,
            "source_type": "hook",
            "source_id": "hook-run-1",
            "status": "completed",
            "rounds_budget": 3,
            "rounds_used": 1,
            "rules_snapshot": cycle.rules_snapshot,
            "space_policy": cycle.space_policy,
            "effective_instruction": "Read the inbox and summarize actionable messages.",
            "summary": "Mail handled",
            "coverage_gaps": None,
            "error_message": None,
            "created_at": cycle.created_at,
            "started_at": cycle.started_at,
            "finished_at": cycle.finished_at,
            "updated_at": cycle.updated_at,
        }
    ]
    assert detail["cycles"][0]["space_policy"]["read_mode"] == "all"


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


@pytest.mark.asyncio
async def test_auto_decision_answer_observes_ambiguous_marker_and_pauses_series(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    app = create_app(
        config,
        agent_registry=ready_agent_registry(config),
    )
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    leader = app.state.persona_service.create_persona("Lead", "lead", "d", [], [])
    worker = app.state.persona_service.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "auto goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=2,
        auto_interval_seconds=60,
    )
    request = cycles.claim_next(run.id)
    assert request is not None
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    worker_agent = next(
        agent for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    task = teams.create_task(
        run.id,
        "Deploy",
        "choose target",
        owner_agent_id=worker_agent.id,
        cycle_id=cycle.id,
    )
    teams.start_task(task.id, worker_agent.id)
    teams.defer_task_for_user_decision(
        task.id,
        worker_agent.id,
        {
            "topic": "target",
            "question": "Where?",
            "why_needed": "Changes config.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
    )
    decision = teams.publish_decision_request(run.id, cycle.id)

    class AmbiguousRuntime:
        async def resume(self, team_run_id, cycle_id=None):
            teams.set_run_status(team_run_id, "interrupted")
            raise AmbiguousModelOperation(
                "operation-1",
                "consumer-1",
                "run_timeout",
            )

    app.state.team_runtime = AmbiguousRuntime()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        client.cookies.set(
            "agent_session",
            app.state.auth_session_service.issue().token,
        )
        response = await client.post(
            f"/api/team-runs/{run.id}/decision-request/answer",
            json={
                "request_id": decision.id,
                "revision": decision.revision,
                "answers": {"Q-001": "staging"},
            },
        )

    assert response.status_code == 200
    await _poll_until(
        lambda: cycles.policy_status(run.id) == "paused_interrupted"
    )
    assert cycles.get_request(request.id).status == "dispatching"
    assert teams.get_cycle(cycle.id).status == "interrupted"
    assert app.state.team_cycle_dispatcher.last_error is None
    assert [
        event["type"] for event in app.state.event_bus.recent()
    ].count("team.auto_series.paused") == 1


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


# The effective limit used to be one -- max_workers was not an accepted field
# at all and the endpoint passed the constant -- so this refused 2. It now
# refuses above the executor's ceiling instead. The old version also sent
# run_mode, which is not an accepted field either, so it went on passing for
# that reason after max_workers became legal: it asserted 422 while testing
# nothing its name claims. Dropped, so the assertion is about max_workers.
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
            "max_workers": MAX_CONCURRENT_WORKERS + 1,
        },
    )

    assert response.status_code == 422


def test_create_team_run_accepts_parallel_assignments_up_to_the_ceiling(
    tmp_path: Path,
) -> None:
    """A run created through the product could not overlap anything before
    this: the field was forbidden and the endpoint passed 1."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    team_id = create_team(client, leader_id)

    response = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Ship it",
            "execution_policy": "triggered",
            "max_workers": MAX_CONCURRENT_WORKERS,
        },
    )

    assert response.status_code == 200
    run = response.json()["team_run"]
    assert run["configured_max_workers"] == MAX_CONCURRENT_WORKERS
    # Reported as 1 because this gateway has overlap off; the run still records
    # what it was asked for.
    assert run["max_workers"] == 1
    assert run["execution_mode"] == "sequential"


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


def test_delete_team_run_whose_worktree_branch_is_already_gone(tmp_path: Path) -> None:
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
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    client.app.state.team_run_service._db.execute(
        "update team_runs set space_policy_snapshot_json = ?, working_root = ?, "
        "worktree_branch = ? where id = ?",
        (
            json.dumps({"write_mode": "worktree", "workspace_path": str(repository)}),
            str(workspace / "project"),
            f"team-run/{run['id']}",
            run["id"],
        ),
    )

    deleted = client.delete(f"/api/team-runs/{run['id']}")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/api/team-runs/{run['id']}").status_code == 404
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


@pytest.mark.parametrize("terminal_status", ["completed", "blocked"])
def test_cancel_does_not_overwrite_already_terminal_run(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    """registry에 없는(이미 끝난) 팀런을 /cancel해도 실제 종료 상태가 덮어써지지 않아야 함."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    created = create_standard_run(client.app, leader_id, run_mode="planning_only")
    run_id = created["id"]

    service = client.app.state.team_run_service
    service.set_run_status(run_id, terminal_status, summary="real result")

    cancel_resp = client.post(f"/api/team-runs/{run_id}/cancel")
    assert cancel_resp.status_code == 200

    final = client.get(f"/api/team-runs/{run_id}").json()["team_run"]
    assert final["status"] == terminal_status
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


def test_add_work_rejects_a_canceled_team_run(tmp_path: Path) -> None:
    """A canceled run must stay canceled.

    _TERMINAL contains "canceled", so add_work passed its guards and the
    resume branch below it put the run back to running -- Stop reported
    success while the agent kept writing to the workspace.
    """
    app = create_app(make_config(tmp_path))
    leader = app.state.persona_service.create_persona("Tech Lead", "lead", "d", [], [])
    leader_id = leader.id
    created = create_standard_run(app, leader_id)
    run_id = created["id"]
    service = app.state.team_run_service
    service.set_run_status(run_id, "canceled")

    with TestClient(app) as client:
        client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
        response = client.post(
            f"/api/team-runs/{run_id}/add-work",
            json={"instruction": "keep going"},
        )

    assert response.status_code == 409
    assert "cancel" in response.json()["detail"].lower()
    assert service.get_team_run(run_id).status == "canceled"


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


def test_team_run_detail_shows_what_each_task_built(tmp_path: Path) -> None:
    """The operator cannot contest a plan whose coverage they cannot see, so the
    promised-versus-built comparison has to reach the client."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    team_id = create_team(client, leader_id, [member_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Show build evidence",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]
    workspace = Path(run["working_root"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "kept.md").write_text("x", encoding="utf-8")
    service = client.app.state.team_run_service
    task = service.create_task(
        run["id"],
        "Write the guide",
        "Write it.",
        acceptance=TaskAcceptance(("kept.md", "forgotten.md"), ()),
    )
    with client.app.state.database.connection() as connection:
        connection.execute(
            "update team_tasks set status = 'completed', outcome_json = ?, "
            "acceptance_result_json = ? where id = ?",
            (
                json.dumps({"deliverables": [{"path": "kept.md"}, {"path": "ghost.md"}]}),
                json.dumps(
                    {
                        "evidence": {
                            "verifications": {
                                "n": {"mode": "attested", "status": "passed"}
                            },
                            "attested_only": True,
                        }
                    }
                ),
                task.id,
            ),
        )

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    evidence = detail["tasks"][0]["build_evidence"]
    assert evidence["undeclared_promises"] == ["forgotten.md"]
    assert evidence["extra_declarations"] == ["ghost.md"]
    assert evidence["missing_files"] == ["ghost.md"]
    assert evidence["verifications"] == [
        {"name": "n", "mode": "attested", "status": "passed"}
    ]
    assert detail["build_evidence_summary"] == {
        "task_count": 1,
        "worker_asserted_only_count": 1,
        "missing_file_count": 1,
        "unverified_task_count": 0,
        "undeclared_promise_count": 1,
    }


def test_detail_reports_why_nothing_ran(tmp_path: Path) -> None:
    """When negotiation ends a run, the objections are the only explanation the
    operator gets, so /detail must carry them in full."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    team_id = create_team(client, leader_id, [member_id])
    run = client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Negotiate the plan",
            "execution_policy": "triggered",
            "plan_negotiation": True,
        },
    ).json()["team_run"]

    service = client.app.state.team_run_service
    cycle = service.create_cycle(run["id"], "manual", "manual-1")
    worker = next(
        agent for agent in service.list_agents(run["id"]) if agent.role != "leader"
    )
    revision = service.create_plan_revision(run["id"], cycle.id, ["t"], [worker.id])
    objections = [{"kind": "gap", "task_ref": "T-01", "detail": "마이그레이션 담당 없음"}]
    service.record_plan_review(revision.id, worker.id, "object", objections)
    service.set_plan_revision_status(revision.id, "superseded")

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    (revision_payload,) = detail["plan_revisions"]
    assert revision_payload["status"] == "superseded"
    assert revision_payload["objections"] != {}


def _create_triggered_run(client: TestClient, leader_id: str, member_ids: list[str]) -> dict[str, object]:
    """A contest can only ever be filed against the shape real runs have:
    plan_and_execute + continuous lifecycle. POST /api/team-runs hardcodes
    lifecycle_mode="continuous"; passing execution_policy="triggered" gives a
    run where enqueue_request's policy gate for source_type="contest" is
    satisfied, the same way test_team_run_detail_aggregate_includes_documents_summary
    builds its run."""
    team_id = create_team(client, leader_id, member_ids)
    return client.post(
        "/api/team-runs",
        json={
            "team_id": team_id,
            "goal": "Contest test",
            "execution_policy": "triggered",
        },
    ).json()["team_run"]


def test_contesting_the_plan_queues_a_request(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])

    response = client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "T-04 and T-15 have no owner", "client_request_id": "c1"},
    )

    assert response.status_code == 200
    assert response.json()["cycle_request"]["source_type"] == "contest"


def test_contesting_the_same_objection_twice_is_idempotent(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    payload = {"objection": "T-04 has no owner", "client_request_id": "c1"}

    first = client.post(f"/api/team-runs/{run['id']}/contests", json=payload).json()
    second = client.post(f"/api/team-runs/{run['id']}/contests", json=payload).json()

    assert first["cycle_request"]["id"] == second["cycle_request"]["id"]


def test_a_canceled_run_refuses_a_contest(tmp_path: Path) -> None:
    """claim_next raises for a canceled run, and enqueue_request refuses too, so
    the endpoint has to surface that as a 409 rather than a 500."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    client.post(f"/api/team-runs/{run['id']}/cancel")

    response = client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "too late", "client_request_id": "c1"},
    )

    assert response.status_code == 409


def test_an_unadjudicated_contest_appears_in_detail_with_a_null_kind(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "T-04 and T-15 have no owner", "client_request_id": "c1"},
    )

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    assert detail["contests"] == [
        {
            "objection": "T-04 and T-15 have no owner",
            "kind": None,
            "reason": None,
            "supersedes": [],
            "status": "queued",
            "error_message": None,
            "created_at": detail["contests"][0]["created_at"],
        }
    ]


def test_a_contest_whose_cycle_died_is_not_reported_as_awaiting_a_ruling(
    tmp_path: Path,
) -> None:
    """kind is null for a contest still waiting and for one whose cycle failed,
    and refiling the same objection is idempotent -- so without the request
    status and the cycle's error message the UI shows a dead contest as pending
    forever, which is what the live run did."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "T-04 has no owner", "client_request_id": "c1"},
    )
    app = client.app
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    claimed = cycles.claim_next(run["id"])
    cycle = teams.create_cycle(
        run["id"], claimed.source_type, claimed.source_id, request_id=claimed.id
    )
    teams.set_cycle_status(
        cycle.id,
        "failed",
        error_message="Team run status 'draft' cannot be contested",
    )
    cycles.settle_cycle(cycle.id)

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    contest = detail["contests"][0]
    assert contest["kind"] is None
    assert contest["status"] == "settled"
    assert contest["error_message"] == (
        "Team run status 'draft' cannot be contested"
    )


def test_an_adjudicated_contest_reports_the_structured_verdict_not_prose(
    tmp_path: Path,
) -> None:
    """The verdict must come from the operation's structured result_json, not
    from splitting the plan_adjudication message's text -- so the reason and
    decision here deliberately contain the separators a prose parser would
    split on (" Supersedes: " and "; "). If detail["contests"] ever goes back
    to parsing the message, this mangles the reason and this test fails."""
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    client.post(
        f"/api/team-runs/{run['id']}/contests",
        json={"objection": "T-04 has no owner", "client_request_id": "c1"},
    )

    app = client.app
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    claimed = cycles.claim_next(run["id"])
    cycle = teams.create_cycle(
        run["id"], claimed.source_type, claimed.source_id, request_id=claimed.id
    )
    actor = teams.get_agent(run["leader_agent_id"])
    operations = app.state.team_model_operation_service
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:cycle_contest:0",
            team_run_id=run["id"],
            cycle_id=cycle.id,
            task_id=None,
            agent_id=actor.id,
            provider=actor.backend,
            stage="cycle_contest",
            stage_ordinal=0,
            request_digest=hashlib.sha256(b"cycle_contest").hexdigest(),
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    reason = (
        'T-04 has no owner. This reason contains " Supersedes: " on purpose, '
        "to prove the verdict is read structurally and not by splitting text."
    )
    task_spec = {
        "title": "Own T-04",
        "description": "Assign an owner to T-04.",
        "owner_agent_id": None,
        "required": True,
        "plan_task_id": "own-t-04",
        "depends_on_task_ids": [],
        "acceptance": {
            "required_outputs": ["own-t-04.md"],
            "required_verifications": [],
        },
    }
    operation = operations.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult(
            "contest_verdict",
            {
                "kind": "amend",
                "reason": reason,
                "tasks": [task_spec],
                "supersedes": [
                    {
                        "document_path": "docs/plan.md",
                        "decision": "revised; then re-reviewed",
                    }
                ],
            },
        ),
        upstream_session_id="lead-session",
    )
    app.state.team_model_effect_service.apply_contest_verdict(operation.id)

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    contest = next(
        entry for entry in detail["contests"] if entry["objection"] == "T-04 has no owner"
    )
    assert contest["kind"] == "amend"
    assert contest["reason"] == reason
    assert contest["supersedes"] == [
        {"document_path": "docs/plan.md", "decision": "revised; then re-reviewed"}
    ]


def test_detail_reads_the_real_synthesis_not_the_question_it_asked_first(
    tmp_path: Path,
) -> None:
    """A cycle whose synthesis asked the user holds two applied cycle_synthesis
    operations: ordinal 0 carrying the question, ordinal 1 carrying the actual
    summary and its coverage gaps. Taking the first applied one reported "did
    not report" for a cycle where the leader did report."""
    client = authenticated_client(tmp_path)
    app = client.app
    teams = app.state.team_run_service
    cycles = app.state.team_cycle_service
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "Developer")
    run = _create_triggered_run(client, leader_id, [member_id])
    enqueued = cycles.enqueue_request(
        run["id"], "manual", "m1", "work", previous_cycle_id=None
    )
    cycles.claim_next(run["id"])
    cycle = teams.create_cycle(
        run["id"], "manual", enqueued.source_id, request_id=enqueued.id
    )
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run["id"], "running")
    worker = next(
        agent
        for agent in teams.list_agents(run["id"])
        if agent.id != run["leader_agent_id"]
    )
    task = teams.create_task(
        run["id"], "work", "work", owner_agent_id=worker.id, cycle_id=cycle.id
    )
    teams.start_task(task.id, worker.id)
    teams.finish_task(task.id, worker.id, "completed", result="done")
    teams.set_run_status(run["id"], "summarizing")
    operations = app.state.team_model_operation_service
    effects = app.state.team_model_effect_service
    actor = teams.get_agent(run["leader_agent_id"])

    def complete_synthesis(ordinal, kind, payload):
        reserved = operations.reserve(
            OperationSpec(
                operation_key=f"{cycle.id}:cycle_synthesis:{ordinal}",
                team_run_id=run["id"],
                cycle_id=cycle.id,
                task_id=None,
                agent_id=actor.id,
                provider=actor.backend,
                stage="cycle_synthesis",
                stage_ordinal=ordinal,
                request_digest=hashlib.sha256(
                    f"cycle_synthesis:{ordinal}".encode()
                ).hexdigest(),
            )
        )
        invoking = operations.begin_attempt(reserved.id, "consumer-1")
        return operations.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult(kind, payload),
            upstream_session_id="lead-session",
        )

    question = complete_synthesis(
        0,
        "user_decision",
        {
            "kind": "ask_user",
            "topic": "format",
            "question": "Which final format?",
            "why_needed": "The requested format is ambiguous.",
            "options": [
                {"id": "short", "label": "Short", "impact": "Keeps it concise."}
            ],
            "recommended_option_id": "short",
            "blocking_scope": "run",
        },
    )
    effects.apply_synthesis_decision(question.id)
    teams.publish_decision_request(run["id"], cycle.id)
    decision_request = teams.get_active_decision_request(run["id"], cycle.id)
    teams.answer_decision_request(
        run["id"],
        decision_request.id,
        decision_request.revision,
        {decision_request.items[0]["id"]: "short"},
    )
    teams.set_run_status(run["id"], "summarizing")
    teams.set_cycle_status(cycle.id, "running")
    gaps = [
        {"obligation": "T-04 discard", "document": "docs/plan.md §4", "note": ""}
    ]
    synthesis = complete_synthesis(
        1, "synthesis", {"summary": "Built it.", "coverage_gaps": gaps}
    )
    effects.apply_synthesis(synthesis.id, "Built it.")

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    reported = next(
        entry for entry in detail["cycles"] if entry["id"] == cycle.id
    )
    assert reported["coverage_gaps"] == gaps
