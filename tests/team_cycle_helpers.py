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
    *,
    auto_repeat_count: int = 2,
    auto_interval_seconds: int = 300,
) -> tuple[Database, TeamRunService, TeamCycleService, TeamRun]:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "workspace",
        cycle_service=cycles,
    )
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy=execution_policy,
        auto_repeat_count=(
            auto_repeat_count if execution_policy == "auto" else None
        ),
        auto_interval_seconds=(
            auto_interval_seconds if execution_policy == "auto" else None
        ),
    )
    return db, teams, cycles, run


def make_triggered_run(tmp_path: Path):
    return make_cycle_services(tmp_path, "triggered")


def make_auto_run(
    tmp_path: Path,
    target_slots: int = 2,
    interval_seconds: int = 300,
):
    return make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=target_slots,
        auto_interval_seconds=interval_seconds,
    )


class RecordingOrchestrator:
    def __init__(self, teams: TeamRunService) -> None:
        self.teams = teams
        self.calls: list[tuple[str, str, str]] = []

    async def run_cycle(
        self,
        team_run_id: str,
        cycle_id: str,
        instruction: str,
    ) -> TeamRun:
        self.calls.append((team_run_id, cycle_id, instruction))
        return self.teams.get_team_run(team_run_id)
