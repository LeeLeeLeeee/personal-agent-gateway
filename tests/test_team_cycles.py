from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from team_cycle_helpers import dt, make_auto_run, make_triggered_run


def test_cycle_requests_are_idempotent_and_claimed_fifo(tmp_path: Path) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    first = cycles.enqueue_request(run.id, "manual", "client-1", "first", previous_cycle_id=None)
    duplicate = cycles.enqueue_request(
        run.id, "manual", "client-1", "ignored", previous_cycle_id=None
    )
    second = cycles.enqueue_request(run.id, "hook", "hook-run-1", "second", previous_cycle_id=None)

    assert duplicate.id == first.id
    assert cycles.claim_next(run.id).id == first.id
    assert cycles.claim_next(run.id) is None
    cycles.mark_request_settled(first.id)
    assert cycles.claim_next(run.id).id == second.id


def test_concurrent_enqueue_and_claim_keep_one_request_and_dispatcher(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)

    def enqueue():
        return cycles.enqueue_request(run.id, "manual", "client-1", "work", previous_cycle_id=None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = list(executor.map(lambda _: enqueue(), range(2)))

    assert requests[0].id == requests[1].id
    assert len(cycles.list_requests(run.id)) == 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: cycles.claim_next(run.id), range(2)))

    assert sum(claim is not None for claim in claims) == 1
    assert cycles.get_dispatching(run.id).id == requests[0].id


def test_auto_series_counts_continue_and_keeps_retry_in_same_slot(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series, first = cycles.create_auto_series(
        run.id,
        target_slots=2,
        interval_seconds=300,
        now=dt("2026-07-20T00:00:00+00:00"),
    )
    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(cycle.id, "failed", error_message="boom")

    paused = cycles.settle_cycle(cycle.id, now=dt("2026-07-20T00:01:00+00:00"))
    assert paused.series.status == "paused_failure"
    retry = cycles.retry_failed(run.id, series.id, now=dt("2026-07-20T00:02:00+00:00"))
    assert retry.slot_ordinal == 1
    assert retry.retry_of_request_id == first.id

    retry_cycle = teams.create_cycle(run.id, "retry", retry.source_id, request_id=retry.id)
    teams.set_cycle_status(retry_cycle.id, "failed", error_message="again")
    cycles.settle_cycle(retry_cycle.id, now=dt("2026-07-20T00:03:00+00:00"))
    continued = cycles.continue_failed(run.id, series.id, now=dt("2026-07-20T00:04:00+00:00"))
    assert continued.settled_slots == 1
    assert continued.status == "waiting_interval"
    assert continued.next_run_at == "2026-07-20T00:09:00+00:00"


@pytest.mark.parametrize("status", ["completed", "completed_with_failures"])
def test_completed_cycle_statuses_settle_auto_slot(tmp_path: Path, status: str) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series, request = cycles.create_auto_series(run.id, 1, 300)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, status, summary="done")

    settled = cycles.settle_cycle(cycle.id)

    assert settled.series.status == "auto_completed"
    assert settled.series.settled_slots == 1
    assert settled.request.status == "settled"


def test_request_policy_and_lineage_validation_snapshots_previous_cycle(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    previous = teams.create_cycle(run.id, "manual", "previous")
    teams.set_cycle_status(previous.id, "completed", summary="previous result")

    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "next",
        previous_cycle_id=previous.id,
    )

    assert request.previous_cycle_id == previous.id
    assert request.previous_summary_text == "previous result"
    assert cycles.latest_settled_cycle(run.id).id == previous.id
    with pytest.raises(ValueError, match="AUTO"):
        cycles.enqueue_request(run.id, "auto", "wrong-policy", "work", previous_cycle_id=None)

    db, teams, cycles, run = make_auto_run(tmp_path / "auto")
    with pytest.raises(ValueError, match="series"):
        cycles.enqueue_request(run.id, "auto", "missing-series", "work", previous_cycle_id=None)


def test_auto_due_slot_read_models_and_restart(tmp_path: Path) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series, first = cycles.create_auto_series(run.id, 2, 300, now=dt("2026-07-20T00:00:00+00:00"))

    assert cycles.get_active_series(run.id).id == series.id
    assert cycles.get_request(first.id) == first
    assert cycles.list_requests(run.id) == [first]
    assert cycles.count_queued(run.id) == 1
    assert cycles.queue_position(first.id) == 1
    assert cycles.policy_status(run.id) == "queued"
    assert cycles.list_runnable_team_run_ids() == [run.id]

    claimed = cycles.claim_next(run.id, now=dt("2026-07-20T00:00:01+00:00"))
    assert cycles.get_dispatching(run.id) == claimed
    assert cycles.list_dispatching_requests() == [claimed]
    first_cycle = teams.create_cycle(run.id, "auto", claimed.source_id, request_id=claimed.id)
    teams.set_cycle_status(first_cycle.id, "completed", summary="one")
    cycles.settle_cycle(first_cycle.id, now=dt("2026-07-20T00:01:00+00:00"))

    assert cycles.enqueue_due_auto_requests(now=dt("2026-07-20T00:05:59+00:00")) == []
    due = cycles.enqueue_due_auto_requests(now=dt("2026-07-20T00:06:00+00:00"))
    assert [request.slot_ordinal for request in due] == [2]
    assert cycles.enqueue_due_auto_requests(now=dt("2026-07-20T00:06:00+00:00")) == []

    second = cycles.claim_next(run.id)
    second_cycle = teams.create_cycle(run.id, "auto", second.source_id, request_id=second.id)
    teams.set_cycle_status(second_cycle.id, "completed", summary="two")
    cycles.settle_cycle(second_cycle.id)

    assert cycles.get_active_series(run.id) is None
    assert cycles.policy_status(run.id) == "auto_completed"
    restarted, restarted_request = cycles.restart_series(run.id)
    assert restarted.series_number == 2
    assert restarted.target_slots == 2
    assert restarted_request.slot_ordinal == 1


def test_pause_and_reconcile_preserve_or_requeue_dispatching_request(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path / "auto")
    series, request = cycles.create_auto_series(run.id, 1, 300)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "waiting_for_user")

    paused = cycles.pause_for_user(cycle.id)

    assert paused.series.status == "paused_user"
    assert paused.request.status == "dispatching"
    assert cycles.policy_status(run.id) == "paused_user"

    db, teams, cycles, run = make_triggered_run(tmp_path / "triggered")
    request = cycles.enqueue_request(run.id, "manual", "client-1", "work", previous_cycle_id=None)
    cycles.claim_next(run.id)

    assert cycles.reconcile(teams) == [run.id]
    assert cycles.get_request(request.id).status == "queued"
    assert cycles.requeue_claim(cycles.claim_next(run.id).id).status == "queued"

    db, teams, cycles, run = make_auto_run(tmp_path / "interrupted")
    series, request = cycles.create_auto_series(run.id, 1, 300)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "interrupted")

    paused = cycles.pause_interrupted(cycle.id)

    assert paused.series.status == "paused_interrupted"
    assert paused.request.status == "dispatching"
