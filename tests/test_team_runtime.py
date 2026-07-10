from dataclasses import dataclass

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


@dataclass
class FakeModel:
    content: str

    async def complete(self, messages):
        return ModelResponse(content=self.content, tool_calls=[])


@dataclass
class ScriptedModel:
    """호출마다 responses에서 순서대로 반환. 소진되면 마지막 값 반복."""
    responses: list

    def __post_init__(self):
        self._calls = 0

    async def complete(self, messages):
        idx = min(self._calls, len(self.responses) - 1)
        self._calls += 1
        value = self.responses[idx]
        if isinstance(value, Exception):
            raise value
        return ModelResponse(content=value, tool_calls=[], upstream_session_id=f"sess-{self._calls}")


def _factory_by_role(leader_responses, worker_responses):
    from personal_agent_gateway.teams import TeamAgent
    models = {}
    def factory(agent: TeamAgent):
        if agent.id not in models:
            responses = leader_responses if agent.role == "leader" else worker_responses
            models[agent.id] = ScriptedModel(list(responses))
        return models[agent.id]
    return factory


@pytest.mark.asyncio
async def test_planning_only_creates_tasks_and_completes_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel(
            '[{"title":"Define schema","description":"Add team tables"},'
            '{"title":"Design UI","description":"Add team screens"}]'
        ),
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id)] == ["Define schema", "Design UI"]
    assert "Planning completed" in teams.list_messages(run.id)[-1].content
    leader_agent = teams.list_agents(run.id)[0]
    assert leader_agent.status == "completed"


@pytest.mark.asyncio
async def test_planning_failure_fails_run_and_settles_leader(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel("not json at all"),
    )

    failed = await runtime.start(run.id)

    assert failed.status == "failed"
    assert failed.error_message
    assert teams.list_agents(run.id)[0].status == "failed"


@pytest.mark.asyncio
async def test_plan_and_execute_assigns_tasks_to_workers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    worker = personas.create_persona("QA Tester", "Quality", "Checks work.", ["Test"], [])
    run = teams.create_team_run("Build teams", leader.id, [worker.id], "plan_and_execute", 1)

    responses = iter([
        '[{"title":"Verify API","description":"Check team run endpoints"}]',
        "Verified API behavior. No files changed. Evidence: tests passed.",
    ])
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(next(responses)))

    completed = await runtime.start(run.id)

    tasks = teams.list_tasks(run.id)
    messages = teams.list_messages(run.id)
    assert completed.status == "completed"
    assert tasks[0].status == "completed"
    assert "Verified API behavior" in tasks[0].result
    assert any(message.kind == "agent_output" for message in messages)


@pytest.mark.asyncio
async def test_plan_and_execute_with_no_workers_fails_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "plan_and_execute", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel(
            '[{"title":"Verify API","description":"Check team run endpoints"}]'
        ),
    )

    result = await runtime.start(run.id)

    assert result.status == "failed"
    assert result.error_message and "worker" in result.error_message
    assert result.status != "completed"


@pytest.mark.asyncio
async def test_team_runtime_publishes_team_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    bus = EventBus()
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel('[{"title":"Define schema","description":"Add tables"}]'),
        event_bus=bus,
    )

    await runtime.start(run.id)

    event_types = [event["type"] for event in bus.recent()]
    assert "team.run.started" in event_types
    assert "team.task.created" in event_types
    assert "team.run.completed" in event_types


@pytest.mark.asyncio
async def test_partial_failure_yields_completed_with_failures(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"},{"title":"T2","description":"d2"}]'
    # 워커: T1 성공, T2 예외
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary"], ["ok result", RuntimeError("boom")]),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed_with_failures"
    tasks = teams.list_tasks(run.id)
    assert {t.title: t.status for t in tasks} == {"T1": "completed", "T2": "failed"}


@pytest.mark.asyncio
async def test_all_workers_fail_yields_failed(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary"], [RuntimeError("boom")]),
    )
    result = await runtime.start(run.id)
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_worker_query_consumes_round_and_reinvokes(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, "use schema X"],
            [
                'Working...\n```json\n{"needs_info":{"topic":"schema","question":"what schema?"}}\n```',
                "final result using schema X",
            ],
        ),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 1
    agent = [a for a in teams.list_agents(run.id) if a.role == "member"][0]
    assert agent.reinvocations == 1
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "query" in kinds and "answer" in kinds


@pytest.mark.asyncio
async def test_budget_exhausted_rejects_and_best_effort(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    # 예산 0으로 생성 → 즉시 거절 경로
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1, rounds_budget=0)
    plan = '[{"title":"T1","description":"d1"}]'
    needs = 'x\n```json\n{"needs_info":{"topic":"t","question":"q"}}\n```'

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan], [needs, "best effort final"]),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 0
    task = teams.list_tasks(run.id)[0]
    assert task.result == "best effort final"
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "answer" not in kinds  # 중재 없음
