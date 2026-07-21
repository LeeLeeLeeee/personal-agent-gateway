from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_cycle_dispatcher import TeamCycleDispatcher
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.teams import TeamRunService
from team_cycle_helpers import RecordingOrchestrator, dt, make_cycle_services


@dataclass(frozen=True)
class ReopenedServices:
    teams: TeamRunService
    cycles: TeamCycleService
    dispatcher: TeamCycleDispatcher


def reopen_services(tmp_path: Path) -> ReopenedServices:
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
    dispatcher = TeamCycleDispatcher(
        cycles,
        teams,
        RecordingOrchestrator(teams),
        EventBus(),
    )
    return ReopenedServices(teams, cycles, dispatcher)


@pytest.mark.parametrize(
    ("cycle_status", "run_status", "series_status", "expected_policy_status"),
    [
        ("running", "running", "running", "paused_interrupted"),
        ("waiting_for_user", "waiting_for_user", "paused_user", "paused_user"),
    ],
)
def test_reconcile_preserves_blocking_cycle_without_dispatching_next(
    tmp_path: Path,
    cycle_status: str,
    run_status: str,
    series_status: str,
    expected_policy_status: str,
) -> None:
    db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    series = cycles.get_active_series(run.id)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        "auto",
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, cycle_status)
    teams.set_run_status(run.id, run_status)
    db.execute(
        "update team_run_auto_series set status = ? where id = ?",
        (series_status, series.id),
    )

    restarted = reopen_services(tmp_path)
    restarted.teams.interrupt_active_runs()
    first = restarted.dispatcher.reconcile()
    second = restarted.dispatcher.reconcile()

    recovered_cycle = restarted.teams.get_cycle(cycle.id)
    recovered_series = restarted.cycles.get_active_series(run.id)
    assert first == []
    assert second == []
    assert restarted.cycles.policy_status(run.id) == expected_policy_status
    assert restarted.cycles.get_request(request.id).status == "dispatching"
    assert restarted.cycles.claim_next(run.id) is None
    assert recovered_series.paused_cycle_id == cycle.id
    assert recovered_cycle.status == (
        "interrupted" if cycle_status == "running" else "waiting_for_user"
    )
    assert len(restarted.teams.list_cycles(run.id)) == 1


@pytest.mark.parametrize(
    ("cycle_status", "expected_request_status"),
    [
        ("completed", "settled"),
        ("completed_with_failures", "settled"),
        ("failed", "settled"),
        ("canceled", "canceled"),
    ],
)
def test_terminal_cycle_with_dispatching_request_settles_once_after_reopen(
    tmp_path: Path,
    cycle_status: str,
    expected_request_status: str,
) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, cycle_status)

    first = reopen_services(tmp_path)
    assert first.dispatcher.reconcile() == []
    second = reopen_services(tmp_path)
    assert second.dispatcher.reconcile() == []

    assert second.cycles.get_request(request.id).status == expected_request_status
    assert len(second.cycles.list_requests(run.id)) == 1
    assert len(second.teams.list_cycles(run.id)) == 1
    assert second.teams.get_cycle_for_request(request.id).id == cycle.id


def test_dispatching_request_without_cycle_requeues_once_after_reopen(
    tmp_path: Path,
) -> None:
    _db, _teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "work",
        previous_cycle_id=None,
    )
    request = cycles.claim_next(run.id)

    first = reopen_services(tmp_path)
    assert first.dispatcher.reconcile() == [run.id]
    second = reopen_services(tmp_path)
    assert second.dispatcher.reconcile() == [run.id]

    recovered = second.cycles.get_request(request.id)
    assert recovered.status == "queued"
    assert recovered.claimed_at is None
    assert len(second.cycles.list_requests(run.id)) == 1
    assert second.teams.list_cycles(run.id) == []


