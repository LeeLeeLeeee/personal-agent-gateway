from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
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


def reopen_app(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        AppConfig(
            workspace_root=workspace,
            session_dir=tmp_path / "sessions",
            app_db_path=tmp_path / "app.db",
            openai_api_key="test-key",
        )
    )


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
    recovered_requests = restarted.cycles.list_requests(run.id)
    recovered_cycles = restarted.teams.list_cycles(run.id)
    assert first == []
    assert second == []
    assert restarted.cycles.policy_status(run.id) == expected_policy_status
    assert restarted.cycles.get_request(request.id).status == "dispatching"
    assert restarted.cycles.claim_next(run.id) is None
    assert len(recovered_requests) == 1
    assert len(recovered_cycles) == 1
    assert recovered_requests[0].id == request.id
    assert recovered_requests[0].team_run_id == run.id
    assert recovered_requests[0].auto_series_id == series.id
    assert recovered_requests[0].slot_ordinal == 1
    assert recovered_requests[0].source_type == "auto"
    assert recovered_cycles[0].id == cycle.id
    assert recovered_cycles[0].request_id == request.id
    assert recovered_cycles[0].team_run_id == run.id
    assert recovered_series.id == series.id
    assert recovered_series.team_run_id == run.id
    assert recovered_series.paused_cycle_id == cycle.id
    assert recovered_cycle.status == (
        "interrupted" if cycle_status == "running" else "waiting_for_user"
    )
    assert len(restarted.teams.list_cycles(run.id)) == 1


def test_auto_terminal_reconcile_is_idempotent_across_fresh_reopens(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
        auto_interval_seconds=300,
    )
    series = cycles.get_active_series(run.id)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, "completed", summary="slot one")

    first = reopen_services(tmp_path)
    assert (
        first.cycles.reconcile(
            first.teams,
            now=dt("2026-07-20T00:01:00+00:00"),
        )
        == []
    )
    first_request = first.cycles.get_request(request.id)
    first_series = first.cycles.get_active_series(run.id)
    first_snapshot = (
        first_request.status,
        first_series.settled_slots,
        first_series.status,
        first_series.next_run_at,
    )

    second = reopen_services(tmp_path)
    assert (
        second.cycles.reconcile(
            second.teams,
            now=dt("2026-07-20T00:02:00+00:00"),
        )
        == []
    )
    second_request = second.cycles.get_request(request.id)
    second_series = second.cycles.get_active_series(run.id)

    assert first_snapshot == (
        "settled",
        1,
        "waiting_interval",
        "2026-07-20T00:06:00+00:00",
    )
    assert (
        second_request.status,
        second_series.settled_slots,
        second_series.status,
        second_series.next_run_at,
    ) == first_snapshot
    assert second_request.team_run_id == run.id
    assert second_request.auto_series_id == series.id
    assert second_request.slot_ordinal == 1
    assert second.teams.get_cycle(cycle.id).request_id == request.id
    assert len(second.cycles.list_requests(run.id)) == 1
    assert len(second.teams.list_cycles(run.id)) == 1


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
    before_counts = {
        retry_run.id: (
            len(cycles.list_requests(retry_run.id)),
            len(teams.list_cycles(retry_run.id)),
        ),
        continue_run.id: (
            len(cycles.list_requests(continue_run.id)),
            len(teams.list_cycles(continue_run.id)),
        ),
    }

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

    assert before_counts == {
        retry_run.id: (1, 1),
        continue_run.id: (1, 1),
    }
    assert len(restarted.cycles.list_requests(retry_run.id)) == 2
    assert len(restarted.teams.list_cycles(retry_run.id)) == 1
    assert len(restarted.cycles.list_requests(continue_run.id)) == 1
    assert len(restarted.teams.list_cycles(continue_run.id)) == 1
    assert retried.team_run_id == retry_run.id
    assert retried.source_type == "retry"
    assert retried.source_id == f"{retry_series.id}:1:2"
    assert retried.retry_of_request_id == retry_request.id
    assert retried.slot_ordinal == retry_request.slot_ordinal
    assert retried.auto_series_id == retry_series.id
    assert continued.id == continue_series.id
    assert continued.team_run_id == continue_run.id
    assert continued.settled_slots == 1
    assert continued.status == "waiting_interval"
    assert continued.next_run_at == "2026-07-20T00:15:00+00:00"
    assert restarted.teams.get_cycle(retry_cycle.id).status == "failed"
    assert restarted.teams.get_cycle(continue_cycle.id).status == "failed"

    due = reopen_services(tmp_path)
    due_run_ids = due.cycles.reconcile(
        due.teams,
        now=dt("2026-07-20T00:15:00+00:00"),
    )
    continue_requests = due.cycles.list_requests(continue_run.id)
    next_slot = next(item for item in continue_requests if item.slot_ordinal == 2)

    assert set(due_run_ids) == {retry_run.id, continue_run.id}
    assert len(continue_requests) == 2
    assert len(due.teams.list_cycles(continue_run.id)) == 1
    assert next_slot.team_run_id == continue_run.id
    assert next_slot.auto_series_id == continue_series.id
    assert next_slot.source_type == "auto"
    assert next_slot.source_id == f"{continue_series.id}:2:1"
    assert next_slot.retry_of_request_id is None
    assert next_slot.status == "queued"
    due_series = due.cycles.get_active_series(continue_run.id)
    assert due_series.id == continue_series.id
    assert due_series.team_run_id == continue_run.id
    assert due_series.settled_slots == 1
    assert due_series.status == "running"


