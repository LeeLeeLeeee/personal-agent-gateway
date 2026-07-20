from datetime import datetime
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.teams import TeamRun, TeamRunService


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def make_cycle_services(
    tmp_path: Path,
    execution_policy: str,
) -> tuple[Database, TeamRunService, TeamCycleService, TeamRun]:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
    )
    db.execute(
        "update team_runs set execution_policy = ? where id = ?",
        (execution_policy, run.id),
    )
    return db, teams, cycles, teams.get_team_run(run.id)


def make_triggered_run(tmp_path: Path):
    return make_cycle_services(tmp_path, "triggered")


def make_auto_run(tmp_path: Path):
    return make_cycle_services(tmp_path, "auto")
