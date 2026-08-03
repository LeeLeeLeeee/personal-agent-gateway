import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.remote_model_client import (
    RemoteRunAbortedError,
    RemoteRunFailedError,
)
from personal_agent_gateway.team_acceptance import AcceptanceResult
from personal_agent_gateway.team_artifact_publisher import ArtifactPublicationError
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_cycle_dispatcher import TeamCycleDispatcher
from personal_agent_gateway.team_model_effects import (
    TeamModelEffectService,
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_invoker import (
    AmbiguousModelOperation,
    TeamModelInvoker,
)
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    OperationSpec,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from personal_agent_gateway.team_outcomes import TaskOutcome
from personal_agent_gateway.team_results import workspace_snapshot
from personal_agent_gateway.team_provider_recovery import (
    ProviderOperationWaiting,
    TeamProviderRecovery,
)
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.run_state import TeamRunRegistry
from personal_agent_gateway.team_runtime import (
    WORKER_PROMPT,
    TeamRuntime,
    _bounded_path_exists,
    _parse_acceptance_review_resolution,
    _parse_task_plan,
    _rules_block,
    _safe_relative_output,
    _task_delta,
    _terminal_status,
)
from personal_agent_gateway.team_verification_checks import VerificationCheck
from personal_agent_gateway.teams import RequiredVerification, TaskAcceptance, TeamRunService


@dataclass
class FakeModel:
    content: str
    normalize_worker: bool = True

    async def complete(self, messages):
        content = _complete_plan_fixture(self.content)
        if self.normalize_worker and _is_worker_prompt(messages):
            content = _complete_worker_fixture(content)
        return ModelResponse(content=content, tool_calls=[])

    async def complete_operation(self, messages, *, consumer_run_id):
        return await self.complete(messages)


@dataclass
class ScriptedModel:
    """호출마다 responses에서 순서대로 반환. 소진되면 마지막 값 반복."""
    responses: list
    normalize_worker: bool = True

    def __post_init__(self):
        self._calls = 0
        self._is_worker = False
        self.messages = []
        self.operation_session_id = None

    async def complete(self, messages):
        self.messages.append(messages)
        self._is_worker = self._is_worker or _is_worker_prompt(messages)
        idx = min(self._calls, len(self.responses) - 1)
        self._calls += 1
        value = self.responses[idx]
        if isinstance(value, Exception):
            raise value
        content = _complete_plan_fixture(value)
        if self.normalize_worker and self._is_worker:
            content = _complete_worker_fixture(content)
        return ModelResponse(
            content=content,
            tool_calls=[],
            upstream_session_id=f"sess-{self._calls}",
        )

    async def complete_operation(self, messages, *, consumer_run_id):
        response = await self.complete(messages)
        if self.operation_session_id is None:
            return response
        return ModelResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            upstream_session_id=self.operation_session_id,
        )


def _complete_plan_fixture(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(parsed, list) or any(
        not isinstance(item, dict)
        or "title" not in item
        or "description" not in item
        for item in parsed
    ):
        return value
    for item in parsed:
        item.setdefault("owner_agent_id", None)
        item.setdefault("required", True)
        item.setdefault(
            "acceptance",
            {
                "required_outputs": [],
                "required_verifications": ["worker-result"],
            },
        )
    return json.dumps(parsed)


def _is_worker_prompt(messages) -> bool:
    return any(
        "CONCRETE ASSIGNMENT" in str(message.get("content", ""))
        for message in messages
    )


def _complete_worker_fixture(value):
    if not isinstance(value, str) or '"needs_info"' in value:
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and set(parsed) == {
        "status",
        "summary",
        "reason_code",
        "deliverables",
        "verifications",
    }:
        return value
    return json.dumps(
        {
            "status": "completed",
            "summary": value,
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


def _outcome_json(
    summary: str,
    *,
    deliverables: list[dict[str, str]] | None = None,
    verification: str = "worker-result",
) -> str:
    return json.dumps(
        {
            "status": "completed",
            "summary": summary,
            "reason_code": None,
            "deliverables": deliverables or [],
            "verifications": [
                {
                    "name": verification,
                    "status": "passed",
                    "evidence": "checked",
                }
            ],
        }
    )


def _retry_review(instruction: str = "Return a corrected outcome.") -> str:
    return json.dumps(
        {
            "resolution": {
                "kind": "retry_worker",
                "instruction": instruction,
                "reason": "The current outcome is not acceptable.",
            }
        }
    )


def _ask_user_resolution(question="Which scope should be used?") -> str:
    return json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "scope",
                "question": question,
                "why_needed": "The Team cannot infer the intended scope.",
                "options": [
                    {
                        "id": "current",
                        "label": "Current scope",
                        "impact": "Uses the current task scope.",
                    }
                ],
                "recommended_option_id": "current",
                "blocking_scope": "task",
            }
        }
    )


_LIBRARY_DRAFT_SUMMARY = (
    "Draft ready.\n\n"
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)


def _set_library_draft_contract(setup) -> None:
    existing = setup.teams.get_cycle_effective_instruction(setup.cycle.id)
    setup.teams.set_cycle_effective_instruction(
        setup.cycle.id,
        existing or "Prepare the delegated Knowledge Request as a Library review draft.",
        output_contract_id="library_draft",
    )


def _factory_by_role(
    leader_responses,
    worker_responses,
    *,
    normalize_worker=True,
):
    from personal_agent_gateway.teams import TeamAgent
    models = {}
    def factory(agent: TeamAgent, _cycle_id: str | None = None):
        if agent.id not in models:
            responses = leader_responses if agent.role == "leader" else worker_responses
            models[agent.id] = ScriptedModel(
                list(responses),
                normalize_worker=normalize_worker if agent.role != "leader" else True,
            )
        models[agent.id].operation_session_id = agent.upstream_session_id
        return models[agent.id]
    return factory


@dataclass
class OperationModel:
    responses: list[ModelResponse | Exception]

    def __post_init__(self):
        self.calls = 0
        self.messages = []

    async def complete_operation(self, messages, *, consumer_run_id):
        self.calls += 1
        self.messages.append(messages)
        before_complete = getattr(self, "before_complete", None)
        if before_complete is not None:
            before_complete(self.calls)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def complete(self, messages):
        return await self.complete_operation(messages, consumer_run_id="direct")


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterOperationStage:
    def __init__(self, delegate, stage, ordinal, status):
        self._delegate = delegate
        self._stage = stage
        self._ordinal = ordinal
        self._status = status
        self._crashed = False

    async def invoke(self, operation, client, messages, parser):
        if (
            not self._crashed
            and operation.stage == self._stage
            and operation.stage_ordinal == self._ordinal
        ):
            self._crashed = True
            if self._status == "completed":
                await self._delegate.invoke(
                    operation,
                    client,
                    messages,
                    parser,
                )
            raise SimulatedProcessCrash
        return await self._delegate.invoke(
            operation,
            client,
            messages,
            parser,
        )


class CrashAfterAppliedAcceptanceLead:
    def __init__(self, delegate):
        self._delegate = delegate
        self._crashed = False

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def apply_acceptance_lead(self, operation_id, resolution):
        result = self._delegate.apply_acceptance_lead(
            operation_id,
            resolution,
        )
        if not self._crashed:
            self._crashed = True
            raise SimulatedProcessCrash
        return result


async def _no_sleep(_delay):
    return None


def crash_after_next_task_start(teams):
    original = teams.start_task

    def crash_after_start(task_id, agent_id):
        original(task_id, agent_id)
        raise SimulatedProcessCrash

    teams.start_task = crash_after_start
    return original


def valid_plan_json(owner_agent_id=None, verification="review"):
    return json.dumps(
        [
            {
                "title": "Research",
                "description": "Research the request.",
                "owner_agent_id": owner_agent_id,
                "required": True,
                "acceptance": {
                    "required_outputs": [],
                    "required_verifications": [verification],
                },
            }
        ]
    )


def make_operation_runtime(tmp_path, *, cycle_instruction=None):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle_request = None
    if cycle_instruction is None:
        cycle = teams.create_cycle(run.id, "manual", "manual-1")
    else:
        cycles = TeamCycleService(db)
        cycle_request = cycles.enqueue_request(
            run.id,
            "manual",
            "manual-1",
            cycle_instruction,
            previous_cycle_id=None,
        )
        cycle_request = cycles.claim_next(run.id)
        assert cycle_request is not None
        cycle = teams.create_cycle(
            run.id,
            "manual",
            cycle_request.source_id,
            request_id=cycle_request.id,
        )
    teams.set_cycle_status(cycle.id, "running")
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.role == "member"
    )
    operations = TeamModelOperationService(
        db,
        result_validators=team_model_effect_result_validators(),
    )
    lead_client = OperationModel([ModelResponse("summary", [])])
    worker_client = OperationModel(
        [ModelResponse(_outcome_json("done"), [])]
    )
    factory_sessions = []

    def model_factory(agent, _cycle_id=None):
        factory_sessions.append((agent.id, agent.upstream_session_id))
        return lead_client if agent.role == "leader" else worker_client

    runtime = TeamRuntime(
        teams,
        model_factory,
        operations=operations,
        model_invoker=TeamModelInvoker(operations, sleep=_no_sleep),
        model_effects=TeamModelEffectService(db, teams, operations),
    )
    return SimpleNamespace(
        db=db,
        teams=teams,
        run=run,
        cycle=cycle,
        cycle_request=cycle_request,
        worker=worker_agent,
        operations=operations,
        lead_client=lead_client,
        worker_client=worker_client,
        factory_sessions=factory_sessions,
        model_factory=model_factory,
        runtime=runtime,
    )


