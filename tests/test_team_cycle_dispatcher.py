import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.run_state import TeamRunRegistry
from personal_agent_gateway.team_cycle_dispatcher import TeamCycleDispatcher
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.teams import TeamRun, TeamRunService
from team_cycle_helpers import RecordingOrchestrator, make_cycle_services


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
    dispatcher = TeamCycleDispatcher(cycles, teams, orchestrator, event_bus)
    return DispatcherServices(
        run,
        teams,
        cycles,
        orchestrator,
        event_bus,
        dispatcher,
    )


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

    events = event_bus.recent()
    assert expected_event in [event["type"] for event in events]
    settled = next(
        event
        for event in events
        if event["type"] == "team.cycle.settled"
    )
    assert settled["policy_status"] == expected_policy_status
