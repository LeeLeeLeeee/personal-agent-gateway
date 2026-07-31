import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.lmg_client import ProviderExecutionCapabilities
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.run_state import TeamRunRegistry
from personal_agent_gateway.team_cycle_dispatcher import TeamCycleDispatcher
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_provider_recovery import (
    OperationReconcileResult,
    ProviderOperationWaiting,
    ProviderRecoveryRequired,
    TeamProviderRecovery,
)
from personal_agent_gateway.team_model_invoker import AmbiguousModelOperation
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRun, TeamRunService
from team_cycle_helpers import RecordingOrchestrator, dt, make_cycle_services


@dataclass
class DispatcherServices:
    run: TeamRun
    teams: TeamRunService
    cycles: TeamCycleService
    orchestrator: RecordingOrchestrator
    event_bus: EventBus
    dispatcher: TeamCycleDispatcher


def make_dispatcher_services(tmp_path: Path) -> DispatcherServices:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    orchestrator = RecordingOrchestrator(teams)
    event_bus = EventBus()
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        event_bus,
        provider_recovery=_provider_recovery(teams),
    )
    return DispatcherServices(
        run,
        teams,
        cycles,
        orchestrator,
        event_bus,
        dispatcher,
    )


def _dispatching_cycle(teams, cycles, run):
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "provider-freeze",
        "work",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None and claimed.id == request.id
    return teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )


def _execution_capability():
    return ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified", "denied", "required"),
        sandbox_modes=("read-only", "workspace-write"),
        permission_modes=(),
    )


class _FrozenRegistry:
    def get(self, provider):
        return SimpleNamespace(
            ready=True,
            readiness_error=None,
            snapshot_status="fresh",
            detected_at="2026-07-30T00:00:00Z",
            execution_capabilities=_execution_capability(),
        )


def _provider_recovery(teams):
    return TeamProviderRecovery(teams, _FrozenRegistry())


def test_dispatcher_requires_provider_recovery(tmp_path):
    _db, teams, cycles, _run = make_cycle_services(tmp_path, "triggered")

    with pytest.raises(TypeError, match="provider_recovery"):
        TeamCycleDispatcher(
            cycles,
            teams,
            RecordingOrchestrator(teams),
            EventBus(),
        )