def add_completed_operation_task(setup):
    task = setup.teams.create_task(
        setup.run.id,
        "Existing work",
        "Previously completed work.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    task, worker = setup.teams.start_task(task.id, setup.worker.id)
    task, worker = setup.teams.finish_task(
        task.id,
        worker.id,
        "completed",
        result="existing result",
    )
    return task


@pytest.mark.asyncio
async def test_internal_resume_does_not_claim_or_mutate_ambiguous_operation(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.teams.set_run_status(setup.run.id, "running")
    task = setup.teams.create_task(
        setup.run.id,
        "work",
        "work",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
    )
    setup.teams.start_task(task.id, setup.worker.id)
    operation = setup.operations.reserve(
        OperationSpec(
            operation_key=f"{setup.cycle.id}:worker_execution:0",
            team_run_id=setup.run.id,
            cycle_id=setup.cycle.id,
            task_id=task.id,
            agent_id=setup.worker.id,
            provider=setup.worker.backend,
            stage="worker_execution",
            stage_ordinal=0,
            request_digest="0" * 64,
        )
    )
    operation = setup.operations.begin_attempt(operation.id, "consumer-1")
    setup.db.execute(
        """
        update team_model_operations set status = 'ambiguous'
        where id = ?
        """,
        (operation.id,),
    )
    setup.db.execute(
        """
        update team_tasks set status = 'pending', started_at = null
        where id = ?
        """,
        (task.id,),
    )
    setup.db.execute(
        """
        update team_agents set status = 'pending', current_task_id = null
        where team_run_id = ?
        """,
        (setup.run.id,),
    )
    setup.teams.set_run_status(setup.run.id, "interrupted")
    setup.teams.set_cycle_status(setup.cycle.id, "interrupted")

    with pytest.raises(AmbiguousModelOperation):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.operations.get(operation.id).status == "ambiguous"
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"
    assert setup.teams.get_cycle(setup.cycle.id).status == "interrupted"
    assert setup.worker_client.calls == 0


@pytest.mark.asyncio
async def test_cycle_add_work_safe_admission_exhaustion_enters_waiting(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.teams.set_cycle_status(setup.cycle.id, "queued")
    setup.teams.set_run_status(setup.run.id, "draft")
    setup.lead_client.responses = [
        RemoteRunFailedError(
            "provider_unavailable",
            "not ready",
            pre_stream=True,
        )
        for _ in range(3)
    ]
    recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )
    setup.runtime._provider_recovery = recovery

    with pytest.raises(ProviderOperationWaiting):
        await setup.runtime.add_work(
            setup.run.id,
            "work",
            setup.cycle.id,
        )

    operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert operation is not None
    assert operation.status == "waiting_for_provider"
    assert operation.attempts == 3
    assert setup.teams.get_team_run(
        setup.run.id
    ).status == "waiting_for_provider"
    assert setup.teams.get_cycle(
        setup.cycle.id
    ).status == "waiting_for_provider"
    assert setup.cycle_request is not None
    cycles = TeamCycleService(setup.db)
    assert cycles.get_request(
        setup.cycle_request.id
    ).status == "dispatching"
    assert setup.teams.get_agent(
        setup.run.leader_agent_id
    ).upstream_session_id is None


@pytest.mark.asyncio
async def test_invalid_add_work_closes_initial_operation_then_waits_on_repair(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.teams.set_cycle_status(setup.cycle.id, "queued")
    setup.teams.set_run_status(setup.run.id, "draft")
    setup.lead_client.responses = [
        ModelResponse(
            "invalid plan",
            [],
            upstream_session_id="lead-repair-session",
        ),
        *[
            RemoteRunFailedError(
                "provider_unavailable",
                "not ready",
                pre_stream=True,
            )
            for _ in range(3)
        ],
    ]
    setup.runtime._provider_recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )

    with pytest.raises(ProviderOperationWaiting):
        await setup.runtime.add_work(
            setup.run.id,
            "work",
            setup.cycle.id,
        )

    initial, repair = setup.operations.list_for_cycle(setup.cycle.id)
    assert (initial.stage, initial.status) == ("cycle_add_work", "failed")
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "cycle_planning_repair",
        2,
        "waiting_for_provider",
    )
    assert repair.upstream_session_id == "lead-repair-session"
    assert setup.teams.get_agent(
        setup.run.leader_agent_id
    ).upstream_session_id is None
    assert setup.teams.get_team_run(
        setup.run.id
    ).status == "waiting_for_provider"
    assert setup.teams.get_cycle(
        setup.cycle.id
    ).status == "waiting_for_provider"
    assert setup.cycle_request is not None
    assert TeamCycleService(setup.db).get_request(
        setup.cycle_request.id
    ).status == "dispatching"


@pytest.mark.asyncio
async def test_cycle_add_work_ambiguous_call_interrupts_without_replay(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    setup.teams.set_cycle_status(setup.cycle.id, "queued")
    setup.teams.set_run_status(setup.run.id, "draft")
    setup.lead_client.responses = [
        RemoteRunAbortedError("run_timeout", "timed out")
    ]
    setup.runtime._provider_recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )

    with pytest.raises(AmbiguousModelOperation):
        await setup.runtime.add_work(
            setup.run.id,
            "work",
            setup.cycle.id,
        )

    operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert operation is not None
    assert operation.status == "ambiguous"
    assert operation.attempts == 1
    assert setup.lead_client.calls == 1
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"
    assert setup.teams.get_cycle(setup.cycle.id).status == "interrupted"


def restart_operation_runtime(setup):
    return TeamRuntime(
        setup.teams,
        setup.model_factory,
        operations=setup.operations,
        model_invoker=TeamModelInvoker(setup.operations, sleep=_no_sleep),
        model_effects=TeamModelEffectService(
            setup.db,
            setup.teams,
            setup.operations,
        ),
    )


def make_operation_runtime_with_completed_worker(
    tmp_path,
    *,
    linked_cycle=False,
):
    setup = make_operation_runtime(
        tmp_path,
        cycle_instruction="work" if linked_cycle else None,
    )
    worker = next(
        agent
        for agent in setup.teams.list_agents(setup.run.id)
        if agent.role == "member"
    )
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    task, worker = setup.teams.start_task(task.id, worker.id)
    reserved = setup.operations.reserve(
        OperationSpec(
            operation_key=(
                f"{setup.cycle.id}:{task.id}:worker_execution:0"
            ),
            team_run_id=setup.run.id,
            cycle_id=setup.cycle.id,
            task_id=task.id,
            agent_id=worker.id,
            provider=worker.backend,
            stage="worker_execution",
            stage_ordinal=0,
            request_digest="a" * 64,
        )
    )
    invoking = setup.operations.begin_attempt(reserved.id, "consumer-1")
    outcome = json.loads(_outcome_json("done"))
    operation = setup.operations.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_outcome", outcome),
        upstream_session_id="worker-session-1",
    )
    setup.teams.set_run_status(setup.run.id, "running")
    values = vars(setup).copy()
    values.update(
        task=task,
        worker=worker,
        worker_operation=operation,
    )
    return SimpleNamespace(**values)


def make_recoverable_acceptance_runtime(
    tmp_path,
    *,
    linked_cycle=False,
):
    setup = make_operation_runtime(
        tmp_path,
        cycle_instruction="work" if linked_cycle else None,
    )
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse(
            _outcome_json("draft", verification="wrong-check"),
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("draft-fixed"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    values = vars(setup).copy()
    values["task"] = task
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_lead_acceptance_retry_uses_separate_worker_operation(tmp_path):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review("Fix the missing citation check."), []),
        ModelResponse("summary", []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.calls == 2
    lead_operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    )
    worker_operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_worker:1"
    )
    assert lead_operation is not None
    assert lead_operation.status == "applied"
    assert worker_operation is not None
    assert worker_operation.status == "applied"
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:worker_execution:0"
    ).status == "applied"


@pytest.mark.asyncio
async def test_lead_review_session_is_owned_by_lead_and_keeps_worker_applied(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "fail",
                        "reason_code": "requirements_not_met",
                        "summary": "The requirements cannot be met.",
                    }
                }
            ),
            [],
            upstream_session_id="lead-session",
        )
    ]
    setup.worker_client.responses = setup.worker_client.responses[:1]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    )
    assert operation is not None
    assert operation.stage == "acceptance_lead"
    assert operation.agent_id == setup.run.leader_agent_id
    assert operation.status == "applied"
    assert setup.teams.get_agent(
        setup.run.leader_agent_id
    ).upstream_session_id == "lead-session"
    assert setup.teams.get_agent(setup.worker.id).upstream_session_id == (
        "worker-session"
    )
    assert setup.worker_client.calls == 1


@pytest.mark.asyncio
async def test_worker_applied_then_lead_wait_claims_and_resumes_without_worker_replay(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(
        tmp_path,
        linked_cycle=True,
    )
    setup.lead_client.responses = [
        RemoteRunFailedError(
            "provider_unavailable",
            "not ready",
            pre_stream=True,
        )
        for _ in range(3)
    ]
    recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )
    setup.runtime._provider_recovery = recovery

    with pytest.raises(ProviderOperationWaiting):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    worker_operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:worker_execution:0"
    )
    lead_operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert worker_operation is not None
    assert worker_operation.status == "applied"
    assert lead_operation is not None
    assert (lead_operation.stage, lead_operation.status) == (
        "acceptance_lead",
        "waiting_for_provider",
    )
    assert setup.worker_client.calls == 1

    claim = recovery.claim_operation(setup.cycle.id)
    assert claim is not None
    assert claim.operation_id == lead_operation.id
    setup.lead_client.responses.extend(
        [
            ModelResponse(
                json.dumps(
                    {
                        "resolution": {
                            "kind": "fail",
                            "reason_code": "requirements_not_met",
                            "summary": "The requirements cannot be met.",
                        }
                    }
                ),
                [],
            ),
            ModelResponse("summary", []),
        ]
    )

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.calls == 1
    assert setup.operations.get(lead_operation.id).status == "applied"


@pytest.mark.asyncio
async def test_worker_applied_then_lead_ambiguity_interrupts_without_auto_replay(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(
        tmp_path,
        linked_cycle=True,
    )
    setup.lead_client.responses = [
        RemoteRunAbortedError("run_timeout", "timed out")
    ]
    recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )
    setup.runtime._provider_recovery = recovery

    with pytest.raises(AmbiguousModelOperation):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    worker_operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:worker_execution:0"
    )
    lead_operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert worker_operation is not None
    assert worker_operation.status == "applied"
    assert lead_operation is not None
    assert (lead_operation.stage, lead_operation.status) == (
        "acceptance_lead",
        "ambiguous",
    )
    assert setup.worker_client.calls == 1
    assert setup.lead_client.calls == 1
    assert recovery.reconcile_startup().runnable_cycle_ids == ()

    with pytest.raises(AmbiguousModelOperation):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.calls == 1
    assert setup.lead_client.calls == 1


@pytest.mark.asyncio
async def test_completed_acceptance_lead_applies_after_restart_without_second_call(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review(), [], upstream_session_id="lead-session"),
        ModelResponse("summary", []),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        TeamModelInvoker(setup.operations, sleep=_no_sleep),
        "acceptance_lead",
        1,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    lead_calls = setup.lead_client.calls
    operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert operation is not None
    assert (operation.stage, operation.status) == (
        "acceptance_lead",
        "completed",
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.lead_client.calls == lead_calls + 1
    assert setup.operations.get(operation.id).status == "applied"
    reviews = [
        message
        for message in setup.teams.list_messages(setup.run.id)
        if message.kind == "acceptance_review"
    ]
    assert len(reviews) == 1


@pytest.mark.asyncio
async def test_completed_acceptance_worker_finishes_once_after_restart(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review(), []),
        ModelResponse("summary", []),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        TeamModelInvoker(setup.operations, sleep=_no_sleep),
        "acceptance_worker",
        1,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    worker_calls = setup.worker_client.calls
    operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert operation is not None
    assert (operation.stage, operation.status) == (
        "acceptance_worker",
        "completed",
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == worker_calls
    assert setup.operations.get(operation.id).status == "applied"
    assert setup.teams.get_task(setup.task.id).status == "completed"
    outputs = [
        message
        for message in setup.teams.list_messages(setup.run.id)
        if message.kind == "agent_output"
    ]
    assert len(outputs) == 2


@pytest.mark.asyncio
async def test_applied_acceptance_lead_resumes_worker_without_repeating_audit(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review(), []),
        ModelResponse("summary", []),
    ]
    setup.runtime._model_effects = CrashAfterAppliedAcceptanceLead(
        TeamModelEffectService(setup.db, setup.teams, setup.operations)
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    ).status == "applied"
    assert setup.operations.get_open_for_cycle(setup.cycle.id) is None

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == 2
    reviews = [
        message
        for message in setup.teams.list_messages(setup.run.id)
        if message.kind == "acceptance_review"
    ]
    assert len(reviews) == 1


@pytest.mark.asyncio
async def test_mediation_user_answer_resumes_distinct_worker_operation(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse(
            '```json\n{"needs_info":{"topic":"scope","question":"Which scope?"}}\n```',
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution(), []),
        ModelResponse("summary", []),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]
    assert waiting.status == "waiting_for_user"
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current scope."},
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == 2
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{task.id}:worker_execution:0"
    ).attempts == 1
    continuation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{task.id}:mediation_worker:1"
    )
    assert continuation is not None
    assert continuation.status == "applied"


@pytest.mark.asyncio
async def test_acceptance_user_answer_resumes_distinct_worker_operation(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution("Which acceptance scope?"), []),
        ModelResponse("summary", []),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]
    assert waiting.status == "waiting_for_user"
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current acceptance scope."},
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == 2
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:worker_execution:0"
    ).attempts == 1
    continuation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_worker:1"
    )
    assert continuation is not None
    assert continuation.status == "applied"


@pytest.mark.asyncio
async def test_mediation_answer_recovers_after_crash_between_start_and_reserve(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse(
            '```json\n{"needs_info":{"topic":"scope","question":"Which scope?"}}\n```',
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution(), []),
        ModelResponse("summary", []),
    ]
    await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current scope."},
    )
    original_start = crash_after_next_task_start(setup.teams)

    with pytest.raises(SimulatedProcessCrash):
        await restart_operation_runtime(setup).resume(
            setup.run.id,
            setup.cycle.id,
        )

    setup.teams.start_task = original_start
    assert setup.teams.get_task(task.id).status == "in_progress"
    assert (
        setup.operations.get_by_key(
            f"{setup.cycle.id}:{task.id}:mediation_worker:1"
        )
        is None
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == 2
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{task.id}:mediation_worker:1"
    ).status == "applied"


@pytest.mark.asyncio
async def test_acceptance_answer_recovers_after_crash_between_start_and_reserve(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution("Which acceptance scope?"), []),
        ModelResponse("summary", []),
    ]
    await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current acceptance scope."},
    )
    original_start = crash_after_next_task_start(setup.teams)

    with pytest.raises(SimulatedProcessCrash):
        await restart_operation_runtime(setup).resume(
            setup.run.id,
            setup.cycle.id,
        )

    setup.teams.start_task = original_start
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    assert (
        setup.operations.get_by_key(
            f"{setup.cycle.id}:{setup.task.id}:acceptance_worker:1"
        )
        is None
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.worker_client.calls == 2
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_worker:1"
    ).status == "applied"