def test_reconcile_enqueues_due_and_existing_queued_requests_once(
    tmp_path: Path,
) -> None:
    db, teams, cycles, auto_run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    series = cycles.get_active_series(auto_run.id)
    first_request = cycles.claim_next(auto_run.id)
    first_cycle = teams.create_cycle(
        auto_run.id,
        "auto",
        first_request.source_id,
        request_id=first_request.id,
    )
    teams.set_cycle_status(first_cycle.id, "completed", summary="done")
    cycles.settle_cycle(
        first_cycle.id,
        now=dt("2026-07-20T00:05:00+00:00"),
    )
    db.execute(
        "update team_run_auto_series set next_run_at = ? where id = ?",
        ("2026-07-20T00:00:00+00:00", series.id),
    )

    _db, _teams, triggered_cycles, triggered_run = make_cycle_services(
        tmp_path,
        "triggered",
    )
    queued = triggered_cycles.enqueue_request(
        triggered_run.id,
        "manual",
        "client-queued",
        "queued work",
        previous_cycle_id=None,
        now=dt("2026-07-20T00:06:00+00:00"),
    )

    first = reopen_services(tmp_path)
    first_run_ids = first.cycles.reconcile(
        first.teams,
        now=dt("2026-07-20T00:10:00+00:00"),
    )
    second = reopen_services(tmp_path)
    second_run_ids = second.cycles.reconcile(
        second.teams,
        now=dt("2026-07-20T00:10:00+00:00"),
    )

    assert set(first_run_ids) == {auto_run.id, triggered_run.id}
    assert set(second_run_ids) == {auto_run.id, triggered_run.id}
    auto_requests = second.cycles.list_requests(auto_run.id)
    assert sorted(item.slot_ordinal for item in auto_requests) == [1, 2]
    assert len({item.source_id for item in auto_requests}) == 2
    assert second.cycles.list_requests(triggered_run.id) == [queued]
    assert len(second.teams.list_cycles(auto_run.id)) == 1


def test_failure_recovery_preserves_retry_and_continue_ownership(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, retry_run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    retry_series = cycles.get_active_series(retry_run.id)
    retry_request = cycles.claim_next(retry_run.id)
    retry_cycle = teams.create_cycle(
        retry_run.id,
        "auto",
        retry_request.source_id,
        request_id=retry_request.id,
    )
    teams.set_cycle_status(retry_cycle.id, "failed", error_message="retry me")

    _db, teams, cycles, continue_run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    continue_series = cycles.get_active_series(continue_run.id)
    continue_request = cycles.claim_next(continue_run.id)
    continue_cycle = teams.create_cycle(
        continue_run.id,
        "auto",
        continue_request.source_id,
        request_id=continue_request.id,
    )
    teams.set_cycle_status(
        continue_cycle.id,
        "failed",
        error_message="continue me",
    )

    restarted = reopen_services(tmp_path)
    assert restarted.dispatcher.reconcile() == []
    assert restarted.cycles.get_active_series(retry_run.id).paused_cycle_id == retry_cycle.id
    assert restarted.cycles.get_active_series(continue_run.id).paused_cycle_id == continue_cycle.id

    with pytest.raises(ValueError, match="different team run"):
        restarted.cycles.retry_failed(retry_run.id, continue_series.id)

    retried = restarted.cycles.retry_failed(retry_run.id, retry_series.id)
    continued = restarted.cycles.continue_failed(
        continue_run.id,
        continue_series.id,
        now=dt("2026-07-20T00:10:00+00:00"),
    )

    assert retried.retry_of_request_id == retry_request.id
    assert retried.slot_ordinal == retry_request.slot_ordinal
    assert retried.auto_series_id == retry_series.id
    assert continued.settled_slots == 1
    assert continued.status == "waiting_interval"
    assert restarted.teams.get_cycle(retry_cycle.id).status == "failed"
    assert restarted.teams.get_cycle(continue_cycle.id).status == "failed"


def test_recovery_preserves_hook_manual_fifo_and_source_idempotency(
    tmp_path: Path,
) -> None:
    _db, _teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    created_at = dt("2026-07-20T00:00:00+00:00")
    manual = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "manual work",
        previous_cycle_id=None,
        now=created_at,
    )
    hook = cycles.enqueue_request(
        run.id,
        "hook",
        "hook-run-1",
        "hook work",
        previous_cycle_id=None,
        now=created_at,
    )

    restarted = reopen_services(tmp_path)
    duplicate_manual = restarted.cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "changed manual work",
        previous_cycle_id=None,
        now=created_at,
    )
    duplicate_hook = restarted.cycles.enqueue_request(
        run.id,
        "hook",
        "hook-run-1",
        "changed hook work",
        previous_cycle_id=None,
        now=created_at,
    )

    assert restarted.dispatcher.reconcile() == [run.id]
    assert duplicate_manual == manual
    assert duplicate_hook == hook
    assert restarted.cycles.list_requests(run.id) == [manual, hook]
    assert restarted.cycles.claim_next(run.id).id == manual.id

    manual_cycle = restarted.teams.create_cycle(
        run.id,
        manual.source_type,
        manual.source_id,
        request_id=manual.id,
    )
    restarted.teams.set_cycle_status(manual_cycle.id, "completed", summary="done")
    restarted.cycles.settle_cycle(manual_cycle.id)

    assert restarted.cycles.claim_next(run.id).id == hook.id