def test_freeze_cycle_persists_every_roster_provider(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    worker = next(
        agent for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    db.execute(
        "update team_agents set backend = 'claude' where id = ?",
        (worker.id,),
    )
    cycle = _dispatching_cycle(teams, cycles, run)
    registry = _FrozenRegistry()
    recovery = TeamProviderRecovery(teams, registry)

    frozen = recovery.freeze_cycle(cycle.id)

    snapshots = frozen.execution_metadata["provider_capabilities"]
    assert set(snapshots) == {"codex", "claude"}
    assert snapshots["codex"]["execution"]["network_modes"] == [
        "unspecified", "denied", "required"
    ]
    assert snapshots["claude"]["snapshot_status"] == "fresh"


def test_freeze_cycle_rejects_missing_roster_capability_without_partial_snapshot(
    tmp_path,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    worker = next(
        agent for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    db.execute(
        "update team_agents set backend = 'claude' where id = ?",
        (worker.id,),
    )
    cycle = _dispatching_cycle(teams, cycles, run)
    registry = _FrozenRegistry()
    registry.get = lambda provider: SimpleNamespace(
        ready=False,
        readiness_error="capabilities_unavailable",
        snapshot_status="unavailable",
        detected_at="",
        execution_capabilities=(
            None if provider == "claude" else _execution_capability()
        ),
    )
    recovery = TeamProviderRecovery(teams, registry)

    with pytest.raises(ProviderRecoveryRequired) as error:
        recovery.freeze_cycle(cycle.id)

    assert error.value.provider == "claude"
    assert error.value.reason_code == "capabilities_unavailable"
    assert teams.get_cycle(cycle.id).execution_metadata is None


def test_freeze_cycle_normalizes_absent_registry_provider(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = _dispatching_cycle(teams, cycles, run)

    def missing_provider(provider):
        raise ValueError(f"Unknown agent: {provider}")

    registry = SimpleNamespace(get=missing_provider)
    recovery = TeamProviderRecovery(teams, registry)

    with pytest.raises(ProviderRecoveryRequired) as error:
        recovery.freeze_cycle(cycle.id)

    assert error.value.provider == "codex"
    assert error.value.reason_code == "capabilities_unavailable"
    assert teams.get_cycle(cycle.id).execution_metadata is None


@pytest.mark.asyncio
async def test_dispatcher_persists_preparer_replacement_as_semantic_source(
    tmp_path,
):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    orchestrator = RecordingOrchestrator(teams)
    recovery = TeamProviderRecovery(teams, _FrozenRegistry())
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        EventBus(),
        provider_recovery=recovery,
    )

    async def assert_frozen(_request, cycle):
        snapshots = cycle.execution_metadata["provider_capabilities"]
        assert set(snapshots) == {"codex"}
        worker = next(
            agent
            for agent in teams.list_agents(run.id)
            if agent.id != run.leader_agent_id
        )
        teams.set_cycle_agent_execution_metadata(
            cycle.id,
            worker.id,
            {"provider": "codex"},
        )
        return "prepared work"

    dispatcher.add_preparer(assert_frozen)
    cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )

    await dispatcher.run_one(run.id)

    assert len(orchestrator.calls) == 1
    cycle = teams.get_cycle_for_source("manual", "client-1")
    assert cycle is not None
    assert teams.get_cycle_effective_instruction(cycle.id) == "prepared work"
    metadata = teams.get_cycle(cycle.id).execution_metadata or {}
    assert "provider_capabilities" in metadata
    assert len(metadata["agents"]) == 1


@pytest.mark.asyncio
async def test_dispatcher_runs_fifo_and_passes_previous_summary_to_leader_only(
    tmp_path: Path,
) -> None:
    services = make_dispatcher_services(tmp_path)
    previous = services.teams.create_cycle(
        services.run.id,
        "manual",
        "old",
    )
    services.teams.set_cycle_status(
        previous.id,
        "completed",
        summary="previous result",
    )
    first = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "next work",
        previous_cycle_id=previous.id,
    )
    second = services.cycles.enqueue_request(
        services.run.id,
        "hook",
        "hook-1",
        "hook work",
        previous_cycle_id=previous.id,
    )

    await services.dispatcher.run_one(services.run.id)

    call = services.orchestrator.calls[0]
    assert call[0] == services.run.id
    assert call[2] == (
        "next work\n\nPREVIOUS CYCLE SUMMARY\nprevious result"
    )
    assert services.cycles.get_request(second.id).status == "queued"

    first_cycle = services.teams.get_cycle_for_request(first.id)
    assert first_cycle is not None
    assert services.teams.get_cycle_effective_instruction(first_cycle.id) == (
        "next work\n\nPREVIOUS CYCLE SUMMARY\nprevious result"
    )
    services.teams.set_cycle_status(
        first_cycle.id,
        "completed",
        summary="first done",
    )
    await services.dispatcher.on_team_run_settled(
        services.run,
        first_cycle.id,
    )
    await services.dispatcher.run_one(services.run.id)

    assert services.orchestrator.calls[1][2].startswith("hook work")
    assert services.cycles.get_request(second.id).status == "dispatching"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["waiting_for_user", "interrupted"])
async def test_nonterminal_cycle_keeps_request_until_same_cycle_completes(
    tmp_path: Path,
    status: str,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    await services.dispatcher.run_one(services.run.id)
    cycle = services.teams.get_cycle_for_request(request.id)
    assert cycle is not None

    services.teams.set_cycle_status(cycle.id, status)
    await services.dispatcher.on_team_run_settled(
        services.run,
        cycle.id,
    )

    assert services.cycles.get_request(request.id).status == "dispatching"
    assert "team.cycle.settled" not in {
        event["type"] for event in services.event_bus.recent()
    }

    services.teams.set_cycle_status(
        cycle.id,
        "completed",
        summary="answered",
    )
    await services.dispatcher.on_team_run_settled(
        services.run,
        cycle.id,
    )

    assert services.cycles.get_request(request.id).status == "settled"


@pytest.mark.asyncio
async def test_cancellation_in_preparer_interrupts_same_cycle_and_request(
    tmp_path: Path,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    entered = asyncio.Event()

    async def gated_preparer(*_args):
        entered.set()
        await asyncio.Event().wait()

    services.dispatcher.add_preparer(gated_preparer)
    task = asyncio.create_task(services.dispatcher.run_one(services.run.id))
    await asyncio.wait_for(entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cycle = services.teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "interrupted"
    assert services.cycles.get_request(request.id).status == "dispatching"
    assert "team.cycle.settled" not in {
        event["type"] for event in services.event_bus.recent()
    }


@pytest.mark.asyncio
async def test_noninterrupting_stop_leaves_active_lineage_for_persistent_cancel(
    tmp_path: Path,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    entered = asyncio.Event()

    async def gated_preparer(*_args):
        entered.set()
        await asyncio.Event().wait()

    services.dispatcher.add_preparer(gated_preparer)
    await services.dispatcher.start()
    await services.dispatcher.enqueue_run(services.run.id)
    await asyncio.wait_for(entered.wait(), timeout=1)

    await services.dispatcher.stop(interrupt_active=False)

    cycle = services.teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "queued"
    assert services.cycles.get_request(request.id).status == "dispatching"
    services.cycles.cancel_run(services.run.id, reason="emergency_stop")
    assert services.teams.get_cycle(cycle.id).status == "canceled"
    assert services.cycles.get_request(request.id).status == "canceled"


@pytest.mark.asyncio
async def test_cancellation_during_real_leader_add_work_preserves_cycle(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    entered = asyncio.Event()

    class GatedLeaderModel:
        async def complete(self, _messages):
            entered.set()
            await asyncio.Event().wait()
            return ModelResponse(content="[]", tool_calls=[])

        async def complete_operation(self, messages, *, consumer_run_id):
            return await self.complete(messages)

    runtime = TeamRuntime(
        teams,
        lambda _agent, _cycle_id=None: GatedLeaderModel(),
    )
    orchestrator = TeamRunOrchestrator(TeamRunRegistry(), lambda: runtime)
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        EventBus(),
        provider_recovery=_provider_recovery(teams),
    )
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    task = asyncio.create_task(dispatcher.run_one(run.id))
    await asyncio.wait_for(entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cycle = teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "interrupted"
    assert cycles.get_request(request.id).status == "dispatching"
    assert teams.list_tasks(run.id, cycle.id) == []


def test_reconcile_interrupts_linked_queued_cycle_for_explicit_resume(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=1,
    )
    request = cycles.claim_next(run.id)
    assert request is not None
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        RecordingOrchestrator(teams),
        EventBus(),
        provider_recovery=_provider_recovery(teams),
    )

    assert dispatcher.reconcile() == []

    assert teams.get_cycle(cycle.id).status == "interrupted"
    assert cycles.get_request(request.id).status == "dispatching"
    assert cycles.get_active_series(run.id).status == "paused_interrupted"


@pytest.mark.asyncio
async def test_cycle_creation_failure_requeues_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )

    def fail_create_cycle(*_args, **_kwargs):
        raise RuntimeError("create failed")

    monkeypatch.setattr(
        services.teams,
        "create_cycle",
        fail_create_cycle,
    )

    with pytest.raises(RuntimeError, match="create failed"):
        await services.dispatcher.run_one(services.run.id)

    assert services.cycles.get_request(request.id).status == "queued"
    assert services.teams.get_cycle_for_request(request.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_source", ["preparer", "orchestrator"])
async def test_dispatch_failure_marks_cycle_failed_and_settles_request(
    tmp_path: Path,
    failure_source: str,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )

    if failure_source == "preparer":

        async def fail_preparer(*_args):
            raise RuntimeError("prepare failed")

        services.dispatcher.add_preparer(fail_preparer)
    else:

        async def fail_run_cycle(*_args):
            raise RuntimeError("orchestration failed")

        services.orchestrator.run_cycle = fail_run_cycle

    await services.dispatcher.run_one(services.run.id)

    cycle = services.teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "failed"
    assert services.cycles.get_request(request.id).status == "settled"
    event_types = [
        event["type"]
        for event in services.event_bus.recent()
    ]
    assert "team.cycle.settled" in event_types
    assert (
        "team.cycle.started" in event_types
    ) is (failure_source == "orchestrator")


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["waiting", "ambiguous"])
async def test_operation_markers_preserve_persisted_cycle_state(
    tmp_path: Path,
    marker: str,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        f"marker-{marker}",
        "work",
        previous_cycle_id=None,
    )

    async def raise_persisted_marker(team_run_id, cycle_id, _instruction):
        if marker == "waiting":
            services.teams.mark_waiting_for_provider(
                cycle_id,
                provider="codex",
                reason_code="provider_unavailable",
                attempts=3,
                task_id=None,
                agent_id=None,
                now=dt("2026-07-31T00:00:00+00:00"),
            )
            raise ProviderOperationWaiting("operation-1")
        services.teams.set_run_status(team_run_id, "interrupted")
        services.teams.set_cycle_status(cycle_id, "interrupted")
        raise AmbiguousModelOperation(
            "operation-1",
            "consumer-1",
            "run_timeout",
        )

    services.orchestrator.run_cycle = raise_persisted_marker

    await services.dispatcher.run_one(services.run.id)

    cycle = services.teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == (
        "waiting_for_provider" if marker == "waiting" else "interrupted"
    )
    assert services.cycles.get_request(request.id).status == "dispatching"


@pytest.mark.asyncio
async def test_waiting_cycle_observer_does_not_settle(tmp_path: Path) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "observer-waiting",
        "work",
        previous_cycle_id=None,
    )
    await services.dispatcher.run_one(services.run.id)
    cycle = services.teams.get_cycle_for_request(request.id)
    services.teams.mark_waiting_for_provider(
        cycle.id,
        provider="codex",
        reason_code="provider_unavailable",
        attempts=3,
        task_id=None,
        agent_id=None,
        now=dt("2026-07-31T00:00:00+00:00"),
    )

    await services.dispatcher.on_team_run_settled(
        services.teams.get_team_run(services.run.id),
        cycle.id,
    )

    assert services.cycles.get_request(request.id).status == "dispatching"


@pytest.mark.asyncio
async def test_startup_reconciliation_schedules_only_runnable_operations(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = _dispatching_cycle(teams, cycles, run)
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")

    class Recovery:
        def freeze_cycle(self, cycle_id):
            return teams.get_cycle(cycle_id)

        def reconcile_startup(self):
            return OperationReconcileResult((cycle.id,), (), ("ambiguous",))

        def get_open_operation(self, cycle_id):
            assert cycle_id == cycle.id
            return SimpleNamespace(
                stage="cycle_add_work",
                stage_ordinal=0,
            )

    class Orchestrator:
        def __init__(self):
            self.resume_calls = []
            self.continue_calls = []

        def resume(self, team_run_id, cycle_id=None):
            self.resume_calls.append((team_run_id, cycle_id))

        def continue_cycle(self, team_run_id, cycle_id, instruction):
            self.continue_calls.append(
                (team_run_id, cycle_id, instruction)
            )

    orchestrator = Orchestrator()
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        EventBus(),
        provider_recovery=Recovery(),
    )

    assert dispatcher.reconcile() == []
    await dispatcher.start()
    try:
        assert orchestrator.resume_calls == []
        assert orchestrator.continue_calls == [
            (run.id, cycle.id, "work")
        ]
        assert teams.get_cycle(cycle.id).status == "running"
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_real_orchestrator_notifies_dispatcher_observer_before_return(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    event_bus = EventBus()

    class CompletingRuntime:
        async def add_work(
            self,
            _team_run_id: str,
            _instruction: str,
            cycle_id: str | None = None,
        ) -> list[object]:
            assert cycle_id is not None
            teams.set_cycle_status(cycle_id, "running")
            return []

        async def resume(
            self,
            team_run_id: str,
            cycle_id: str | None = None,
        ) -> TeamRun:
            assert cycle_id is not None
            teams.set_cycle_status(
                cycle_id,
                "completed",
                summary="done",
            )
            return teams.get_team_run(team_run_id)

    orchestrator = TeamRunOrchestrator(
        TeamRunRegistry(),
        CompletingRuntime,
    )
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        event_bus,
        provider_recovery=_provider_recovery(teams),
    )
    orchestrator.add_observer(dispatcher.on_team_run_settled)
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )

    await dispatcher.run_one(run.id)

    assert cycles.get_request(request.id).status == "settled"
    assert [
        event["type"]
        for event in event_bus.recent()
    ] == [
        "team.cycle.started",
        "team.cycle.settled",
    ]


@pytest.mark.asyncio
async def test_duplicate_terminal_callback_emits_and_wakes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = make_dispatcher_services(tmp_path)
    first = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "first",
        previous_cycle_id=None,
    )
    second = services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-2",
        "second",
        previous_cycle_id=None,
    )
    await services.dispatcher.run_one(services.run.id)
    cycle = services.teams.get_cycle_for_request(first.id)
    assert cycle is not None
    services.teams.set_cycle_status(cycle.id, "completed", summary="done")
    enqueued: list[str] = []

    async def record_enqueue(team_run_id: str) -> None:
        enqueued.append(team_run_id)

    monkeypatch.setattr(services.dispatcher, "enqueue_run", record_enqueue)

    await services.dispatcher.on_team_run_settled(services.run, cycle.id)
    await services.dispatcher.on_team_run_settled(services.run, cycle.id)

    assert [
        event["type"] for event in services.event_bus.recent()
    ].count("team.cycle.settled") == 1
    assert enqueued == [services.run.id]
    assert services.cycles.get_request(first.id).status == "settled"
    assert services.cycles.get_request(second.id).status == "queued"