@pytest.mark.asyncio
async def test_mediation_budget_forces_worker_final_without_second_lead(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.db.execute(
        "update team_run_cycles set rounds_budget = 1 where id = ?",
        (setup.cycle.id,),
    )
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    query = (
        '```json\n{"needs_info":{"topic":"scope",'
        '"question":"Which scope?"}}\n```'
    )
    setup.worker_client.responses = [
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(
            _outcome_json("best effort"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(
            '{"resolution":{"kind":"answer","answer":"Use current scope."}}',
            [],
        ),
        ModelResponse("summary", []),
    ]

    completed = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert completed.status == "completed"
    lead_operations = [
        operation
        for operation in setup.operations.list_for_cycle(setup.cycle.id)
        if operation.stage == "mediation_lead"
    ]
    assert len(lead_operations) == 1
    assert setup.worker_client.calls == 3
    assert setup.teams.get_agent(setup.worker.id).reinvocations == 2
    forced = setup.operations.get_by_key(
        f"{setup.cycle.id}:{task.id}:mediation_worker:2"
    )
    assert forced is not None
    assert forced.status == "applied"


@pytest.mark.asyncio
async def test_mediation_decisions_reserve_budget_before_batch_answer(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.db.execute(
        "update team_run_cycles set rounds_budget = 1 where id = ?",
        (setup.cycle.id,),
    )
    first = setup.teams.create_task(
        setup.run.id,
        "First",
        "Research the first request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    second = setup.teams.create_task(
        setup.run.id,
        "Second",
        "Research the second request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    query = (
        '```json\n{"needs_info":{"topic":"scope",'
        '"question":"Which scope?"}}\n```'
    )
    setup.worker_client.responses = [
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(
            _outcome_json("second best effort"),
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("first resolved"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution(), []),
        ModelResponse("summary", []),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]

    assert waiting.status == "waiting_for_user"
    assert setup.teams.get_cycle(setup.cycle.id).rounds_used == 1
    assert len(request.items) == 1
    assert request.items[0]["blocking_task_ids"] == [first.id]
    assert setup.teams.get_task(second.id).status == "completed"
    assert len(
        [
            operation
            for operation in setup.operations.list_for_cycle(setup.cycle.id)
            if operation.stage == "mediation_lead"
        ]
    ) == 1

    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current scope."},
    )
    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.teams.get_cycle(setup.cycle.id).rounds_used == 1


@pytest.mark.asyncio
async def test_batched_mediation_decisions_keep_both_operation_receipts_valid(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.db.execute(
        "update team_run_cycles set rounds_budget = 2 where id = ?",
        (setup.cycle.id,),
    )
    tasks = [
        setup.teams.create_task(
            setup.run.id,
            title,
            f"Research {title.lower()}.",
            owner_agent_id=setup.worker.id,
            cycle_id=setup.cycle.id,
            acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
        )
        for title in ("First", "Second")
    ]
    query = (
        '```json\n{"needs_info":{"topic":"scope",'
        '"question":"Which scope?"}}\n```'
    )
    setup.worker_client.responses = [
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(query, [], upstream_session_id="worker-session"),
        ModelResponse(
            _outcome_json("first resolved"),
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("second resolved"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(_ask_user_resolution(), []),
        ModelResponse(_ask_user_resolution(), []),
        ModelResponse("summary", []),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.list_decision_requests(setup.run.id)[0]

    assert waiting.status == "waiting_for_user"
    assert len(request.items) == 1
    assert set(request.items[0]["blocking_task_ids"]) == {
        task.id for task in tasks
    }
    assert len(request.items[0]["query_message_ids"]) == 2
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "Use the current scope."},
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.teams.get_cycle(setup.cycle.id).rounds_used == 2
    for ordinal, task in enumerate(tasks, start=1):
        assert setup.operations.get_by_key(
            f"{setup.cycle.id}:{task.id}:mediation_worker:{ordinal}"
        ).status == "applied"


@pytest.mark.asyncio
async def test_completed_mediation_worker_recovery_increments_reinvocation_once(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse(
            '```json\n{"needs_info":{"topic":"scope","question":"Which scope?"}}\n```',
            [],
            upstream_session_id="worker-session",
        ),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(
            '{"resolution":{"kind":"answer","answer":"Use current scope."}}',
            [],
        ),
        ModelResponse("summary", []),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        TeamModelInvoker(setup.operations, sleep=_no_sleep),
        "mediation_worker",
        1,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.teams.get_agent(setup.worker.id).reinvocations == 0
    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.teams.get_agent(setup.worker.id).reinvocations == 1


@pytest.mark.asyncio
async def test_completed_worker_recovery_uses_durable_workspace_baseline(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(_retry_review(), []),
        ModelResponse("summary", []),
    ]
    changed = Path(setup.run.working_root) / "revision.txt"

    def write_revision(call):
        if call == 2:
            changed.write_text("revised", encoding="utf-8")

    setup.worker_client.before_complete = write_revision
    setup.runtime._model_invoker = CrashAfterOperationStage(
        TeamModelInvoker(setup.operations, sleep=_no_sleep),
        "acceptance_worker",
        1,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    operation = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_worker:1"
    )
    output = next(
        message
        for message in setup.teams.list_messages(
            setup.run.id,
            setup.cycle.id,
        )
        if message.kind == "agent_output"
        and message.metadata.get("operation_id") == operation.id
    )
    assert output.metadata["created"] == ["revision.txt"]


@pytest.mark.asyncio
async def test_add_work_repair_uses_separate_operation_and_defers_lead_session(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("not-json", [], upstream_session_id="lead-session-1"),
        ModelResponse(
            valid_plan_json(setup.worker.id),
            [],
            upstream_session_id="lead-session-1",
        ),
    ]

    await setup.runtime.add_work(
        setup.run.id,
        "research",
        setup.cycle.id,
    )

    operations = setup.operations.list_for_cycle(setup.cycle.id)
    assert [item.stage for item in operations] == [
        "cycle_add_work",
        "cycle_planning_repair",
    ]
    assert all(item.status in {"failed", "applied"} for item in operations)
    assert setup.teams.get_agent(
        setup.run.leader_agent_id
    ).upstream_session_id == "lead-session-1"
    assert setup.factory_sessions == [
        (setup.run.leader_agent_id, None),
        (setup.run.leader_agent_id, "lead-session-1"),
    ]


@pytest.mark.asyncio
async def test_planning_and_later_add_work_repairs_have_distinct_identity(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("invalid planning", []),
        ModelResponse(valid_plan_json(setup.worker.id, "worker-result"), []),
        ModelResponse("Initial summary.", []),
        ModelResponse("invalid add work", []),
        ModelResponse(valid_plan_json(setup.worker.id), []),
    ]

    completed = await setup.runtime.start(setup.run.id, setup.cycle.id)
    created = await setup.runtime.add_work(
        setup.run.id,
        "Research another source.",
        setup.cycle.id,
    )

    repairs = [
        operation
        for operation in setup.operations.list_for_cycle(setup.cycle.id)
        if operation.stage == "cycle_planning_repair"
    ]
    assert completed.status == "completed", completed.error_message
    assert len(created) == 1
    assert [operation.stage_ordinal for operation in repairs] == [1, 2]
    assert all(operation.status == "applied" for operation in repairs)


@pytest.mark.asyncio
async def test_prepared_planning_repair_is_recovered_with_exact_operation(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("invalid planning", [], upstream_session_id="lead-session-1"),
        ModelResponse(
            valid_plan_json(setup.worker.id, "worker-result"),
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse("summary", [], upstream_session_id="lead-session-1"),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_planning_repair",
        1,
        "prepared",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.start(setup.run.id, setup.cycle.id)

    repair = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert repair is not None
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "cycle_planning_repair",
        1,
        "prepared",
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    recovered = setup.operations.get(repair.id)
    assert completed.status == "completed"
    assert recovered.status == "applied"
    assert recovered.operation_key == repair.operation_key
    assert setup.lead_client.calls == 3


@pytest.mark.asyncio
async def test_completed_add_work_repair_is_applied_without_another_model_call(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("invalid add work", [], upstream_session_id="lead-session-1"),
        ModelResponse(
            valid_plan_json(setup.worker.id),
            [],
            upstream_session_id="lead-session-1",
        ),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_planning_repair",
        2,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.add_work(
            setup.run.id,
            "Research another source.",
            setup.cycle.id,
        )

    repair = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert repair is not None
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "cycle_planning_repair",
        2,
        "completed",
    )
    calls_before_restart = setup.lead_client.calls

    created = await restart_operation_runtime(setup).add_work(
        setup.run.id,
        "Research another source.",
        setup.cycle.id,
    )

    assert len(created) == 1
    assert setup.operations.get(repair.id).status == "applied"
    assert setup.lead_client.calls == calls_before_restart


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_instruction", "effective_instruction"),
    [
        ("Original request.", "Prepared replacement."),
        (
            "Next work.",
            "Next work.\n\nPREVIOUS CYCLE SUMMARY\nPrevious result.",
        ),
    ],
)
async def test_resume_invokes_prepared_add_work_from_effective_instruction(
    tmp_path,
    request_instruction,
    effective_instruction,
):
    setup = make_operation_runtime(
        tmp_path,
        cycle_instruction=request_instruction,
    )
    add_completed_operation_task(setup)
    setup.teams.set_cycle_execution_metadata(
        setup.cycle.id,
        {
            "semantic_source": {
                "effective_instruction": effective_instruction,
            }
        },
    )
    setup.lead_client.responses = [
        ModelResponse(
            valid_plan_json(setup.worker.id, "worker-result"),
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse(
            "summary",
            [],
            upstream_session_id="lead-session-1",
        ),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_add_work",
        0,
        "prepared",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.add_work(
            setup.run.id,
            effective_instruction,
            setup.cycle.id,
        )

    add_work = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert add_work is not None
    assert (add_work.stage, add_work.stage_ordinal, add_work.status) == (
        "cycle_add_work",
        0,
        "prepared",
    )

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    recovered = setup.operations.get(add_work.id)
    assert completed.status == "completed"
    assert recovered.status == "applied"
    assert recovered.attempts == 1
    assert setup.teams.get_cycle_objective(setup.cycle.id) == request_instruction
    assert setup.lead_client.calls == 2


@pytest.mark.asyncio
async def test_resume_applies_completed_add_work_repair_without_reinvoking_it(
    tmp_path,
):
    instruction = "Research another source."
    setup = make_operation_runtime(
        tmp_path,
        cycle_instruction=instruction,
    )
    add_completed_operation_task(setup)
    setup.lead_client.responses = [
        ModelResponse(
            "invalid add work",
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse(
            valid_plan_json(setup.worker.id, "worker-result"),
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse(
            "summary",
            [],
            upstream_session_id="lead-session-1",
        ),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_planning_repair",
        2,
        "completed",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.add_work(
            setup.run.id,
            instruction,
            setup.cycle.id,
        )

    repair = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert repair is not None
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "cycle_planning_repair",
        2,
        "completed",
    )
    attempts_before_resume = repair.attempts
    calls_before_resume = setup.lead_client.calls

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    recovered = setup.operations.get(repair.id)
    assert completed.status == "completed"
    assert recovered.status == "applied"
    assert recovered.attempts == attempts_before_resume
    assert setup.lead_client.calls == calls_before_resume + 1


@pytest.mark.asyncio
async def test_changed_add_work_messages_conflict_before_second_model_call(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(valid_plan_json(setup.worker.id), []),
        ModelResponse(valid_plan_json(setup.worker.id), []),
    ]
    await setup.runtime.add_work(
        setup.run.id,
        "research",
        setup.cycle.id,
    )

    with pytest.raises(OperationConflict):
        await setup.runtime.add_work(
            setup.run.id,
            "changed instruction",
            setup.cycle.id,
        )

    assert setup.lead_client.calls == 1


@pytest.mark.asyncio
async def test_waiting_cycle_operation_is_not_reinvoked(tmp_path):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(valid_plan_json(setup.worker.id), []),
        ModelResponse(valid_plan_json(setup.worker.id), []),
    ]
    await setup.runtime.add_work(
        setup.run.id,
        "research",
        setup.cycle.id,
    )
    operation = setup.operations.list_for_cycle(setup.cycle.id)[0]
    setup.db.execute(
        """
        update team_model_operations
        set status = 'waiting_for_provider'
        where id = ?
        """,
        (operation.id,),
    )

    with pytest.raises(OperationConflict):
        await setup.runtime.add_work(
            setup.run.id,
            "research",
            setup.cycle.id,
        )

    assert setup.lead_client.calls == 1


@pytest.mark.asyncio
async def test_completed_worker_operation_applies_without_second_model_call(
    tmp_path,
):
    setup = make_operation_runtime_with_completed_worker(tmp_path)

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.calls == 0
    assert setup.operations.get(setup.worker_operation.id).status == "applied"
    assert setup.teams.get_task(setup.task.id).outcome is not None
    assert result.status in {"running", "completed", "completed_with_failures"}


@pytest.mark.asyncio
async def test_synthesis_prompt_uses_the_output_contract(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [ModelResponse(_LIBRARY_DRAFT_SUMMARY, [])]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    prompt = setup.lead_client.messages[-1][0]["content"]
    assert "<library_draft>" in prompt
    assert "concise plain-text summary" not in prompt
    assert "ask_user" in prompt


@pytest.mark.asyncio
async def test_synthesis_prompt_is_unchanged_without_a_contract(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    setup.lead_client.responses = [ModelResponse("summary", [])]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    prompt = setup.lead_client.messages[-1][0]["content"]
    assert "concise plain-text summary" in prompt
    assert "<library_draft>" not in prompt


_PROSE_SUMMARY = "## 완료 요약\n\n초안을 파일로 정리했습니다."


@pytest.mark.asyncio
async def test_contract_violation_triggers_exactly_one_repair(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY, []),
        ModelResponse(_LIBRARY_DRAFT_SUMMARY, []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    stages = [item.stage for item in setup.operations.list_for_cycle(setup.cycle.id)]
    assert stages.count("cycle_synthesis") == 1
    assert stages.count("cycle_synthesis_repair") == 1
    assert setup.lead_client.calls == 2
    summary = setup.teams.get_cycle(setup.cycle.id).summary or ""
    assert summary.startswith("Draft ready.")
    assert "<library_draft>" not in summary


@pytest.mark.asyncio
async def test_successful_contract_stores_prose_summary_and_ledger_payload(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [ModelResponse(_LIBRARY_DRAFT_SUMMARY, [])]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    summary = setup.teams.get_cycle(setup.cycle.id).summary or ""
    assert summary.strip() == "Draft ready."
    assert "<library_draft>" not in summary
    applied = [
        item
        for item in setup.operations.list_for_cycle(setup.cycle.id)
        if item.stage == "cycle_synthesis" and item.status == "applied"
    ]
    assert len(applied) == 1
    assert "<library_draft>" in applied[0].result_json["payload"]["contract_payload"]


@pytest.mark.asyncio
async def test_second_contract_violation_is_returned_as_is(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY, []),
        ModelResponse(_PROSE_SUMMARY, []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.lead_client.calls == 2
    cycle = setup.teams.get_cycle(setup.cycle.id)
    assert cycle.status == "completed"
    assert "<library_draft>" not in (cycle.summary or "")
    assert (cycle.summary or "").strip() == _PROSE_SUMMARY.strip()


@pytest.mark.asyncio
async def test_ask_user_resolution_is_not_treated_as_a_violation(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "ask_user",
                        "topic": "publication",
                        "question": "Publish as a shared Library entry?",
                        "why_needed": "The audience changes the wording.",
                        "options": [
                            {"id": "shared", "label": "Shared", "impact": "everyone"}
                        ],
                        "recommended_option_id": "shared",
                        "blocking_scope": "run",
                    }
                }
            ),
            [],
        )
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.lead_client.calls == 1
    assert setup.teams.get_cycle(setup.cycle.id).status == "waiting_for_user"


@pytest.mark.asyncio
async def test_completed_worker_operation_startup_applies_locally_without_worker_call(
    tmp_path,
):
    setup = make_operation_runtime_with_completed_worker(
        tmp_path,
        linked_cycle=True,
    )
    recovery = TeamProviderRecovery(
        setup.teams,
        SimpleNamespace(),
        setup.operations,
    )
    runtime = TeamRuntime(
        setup.teams,
        setup.model_factory,
        operations=setup.operations,
        model_invoker=TeamModelInvoker(setup.operations, sleep=_no_sleep),
        model_effects=TeamModelEffectService(
            setup.db,
            setup.teams,
            setup.operations,
        ),
        provider_recovery=recovery,
    )
    registry = TeamRunRegistry()
    orchestrator = TeamRunOrchestrator(registry, lambda: runtime)
    dispatcher = TeamCycleDispatcher(
        TeamCycleService(setup.db),
        setup.teams,
        orchestrator,
        EventBus(),
        provider_recovery=recovery,
    )
    orchestrator.add_observer(dispatcher.on_team_run_settled)

    dispatcher.reconcile()
    await dispatcher.start()
    try:
        for _ in range(100):
            if setup.operations.get(setup.worker_operation.id).status == "applied":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("startup did not apply completed operation")
    finally:
        await dispatcher.stop(interrupt_active=False)

    assert setup.worker_client.calls == 0
    assert setup.operations.get(setup.worker_operation.id).status == "applied"
    assert setup.teams.get_task(setup.task.id).outcome is not None


@pytest.mark.asyncio
async def test_cycle_synthesis_uses_separate_operation_and_atomic_apply(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    operations = setup.operations.list_for_cycle(setup.cycle.id)
    assert [item.stage for item in operations] == [
        "worker_execution",
        "cycle_synthesis",
    ]
    assert all(item.status == "applied" for item in operations)
    assert result.status == "completed"
    assert result.summary == "summary"
    assert setup.teams.get_cycle(setup.cycle.id).summary == "summary"
    synthesis_messages = [
        message
        for message in setup.teams.list_messages(setup.run.id, setup.cycle.id)
        if message.kind == "synthesis"
    ]
    assert len(synthesis_messages) == 1
    assert synthesis_messages[0].metadata == {
        "operation_id": operations[-1].id
    }


@pytest.mark.asyncio
async def test_cycle_synthesis_decision_applies_before_waiting_for_user(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "ask_user",
                        "topic": "format",
                        "question": "Which final format?",
                        "why_needed": "The requested format is ambiguous.",
                        "options": [
                            {
                                "id": "short",
                                "label": "Short",
                                "impact": "Keeps the result concise.",
                            }
                        ],
                        "recommended_option_id": "short",
                        "blocking_scope": "run",
                    }
                }
            ),
            [],
            upstream_session_id="lead-session-1",
        )
    ]

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    synthesis = setup.operations.list_for_cycle(setup.cycle.id)[-1]
    assert synthesis.stage == "cycle_synthesis"
    assert synthesis.status == "applied"
    assert result.status == "waiting_for_user"
    requests = setup.teams.list_decision_requests(setup.run.id)
    assert len(requests) == 1
    assert requests[0].items[0]["stage"] == "synthesis"


@pytest.mark.asyncio
async def test_answered_cycle_synthesis_uses_next_immutable_operation(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "ask_user",
                        "topic": "format",
                        "question": "Which final format?",
                        "why_needed": "The requested format is ambiguous.",
                        "options": [
                            {
                                "id": "short",
                                "label": "Short",
                                "impact": "Keeps the result concise.",
                            }
                        ],
                        "recommended_option_id": "short",
                        "blocking_scope": "run",
                    }
                }
            ),
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse(
            "Final concise summary.",
            [],
            upstream_session_id="lead-session-1",
        ),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.get_active_decision_request(
        setup.run.id,
        setup.cycle.id,
    )
    assert request is not None
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {"Q-001": "short"},
    )

    completed = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    synthesis_operations = [
        operation
        for operation in setup.operations.list_for_cycle(setup.cycle.id)
        if operation.stage == "cycle_synthesis"
    ]
    assert waiting.status == "waiting_for_user"
    assert completed.status == "completed"
    assert completed.summary == "Final concise summary."
    assert [operation.stage_ordinal for operation in synthesis_operations] == [0, 1]
    assert [operation.result_kind for operation in synthesis_operations] == [
        "user_decision",
        "synthesis",
    ]
    assert all(operation.status == "applied" for operation in synthesis_operations)
    assert setup.lead_client.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_status", ["prepared", "completed"])
async def test_synthesis_restart_recovers_exact_open_operation(
    tmp_path,
    crash_status,
):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    setup.lead_client.responses = [ModelResponse("summary", [])]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_synthesis",
        0,
        crash_status,
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    synthesis = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert synthesis is not None
    assert (synthesis.stage, synthesis.stage_ordinal, synthesis.status) == (
        "cycle_synthesis",
        0,
        crash_status,
    )
    calls_before_restart = setup.lead_client.calls

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert completed.summary == "summary"
    assert setup.operations.get(synthesis.id).status == "applied"
    assert setup.lead_client.calls == calls_before_restart + (
        1 if crash_status == "prepared" else 0
    )


@pytest.mark.asyncio
async def test_synthesis_repair_restart_recovers_exact_open_operation(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY, []),
        ModelResponse(_LIBRARY_DRAFT_SUMMARY, []),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_synthesis_repair",
        0,
        "prepared",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    repair = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert repair is not None
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "cycle_synthesis_repair",
        0,
        "prepared",
    )
    assert setup.lead_client.calls == 1

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert completed.summary == "Draft ready."
    assert setup.operations.get(repair.id).status == "applied"
    assert setup.lead_client.calls == 2
    applied = setup.operations.get(repair.id)
    assert "<library_draft>" in applied.result_json["payload"]["contract_payload"]


@pytest.mark.asyncio
async def test_recovered_base_synthesis_contract_violation_still_repairs(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY, []),
        ModelResponse(_LIBRARY_DRAFT_SUMMARY, []),
    ]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "cycle_synthesis",
        0,
        "prepared",
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    base = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert base is not None
    assert (base.stage, base.stage_ordinal, base.status) == (
        "cycle_synthesis",
        0,
        "prepared",
    )
    assert setup.lead_client.calls == 0

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert completed.summary == "Draft ready."
    assert setup.lead_client.calls == 2
    operations = setup.operations.list_for_cycle(setup.cycle.id)
    stages = [item.stage for item in operations]
    assert stages.count("cycle_synthesis") == 1
    assert stages.count("cycle_synthesis_repair") == 1
    assert setup.operations.get(base.id).status == "failed"
    repair = next(item for item in operations if item.stage == "cycle_synthesis_repair")
    assert repair.status == "applied"
    assert "<library_draft>" in repair.result_json["payload"]["contract_payload"]


@pytest.mark.asyncio
async def test_ask_user_during_repair_does_not_poison_next_synthesis_key(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY, [], upstream_session_id="lead-session-1"),
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "ask_user",
                        "topic": "publication",
                        "question": "Publish as a shared Library entry?",
                        "why_needed": "The audience changes the wording.",
                        "options": [
                            {"id": "shared", "label": "Shared", "impact": "everyone"}
                        ],
                        "recommended_option_id": "shared",
                        "blocking_scope": "run",
                    }
                }
            ),
            [],
            upstream_session_id="lead-session-1",
        ),
        ModelResponse(
            _LIBRARY_DRAFT_SUMMARY,
            [],
            upstream_session_id="lead-session-1",
        ),
    ]

    waiting = await setup.runtime.resume(setup.run.id, setup.cycle.id)
    assert waiting.status == "waiting_for_user"

    request = setup.teams.get_active_decision_request(setup.run.id, setup.cycle.id)
    assert request is not None
    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {"Q-001": "shared"},
    )

    completed = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert completed.status == "completed"
    assert completed.summary == "Draft ready."
    assert setup.lead_client.calls == 3
    operations = setup.operations.list_for_cycle(setup.cycle.id)
    stages = [item.stage for item in operations]
    assert stages.count("cycle_synthesis") == 2
    assert stages.count("cycle_synthesis_repair") == 1
    synthesis_operations = [
        item for item in operations if item.stage == "cycle_synthesis"
    ]
    assert [item.stage_ordinal for item in synthesis_operations] == [0, 1]
    failed_base = next(
        item
        for item in operations
        if item.stage == "cycle_synthesis" and item.stage_ordinal == 0
    )
    repair_operation = next(
        item for item in operations if item.stage == "cycle_synthesis_repair"
    )
    assert repair_operation.upstream_session_id == failed_base.upstream_session_id