def test_interrupted_resume_targets_same_cycle_after_fresh_reopen(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    series = cycles.get_active_series(run.id)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")

    app = reopen_app(tmp_path)
    resume_calls: list[tuple[str, str | None]] = []
    app.state.team_run_orchestrator.resume = lambda team_run_id, cycle_id=None: resume_calls.append(
        (team_run_id, cycle_id)
    )

    with TestClient(app) as client:
        client.cookies.set(
            "agent_session",
            app.state.auth_session_service.issue().token,
        )
        assert app.state.team_cycle_service.policy_status(run.id) == "paused_interrupted"

        response = client.post(f"/api/team-runs/{run.id}/resume")

        assert response.status_code == 200
        assert resume_calls == [(run.id, cycle.id)]
        recovered_request = app.state.team_cycle_service.get_request(request.id)
        recovered_cycle = app.state.team_run_service.get_cycle(cycle.id)
        recovered_series = app.state.team_cycle_service.get_active_series(run.id)
        assert recovered_request.status == "dispatching"
        assert recovered_request.auto_series_id == series.id
        assert recovered_request.slot_ordinal == 1
        assert recovered_cycle.status == "interrupted"
        assert recovered_cycle.request_id == request.id
        assert recovered_series.paused_cycle_id == cycle.id
        assert len(app.state.team_cycle_service.list_requests(run.id)) == 1
        assert len(app.state.team_run_service.list_cycles(run.id)) == 1


def test_waiting_user_answer_targets_same_cycle_after_fresh_reopen(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=2,
    )
    series = cycles.get_active_series(run.id)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id,
        request.source_type,
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(cycle.id, "waiting_for_user")
    teams.defer_run_for_user_decision(
        run.id,
        {
            "topic": "scope",
            "question": "Which scope?",
            "why_needed": "Changes execution.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "run",
        },
        stage="planning",
        cycle_id=cycle.id,
    )
    decision = teams.publish_decision_request(run.id, cycle.id)
    cycles.pause_for_user(cycle.id)

    app = reopen_app(tmp_path)
    resume_calls: list[tuple[str, str | None]] = []
    app.state.team_run_orchestrator.resume = lambda team_run_id, cycle_id=None: resume_calls.append(
        (team_run_id, cycle_id)
    )

    with TestClient(app) as client:
        client.cookies.set(
            "agent_session",
            app.state.auth_session_service.issue().token,
        )
        assert app.state.team_cycle_service.policy_status(run.id) == "paused_user"

        response = client.post(
            f"/api/team-runs/{run.id}/decision-request/answer",
            json={
                "request_id": decision.id,
                "revision": decision.revision,
                "answers": {"Q-001": "backend only"},
            },
        )

        assert response.status_code == 200
        assert response.json()["decision_request"]["status"] == "resolved"
        assert resume_calls == [(run.id, cycle.id)]
        recovered_request = app.state.team_cycle_service.get_request(request.id)
        recovered_cycle = app.state.team_run_service.get_cycle(cycle.id)
        recovered_series = app.state.team_cycle_service.get_active_series(run.id)
        assert recovered_request.status == "dispatching"
        assert recovered_request.auto_series_id == series.id
        assert recovered_request.slot_ordinal == 1
        assert recovered_cycle.status == "interrupted"
        assert recovered_cycle.request_id == request.id
        assert recovered_series.paused_cycle_id == cycle.id
        assert len(app.state.team_cycle_service.list_requests(run.id)) == 1
        assert len(app.state.team_run_service.list_cycles(run.id)) == 1


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