@pytest.mark.asyncio
async def test_later_observer_error_preserves_dispatcher_settlement(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=1,
    )
    event_bus = EventBus()

    class CompletingRuntime:
        async def add_work(
            self,
            _team_run_id: str,
            _instruction: str,
            cycle_id: str | None = None,
        ) -> list[object]:
            assert cycle_id is not None
            teams.set_cycle_status(cycle_id, "running")
            return []

        async def resume(
            self,
            team_run_id: str,
            cycle_id: str | None = None,
        ) -> TeamRun:
            assert cycle_id is not None
            teams.set_cycle_status(cycle_id, "completed", summary="done")
            return teams.get_team_run(team_run_id)

    orchestrator = TeamRunOrchestrator(
        TeamRunRegistry(),
        CompletingRuntime,
    )
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        event_bus,
        provider_recovery=_provider_recovery(teams),
    )
    orchestrator.add_observer(dispatcher.on_team_run_settled)

    async def fail_later_observer(_run, _cycle_id):
        raise RuntimeError("later observer failed")

    orchestrator.add_observer(fail_later_observer)
    request = cycles.list_requests(run.id)[0]

    with pytest.raises(RuntimeError, match="later observer failed"):
        await dispatcher.run_one(run.id)

    cycle = teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "completed"
    assert cycles.get_request(request.id).status == "settled"
    assert cycles.policy_status(run.id) == "auto_completed"
    assert [
        event["type"] for event in event_bus.recent()
    ].count("team.cycle.settled") == 1