@pytest.mark.asyncio
async def test_invalid_worker_output_uses_one_separate_repair_operation(tmp_path):
    setup = make_operation_runtime(tmp_path)
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse("not-json", [], upstream_session_id="worker-session-1"),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session-1",
        ),
    ]
    setup.lead_client.responses = [ModelResponse("summary", [])]

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    worker_operations = [
        operation
        for operation in setup.operations.list_for_cycle(setup.cycle.id)
        if operation.stage == "worker_execution"
    ]
    assert [operation.stage_ordinal for operation in worker_operations] == [0, 1]
    assert [operation.status for operation in worker_operations] == [
        "failed",
        "applied",
    ]
    assert setup.worker_client.calls == 2
    assert setup.teams.get_task(task.id).status == "completed"
    assert setup.teams.get_agent(
        setup.worker.id
    ).upstream_session_id == "worker-session-1"
    assert result.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_status", ["prepared", "completed"])
async def test_worker_repair_restart_recovers_exact_open_operation(
    tmp_path,
    crash_status,
):
    setup = make_operation_runtime(tmp_path)
    task = setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse("not-json", [], upstream_session_id="worker-session-1"),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session-1",
        ),
    ]
    setup.lead_client.responses = [ModelResponse("summary", [])]
    setup.runtime._model_invoker = CrashAfterOperationStage(
        setup.runtime._model_invoker,
        "worker_execution",
        1,
        crash_status,
    )

    with pytest.raises(SimulatedProcessCrash):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    repair = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert repair is not None
    assert (repair.stage, repair.stage_ordinal, repair.status) == (
        "worker_execution",
        1,
        crash_status,
    )
    calls_before_restart = setup.worker_client.calls

    completed = await restart_operation_runtime(setup).resume(
        setup.run.id,
        setup.cycle.id,
    )

    assert completed.status == "completed"
    assert setup.operations.get(repair.id).status == "applied"
    assert setup.teams.get_task(task.id).status == "completed"
    assert setup.worker_client.calls == calls_before_restart + (
        1 if crash_status == "prepared" else 0
    )


