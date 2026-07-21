import shutil
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_directory import TeamService
from personal_agent_gateway.teams import TeamRunService


def make_services(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    return personas, teams


def make_policy_services(tmp_path: Path):
    db = Database(tmp_path / "policy.db")
    db.initialize()
    personas = PersonaService(db)
    cycle_service = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "policy-workspace",
        cycle_service=cycle_service,
    )
    leader = personas.create_persona("Policy Lead", "lead", "d", [], [])
    worker = personas.create_persona("Policy Worker", "worker", "d", [], [])
    return db, teams, cycle_service, leader.id, worker.id


def test_new_auto_run_is_continuous_and_creates_first_request_atomically(
    tmp_path: Path,
) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)

    run = teams.create_team_run(
        "goal",
        leader_id,
        [worker_id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=3,
        auto_interval_seconds=600,
    )

    assert run.lifecycle_mode == "continuous"
    assert run.execution_policy == "auto"
    series = cycle_service.get_active_series(run.id)
    assert (series.target_slots, series.interval_seconds) == (3, 600)
    assert [
        request.slot_ordinal for request in cycle_service.list_requests(run.id)
    ] == [1]


def test_auto_initialization_failure_rolls_back_team_run_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    workspace_root = tmp_path / "policy-workspace"
    workspace_root.mkdir()
    sentinel = workspace_root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        cycle_service,
        "initialize_auto_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        teams.create_team_run(
            "goal",
            leader_id,
            [worker_id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy="auto",
            auto_repeat_count=2,
            auto_interval_seconds=60,
        )

    assert db.fetchone("select id from team_runs") is None
    assert db.fetchone("select id from team_agents") is None
    assert db.fetchone("select id from team_run_auto_series") is None
    assert db.fetchone("select id from team_cycle_requests") is None
    assert list(workspace_root.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    ("execution_policy", "auto_repeat_count", "auto_interval_seconds", "message"),
    [
        (None, None, None, "requires an execution policy"),
        ("auto", 0, 60, "repeat count must be positive"),
        ("auto", 2, 59, "interval must be at least 60 seconds"),
        ("triggered", 2, None, "does not accept AUTO settings"),
    ],
)
def test_continuous_run_validates_execution_policy_settings(
    tmp_path: Path,
    execution_policy,
    auto_repeat_count,
    auto_interval_seconds,
    message: str,
) -> None:
    _, teams, _, leader_id, worker_id = make_policy_services(tmp_path)

    with pytest.raises(ValueError, match=message):
        teams.create_team_run(
            "goal",
            leader_id,
            [worker_id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy=execution_policy,
            auto_repeat_count=auto_repeat_count,
            auto_interval_seconds=auto_interval_seconds,
        )


def test_create_team_run_from_team_forwards_auto_policy(tmp_path: Path) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    personas = PersonaService(db)
    directory = TeamService(db, personas)
    rules = RuleSetService(db)
    rules.seed_defaults()
    team = directory.create_team(
        "Policy Team",
        "continuous work",
        leader_id,
        [worker_id],
    )

    run = teams.create_team_run_from_team(
        directory,
        rules,
        team.id,
        "goal",
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=2,
        auto_interval_seconds=300,
    )

    assert run.execution_policy == "auto"
    assert cycle_service.get_active_series(run.id).target_slots == 2
    assert [request.slot_ordinal for request in cycle_service.list_requests(run.id)] == [
        1
    ]


def test_create_team_run_snapshots_personas(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], ["Stay scoped"])
    member = personas.create_persona("QA Tester", "Quality", "Checks risk.", ["Test"], ["Report evidence"])

    run = teams.create_team_run(
        goal="Design Agent Teams",
        leader_persona_id=leader.id,
        member_persona_ids=[member.id],
        run_mode="planning_only",
        max_workers=2,
    )

    agents = teams.list_agents(run.id)
    assert run.status == "draft"
    assert Path(run.workspace_root).is_dir()
    assert len(agents) == 2
    assert agents[0].persona_snapshot["name"] == "Tech Lead"
    assert agents[1].persona_snapshot["name"] == "QA Tester"

    personas.update_persona(member.id, name="Changed QA")

    unchanged = teams.list_agents(run.id)[1]
    assert unchanged.persona_snapshot["name"] == "QA Tester"


def test_list_team_runs_enriched(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [], avatar="a01")
    member = personas.create_persona("Frontend Dev", "fe", "d", [], [], avatar="a05")
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 2)
    t1 = teams.create_task(run.id, "t1", "d")
    teams.create_task(run.id, "t2", "d")
    teams.set_task_status(t1.id, "completed", result="ok")

    enriched = teams.list_team_runs_enriched()
    row = next(r for r in enriched if r["id"] == run.id)
    assert row["leader_name"] == "Lead"
    assert row["leader"] == {"name": "Lead", "avatar": "a01", "initials": "L"}
    assert {m["name"] for m in row["members"]} == {"Frontend Dev"}
    assert row["members"][0]["avatar"] == "a05"
    assert row["members"][0]["initials"] == "FD"
    assert row["task_total"] == 2
    assert row["task_done"] == 1
    assert row["task_counts"]["completed"] == 1
    assert isinstance(row["elapsed_seconds"], (int, float))