@pytest.mark.asyncio
async def test_earlier_observer_error_settles_terminal_dispatching_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    event_bus = EventBus()

    class CompletingRuntime:
        async def add_work(
            self,
            _team_run_id: str,
            _instruction: str,
            cycle_id: str | None = None,
        ) -> list[object]:
            assert cycle_id is not None
            teams.set_cycle_status(cycle_id, "running")
            return []

        async def resume(
            self,
            team_run_id: str,
            cycle_id: str | None = None,
        ) -> TeamRun:
            assert cycle_id is not None
            teams.set_cycle_status(cycle_id, "completed", summary="done")
            return teams.get_team_run(team_run_id)

    orchestrator = TeamRunOrchestrator(
        TeamRunRegistry(),
        CompletingRuntime,
    )
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        event_bus,
        provider_recovery=_provider_recovery(teams),
    )
    enqueued: list[str] = []

    async def record_enqueue(team_run_id: str) -> None:
        enqueued.append(team_run_id)

    monkeypatch.setattr(dispatcher, "enqueue_run", record_enqueue)

    async def fail_earlier_observer(_run, _cycle_id):
        raise RuntimeError("earlier observer failed")

    orchestrator.add_observer(fail_earlier_observer)
    orchestrator.add_observer(dispatcher.on_team_run_settled)
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "first",
        previous_cycle_id=None,
    )
    queued = cycles.enqueue_request(
        run.id,
        "manual",
        "client-2",
        "second",
        previous_cycle_id=None,
    )

    with pytest.raises(RuntimeError, match="earlier observer failed"):
        await dispatcher.run_one(run.id)

    cycle = teams.get_cycle_for_request(request.id)
    assert cycle is not None
    assert cycle.status == "completed"
    assert cycles.get_request(request.id).status == "settled"
    assert cycles.get_request(queued.id).status == "queued"
    assert enqueued == [run.id]
    assert [
        event["type"] for event in event_bus.recent()
    ].count("team.cycle.settled") == 1