@pytest.mark.asyncio
async def test_worker_query_operation_applies_before_current_lead_mediation(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.teams.create_task(
        setup.run.id,
        "Research",
        "Research the request.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        acceptance=TaskAcceptance((), (RequiredVerification("worker-result"),)),
    )
    setup.worker_client.responses = [
        ModelResponse(
            '```json\n{"needs_info":{"topic":"scope","question":"Which scope?"}}\n```',
            [],
            upstream_session_id="worker-session-1",
        ),
        ModelResponse(
            _outcome_json("done"),
            [],
            upstream_session_id="worker-session-1",
        ),
    ]
    setup.lead_client.responses = [
        ModelResponse(
            '{"resolution":{"kind":"answer","answer":"Use the current scope."}}',
            [],
        ),
        ModelResponse("summary", []),
    ]

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    worker_operation = setup.operations.list_for_cycle(setup.cycle.id)[0]
    assert worker_operation.result_kind == "worker_query"
    assert worker_operation.status == "applied"
    query = next(
        message
        for message in setup.teams.list_messages(setup.run.id, setup.cycle.id)
        if message.kind == "query"
    )
    assert query.metadata["operation_id"] == worker_operation.id
    operations = setup.operations.list_for_cycle(setup.cycle.id)
    mediation_lead = next(
        operation
        for operation in operations
        if operation.stage == "mediation_lead"
    )
    mediation_worker = next(
        operation
        for operation in operations
        if operation.stage == "mediation_worker"
    )
    assert mediation_lead.agent_id == setup.run.leader_agent_id
    assert mediation_lead.status == "applied"
    assert mediation_worker.agent_id == setup.worker.id
    assert mediation_worker.status == "applied"
    assert result.status == "completed"


def test_worker_prompt_presents_a_complete_concrete_assignment() -> None:
    prompt = WORKER_PROMPT.format(
        persona_snapshot_json="{}",
        goal="Summarize the mail",
        task_title="Read mail context",
        task_description="Read CYCLES/cycle-1/MAIL_CONTEXT.md",
    )

    assert "Perform the concrete assignment below now" in prompt
    assert "Do not ask the user what work to do" in prompt
    assert "Read CYCLES/cycle-1/MAIL_CONTEXT.md" in prompt
    assert "changed files" not in prompt
    assert '"deliverables"' in prompt
    assert '"verifications"' in prompt
    assert "final response must contain only" in prompt


def test_worker_prompt_uses_cycle_space_instead_of_run_space(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    cycle_policy = dict(cycle.space_policy or {})
    cycle_policy["read_mode"] = "all"
    db.execute(
        "update team_run_cycles set space_policy_snapshot_json = ? where id = ?",
        (json.dumps(cycle_policy), cycle.id),
    )
    task = teams.create_task(
        run.id,
        "Inspect",
        "Inspect the source",
        cycle_id=cycle.id,
    )
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.role == "member"
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("done"))

    prompt = runtime._worker_prompt(run, worker_agent, task)

    assert "SPACE POLICY (frozen at cycle start):" in prompt
    assert "- Read scope: all" in prompt
    assert "- Read scope: none" not in prompt
    assert "- Write mode: isolated" in prompt


def test_worker_prompt_without_cycle_keeps_run_space(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    task = teams.create_task(run.id, "Inspect", "Inspect the source")
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.role == "member"
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("done"))

    prompt = runtime._worker_prompt(run, worker_agent, task)

    assert "SPACE POLICY (frozen at run start):" in prompt
    assert "- Read scope: none" in prompt
    assert "- Write mode: isolated" in prompt


@pytest.mark.asyncio
async def test_add_work_passes_cycle_space_to_leader_model_factory(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [],
        "planning_only",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    seen_cycle_ids: list[str | None] = []

    def model_factory(_agent, cycle_id=None):
        seen_cycle_ids.append(cycle_id)
        return FakeModel('[{"title":"Inspect","description":"Inspect the source"}]')

    runtime = TeamRuntime(teams, model_factory)

    await runtime.add_work(run.id, "Inspect the source", cycle.id)

    assert seen_cycle_ids == [cycle.id]


@pytest.mark.asyncio
async def test_worker_final_response_is_parsed_as_task_outcome(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(run.id, "T", "D")
    model = FakeModel(
        json.dumps(
            {
                "status": "completed",
                "summary": "Done",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {"name": "review", "status": "passed", "evidence": "checked"}
                ],
            }
        )
    )
    runtime = TeamRuntime(teams, lambda _agent: model)

    outcome = await runtime._run_task(run, leader_agent, worker_agent, task)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.status == "completed"
    assert outcome.summary == "Done"


@pytest.mark.asyncio
async def test_fenced_worker_outcome_reaches_normal_acceptance_path(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Inspect",
        "Inspect dashboard",
        acceptance=TaskAcceptance((), (RequiredVerification("pytest"),)),
    )
    payload = json.dumps(
        {
            "status": "completed",
            "summary": "Done",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "pytest",
                    "status": "passed",
                    "evidence": "tests passed",
                }
            ],
        }
    )
    runtime = TeamRuntime(
        teams,
        lambda _agent: FakeModel(
            f"```json\n{payload}\n```",
            normalize_worker=False,
        ),
    )

    outcome = await runtime._run_task(
        run,
        leader_agent,
        worker_agent,
        task,
    )

    assert outcome.status == "completed"
    assert outcome.reason_code is None
    assert outcome.verifications[0].name == "pytest"


@pytest.mark.asyncio
async def test_worker_prose_becomes_invalid_task_outcome(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(run.id, "T", "D")
    runtime = TeamRuntime(
        teams,
        lambda _agent: FakeModel(
            "권한이 없어 실패했습니다.",
            normalize_worker=False,
        ),
    )

    outcome = await runtime._run_task(run, leader_agent, worker_agent, task)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.status == "blocked"
    assert outcome.reason_code == "invalid_task_outcome"
    assert outcome.summary == "권한이 없어 실패했습니다."


@pytest.mark.asyncio
async def test_worker_prose_cannot_complete_team_run(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    plan = '[{"title":"T","description":"D"}]'
    runtime = TeamRuntime(
        teams,
        _factory_by_role(
            [
                plan,
                json.dumps(
                    {
                        "resolution": {
                            "kind": "retry_worker",
                            "instruction": "Return the required JSON outcome.",
                            "reason": "The Worker returned prose.",
                        }
                    }
                ),
                json.dumps(
                    {
                        "resolution": {
                            "kind": "retry_worker",
                            "instruction": "Return strict JSON only.",
                            "reason": "The Worker returned prose again.",
                        }
                    }
                ),
            ],
            [
                "I could not inspect files.",
                "Still not valid JSON.",
                "The final response is still prose.",
            ],
            normalize_worker=False,
        ),
    )

    result = await runtime.start(run.id)

    assert result.status == "failed"
    task = teams.list_tasks(run.id)[0]
    assert task.status == "failed"
    assert task.acceptance_recovery_attempts == 2
    assert task.outcome is not None
    assert task.outcome["reason_code"] == "invalid_task_outcome"
    assert task.acceptance_result is not None
    assert task.acceptance_result["accepted"] is False
    reviews = [
        message
        for message in teams.list_messages(run.id)
        if message.kind == "acceptance_review"
    ]
    assert reviews[0].metadata["rejected_verifications"] == ["worker-result"]


@pytest.mark.asyncio
async def test_acceptance_recovery_removes_undeclared_deliverable(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    plan = json.dumps(
        [
            {
                "title": "T",
                "description": "D",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": [],
                    "required_verifications": ["source-check"],
                },
            }
        ]
    )
    review = json.dumps(
        {
            "resolution": {
                "kind": "retry_worker",
                "instruction": "Remove docs/d3.md and omit the deliverable.",
                "reason": "The file is outside the Task contract.",
            }
        }
    )
    first_outcome = json.dumps(
        {
            "status": "completed",
            "summary": "Created an extra file.",
            "reason_code": None,
            "deliverables": [{"path": "docs/d3.md", "kind": "markdown"}],
            "verifications": [
                {
                    "name": "source-check",
                    "status": "passed",
                    "evidence": "checked",
                }
            ],
        }
    )
    second_outcome = json.dumps(
        {
            "status": "completed",
            "summary": "Removed the extra file.",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "source-check",
                    "status": "passed",
                    "evidence": "checked",
                }
            ],
        }
    )
    working_root = Path(run.working_root)

    class CleanupWorkerModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            path = working_root / "docs" / "d3.md"
            if self.calls == 1:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("extra", encoding="utf-8")
                content = first_outcome
            else:
                path.unlink()
                content = second_outcome
            return ModelResponse(
                content=content,
                tool_calls=[],
                upstream_session_id=f"worker-{self.calls}",
            )

    leader_model = ScriptedModel([plan, review, "All work completed."])
    worker_model = CleanupWorkerModel()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    completed = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert completed.status == "completed"
    assert task.status == "completed"
    assert task.acceptance_recovery_attempts == 1
    assert not (Path(run.working_root) / "docs/d3.md").exists()
    assert [m.kind for m in teams.list_messages(run.id)].count(
        "acceptance_review"
    ) == 1
    assert task.error_message is None


@pytest.mark.asyncio
async def test_acceptance_revision_publishes_only_resubmitted_outcome(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    plan = json.dumps(
        [
            {
                "title": "T",
                "description": "D",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": [],
                    "required_verifications": ["source-check"],
                },
            }
        ]
    )
    review = json.dumps(
        {
            "resolution": {
                "kind": "revise_acceptance",
                "acceptance": {
                    "required_outputs": ["docs/d3.md"],
                    "required_verifications": ["source-check"],
                },
                "instruction": "Resubmit docs/d3.md under the revised contract.",
                "reason": "The Task contract omitted the requested guide.",
            }
        }
    )

    def outcome(summary: str) -> str:
        return json.dumps(
            {
                "status": "completed",
                "summary": summary,
                "reason_code": None,
                "deliverables": [{"path": "docs/d3.md", "kind": "markdown"}],
                "verifications": [
                    {
                        "name": "source-check",
                        "status": "passed",
                        "evidence": "checked",
                    }
                ],
            }
        )

    working_root = Path(run.working_root)

    class RevisionWorkerModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            path = working_root / "docs" / "d3.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"version {self.calls}", encoding="utf-8")
            return ModelResponse(
                content=outcome(f"submission {self.calls}"),
                tool_calls=[],
                upstream_session_id=f"worker-{self.calls}",
            )

    class RecordingPublisher:
        def __init__(self) -> None:
            self.outcomes = []

        def publish(self, _run_id, _cycle_id, _task, published, _root) -> None:
            self.outcomes.append(published)

    publisher = RecordingPublisher()
    leader_model = ScriptedModel([plan, review, "All work completed."])
    worker_model = RevisionWorkerModel()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
        artifact_publisher=publisher,
    )

    completed = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert completed.status == "completed"
    assert task.acceptance.required_outputs == ("docs/d3.md",)
    assert task.acceptance_result is not None
    assert task.acceptance_result["accepted"] is True
    assert [item.summary for item in publisher.outcomes] == ["submission 2"]


