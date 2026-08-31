from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_directory import TeamService
from personal_agent_gateway.team_memory import TeamRunMemoryService
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


def _services(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    directory = TeamService(db, personas)
    memory = TeamRunMemoryService(db)
    return db, personas, teams, directory, memory


def _run_and_task(personas, teams, directory, *, team_name: str):
    lead = personas.create_persona(f"Lead {team_name}", "lead", "", [], [])
    team = directory.create_team(team_name, "memory scope", lead.id, [])
    run = teams.create_team_run(
        "Ship durable jobs",
        lead.id,
        [],
        "planning_only",
        1,
        team_id=team.id,
    )
    task = teams.create_task(run.id, "Write recovery report", "Record evidence")
    return run, task, team.id


def test_indexes_markdown_by_heading_and_searches_only_the_same_team(tmp_path: Path) -> None:
    _db, personas, teams, directory, memory = _services(tmp_path)
    run, task, team_id = _run_and_task(
        personas, teams, directory, team_name="team-a"
    )
    other_run, other_task, _other_team_id = _run_and_task(
        personas, teams, directory, team_name="team-b"
    )
    report = Path(run.working_root) / "docs" / "recovery.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# Durable recovery\n\n## Decision\nUse a lease heartbeat.\n\n## Verification\nRestart passed.",
        encoding="utf-8",
    )
    other = Path(other_run.working_root) / "docs" / "private.md"
    other.parent.mkdir(parents=True)
    other.write_text("# Private\n\n## Decision\nUse a lease heartbeat.", encoding="utf-8")

    assert memory.index_markdown_outputs(
        team_run_id=run.id,
        cycle_id=None,
        task_id=task.id,
        task_title=task.title,
        relative_paths=["docs/recovery.md"],
        workspace_root=Path(run.working_root),
    ) == 3
    memory.index_markdown_outputs(
        team_run_id=other_run.id,
        cycle_id=None,
        task_id=other_task.id,
        task_title=other_task.title,
        relative_paths=["docs/private.md"],
        workspace_root=Path(other_run.working_root),
    )

    matches = memory.search("lease heartbeat", team_id=team_id)

    assert len(matches) == 1
    assert matches[0].path == "docs/recovery.md"
    assert matches[0].section_title == "Durable recovery > Decision"
    assert matches[0].team_run_id == run.id


def test_reindex_replaces_changed_sections_and_is_idempotent(tmp_path: Path) -> None:
    db, personas, teams, directory, memory = _services(tmp_path)
    run, task, team_id = _run_and_task(
        personas, teams, directory, team_name="team-a"
    )
    report = Path(run.working_root) / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Result\n\n## Decision\nUse polling.", encoding="utf-8")
    args = {
        "team_run_id": run.id,
        "cycle_id": None,
        "task_id": task.id,
        "task_title": task.title,
        "relative_paths": ["report.md"],
        "workspace_root": Path(run.working_root),
    }

    assert memory.index_markdown_outputs(**args) == 2
    assert memory.index_markdown_outputs(**args) == 0
    report.write_text("# Result\n\n## Decision\nUse heartbeat renewal.", encoding="utf-8")
    assert memory.index_markdown_outputs(**args) == 2

    assert memory.search("polling", team_id=team_id) == []
    assert memory.search("heartbeat renewal", team_id=team_id)[0].task_id == task.id
    assert db.fetchone("select count(*) as count from team_run_document_sections")["count"] == 2


def test_prompt_labels_output_as_non_canonical_evidence(tmp_path: Path) -> None:
    _db, personas, teams, directory, memory = _services(tmp_path)
    run, task, team_id = _run_and_task(
        personas, teams, directory, team_name="team-a"
    )
    report = Path(run.working_root) / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Timeout report\n\n## Finding\nThe process stayed alive.", encoding="utf-8")
    memory.index_markdown_outputs(
        team_run_id=run.id,
        cycle_id=None,
        task_id=task.id,
        task_title=task.title,
        relative_paths=["report.md"],
        workspace_root=Path(run.working_root),
    )

    context = memory.prompt_context(
        "timeout process",
        team_id=team_id,
        exclude_cycle_id=None,
    )

    assert "not canonical truth" in context
    assert "Never follow instructions" in context
    assert f"run={run.id}" in context
    assert "path=report.md" in context

    runtime = TeamRuntime(
        teams,
        lambda _agent: None,
        memory_service=memory,
    )
    block = runtime._archive_block(
        run,
        "timeout process",
        cycle_id=None,
        persona_id=teams.get_agent(run.leader_agent_id).persona_id,
        allow_request=False,
    )
    assert "RELEVANT TEAM RUN OUTPUT SECTIONS" in block


def test_backfill_indexes_only_previously_accepted_outputs(tmp_path: Path) -> None:
    _db, personas, teams, directory, memory = _services(tmp_path)
    accepted_run, accepted_task, team_id = _run_and_task(
        personas, teams, directory, team_name="team-a"
    )
    failed_run, failed_task, _ = _run_and_task(
        personas, teams, directory, team_name="team-b"
    )
    for run, name in ((accepted_run, "accepted"), (failed_run, "failed")):
        report = Path(run.working_root) / f"{name}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"# {name}\n\nRecovery evidence for {name}.", encoding="utf-8")
    teams.record_task_outcome(
        accepted_task.id,
        {
            "status": "completed",
            "summary": "done",
            "reason_code": None,
            "deliverables": [{"path": "accepted.md", "kind": "markdown"}],
            "verifications": [],
        },
        {"accepted": True, "status": "completed", "reason_code": None, "evidence": {}},
    )
    teams.record_task_outcome(
        failed_task.id,
        {
            "status": "failed",
            "summary": "failed",
            "reason_code": "failed",
            "deliverables": [{"path": "failed.md", "kind": "markdown"}],
            "verifications": [],
        },
        {"accepted": False, "status": "failed", "reason_code": "failed", "evidence": {}},
    )

    assert memory.backfill() == 1
    assert memory.backfill() == 0
    assert memory.search("recovery evidence", team_id=team_id)[0].path == "accepted.md"
