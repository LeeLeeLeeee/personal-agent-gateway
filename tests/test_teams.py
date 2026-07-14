from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.teams import TeamRunService


def make_services(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    return personas, teams


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
    Path(run.workspace_root).rmdir()

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


def test_retry_failed_task_requeues_only_selected_task_and_interrupts_run(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    completed = teams.create_task(run.id, "done", "kept")
    failed = teams.create_task(run.id, "failed", "retry me")
    teams.set_task_status(completed.id, "completed", result="kept result")
    teams.set_task_status(failed.id, "failed", error_message="timed out")
    teams.set_run_status(run.id, "completed_with_failures", summary="old summary")

    updated_run, updated_task = teams.retry_failed_task(run.id, failed.id)

    assert updated_run.status == "interrupted"
    assert updated_run.summary is None
    assert updated_run.error_message is None
    assert updated_run.finished_at is None
    assert updated_task.status == "pending"
    assert updated_task.result is None
    assert updated_task.error_message is None
    assert updated_task.started_at is None
    assert updated_task.finished_at is None
    assert teams.list_tasks(run.id)[0].result == "kept result"
    message = teams.list_messages(run.id)[-1]
    assert message.kind == "system_task_retried"
    assert message.metadata == {"task_id": failed.id, "previous_error": "timed out"}


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


from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.team_directory import TeamService


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