@pytest.mark.asyncio
async def test_acceptance_review_keeps_task_run_and_cycle_active(tmp_path) -> None:
    import asyncio

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        tmp_path / "workspace",
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
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    review_started = asyncio.Event()
    release_review = asyncio.Event()
    plan = _complete_plan_fixture('[{"title":"T","description":"D"}]')

    class GatedLeaderModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            if self.calls == 1:
                content = plan
            elif self.calls == 2:
                review_started.set()
                await release_review.wait()
                content = _retry_review()
            else:
                content = "completed"
            return ModelResponse(content=content, tool_calls=[])

        async def complete_operation(self, messages, *, consumer_run_id):
            return await self.complete(messages)

    leader_model = GatedLeaderModel()
    worker_model = ScriptedModel(
        [
            _outcome_json("invalid", verification="other"),
            _outcome_json("corrected"),
        ],
        normalize_worker=False,
    )

    def model_factory(agent, _cycle_id=None):
        model = leader_model if agent.role == "leader" else worker_model
        if agent.role != "leader":
            model.operation_session_id = agent.upstream_session_id
        return model

    runtime = TeamRuntime(
        teams,
        model_factory,
    )
    running = asyncio.create_task(runtime.start(run.id, cycle.id))
    await asyncio.wait_for(review_started.wait(), timeout=2)

    task = teams.list_tasks(run.id, cycle.id)[0]
    assert task.status == "in_progress"
    assert teams.get_team_run(run.id).status == "running"
    assert teams.get_cycle(cycle.id).status == "running"

    release_review.set()
    completed = await running
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_acceptance_review_ask_user_defers_without_consuming_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    plan = '[{"title":"T","description":"D"}]'
    ask_user = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "scope",
                "question": "Which scope should be used?",
                "why_needed": "The Team cannot infer the requested scope.",
                "options": [
                    {
                        "id": "small",
                        "label": "Small",
                        "impact": "Limits the change.",
                    }
                ],
                "recommended_option_id": "small",
                "blocking_scope": "task",
            }
        }
    )
    delegated: list[str] = []
    original = teams.defer_task_for_user_decision

    def record_delegation(task_id, agent_id, decision):
        delegated.append(task_id)
        return original(task_id, agent_id, decision)

    monkeypatch.setattr(
        teams,
        "defer_task_for_user_decision",
        record_delegation,
    )
    runtime = TeamRuntime(
        teams,
        _factory_by_role(
            [plan, ask_user],
            ["invalid prose"],
            normalize_worker=False,
        ),
    )

    waiting = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert waiting.status == "waiting_for_user"
    assert delegated == [task.id]
    assert task.acceptance_recovery_attempts == 0
    assert task.status == "blocked"


@pytest.mark.asyncio
async def test_acceptance_review_fail_uses_stable_reason_code(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    fail = json.dumps(
        {
            "resolution": {
                "kind": "fail",
                "reason_code": "frozen_rule_conflict",
                "summary": "The task conflicts with frozen rules.",
            }
        }
    )
    runtime = TeamRuntime(
        teams,
        _factory_by_role(
            ['[{"title":"T","description":"D"}]', fail],
            ["invalid prose"],
            normalize_worker=False,
        ),
    )

    failed = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert failed.status == "failed"
    assert task.status == "failed"
    assert task.error_message == "frozen_rule_conflict"
    assert task.acceptance_recovery_attempts == 0


@pytest.mark.asyncio
async def test_nonrecoverable_acceptance_skips_lead_review(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(run.id, "T", "D")
    task, worker_agent = teams.start_task(task.id, worker_agent.id)
    model_calls = 0

    def model_factory(_agent):
        nonlocal model_calls
        model_calls += 1
        return FakeModel("unused")

    runtime = TeamRuntime(teams, model_factory)
    outcome = TaskOutcome(
        status="completed",
        summary="done",
        reason_code=None,
        deliverables=(),
        verifications=(),
    )
    acceptance = AcceptanceResult(
        accepted=False,
        status="blocked",
        reason_code="input_snapshot_modified",
        evidence={},
    )
    working_root = Path(run.working_root)

    recovered = await runtime._recover_task_outcome(
        run,
        leader_agent,
        worker_agent,
        task,
        outcome,
        acceptance,
        working_root,
        workspace_snapshot(working_root),
        None,
    )

    assert recovered == (teams.get_task(task.id), outcome, acceptance)
    assert model_calls == 0


@pytest.mark.asyncio
async def test_artifact_publication_failure_skips_lead_review(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    plan = json.dumps(
        [
            {
                "title": "T",
                "description": "D",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": ["docs/d3.md"],
                    "required_verifications": ["source-check"],
                },
            }
        ]
    )
    working_root = Path(run.working_root)

    class FileWorkerModel:
        async def complete(self, _messages):
            path = working_root / "docs" / "d3.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("content", encoding="utf-8")
            return ModelResponse(
                content=_outcome_json(
                    "done",
                    deliverables=[{"path": "docs/d3.md", "kind": "markdown"}],
                    verification="source-check",
                ),
                tool_calls=[],
            )

    class FailingPublisher:
        def publish(self, *_args) -> None:
            raise ArtifactPublicationError("artifact_publication_failed")

    leader_model = ScriptedModel([plan])
    runtime = TeamRuntime(
        teams,
        lambda agent: (
            leader_model if agent.role == "leader" else FileWorkerModel()
        ),
        artifact_publisher=FailingPublisher(),
    )

    failed = await runtime.start(run.id)

    assert failed.status == "failed"
    assert leader_model._calls == 1
    assert teams.list_tasks(run.id)[0].error_message == "artifact_publication_failed"


@pytest.mark.asyncio
async def test_malformed_acceptance_review_retries_json_without_consuming_attempt(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    leader_model = ScriptedModel(
        [
            '[{"title":"T","description":"D"}]',
            "not valid review JSON",
            _retry_review(),
            "completed",
        ]
    )
    worker_model = ScriptedModel(
        ["invalid prose", _outcome_json("corrected")],
        normalize_worker=False,
    )
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    completed = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert completed.status == "completed"
    assert task.acceptance_recovery_attempts == 1
    assert (
        "Return ONLY one valid acceptance review JSON object. "
        "No prose or code fences."
        in leader_model.messages[2][0]["content"]
    )


@pytest.mark.asyncio
async def test_acceptance_recovery_rejects_undeclared_path_left_on_disk(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    plan = json.dumps(
        [
            {
                "title": "T",
                "description": "D",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": [],
                    "required_verifications": ["source-check"],
                },
            }
        ]
    )
    working_root = Path(run.working_root)

    class LingeringFileWorker:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            path = working_root / "docs" / "d3.md"
            if self.calls == 1:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("extra", encoding="utf-8")
                deliverables = [{"path": "docs/d3.md", "kind": "markdown"}]
            else:
                deliverables = []
                if self.calls == 3:
                    path.unlink()
            return ModelResponse(
                content=_outcome_json(
                    f"submission {self.calls}",
                    deliverables=deliverables,
                    verification="source-check",
                ),
                tool_calls=[],
            )

    leader_model = ScriptedModel(
        [plan, _retry_review(), _retry_review(), "completed"]
    )
    worker_model = LingeringFileWorker()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    completed = await runtime.start(run.id)

    task = teams.list_tasks(run.id)[0]
    assert completed.status == "completed"
    assert task.acceptance_recovery_attempts == 2
    assert [m.kind for m in teams.list_messages(run.id)].count(
        "acceptance_review"
    ) == 2


@pytest.mark.asyncio
async def test_acceptance_recovery_resume_preserves_rejection_before_review_audit(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "T",
        "D",
        acceptance=TaskAcceptance((), (RequiredVerification("source-check"),)),
    )
    teams.set_run_status(run.id, "running")
    teams.set_agent_status(leader_agent.id, "running")
    task, worker_agent = teams.start_task(task.id, worker_agent.id)
    working_root = Path(run.working_root)
    rejected_path = working_root / "docs" / "d3.md"
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.write_text("extra", encoding="utf-8")
    teams.record_task_outcome(
        task.id,
        json.loads(
            _outcome_json(
                "rejected before review",
                deliverables=[{"path": "docs/d3.md", "kind": "markdown"}],
                verification="source-check",
            )
        ),
        {
            "accepted": False,
            "status": "failed",
            "reason_code": "undeclared_deliverable",
            "evidence": {},
        },
    )
    teams.interrupt_run(run.id)

    class CleanupWorker:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            if self.calls == 2:
                rejected_path.unlink()
            return ModelResponse(
                content=_outcome_json(
                    f"resumed submission {self.calls}",
                    verification="source-check",
                ),
                tool_calls=[],
                upstream_session_id=f"worker-{self.calls}",
            )

    leader_model = ScriptedModel(
        [_retry_review("Remove docs/d3.md before resubmitting."), "completed"]
    )
    worker_model = CleanupWorker()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    completed = await runtime.resume(run.id)

    resumed_task = teams.get_task(task.id)
    assert completed.status == "completed"
    assert resumed_task.status == "completed"
    assert resumed_task.acceptance_recovery_attempts == 1
    assert worker_model.calls == 2
    assert not rejected_path.exists()


@pytest.mark.asyncio
async def test_acceptance_recovery_ask_user_resume_preserves_rejected_path(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    plan = json.dumps(
        [
            {
                "title": "T",
                "description": "D",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": [],
                    "required_verifications": ["source-check"],
                },
            }
        ]
    )
    ask_user = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "scope",
                "question": "Should the extra draft be retained?",
                "why_needed": "The requested scope is ambiguous.",
                "options": [
                    {
                        "id": "remove",
                        "label": "Remove it",
                        "impact": "Keeps the original contract.",
                    }
                ],
                "recommended_option_id": "remove",
                "blocking_scope": "task",
            }
        }
    )
    working_root = Path(run.working_root)
    rejected_path = working_root / "docs" / "d3.md"

    class CleanupWorker:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            if self.calls == 1:
                rejected_path.parent.mkdir(parents=True, exist_ok=True)
                rejected_path.write_text("extra", encoding="utf-8")
                deliverables = [{"path": "docs/d3.md", "kind": "markdown"}]
            else:
                deliverables = []
                if self.calls == 3:
                    rejected_path.unlink()
            return ModelResponse(
                content=_outcome_json(
                    f"submission {self.calls}",
                    deliverables=deliverables,
                    verification="source-check",
                ),
                tool_calls=[],
                upstream_session_id=f"worker-{self.calls}",
            )

    leader_model = ScriptedModel(
        [
            plan,
            ask_user,
            _retry_review("Remove docs/d3.md before resubmitting."),
            "completed",
        ]
    )
    worker_model = CleanupWorker()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    waiting = await runtime.start(run.id)
    request = teams.get_active_decision_request(run.id)
    assert waiting.status == "waiting_for_user"
    assert request is not None

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "remove"},
    )
    completed = await runtime.resume(run.id)

    task = teams.list_tasks(run.id)[0]
    assert completed.status == "completed"
    assert task.status == "completed"
    assert task.acceptance_recovery_attempts == 1
    assert worker_model.calls == 3
    assert not rejected_path.exists()


def test_acceptance_recovery_does_not_follow_outside_symlink(
    tmp_path,
    monkeypatch,
) -> None:
    working_root = tmp_path / "workspace"
    working_root.mkdir()
    link = working_root / "docs" / "d3.md"
    link.parent.mkdir()
    link.write_text("link placeholder", encoding="utf-8")
    original_resolve = Path.resolve
    original_is_symlink = Path.is_symlink

    def guarded_resolve(path, *args, **kwargs):
        if path == link:
            raise AssertionError("outside symlink target was followed")
        return original_resolve(path, *args, **kwargs)

    def fake_is_symlink(path):
        return path == link or original_is_symlink(path)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    assert _bounded_path_exists(working_root, "docs/d3.md") is True


def test_safe_relative_output_rejects_windows_drive_relative_path(
    tmp_path,
) -> None:
    working_root = tmp_path / "workspace"
    working_root.mkdir()

    assert _safe_relative_output("D:foo") is False
    assert _bounded_path_exists(working_root, "D:foo") is True


def test_bounded_path_exists_treats_parent_path_as_unresolved(tmp_path) -> None:
    working_root = tmp_path / "workspace"
    working_root.mkdir()

    assert _bounded_path_exists(working_root, "../outside.md") is True


def test_bounded_path_exists_treats_resolution_error_as_unresolved(
    tmp_path,
    monkeypatch,
) -> None:
    working_root = tmp_path / "workspace"
    working_root.mkdir()
    candidate = working_root.resolve() / "docs" / "missing.md"
    original_resolve = Path.resolve

    def failing_candidate_resolve(path, *args, **kwargs):
        if path == candidate:
            raise OSError("simulated resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_candidate_resolve)

    assert _bounded_path_exists(working_root, "docs/missing.md") is True


def test_bounded_path_exists_preserves_definite_relative_path_states(
    tmp_path,
) -> None:
    working_root = tmp_path / "workspace"
    working_root.mkdir()
    existing = working_root / "docs" / "existing.md"
    existing.parent.mkdir()
    existing.write_text("content", encoding="utf-8")

    assert _bounded_path_exists(working_root, "docs/missing.md") is False
    assert _bounded_path_exists(working_root, "docs/existing.md") is True


@pytest.mark.asyncio
async def test_canceled_runtime_during_acceptance_review_settles_existing_path(
    tmp_path,
) -> None:
    import asyncio

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1
    )
    review_started = asyncio.Event()

    class HangingReviewLeader:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content=_complete_plan_fixture(
                        '[{"title":"T","description":"D"}]'
                    ),
                    tool_calls=[],
                )
            review_started.set()
            await asyncio.sleep(60)

    leader_model = HangingReviewLeader()
    worker_model = ScriptedModel(["invalid prose"], normalize_worker=False)
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else worker_model,
    )
    running = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(review_started.wait(), timeout=2)
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    task = teams.list_tasks(run.id)[0]
    assert teams.get_team_run(run.id).status == "canceled"
    assert task.status == "canceled"


