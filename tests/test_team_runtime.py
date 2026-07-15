from dataclasses import dataclass

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_runtime import TeamRuntime, _rules_block
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
        "Summary: API endpoints verified successfully.",
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
    db = Database(tmp_path / "app.db")
    db.initialize()
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
    db = Database(tmp_path / "app.db")
    db.initialize()
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
    db = Database(tmp_path / "app.db")
    db.initialize()
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
    db = Database(tmp_path / "app.db")
    db.initialize()
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


@pytest.mark.asyncio
async def test_synthesis_summary_from_leader(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "SYNTHESIZED SUMMARY"], ["result"]),
    )
    result = await runtime.start(run.id)
    assert result.summary == "SYNTHESIZED SUMMARY"
    assert [m.kind for m in teams.list_messages(run.id)].count("synthesis") == 1


@pytest.mark.asyncio
async def test_reinvocation_cap_rejects_after_three(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    # 예산은 넉넉하게 잡아서(캡이 아니라 예산이) 걸림돌이 되지 않도록 한다.
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1, rounds_budget=10)
    plan = '[{"title":"T1","description":"d1"}]'
    needs_q1 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q1?"}}\n```'
    needs_q2 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q2?"}}\n```'
    needs_q3 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q3?"}}\n```'
    needs_q4 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q4?"}}\n```'

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, "answer1", "answer2", "answer3"],
            [needs_q1, needs_q2, needs_q3, needs_q4, "final result after cap"],
        ),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    # 3번의 중재만 예산을 소비한다 (4번째 needs_info는 캡에 막혀 거절된다).
    assert result.rounds_used == 3
    agent = [a for a in teams.list_agents(run.id) if a.role == "member"][0]
    assert agent.reinvocations == 3
    task = teams.list_tasks(run.id)[0]
    assert task.result == "final result after cap"
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert kinds.count("query") == 3
    assert kinds.count("answer") == 3


@pytest.mark.asyncio
async def test_cancel_settles_run_and_task(tmp_path):
    import asyncio
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    started = asyncio.Event()

    class HangingModel:
        def __init__(self, role): self.role = role
        async def complete(self, messages):
            from personal_agent_gateway.model_client import ModelResponse
            if self.role == "leader":
                return ModelResponse(content=plan, tool_calls=[], upstream_session_id="s")
            started.set()
            await asyncio.sleep(60)  # 워커 실행 중 매달림

    runtime = TeamRuntime(teams=teams, model_factory=lambda a: HangingModel(a.role))
    task = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert teams.get_team_run(run.id).status == "canceled"
    assert teams.list_tasks(run.id)[0].status == "canceled"


@pytest.mark.asyncio
async def test_execute_drains_task_added_during_execution(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    state = {"injected": False}
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                models[agent.id] = ScriptedModel([plan, "summary"])
            return models[agent.id]

        class WorkerModel:
            async def complete(self, messages):
                if not state["injected"]:
                    state["injected"] = True
                    teams.create_task(run.id, "T2", "d2")
                return ModelResponse(content="did it", tool_calls=[])

        return WorkerModel()

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_task_added_during_synthesis_is_executed_before_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                class LeaderModel:
                    def __init__(self): self.calls = 0
                    async def complete(self, messages):
                        self.calls += 1
                        if self.calls == 1:
                            return ModelResponse(content=plan, tool_calls=[])
                        if self.calls == 2:
                            # First synthesis pass: user work lands mid-synthesis.
                            teams.create_task(run.id, "T2", "d2")
                            return ModelResponse(content="interim", tool_calls=[])
                        return ModelResponse(content="final summary", tool_calls=[])
                models[agent.id] = LeaderModel()
            return models[agent.id]
        return FakeModel("worker done")

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_resume_runs_added_tasks_on_terminal_run(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary1", "summary2"], ["r1", "r2"]),
    )
    first = await runtime.start(run.id)
    assert first.status == "completed"

    # Simulate add-work having created a new pending task, then reopen.
    teams.create_task(run.id, "T2", "d2")
    resumed = await runtime.resume(run.id)

    assert resumed.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_resume_restarts_planning_when_interrupted_before_tasks_exist(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    teams.set_run_status(run.id, "planning")
    teams.interrupt_active_runs()
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel('[{"title":"T1","description":"d1"}]'),
    )

    resumed = await runtime.resume(run.id)

    assert resumed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id)] == ["T1"]


@pytest.mark.asyncio
async def test_resume_prefers_worker_that_was_running_before_interruption(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    finished_worker = personas.create_persona("W1", "planning", "d", [], [])
    interrupted_worker = personas.create_persona("W2", "developer", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [finished_worker.id, interrupted_worker.id], "plan_and_execute", 2
    )
    leader_agent, first_worker, second_worker = teams.list_agents(run.id)
    task = teams.create_task(run.id, "current", "d")
    teams.set_agent_status(first_worker.id, "completed")
    teams.set_agent_status(second_worker.id, "running")
    teams.set_task_status(task.id, "in_progress")
    teams.set_run_status(run.id, "running")
    teams.interrupt_active_runs()
    worker_calls = []

    def factory(agent):
        if agent.id == leader_agent.id:
            return FakeModel("summary")
        worker_calls.append(agent.name)
        return FakeModel("done")

    resumed = await TeamRuntime(teams=teams, model_factory=factory).resume(run.id)

    assert resumed.status == "completed"
    assert worker_calls[0] == "W2"


@pytest.mark.asyncio
async def test_add_work_creates_pending_tasks_from_instruction(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    decomposition = '[{"title":"Extra A","description":"da"},{"title":"Extra B","description":"db"}]'
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(decomposition))

    created = await runtime.add_work(run.id, "please also do A and B")

    assert [task.title for task in created] == ["Extra A", "Extra B"]
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "Extra A": "pending",
        "Extra B": "pending",
    }
    assert any(m.kind == "plan_note" for m in teams.list_messages(run.id))


def test_rules_block_empty_when_no_snapshot():
    assert _rules_block(None, include_persona_baseline=True) == ""


def test_rules_block_marks_required_and_guideline():
    snapshot = {
        "global": {"personality": "global voice",
                   "rules": [{"level": "REQUIRED", "text": "no destructive writes"}]},
        "team": {"personality": "team voice",
                 "rules": [{"level": "GUIDELINE", "text": "prefer CRF"}]},
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=True)
    assert "global voice" in block
    assert "team voice" in block
    assert "persona voice" in block
    assert "MUST: no destructive writes" in block
    assert "SHOULD: prefer CRF" in block
    assert "MUST: cite paths" in block


def test_rules_block_excludes_persona_baseline_for_leader():
    snapshot = {
        "global": {"personality": "", "rules": []},
        "team": None,
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=False)
    assert "persona voice" not in block
    assert "cite paths" not in block
