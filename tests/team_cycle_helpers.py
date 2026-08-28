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


def make_queued_cycle(teams, cycles, run):
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "provider-recovery",
        "work",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None and claimed.id == request.id
    cycle = teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )
    return cycle


def seed_next_cycle_proposal(db, run, cycle, agent, instruction):
    """이 사이클에 다음 사이클 제안이 이미 적용되어 있던 것처럼 원장에 남긴다.

    이 파일을 쓰는 테스트 다수는 사이클 상태를 직접 지정해 시뮬레이션하고
    태스크는 만들지 않는다 -- previous_cycle_id/previous_summary_text 전파나
    다음 슬롯이 나가는지만 확인하면 되기 때문이다. apply_synthesis 전체를
    거치면 필수 태스크가 있어야 하고 요약/태스크 목록도 새로 계산되어 그
    검증이 어긋난다. 그래서 team_model_operations 원장에만 이미 적용된 합성
    결과를 최소한으로 남기고, 사이클/런/태스크 상태는 건드리지 않는다.
    """
    import hashlib

    from personal_agent_gateway.team_model_effects import (
        team_model_effect_result_validators,
    )
    from personal_agent_gateway.team_model_operations import (
        OperationSpec,
        TeamModelOperationService,
        ValidatedOperationResult,
    )

    operations = TeamModelOperationService(
        db,
        result_validators=team_model_effect_result_validators(),
    )
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:cycle_synthesis:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=None,
            agent_id=agent.id,
            provider=agent.backend,
            stage="cycle_synthesis",
            stage_ordinal=0,
            request_digest=hashlib.sha256(cycle.id.encode()).hexdigest(),
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    completed = operations.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult(
            "synthesis", {"summary": "ok", "next_cycle": instruction}
        ),
    )
    db.execute(
        "update team_model_operations set status = 'applied' where id = ?",
        (completed.id,),
    )


def make_running_task_in_cycle(teams, cycles, run):
    cycle = make_queued_cycle(teams, cycles, run)
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    agent = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )
    task = teams.create_task(
        run.id,
        "current",
        "provider work",
        owner_agent_id=agent.id,
        cycle_id=cycle.id,
    )
    task, agent = teams.start_task(task.id, agent.id)
    return cycle, task, agent


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
        self.contests: list[tuple[str, str, str]] = []

    async def run_cycle(
        self,
        team_run_id: str,
        cycle_id: str,
        instruction: str,
    ) -> TeamRun:
        self.calls.append((team_run_id, cycle_id, instruction))
        return self.teams.get_team_run(team_run_id)

    async def adjudicate_contest(
        self,
        team_run_id: str,
        cycle_id: str,
        objection: str,
    ) -> TeamRun:
        self.contests.append((team_run_id, cycle_id, objection))
        return self.teams.get_team_run(team_run_id)