@pytest.mark.parametrize(
    ("required_status", "optional_status", "expected"),
    [
        ("failed", "completed", "failed"),
        ("blocked", "completed", "blocked"),
        ("completed", "failed", "completed_with_failures"),
        ("completed", "blocked", "completed_with_failures"),
        ("completed", "completed", "completed"),
    ],
)
def test_terminal_status_respects_required_tasks(
    tmp_path,
    required_status,
    optional_status,
    expected,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    required = teams.create_task(
        run.id,
        "required",
        "D",
        required=True,
        acceptance=TaskAcceptance((), (RequiredVerification("review"),)),
    )
    optional = teams.create_task(
        run.id,
        "optional",
        "D",
        required=False,
        acceptance=TaskAcceptance((), (RequiredVerification("review"),)),
    )
    teams.set_task_status(required.id, required_status)
    teams.set_task_status(optional.id, optional_status)

    assert _terminal_status(teams.list_tasks(run.id)) == expected


def test_task_plan_requires_and_returns_immutable_acceptance() -> None:
    tasks = _parse_task_plan(
        json.dumps(
            [
                {
                    "title": "Create D3 guide",
                    "description": "Write the integrated guide.",
                    "owner_agent_id": "worker-1",
                    "required": True,
                    "acceptance": {
                        "required_outputs": ["outputs/d3-guide.md"],
                        "required_verifications": ["markdown-link-check"],
                    },
                }
            ]
        )
    )

    assert tasks == [
        {
            "title": "Create D3 guide",
            "description": "Write the integrated guide.",
            "owner_agent_id": "worker-1",
            "required": True,
            "acceptance": TaskAcceptance(
                required_outputs=("outputs/d3-guide.md",),
                required_verifications=(RequiredVerification("markdown-link-check"),),
            ),
        }
    ]


def test_acceptance_review_resolution_parses_worker_retry() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "retry_worker",
                    "instruction": "Remove the undeclared deliverable and resubmit.",
                    "reason": "The contract declares no output.",
                }
            }
        )
    )

    assert resolution.kind == "retry_worker"
    assert resolution.reason == "The contract declares no output."
    assert resolution.instruction == "Remove the undeclared deliverable and resubmit."
    assert resolution.acceptance is None
    assert resolution.decision is None
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_revised_acceptance() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "revise_acceptance",
                    "acceptance": {
                        "required_outputs": ["docs/knowledge/d3-review.md"],
                        "required_verifications": ["source-check"],
                    },
                    "instruction": "Resubmit the document under the revised contract.",
                    "reason": "The task goal requires a reusable draft.",
                }
            }
        )
    )

    assert resolution.kind == "revise_acceptance"
    assert resolution.reason == "The task goal requires a reusable draft."
    assert resolution.instruction == "Resubmit the document under the revised contract."
    assert resolution.acceptance == TaskAcceptance(
        required_outputs=("docs/knowledge/d3-review.md",),
        required_verifications=(RequiredVerification("source-check"),),
    )
    assert resolution.decision is None
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_user_question() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "ask_user",
                    "topic": "publication scope",
                    "question": "Should this be published?",
                    "why_needed": "The goal is ambiguous.",
                    "options": [],
                    "recommended_option_id": None,
                    "blocking_scope": "task",
                }
            }
        )
    )

    assert resolution.kind == "ask_user"
    assert resolution.reason == "The goal is ambiguous."
    assert resolution.instruction is None
    assert resolution.acceptance is None
    assert resolution.decision == {
        "kind": "ask_user",
        "topic": "publication scope",
        "question": "Should this be published?",
        "why_needed": "The goal is ambiguous.",
        "options": [],
        "recommended_option_id": None,
        "blocking_scope": "task",
    }
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_terminal_failure() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "fail",
                    "reason_code": "unrecoverable_contract",
                    "summary": "The request conflicts with frozen rules.",
                }
            }
        )
    )

    assert resolution.kind == "fail"
    assert resolution.reason == "The request conflicts with frozen rules."
    assert resolution.instruction is None
    assert resolution.acceptance is None
    assert resolution.decision is None
    assert resolution.reason_code == "unrecoverable_contract"


def _ask_user_review_resolution(**updates: object) -> dict[str, object]:
    resolution: dict[str, object] = {
        "kind": "ask_user",
        "topic": "publication scope",
        "question": "Should this be published?",
        "why_needed": "The goal is ambiguous.",
        "options": [
            {
                "id": "publish",
                "label": "Publish",
                "impact": "Makes the draft public.",
            }
        ],
        "recommended_option_id": "publish",
        "blocking_scope": "task",
    }
    resolution.update(updates)
    return resolution


def test_acceptance_review_resolution_rejects_unknown_outer_fields() -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(),
            "unexpected": "not allowed",
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "options",
    [None, {}, "not-a-list"],
)
def test_acceptance_review_resolution_rejects_non_list_user_options(
    options: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(options=options)}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "option",
    [
        "not-an-object",
        {"id": "publish", "label": "Publish"},
        {
            "id": "publish",
            "label": "Publish",
            "impact": "Public.",
            "unexpected": "not allowed",
        },
        {"id": "", "label": "Publish", "impact": "Public."},
        {"id": 1, "label": "Publish", "impact": "Public."},
        {"id": "publish", "label": "", "impact": "Public."},
        {"id": "publish", "label": None, "impact": "Public."},
        {"id": "publish", "label": "Publish", "impact": None},
    ],
)
def test_acceptance_review_resolution_rejects_malformed_user_option(
    option: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(options=[option])}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize("impact", ["", "   "])
def test_acceptance_review_resolution_rejects_blank_user_option_impact(
    impact: str,
) -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(
                options=[
                    {
                        "id": "publish",
                        "label": "Publish",
                        "impact": impact,
                    }
                ]
            )
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("topic", ""),
        ("topic", None),
        ("question", "   "),
        ("question", 1),
        ("why_needed", ""),
        ("why_needed", None),
    ],
)
def test_acceptance_review_resolution_rejects_invalid_user_text_fields(
    field: str,
    value: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(**{field: value})}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize("recommended", ["", "   ", 1, []])
def test_acceptance_review_resolution_rejects_invalid_recommended_option(
    recommended: object,
) -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(
                recommended_option_id=recommended
            )
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize("blocking_scope", ["cycle", "", None, 1])
def test_acceptance_review_resolution_rejects_invalid_blocking_scope(
    blocking_scope: object,
) -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(
                blocking_scope=blocking_scope
            )
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "resolution",
    [
        {"kind": "retry_worker", "instruction": "Retry.", "reason": ""},
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Missing contract.",
            "acceptance": {
                "required_outputs": [],
                "required_verifications": [],
            },
        },
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Duplicate output.",
            "acceptance": {
                "required_outputs": ["docs/review.md", "docs/review.md"],
                "required_verifications": [],
            },
        },
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Unsafe output.",
            "acceptance": {
                "required_outputs": ["../outside.md"],
                "required_verifications": [],
            },
        },
        {
            "kind": "ask_user",
            "topic": "scope",
            "question": "Publish it?",
            "why_needed": "The goal is ambiguous.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
            "unexpected": "not allowed",
        },
        {
            "kind": "ask_user",
            "topic": "scope",
            "question": "Publish it?",
            "why_needed": "",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
        {"kind": "approve", "reason": "No rejection remains."},
    ],
)
def test_acceptance_review_resolution_rejects_invalid_lead_decisions(
    resolution: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(json.dumps({"resolution": resolution}))


def test_task_plan_accepts_one_outer_json_fence() -> None:
    tasks = _parse_task_plan(
        """```json
[{
  "title": "Create D3 guide",
  "description": "Write the integrated guide.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": ["outputs/d3-guide.md"],
    "required_verifications": ["markdown-link-check"]
  }
}]
```"""
    )

    assert tasks[0]["title"] == "Create D3 guide"
    assert tasks[0]["acceptance"] == TaskAcceptance(
        required_outputs=("outputs/d3-guide.md",),
        required_verifications=(RequiredVerification("markdown-link-check"),),
    )


@pytest.mark.parametrize(
    "payload",
    [
        "before\n```json\n[]\n```",
        "```json\n[]\n```\nafter",
        "```JSON\n[]\n```",
        "```json\n[{\n```",
    ],
)
def test_task_plan_rejects_ambiguous_json_envelopes(payload: str) -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _parse_task_plan(payload)


@pytest.mark.parametrize(
    "task",
    [
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["C:/absolute.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["outputs/../secret.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["outputs/a.txt", "outputs/a.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": [""],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": ["pytest"],
            },
            "unexpected": True,
        },
    ],
)
def test_task_plan_rejects_incomplete_or_unsafe_acceptance(task) -> None:
    with pytest.raises(ValueError):
        _parse_task_plan(json.dumps([task]))


def test_cycle_objective_replaces_blank_triggered_run_goal(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace", cycle_service=cycles)
    leader = personas.create_persona("Lead", "Planning", "Plans", [], [])
    run = teams.create_team_run(
        "",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "manual-1",
        "Review the new release",
        previous_cycle_id=None,
    )
    cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id, "manual", request.source_id, request_id=request.id
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("[]"))

    assert runtime._goal_context(run, cycle.id) == "Review the new release"


def test_task_delta_keeps_cycle_id(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "Planning", "Plans", [], [])
    run = teams.create_team_run(
        "Plan",
        leader.id,
        [],
        "planning_only",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    task = teams.create_task(run.id, "Inspect", "Inspect dashboard", cycle_id=cycle.id)

    assert _task_delta(task)["cycle_id"] == cycle.id


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
async def test_planned_task_keeps_a_checked_verification(tmp_path):
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
            json.dumps(
                [
                    {
                        "title": "Define schema",
                        "description": "Add team tables",
                        "owner_agent_id": None,
                        "required": True,
                        "acceptance": {
                            "required_outputs": ["draft.md"],
                            "required_verifications": [
                                {
                                    "name": "marker",
                                    "check": {
                                        "type": "file_contains",
                                        "path": "draft.md",
                                        "value": "<library_draft>",
                                    },
                                }
                            ],
                        },
                    }
                ]
            )
        ),
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    stored_task = teams.list_tasks(run.id)[0]
    assert stored_task.acceptance.required_verifications == (
        RequiredVerification(
            "marker",
            VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
        ),
    )


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
async def test_runtime_failure_redacts_environment_secret_from_state_and_event(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    bus = EventBus()
    monkeypatch.setenv("OPENAI_API_KEY", "backend-secret")
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: ScriptedModel(
            [RuntimeError("backend leaked backend-secret")]
        ),
        event_bus=bus,
    )

    failed = await runtime.start(run.id)

    assert "backend-secret" not in (failed.error_message or "")
    assert "[redacted]" in (failed.error_message or "")
    assert "backend-secret" not in str(bus.recent())


@pytest.mark.asyncio
async def test_plan_and_execute_assigns_tasks_to_workers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    backend = personas.create_persona("Backend", "Development", "Builds APIs.", ["Build"], [])
    worker = personas.create_persona("QA Tester", "Quality", "Checks work.", ["Test"], [])
    run = teams.create_team_run(
        "Build teams", leader.id, [backend.id, worker.id], "plan_and_execute", 1
    )
    qa_agent = next(agent for agent in teams.list_agents(run.id) if agent.persona_id == worker.id)

    responses = iter([
        json.dumps([{
            "title": "Verify API",
            "description": "Check team run endpoints",
            "owner_agent_id": qa_agent.id,
        }]),
        "Verified API behavior. No files changed. Evidence: tests passed.",
        "Summary: API endpoints verified successfully.",
    ])
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(next(responses)))

    completed = await runtime.start(run.id)

    tasks = teams.list_tasks(run.id)
    messages = teams.list_messages(run.id)
    assert completed.status == "completed"
    assert tasks[0].status == "completed"
    assert tasks[0].owner_agent_id == qa_agent.id
    assert "Verified API behavior" in tasks[0].result
    assert any(message.kind == "agent_output" for message in messages)