@pytest.mark.asyncio
async def test_dispatcher_lifecycle_reports_redacted_loop_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = make_dispatcher_services(tmp_path)
    services.cycles.enqueue_request(
        services.run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    attempted = asyncio.Event()
    monkeypatch.setenv("OPENAI_API_KEY", "dispatcher-secret")

    def fail_create_cycle(*_args, **_kwargs):
        attempted.set()
        raise RuntimeError("leaked dispatcher-secret")

    monkeypatch.setattr(
        services.teams,
        "create_cycle",
        fail_create_cycle,
    )

    await services.dispatcher.start()
    await services.dispatcher.enqueue_run(services.run.id)
    await asyncio.wait_for(attempted.wait(), timeout=1)
    await asyncio.sleep(0)

    assert services.dispatcher.alive is True
    assert services.dispatcher.last_error == "leaked [redacted]"

    await services.dispatcher.stop()

    assert services.dispatcher.alive is False
    assert services.dispatcher.last_error == "leaked [redacted]"


@pytest.mark.asyncio
async def test_child_run_cancellation_keeps_dispatcher_alive_for_another_run(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, first_run = make_cycle_services(tmp_path, "triggered")
    agents = teams.list_agents(first_run.id)
    second_run = teams.create_team_run(
        "second goal",
        agents[0].persona_id,
        [agents[1].persona_id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    registry = TeamRunRegistry()
    first_started = asyncio.Event()
    second_completed = asyncio.Event()

    class Runtime:
        async def add_work(self, team_run_id, _instruction, cycle_id=None):
            teams.set_cycle_status(cycle_id, "running")
            return []

        async def resume(self, team_run_id, cycle_id=None):
            if team_run_id == first_run.id:
                first_started.set()
                await asyncio.Event().wait()
            teams.set_cycle_status(cycle_id, "completed", summary="done")
            second_completed.set()
            return teams.get_team_run(team_run_id)

    orchestrator = TeamRunOrchestrator(registry, Runtime)
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        EventBus(),
        provider_recovery=_provider_recovery(teams),
    )
    orchestrator.add_observer(dispatcher.on_team_run_settled)
    cycles.enqueue_request(
        first_run.id, "manual", "first", "work", previous_cycle_id=None
    )
    second_request = cycles.enqueue_request(
        second_run.id, "manual", "second", "work", previous_cycle_id=None
    )

    await dispatcher.start()
    await dispatcher.enqueue_run(first_run.id)
    await asyncio.wait_for(first_started.wait(), timeout=1)
    assert await registry.cancel_and_wait(first_run.id) is True
    await asyncio.sleep(0)

    assert dispatcher.alive is True
    await dispatcher.enqueue_run(second_run.id)
    await asyncio.wait_for(second_completed.wait(), timeout=1)
    await dispatcher._queue.join()
    assert cycles.get_request(second_request.id).status == "settled"

    await dispatcher.stop()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cycle_status", "expected_event", "expected_policy_status"),
    [
        ("failed", "team.auto_series.paused", "paused_failure"),
        ("completed", "team.auto_series.completed", "auto_completed"),
    ],
)
async def test_auto_settlement_publishes_series_event(
    tmp_path: Path,
    cycle_status: str,
    expected_event: str,
    expected_policy_status: str,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=1,
    )
    orchestrator = RecordingOrchestrator(teams)
    event_bus = EventBus()
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        orchestrator,
        event_bus,
        provider_recovery=_provider_recovery(teams),
    )

    await dispatcher.run_one(run.id)
    request = cycles.list_requests(run.id)[0]
    cycle = teams.get_cycle_for_request(request.id)
    assert cycle is not None
    teams.set_cycle_status(
        cycle.id,
        cycle_status,
        summary="done" if cycle_status == "completed" else None,
        error_message="boom" if cycle_status == "failed" else None,
    )

    await dispatcher.on_team_run_settled(run, cycle.id)
    await dispatcher.on_team_run_settled(run, cycle.id)

    events = event_bus.recent()
    event_types = [event["type"] for event in events]
    assert event_types.count(expected_event) == 1
    assert event_types.count("team.cycle.settled") == 1
    started = next(
        event
        for event in events
        if event["type"] == "team.cycle.started"
    )
    assert {
        key: started[key]
        for key in (
            "team_run_id",
            "request_id",
            "cycle_id",
            "series_id",
            "slot_ordinal",
        )
    } == {
        "team_run_id": run.id,
        "request_id": request.id,
        "cycle_id": cycle.id,
        "series_id": request.auto_series_id,
        "slot_ordinal": 1,
    }
    settled = next(
        event
        for event in events
        if event["type"] == "team.cycle.settled"
    )
    assert {
        key: settled[key]
        for key in (
            "team_run_id",
            "request_id",
            "cycle_id",
            "series_id",
            "slot_ordinal",
            "status",
        )
    } == {
        "team_run_id": run.id,
        "request_id": request.id,
        "cycle_id": cycle.id,
        "series_id": request.auto_series_id,
        "slot_ordinal": 1,
        "status": cycle_status,
    }
    assert settled["duration_seconds"] >= 0
    series_event = next(
        event for event in events if event["type"] == expected_event
    )
    if expected_event == "team.auto_series.paused":
        assert series_event["reason"] == "boom"
        assert series_event["available_actions"] == ["retry", "continue"]
    else:
        assert series_event["settled_slots"] == 1
        assert series_event["target_slots"] == 1
    assert cycles.policy_status(run.id) == expected_policy_status