def test_task_assignment_updates_task_and_agent_lifecycle_together(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    member = personas.create_persona("Worker", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = teams.list_agents(run.id)[1]
    task = teams.create_task(run.id, "Build API", "d")

    started_task, started_agent = teams.start_task(task.id, worker.id)

    assert started_task.status == "in_progress"
    assert started_task.owner_agent_id == worker.id
    assert started_agent.status == "running"
    assert started_agent.current_task_id == task.id

    finished_task, finished_agent = teams.finish_task(
        task.id, worker.id, "completed", result="done"
    )

    assert finished_task.status == "completed"
    assert finished_task.owner_agent_id == worker.id
    assert finished_task.result == "done"
    assert finished_agent.status == "completed"
    assert finished_agent.current_task_id is None


def test_delete_team_run_removes_isolated_workspace_and_related_records(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    task = teams.create_task(run.id, "temporary task", "test only")
    teams.append_message(run.id, None, None, "note", "temporary", {"task_id": task.id})
    workspace = Path(run.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "result.txt").write_text("temporary", encoding="utf-8")

    teams.delete_team_run(run.id)

    assert not workspace.exists()
    with pytest.raises(KeyError):
        teams.get_team_run(run.id)
    assert teams._db.fetchone("select id from team_tasks where id = ?", (task.id,)) is None
    assert teams._db.fetchone(
        "select id from team_messages where team_run_id = ?", (run.id,)
    ) is None


def test_delete_team_run_allows_missing_workspace(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    shutil.rmtree(run.workspace_root)

    teams.delete_team_run(run.id)

    with pytest.raises(KeyError):
        teams.get_team_run(run.id)


def test_delete_team_run_rejects_workspace_outside_configured_root(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    outside = tmp_path / "outside" / run.id
    outside.mkdir(parents=True)
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    teams._db.execute(
        "update team_runs set workspace_root = ? where id = ?",
        (str(outside), run.id),
    )

    with pytest.raises(ValueError, match="outside the configured workspace root"):
        teams.delete_team_run(run.id)

    assert sentinel.exists()
    assert teams.get_team_run(run.id).id == run.id


def test_delete_team_run_keeps_record_when_workspace_removal_fails(tmp_path, monkeypatch):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    workspace = Path(run.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_removal(_path):
        raise OSError("workspace is locked")

    monkeypatch.setattr("personal_agent_gateway.teams.shutil.rmtree", fail_removal)

    with pytest.raises(OSError, match="workspace is locked"):
        teams.delete_team_run(run.id)

    assert workspace.exists()
    assert teams.get_team_run(run.id).id == run.id


def test_append_team_message(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    message = teams.append_message(run.id, agent.id, None, "note", "Planning started", {"phase": "planning"})

    assert message.content == "Planning started"
    assert teams.list_messages(run.id)[0].metadata == {"phase": "planning"}


def test_new_run_has_default_budget(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    assert run.rounds_budget == 8
    assert run.rounds_used == 0
    assert run.lifecycle_mode == "standard"
    assert run.execution_policy is None


def test_continuous_team_run_cycles_are_ordered_and_idempotent(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        rounds_budget=6,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )

    first = teams.create_cycle(run.id, "hook", "hook-run-1")
    duplicate = teams.create_cycle(run.id, "hook", "hook-run-1")
    second = teams.create_cycle(run.id, "hook", "hook-run-2", rounds_budget=3)

    assert duplicate.id == first.id
    assert [(cycle.sequence, cycle.source_id) for cycle in teams.list_cycles(run.id)] == [
        (1, "hook-run-1"),
        (2, "hook-run-2"),
    ]
    assert first.status == "queued"
    assert first.rounds_budget == 6
    assert second.rounds_budget == 3
    assert teams.increment_cycle_rounds_used(first.id).rounds_used == 1


def test_continuous_cycle_is_idempotent_by_request(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    db.execute(
        """
        insert into team_cycle_requests (
            id, team_run_id, source_type, source_id, status, instruction,
            created_at, updated_at
        ) values ('request-1', ?, 'manual', 'client-1', 'dispatching', 'go', 't', 't')
        """,
        (run.id,),
    )

    first = teams.create_cycle(
        run.id, "manual", "client-1", request_id="request-1"
    )
    duplicate = teams.create_cycle(
        run.id, "", "", rounds_budget=0, request_id="request-1"
    )

    assert duplicate.id == first.id
    assert first.request_id == "request-1"
    assert teams.get_cycle_for_request("request-1").id == first.id


def test_cycle_request_must_be_dispatching_and_match_source(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    db.execute(
        """
        insert into team_cycle_requests (
            id, team_run_id, source_type, source_id, status, instruction,
            created_at, updated_at
        ) values ('request-1', ?, 'manual', 'client-1', 'queued', 'go', 't', 't')
        """,
        (run.id,),
    )

    with pytest.raises(ValueError, match="dispatching"):
        teams.create_cycle(
            run.id, "manual", "client-1", request_id="request-1"
        )

    db.execute(
        "update team_cycle_requests set status = 'dispatching' where id = 'request-1'"
    )
    with pytest.raises(ValueError, match="source"):
        teams.create_cycle(
            run.id, "hook", "hook-1", request_id="request-1"
        )

    cycle = teams.create_cycle(
        run.id, "manual", "client-1", request_id="request-1"
    )
    assert cycle.request_id == "request-1"


def test_standard_team_run_rejects_explicit_cycles(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)

    with pytest.raises(ValueError, match="continuous"):
        teams.create_cycle(run.id, "hook", "hook-run-1")


def test_task_and_message_keep_cycle_lineage(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "hook", "hook-run-1")

    task = teams.create_task(run.id, "Classify mail", "d", cycle_id=cycle.id)
    message = teams.append_message(
        run.id,
        None,
        None,
        "mail_received",
        "Mail queued.",
        {},
        cycle_id=cycle.id,
    )

    assert task.cycle_id == cycle.id
    assert message.cycle_id == cycle.id


def test_cycle_lineage_rejects_another_team_run_and_cascades(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    first_run = teams.create_team_run(
        "first",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    second_run = teams.create_team_run(
        "second",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(first_run.id, "hook", "hook-run-1")

    with pytest.raises(ValueError, match="different team run"):
        teams.create_task(second_run.id, "wrong", "d", cycle_id=cycle.id)

    teams.delete_team_run(first_run.id)

    with pytest.raises(KeyError):
        teams.get_cycle(cycle.id)


def test_agent_session_and_counters(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 2)
    agent = teams.list_agents(run.id)[0]
    assert agent.reinvocations == 0
    assert agent.upstream_session_id is None

    updated = teams.set_agent_session(agent.id, "thread-123")
    assert updated.upstream_session_id == "thread-123"
    assert teams.increment_agent_reinvocations(agent.id).reinvocations == 1

    assert teams.increment_rounds_used(run.id).rounds_used == 1


def test_completed_with_failures_is_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)
    updated = teams.set_run_status(run.id, "completed_with_failures", summary="1/2")
    assert updated.status == "completed_with_failures"
    assert updated.finished_at is not None


def test_persona_snapshot_includes_avatar(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="person01")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)

    agent = teams.list_agents(run.id)[0]
    assert agent.persona_snapshot["avatar"] == "person01"


def test_persona_snapshot_includes_default_options(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona(
        "L",
        "lead",
        "d",
        [],
        [],
        default_options={"effort": "max", "sandbox": "read-only"},
    )
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)

    assert teams.list_agents(run.id)[0].persona_snapshot["default_options"] == {
        "effort": "max",
        "sandbox": "read-only",
    }


def test_backfill_agent_avatars_populates_missing(tmp_path):
    import json
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="tech03")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    # Simulate a legacy snapshot with no avatar key.
    snapshot = dict(agent.persona_snapshot)
    snapshot.pop("avatar", None)
    db.execute(
        "update team_agents set persona_snapshot_json = ? where id = ?",
        (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), agent.id),
    )
    assert "avatar" not in teams.list_agents(run.id)[0].persona_snapshot

    updated = teams.backfill_agent_avatars()

    assert updated == 1
    assert teams.list_agents(run.id)[0].persona_snapshot["avatar"] == "tech03"
    # Idempotent: a second pass changes nothing.
    assert teams.backfill_agent_avatars() == 0


def test_interrupt_active_run_requeues_only_in_progress_work(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    leader_agent, worker_agent = teams.list_agents(run.id)
    completed = teams.create_task(run.id, "done", "already done", worker_agent.id)
    interrupted = teams.create_task(run.id, "current", "was running", worker_agent.id)
    teams.set_task_status(completed.id, "completed", result="kept result")
    teams.set_task_status(interrupted.id, "in_progress")
    teams.set_agent_session(worker_agent.id, "thread-123")
    teams.set_agent_status(leader_agent.id, "running")
    teams.set_agent_status(worker_agent.id, "running")
    teams._db.execute(  # Simulate the assignment persisted by a running orchestrator.
        "update team_agents set current_task_id = ? where id = ?",
        (interrupted.id, worker_agent.id),
    )
    teams.set_run_status(run.id, "running")

    recovered = teams.interrupt_active_runs()

    assert [item.id for item in recovered] == [run.id]
    updated_run = teams.get_team_run(run.id)
    assert updated_run.status == "interrupted"
    assert updated_run.finished_at is None
    task_by_title = {task.title: task for task in teams.list_tasks(run.id)}
    assert task_by_title["done"].status == "completed"
    assert task_by_title["done"].result == "kept result"
    assert task_by_title["current"].status == "pending"
    assert task_by_title["current"].started_at is None
    updated_worker = teams.get_agent(worker_agent.id)
    assert updated_worker.status == "pending"
    assert updated_worker.current_task_id is None
    assert updated_worker.upstream_session_id == "thread-123"
    assert [message.kind for message in teams.list_messages(run.id)].count("system_interrupted") == 1

    assert teams.interrupt_active_runs() == []
    assert [message.kind for message in teams.list_messages(run.id)].count("system_interrupted") == 1


def test_retry_failed_task_creates_linked_task_and_preserves_original(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    completed = teams.create_task(run.id, "done", "kept")
    failed = teams.create_task(run.id, "failed", "retry me")
    teams.set_task_status(completed.id, "completed", result="kept result")
    teams.set_task_status(failed.id, "failed", error_message="timed out")
    teams.set_run_status(run.id, "completed_with_failures", summary="old summary")

    updated_run, retry_task, retry_cycle = teams.retry_failed_task(run.id, failed.id)

    assert updated_run.status == "interrupted"
    assert updated_run.summary is None
    assert updated_run.error_message is None
    assert updated_run.finished_at is None
    assert retry_cycle is None
    assert retry_task.id != failed.id
    assert retry_task.retry_of_task_id == failed.id
    assert retry_task.status == "pending"
    assert retry_task.result is None
    assert retry_task.error_message is None
    assert retry_task.started_at is None
    assert retry_task.finished_at is None
    original = next(task for task in teams.list_tasks(run.id) if task.id == failed.id)
    assert original.status == "failed"
    assert original.error_message == "timed out"
    assert teams.list_tasks(run.id)[0].result == "kept result"
    message = teams.list_messages(run.id)[-1]
    assert message.kind == "system_task_retried"
    assert message.metadata == {
        "original_cycle_id": None,
        "original_task_id": failed.id,
        "retry_cycle_id": None,
        "retry_task_id": retry_task.id,
        "previous_error": "timed out",
    }


def test_retry_failed_task_rejects_nonfailed_task_and_nonterminal_run(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)
    task = teams.create_task(run.id, "pending", "not failed")

    with pytest.raises(ValueError, match="failed terminal"):
        teams.retry_failed_task(run.id, task.id)

    teams.set_run_status(run.id, "failed", error_message="all failed")
    with pytest.raises(ValueError, match="Only failed tasks"):
        teams.retry_failed_task(run.id, task.id)


def test_decision_request_batches_blocked_tasks_and_projects_workspace_file(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    leader_agent, worker = teams.list_agents(run.id)
    first = teams.create_task(run.id, "Deploy", "choose target")
    second = teams.create_task(run.id, "Notify", "choose audience")
    teams.set_run_status(run.id, "running")
    teams.set_agent_status(leader_agent.id, "running")

    for task in (first, second):
        teams.start_task(task.id, worker.id)
        request = teams.defer_task_for_user_decision(
            task.id,
            worker.id,
            {
                "topic": task.title,
                "question": f"Choose for {task.title}?",
                "why_needed": "The choice changes the result.",
                "options": [
                    {"id": "safe", "label": "Safe", "impact": "Lower risk."},
                    {"id": "fast", "label": "Fast", "impact": "Faster delivery."},
                ],
                "recommended_option_id": "safe",
                "blocking_scope": "task",
            },
        )

    assert request.status == "collecting"
    assert request.revision == 2
    assert [item["id"] for item in request.items] == ["Q-001", "Q-002"]
    assert {task.status for task in teams.list_tasks(run.id)} == {"blocked"}

    published = teams.publish_decision_request(run.id)

    assert published.status == "awaiting_user"
    assert published.revision == 3
    assert teams.get_team_run(run.id).status == "waiting_for_user"
    assert teams.get_agent(leader_agent.id).status == "waiting"
    decision_file = Path(run.workspace_root) / "USER_DECISIONS.md"
    content = decision_file.read_text(encoding="utf-8")
    assert "status: awaiting_user" in content
    assert "Q-001" in content and "Q-002" in content
    assert "Choose for Deploy?" in content


def test_answer_decision_request_requeues_only_listed_tasks_and_rejects_stale_submit(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = teams.list_agents(run.id)[1]
    blocked = teams.create_task(run.id, "Deploy", "choose target")
    untouched = teams.create_task(run.id, "Later", "already pending")
    teams.set_run_status(run.id, "running")
    teams.start_task(blocked.id, worker.id)
    teams.defer_task_for_user_decision(
        blocked.id,
        worker.id,
        {
            "topic": "target",
            "question": "Where?",
            "why_needed": "Changes configuration.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
    )
    request = teams.publish_decision_request(run.id)

    updated_run, resolved = teams.answer_decision_request(
        run.id, request.id, request.revision, {"Q-001": "staging"}
    )

    assert updated_run.status == "running"
    assert resolved.status == "resolved"
    assert resolved.answers == {"Q-001": "staging"}
    task_by_id = {task.id: task for task in teams.list_tasks(run.id)}
    assert task_by_id[blocked.id].status == "pending"
    assert task_by_id[untouched.id].status == "pending"
    assert teams.decision_context_for_task(run.id, blocked.id) == "Q: Where?\nA: staging"
    assert "staging" in (Path(run.workspace_root) / "USER_DECISIONS.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no longer awaiting"):
        teams.answer_decision_request(
            run.id, request.id, request.revision, {"Q-001": "production"}
        )


def test_run_level_decision_request_records_stage_and_resolved_context(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    leader_agent = teams.list_agents(run.id)[0]
    teams.set_run_status(run.id, "planning")
    teams.set_agent_status(leader_agent.id, "running")

    collecting = teams.defer_run_for_user_decision(
        run.id,
        {
            "topic": "scope",
            "question": "Which scope?",
            "why_needed": "Changes the plan.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "run",
        },
        stage="planning",
    )

    assert collecting.status == "collecting"
    assert collecting.items[0]["stage"] == "planning"
    assert collecting.items[0]["blocking_task_ids"] == []

    request = teams.publish_decision_request(run.id)
    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "backend only"},
    )

    assert teams.decision_context_for_run(run.id, stage="planning") == (
        "Q: Which scope?\nA: backend only"
    )
    assert teams.decision_context_for_run(run.id, stage="synthesis") == ""


def test_create_team_run_from_team_snapshots_roster_and_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    from personal_agent_gateway.db import Database  # already imported at top; keep local clarity
    # reuse same db as make_services by rebuilding services on that db:
    db = Database(tmp_path / "app.db")
    directory = TeamService(db, personas)
    rules = RuleSetService(db)
    rules.seed_defaults()
    lead = personas.create_persona("Lead", "lead", "d", ["plan"], ["scoped"])
    member = personas.create_persona("QA", "qa", "d", ["test"], ["evidence"])
    rules.upsert("team", None, "", [])  # noop guard
    team = directory.create_team("Release Crew", "ships", lead.id, [member.id])
    rules.upsert("team", team.id, "team voice", [{"level": "REQUIRED", "text": "green regression"}])

    run = teams.create_team_run_from_team(
        directory, rules, team_id=team.id, goal="ship pdf",
        run_mode="plan_and_execute", max_workers=2,
    )

    assert run.team_id == team.id
    assert run.rules_snapshot["team"]["personality"] == "team voice"
    assert run.rules_snapshot["team"]["name"] == team.name
    assert run.rules_snapshot["global"]["rules"]
    agents = teams.list_agents(run.id)
    assert [a.role for a in agents] == ["leader", "member"]
    assert agents[0].persona_snapshot["name"] == "Lead"


def test_legacy_create_team_run_has_no_team_or_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run("legacy", lead.id, [], "planning_only", 1)
    assert run.team_id is None
    assert run.rules_snapshot is None