@pytest.mark.asyncio
async def test_add_work_keeps_leader_selected_owner(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "Planning", "Plans", ["Assign"], [])
    frontend = personas.create_persona(
        "Frontend", "Frontend development", "Builds UI", ["Implement React UI"], []
    )
    database = personas.create_persona(
        "Database", "Database development", "Builds schema", ["Design schema"], []
    )
    run = teams.create_team_run(
        "Improve dashboard",
        leader.id,
        [frontend.id, database.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    frontend_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.persona_id == frontend.id
    )
    plan = json.dumps([{
        "title": "Build dashboard widget",
        "description": "Implement the React widget",
        "owner_agent_id": frontend_agent.id,
    }])
    runtime = TeamRuntime(teams, lambda _agent, _cycle_id=None: FakeModel(plan))

    tasks = await runtime.add_work(run.id, "Add a dashboard widget", cycle.id)

    assert tasks[0].owner_agent_id == frontend_agent.id


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

    plan = (
        '[{"title":"T1","description":"d1"},'
        '{"title":"T2","description":"d2","required":false}]'
    )
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
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
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
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')

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
async def test_user_decisions_batch_after_independent_tasks_and_resume_with_answers(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    bus = EventBus()
    plan = (
        '[{"title":"Deploy","description":"choose target"},'
        '{"title":"Notify","description":"choose audience"}]'
    )
    needs_target = (
        '```json\n{"needs_info":{"topic":"target","question":"Where deploy?"}}\n```'
    )
    needs_audience = (
        '```json\n{"needs_info":{"topic":"audience","question":"Who gets notified?"}}\n```'
    )
    ask_target = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "target",
                "question": "Where deploy?",
                "why_needed": "Changes configuration.",
                "options": [{"id": "staging", "label": "Staging", "impact": "Safer."}],
                "recommended_option_id": "staging",
                "blocking_scope": "task",
            }
        }
    )
    ask_audience = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "audience",
                "question": "Who gets notified?",
                "why_needed": "Changes recipients.",
                "options": [],
                "recommended_option_id": None,
                "blocking_scope": "task",
            }
        }
    )
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, ask_target, ask_audience, "All decisions applied."],
            [needs_target, needs_audience, "deployed to staging", "notified release team"],
        ),
        event_bus=bus,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.status == "awaiting_user"
    assert [item["id"] for item in request.items] == ["Q-001", "Q-002"]
    assert [task.status for task in teams.list_tasks(run.id)] == ["blocked", "blocked"]
    assert "team.run.input_requested" in [event["type"] for event in bus.recent()]
    assert "synthesis" not in [message.kind for message in teams.list_messages(run.id)]

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "staging", "Q-002": "release team"},
    )
    messages = teams.list_messages(run.id)
    query_ids = {message.content: message.id for message in messages if message.kind == "query"}
    user_answers = {
        message.metadata["query_id"]: message.content
        for message in messages
        if message.kind == "answer" and message.metadata.get("source") == "user_decision"
    }
    assert user_answers == {
        query_ids["Where deploy?"]: "staging",
        query_ids["Who gets notified?"]: "release team",
    }
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "All decisions applied."
    assert [task.result for task in teams.list_tasks(run.id)] == [
        "deployed to staging",
        "notified release team",
    ]


@pytest.mark.asyncio
async def test_leader_can_request_user_decision_during_planning_and_resume(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("Deploy the service", leader.id, [member.id], "plan_and_execute", 1)
    ask_environment = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "deployment environment",
                "question": "Deploy to staging or production?",
                "why_needed": "The target changes the execution plan.",
                "options": [
                    {"id": "staging", "label": "Staging", "impact": "Lower risk."},
                    {"id": "production", "label": "Production", "impact": "User-facing."},
                ],
                "recommended_option_id": "staging",
                "blocking_scope": "run",
            }
        }
    )
    plan = '[{"title":"Deploy staging","description":"Deploy to staging"}]'
    leader_model = ScriptedModel([ask_environment, plan, "Deployment completed."])
    worker_model = ScriptedModel(["deployed"])
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    assert teams.list_tasks(run.id) == []
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.items[0]["stage"] == "planning"
    assert request.items[0]["blocking_task_ids"] == []

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "staging"},
    )
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "Deployment completed."
    assert [task.title for task in teams.list_tasks(run.id)] == ["Deploy staging"]
    assert "Q: Deploy to staging or production?\nA: staging" in (
        leader_model.messages[1][0]["content"]
    )


@pytest.mark.asyncio
async def test_leader_can_request_user_decision_before_final_synthesis(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("Prepare release report", leader.id, [member.id], "plan_and_execute", 1)
    ask_detail = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "report detail",
                "question": "Should the final report include internal diagnostics?",
                "why_needed": "This changes the final report content.",
                "options": [
                    {"id": "omit", "label": "Omit", "impact": "Concise report."},
                    {"id": "include", "label": "Include", "impact": "More detail."},
                ],
                "recommended_option_id": "omit",
                "blocking_scope": "run",
            }
        }
    )
    leader_model = ScriptedModel([
        '[{"title":"Collect results","description":"Collect release results"}]',
        ask_detail,
        "Final report without internal diagnostics.",
    ])
    worker_model = ScriptedModel(["results collected"])
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.items[0]["stage"] == "synthesis"
    assert [task.status for task in teams.list_tasks(run.id)] == ["completed"]

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "omit"},
    )
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "Final report without internal diagnostics."
    assert [message.kind for message in teams.list_messages(run.id)].count("agent_output") == 1
    assert "Q: Should the final report include internal diagnostics?\nA: omit" in (
        leader_model.messages[2][0]["content"]
    )


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
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
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
    canceled_task = teams.list_tasks(run.id)[0]
    canceled_worker = [agent for agent in teams.list_agents(run.id) if agent.role == "member"][0]
    assert canceled_task.status == "canceled"
    assert canceled_task.owner_agent_id == canceled_worker.id
    assert canceled_worker.current_task_id is None


@pytest.mark.asyncio
async def test_runtime_publishes_task_and_agent_assignment_deltas(tmp_path):
    import asyncio
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"Visible task","description":"d"}]'
    started = asyncio.Event()
    release = asyncio.Event()

    class GatedWorkerModel:
        async def complete(self, _messages):
            started.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            return ModelResponse(
                content=_complete_worker_fixture("done"),
                tool_calls=[],
            )

    leader_model = ScriptedModel([plan, "summary"])
    bus = EventBus()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else GatedWorkerModel(),
        bus,
    )
    running = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(started.wait(), timeout=2)

    task = teams.list_tasks(run.id)[0]
    worker = [agent for agent in teams.list_agents(run.id) if agent.role == "member"][0]
    assert teams.get_team_run(run.id).status == "running"
    assert task.owner_agent_id == worker.id
    assert worker.current_task_id == task.id
    assigned = [event for event in bus.recent() if event["type"] == "team.task.updated"][-1]
    assert assigned["task"]["owner_agent_id"] == worker.id
    assert assigned["agent"]["current_task_id"] == task.id

    release.set()
    await running
    event_types = [event["type"] for event in bus.recent()]
    assert "team.run.executing" in event_types
    assert "team.run.summarizing" in event_types


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
                return ModelResponse(
                    content=_complete_worker_fixture("did it"),
                    tool_calls=[],
                )

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

    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
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


@pytest.mark.asyncio
async def test_continuous_cycle_with_fenced_plan_creates_tasks_and_resumes(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-fenced-plan")
    fenced_plan = """```json
[{
  "title": "Process request",
  "description": "Produce the requested result.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": [],
    "required_verifications": ["worker-result"]
  }
}]
```"""
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [fenced_plan, "cycle summary"],
            ["worker result"],
        ),
    )

    created = await runtime.add_work(run.id, "process request", cycle.id)
    completed = await runtime.resume(run.id, cycle.id)

    assert [task.title for task in created] == ["Process request"]
    assert completed.status == "completed"
    assert teams.get_cycle(cycle.id).status == "completed"


@pytest.mark.asyncio
async def test_continuous_run_executes_and_synthesizes_each_cycle_in_isolation(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    first_cycle = teams.create_cycle(run.id, "hook", "hook-run-1")
    second_cycle = teams.create_cycle(run.id, "hook", "hook-run-2")
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [
                '[{"title":"Mail 1","description":"d1"}]',
                "summary-1",
                '[{"title":"Mail 2","description":"d2"}]',
                "summary-2",
            ],
            ["result-1", "result-2"],
        ),
    )

    await runtime.add_work(run.id, "first mail", first_cycle.id)
    await runtime.resume(run.id, first_cycle.id)
    await runtime.add_work(run.id, "second mail", second_cycle.id)
    completed = await runtime.resume(run.id, second_cycle.id)

    assert completed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id, first_cycle.id)] == [
        "Mail 1"
    ]
    assert [task.title for task in teams.list_tasks(run.id, second_cycle.id)] == [
        "Mail 2"
    ]
    assert [
        message.content
        for message in teams.list_messages(run.id, first_cycle.id)
        if message.kind == "synthesis"
    ] == ["summary-1"]
    assert [
        message.content
        for message in teams.list_messages(run.id, second_cycle.id)
        if message.kind == "synthesis"
    ] == ["summary-2"]
    assert teams.get_cycle(first_cycle.id).summary == "summary-1"
    assert teams.get_cycle(second_cycle.id).summary == "summary-2"


@pytest.mark.asyncio
async def test_previous_cycle_summary_is_only_added_to_leader_instruction(
    tmp_path,
):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "client-1")
    leader_model = ScriptedModel(
        [
            '[{"title":"New work","description":"process the next item"}]',
            "done",
        ]
    )
    worker_model = ScriptedModel(["worker result"])
    runtime = TeamRuntime(
        teams,
        lambda agent, _cycle_id=None: (
            leader_model
            if agent.role == "leader"
            else worker_model
        ),
    )
    instruction = (
        "next work\n\nPREVIOUS CYCLE SUMMARY\nprevious result"
    )

    await runtime.add_work(run.id, instruction, cycle.id)
    await runtime.resume(run.id, cycle.id)

    assert "PREVIOUS CYCLE SUMMARY" in (
        leader_model.messages[0][0]["content"]
    )
    assert "PREVIOUS CYCLE SUMMARY" not in (
        worker_model.messages[0][0]["content"]
    )


@pytest.mark.asyncio
async def test_continuous_run_uses_cycle_round_budget_instead_of_run_total(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    exhausted_cycle = teams.create_cycle(
        run.id, "hook", "hook-run-1", rounds_budget=1
    )
    active_cycle = teams.create_cycle(
        run.id, "hook", "hook-run-2", rounds_budget=1
    )
    teams.increment_cycle_rounds_used(exhausted_cycle.id)
    teams.create_task(run.id, "Mail 2", "d", cycle_id=active_cycle.id)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            ['{"resolution":{"kind":"answer","answer":"continue"}}', "summary"],
            [
                '```json\n{"needs_info":{"topic":"scope","question":"Which?"}}\n```',
                "done",
            ],
        ),
    )

    completed = await runtime.resume(run.id, active_cycle.id)

    assert completed.status == "completed"
    assert teams.get_cycle(exhausted_cycle.id).rounds_used == 1
    assert teams.get_cycle(active_cycle.id).rounds_used == 1
    assert teams.get_team_run(run.id).rounds_used == 0
    assert teams.list_tasks(run.id, active_cycle.id)[0].result == "done"


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


@pytest.mark.asyncio
async def test_team_runtime_uses_archive_and_routes_knowledge_gap_to_library(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    archive = ArchiveService(db)
    leader = personas.create_persona("Lead", "Planning", "Plans work", [], [])
    worker = personas.create_persona("QA", "Quality", "Verifies releases", [], [])
    archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title="Release verification",
        summary="Checks required before a release.",
        content_markdown="Run the smoke suite and attach the test report.",
        tags=["release", "verification"],
        source_urls=[],
        persona_ids=[],
    )
    run = teams.create_team_run(
        "Verify the release",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.persona_id == worker.id
    )
    plan = json.dumps(
        [
            {
                "title": "Verify release",
                "description": "Use the release checklist.",
                "owner_agent_id": worker_agent.id,
            }
        ]
    )
    leader_model = ScriptedModel([plan, "Release verification completed."])
    worker_model = ScriptedModel(
        [
            (
                "Smoke suite passed."
                '<knowledge_request>{"title":"Rollback verification",'
                '"reason":"No reusable rollback check exists.",'
                '"suggested_outline":["Trigger rollback","Verify recovery"],'
                '"source_hints":["release runbook"]}</knowledge_request>'
            )
        ]
    )
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: (
            leader_model if agent.role == "leader" else worker_model
        ),
        archive_service=archive,
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    assert "Release verification" in worker_model.messages[0][0]["content"]
    task = teams.list_tasks(run.id)[0]
    assert "<knowledge_request>" not in (task.result or "")
    assert "Library에 요청되었습니다" in (task.result or "")
    requests = archive.list_requests()
    assert len(requests) == 1
    assert requests[0].requested_by_persona_id == worker.id
    assert requests[0].team_run_id == run.id
    assert len(archive.list_entries()) == 1
