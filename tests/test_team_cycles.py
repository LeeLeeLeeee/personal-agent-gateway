from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from team_cycle_helpers import (
    dt,
    make_auto_run,
    make_cycle_services,
    make_triggered_run,
    seed_next_cycle_proposal,
)


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


def test_knowledge_request_uses_triggered_cycle_policy(tmp_path: Path) -> None:
    _db, _teams, cycles, run = make_triggered_run(tmp_path)

    request = cycles.enqueue_knowledge_request(
        run.id,
        "request-1",
        "Research and draft the requested document",
        previous_cycle_id=None,
    )

    assert request.source_type == "knowledge_request"
    assert request.source_id == "request-1"


def test_cycle_copies_its_request_input_artifact_snapshot(tmp_path: Path) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    artifact = ArtifactStore(db, tmp_path / "artifacts").register_bytes(
        "markdown",
        "d3-curriculum-draft.md",
        "previous/d3-curriculum-draft.md",
        b"draft",
        "text/markdown",
    )
    request = cycles.enqueue_knowledge_request(
        run.id,
        "request-1",
        "Research and draft the requested document",
        previous_cycle_id=None,
    )
    cycles.set_request_input_artifacts(request.id, [artifact.id])

    claimed = cycles.claim_next(run.id)
    assert claimed is not None
    cycle = teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )

    inputs = teams.list_cycle_input_artifacts(cycle.id)
    assert [item.artifact_id for item in inputs] == [artifact.id]


def test_knowledge_request_retry_uses_a_new_cycle_source(tmp_path: Path) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    first = cycles.enqueue_knowledge_request(
        run.id,
        "request-1",
        "Research and draft the requested document",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None
    first_cycle = teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )
    teams.set_cycle_status(first_cycle.id, "completed", summary="invalid draft")
    cycles.settle_cycle(first_cycle.id)

    retry = cycles.enqueue_knowledge_request(
        run.id,
        "request-1",
        "Research and draft the requested document",
        previous_cycle_id=first_cycle.id,
    )
    duplicate = cycles.enqueue_knowledge_request(
        run.id,
        "request-1",
        "ignored",
        previous_cycle_id=first_cycle.id,
    )
    claimed_retry = cycles.claim_next(run.id)
    assert claimed_retry is not None
    retry_cycle = teams.create_cycle(
        run.id,
        claimed_retry.source_type,
        claimed_retry.source_id,
        request_id=claimed_retry.id,
    )

    assert retry.id != first.id
    assert retry.source_id == "request-1#attempt-2"
    assert duplicate.id == retry.id
    assert retry_cycle.source_id == retry.source_id


def test_cancel_run_atomically_settles_waiting_auto_lineage_and_blocks_work(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_auto_run(tmp_path)
    request = cycles.claim_next(run.id)
    assert request is not None
    cycle = teams.create_cycle(
        run.id, request.source_type, request.source_id, request_id=request.id
    )
    teams.set_cycle_status(cycle.id, "waiting_for_user")
    cycles.pause_for_user(cycle.id)

    result = cycles.cancel_run(run.id, reason="user")
    repeated = cycles.cancel_run(run.id, reason="user")

    assert result.changed is True
    assert repeated.changed is False
    assert teams.get_team_run(run.id).status == "canceled"
    assert teams.get_cycle(cycle.id).status == "canceled"
    assert cycles.get_request(request.id).status == "canceled"
    assert result.series is not None
    assert result.series.status == "canceled"
    assert cycles.get_dispatching(run.id) is None
    assert cycles.count_queued(run.id) == 0
    with pytest.raises(ValueError, match="canceled"):
        cycles.enqueue_request(
            run.id, "auto", "late", "work", previous_cycle_id=None,
            auto_series_id=result.series.id, slot_ordinal=1,
        )
    with pytest.raises(ValueError, match="canceled"):
        cycles.claim_next(run.id)


@pytest.mark.parametrize(
    "terminal_status",
    ["completed", "completed_with_failures", "failed", "canceled"],
)
def test_cancel_run_preserves_terminal_continuous_run_status(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    request = cycles.enqueue_request(
        run.id, "manual", "queued", "work", previous_cycle_id=None
    )
    teams.set_run_status(run.id, terminal_status)

    result = cycles.cancel_run(run.id, reason="user")

    assert result.changed is True
    assert teams.get_team_run(run.id).status == terminal_status
    assert cycles.get_request(request.id).status == "canceled"


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


def test_equal_created_at_keeps_persistent_insertion_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    request_ids = iter(["z-request", "a-request"])
    monkeypatch.setattr(
        "personal_agent_gateway.team_cycles.uuid4",
        lambda: SimpleNamespace(hex=next(request_ids)),
    )
    now = dt("2026-07-20T00:00:00+00:00")

    first = cycles.enqueue_request(
        run.id,
        "manual",
        "client-1",
        "first",
        previous_cycle_id=None,
        now=now,
    )
    second = cycles.enqueue_request(
        run.id,
        "hook",
        "hook-1",
        "second",
        previous_cycle_id=None,
        now=now,
    )

    assert [request.id for request in cycles.list_requests(run.id)] == [
        first.id,
        second.id,
    ]
    assert cycles.queue_position(first.id) == 1
    assert cycles.queue_position(second.id) == 2
    assert cycles.claim_next(run.id, now=now).id == first.id


def test_auto_series_counts_continue_and_keeps_retry_in_same_slot(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series = cycles.get_active_series(run.id)
    initial = cycles.list_requests(run.id)[0]
    first = cycles.claim_next(run.id)
    assert first.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(cycle.id, "failed", error_message="boom")

    paused = cycles.settle_cycle(cycle.id, now=dt("2026-07-20T00:01:00+00:00"))
    assert paused.series.status == "paused_failure"
    retry = cycles.retry_failed(run.id, series.id, now=dt("2026-07-20T00:02:00+00:00"))
    assert retry.slot_ordinal == 1
    assert retry.retry_of_request_id == first.id

    retry = cycles.claim_next(run.id)
    retry_cycle = teams.create_cycle(run.id, "retry", retry.source_id, request_id=retry.id)
    teams.set_cycle_status(retry_cycle.id, "failed", error_message="again")
    cycles.settle_cycle(retry_cycle.id, now=dt("2026-07-20T00:03:00+00:00"))
    continued = cycles.continue_failed(run.id, series.id, now=dt("2026-07-20T00:04:00+00:00"))
    assert continued.settled_slots == 1
    assert continued.status == "waiting_interval"
    assert continued.next_run_at == "2026-07-20T00:09:00+00:00"


def test_blocked_auto_cycle_pauses_series_as_failure(tmp_path: Path) -> None:
    _db, teams, cycles, run = make_auto_run(tmp_path)
    request = cycles.claim_next(run.id)
    assert request is not None
    cycle = teams.create_cycle(
        run.id,
        "auto",
        request.source_id,
        request_id=request.id,
    )
    teams.set_cycle_status(
        cycle.id,
        "blocked",
        error_message="Required task blocked",
    )

    settled = cycles.settle_cycle(cycle.id)

    assert settled.request.status == "settled"
    assert settled.series is not None
    assert settled.series.status == "paused_failure"
    assert settled.series.pause_reason == "Required task blocked"


def test_retry_preserves_failed_slots_previous_cycle_snapshot(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series = cycles.get_active_series(run.id)
    initial = cycles.list_requests(run.id)[0]
    first = cycles.claim_next(run.id)
    assert first.id == initial.id
    first_cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(first_cycle.id, "completed", summary="slot one snapshot")
    leader = teams.get_agent(run.leader_agent_id)
    seed_next_cycle_proposal(db, run, first_cycle, leader, "continue")
    cycles.settle_cycle(first_cycle.id, now=dt("2026-07-20T00:01:00+00:00"))
    second = cycles.enqueue_due_auto_requests(now=dt("2026-07-20T00:06:00+00:00"))[0]
    assert second.previous_cycle_id == first_cycle.id
    assert second.previous_summary_text == (
        "STATUS: COMPLETED\n\nSUMMARY\nslot one snapshot"
    )

    second = cycles.claim_next(run.id)
    second_cycle = teams.create_cycle(run.id, "auto", second.source_id, request_id=second.id)
    teams.set_cycle_status(second_cycle.id, "failed", error_message="boom")
    cycles.settle_cycle(second_cycle.id, now=dt("2026-07-20T00:07:00+00:00"))
    teams.set_cycle_status(
        first_cycle.id,
        "completed",
        summary="newer text that must not replace the snapshot",
    )

    retry = cycles.retry_failed(run.id, series.id, now=dt("2026-07-20T00:08:00+00:00"))

    assert retry.previous_cycle_id == second.previous_cycle_id
    assert retry.previous_summary_text == second.previous_summary_text


def test_auto_continue_passes_failed_cycle_context_to_next_slot(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_auto_run(tmp_path)
    series = cycles.get_active_series(run.id)
    first = cycles.claim_next(run.id)
    assert first is not None
    failed_cycle = teams.create_cycle(
        run.id,
        "auto",
        first.source_id,
        request_id=first.id,
    )
    teams.set_cycle_status(
        failed_cycle.id,
        "failed",
        error_message="Required task failed",
    )
    leader = teams.get_agent(run.leader_agent_id)
    seed_next_cycle_proposal(_db, run, failed_cycle, leader, "continue")
    cycles.settle_cycle(failed_cycle.id, now=dt("2026-07-20T00:01:00+00:00"))
    cycles.continue_failed(run.id, series.id, now=dt("2026-07-20T00:02:00+00:00"))

    next_request = cycles.enqueue_due_auto_requests(
        now=dt("2026-07-20T00:07:00+00:00")
    )[0]

    assert next_request.previous_cycle_id == failed_cycle.id
    assert next_request.previous_summary_text == (
        "STATUS: FAILED\n\nERROR\nRequired task failed"
    )


@pytest.mark.parametrize("status", ["completed", "completed_with_failures"])
def test_completed_cycle_statuses_settle_auto_slot(tmp_path: Path, status: str) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path, target_slots=1)
    initial = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, status, summary="done")

    settled = cycles.settle_cycle(cycle.id)

    assert settled.series.status == "auto_completed"
    assert settled.series.settled_slots == 1
    assert settled.request.status == "settled"


def test_terminal_cycle_cannot_first_settle_a_queued_request(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path, target_slots=1)
    initial = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "completed", summary="done")
    db.execute(
        "update team_cycle_requests set status = 'queued' where id = ?",
        (request.id,),
    )

    with pytest.raises(ValueError, match="dispatching"):
        cycles.settle_cycle(cycle.id)


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
    assert request.previous_summary_text == (
        "STATUS: COMPLETED\n\nSUMMARY\nprevious result"
    )
    assert cycles.latest_final_cycle(run.id).id == previous.id
    with pytest.raises(ValueError, match="AUTO"):
        cycles.enqueue_request(run.id, "auto", "wrong-policy", "work", previous_cycle_id=None)

    db, teams, cycles, run = make_auto_run(tmp_path / "auto")
    with pytest.raises(ValueError, match="series"):
        cycles.enqueue_request(run.id, "auto", "missing-series", "work", previous_cycle_id=None)


@pytest.mark.parametrize(
    "status",
    ["completed", "completed_with_failures", "failed", "blocked", "canceled"],
)
def test_final_cycle_can_be_snapshotted_as_previous_context(
    tmp_path: Path,
    status: str,
) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    previous = teams.create_cycle(run.id, "manual", f"previous-{status}")
    teams.set_cycle_status(previous.id, status, summary="previous result")

    request = cycles.enqueue_request(
        run.id,
        "manual",
        f"client-{status}",
        "next",
        previous_cycle_id=previous.id,
    )

    assert request.previous_cycle_id == previous.id
    assert request.previous_summary_text is not None
    assert f"STATUS: {status.upper()}" in request.previous_summary_text
    assert "previous result" in request.previous_summary_text


def test_failed_previous_cycle_snapshots_error_and_task_results(
    tmp_path: Path,
) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    previous = teams.create_cycle(run.id, "manual", "previous-failed")
    fixed = teams.create_task(
        run.id,
        "Fix P3 findings",
        "Fix them",
        cycle_id=previous.id,
    )
    teams.set_task_status(
        fixed.id,
        "completed",
        result="Applied the remaining fixes",
    )
    qa = teams.create_task(
        run.id,
        "Run QA",
        "Verify them",
        cycle_id=previous.id,
    )
    teams.record_task_outcome(
        qa.id,
        {
            "status": "blocked",
            "summary": "Draft was unchanged",
            "reason_code": "draft-unmodified",
        },
        {
            "accepted": False,
            "status": "blocked",
            "reason_code": "draft-unmodified",
        },
    )
    teams.set_task_status(qa.id, "failed", error_message="QA task failed")
    teams.set_cycle_status(
        previous.id,
        "failed",
        error_message="Required task failed",
    )

    request = cycles.enqueue_request(
        run.id,
        "manual",
        "client-failed",
        "next",
        previous_cycle_id=previous.id,
    )

    assert request.previous_summary_text is not None
    assert "STATUS: FAILED" in request.previous_summary_text
    assert "ERROR\nRequired task failed" in request.previous_summary_text
    assert "- [COMPLETED] Fix P3 findings" in request.previous_summary_text
    assert "RESULT: Applied the remaining fixes" in request.previous_summary_text
    assert "- [FAILED] Run QA" in request.previous_summary_text
    assert "RESULT: Draft was unchanged" in request.previous_summary_text
    assert "ERROR: QA task failed" in request.previous_summary_text


def test_nonfinal_cycle_cannot_be_previous_context(tmp_path: Path) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    previous = teams.create_cycle(run.id, "manual", "running-cycle")
    teams.set_cycle_status(previous.id, "running")

    with pytest.raises(ValueError, match="final cycle"):
        cycles.enqueue_request(
            run.id,
            "manual",
            "client-running",
            "next",
            previous_cycle_id=previous.id,
        )


def test_public_auto_enqueue_rejects_invalid_slot_retry_and_inactive_series(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path, target_slots=1)
    series = cycles.get_active_series(run.id)
    first = cycles.list_requests(run.id)[0]

    with pytest.raises(ValueError, match="slot"):
        cycles.enqueue_request(
            run.id,
            "auto",
            "out-of-range",
            "work",
            previous_cycle_id=None,
            auto_series_id=series.id,
            slot_ordinal=2,
        )
    with pytest.raises(ValueError, match="failed"):
        cycles.enqueue_request(
            run.id,
            "retry",
            "not-failed",
            "work",
            previous_cycle_id=None,
            auto_series_id=series.id,
            slot_ordinal=1,
            retry_of_request_id=first.id,
        )

    first = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(cycle.id, "completed", summary="done")
    cycles.settle_cycle(cycle.id)

    with pytest.raises(ValueError, match="active"):
        cycles.enqueue_request(
            run.id,
            "auto",
            "inactive-series",
            "work",
            previous_cycle_id=cycle.id,
            auto_series_id=series.id,
            slot_ordinal=1,
        )


def test_auto_state_ownership_and_repeated_settlement_are_idempotent(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path / "first", target_slots=1)
    series = cycles.get_active_series(run.id)
    request = cycles.list_requests(run.id)[0]
    other_db, other_teams, other_cycles, other_run = make_auto_run(tmp_path / "second")

    with pytest.raises(ValueError, match="different team run"):
        cycles.retry_failed(other_run.id, series.id)
    with pytest.raises(ValueError, match="paused"):
        cycles.continue_failed(run.id, series.id)

    initial = request
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "completed", summary="done")

    first = cycles.settle_cycle(cycle.id)
    duplicate = cycles.settle_cycle(cycle.id)

    assert first.transitioned is True
    assert duplicate.transitioned is False
    assert first.series.settled_slots == 1
    assert duplicate.series.settled_slots == 1
    assert duplicate.series.status == "auto_completed"
    assert duplicate.request.status == "settled"


def test_auto_due_slot_read_models_and_restart(tmp_path: Path) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series = cycles.get_active_series(run.id)
    first = cycles.list_requests(run.id)[0]

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
    leader = teams.get_agent(run.leader_agent_id)
    seed_next_cycle_proposal(db, run, first_cycle, leader, "continue")
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


def test_due_comparison_normalizes_equivalent_offset_instants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "personal_agent_gateway.teams._now",
        lambda: "2026-07-20T00:00:00+00:00",
    )
    db, teams, cycles, run = make_auto_run(tmp_path)
    series = cycles.get_active_series(run.id)
    initial = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "completed", summary="one")
    leader = teams.get_agent(run.leader_agent_id)
    seed_next_cycle_proposal(db, run, cycle, leader, "continue")

    settled = cycles.settle_cycle(cycle.id, now=dt("2026-07-20T09:01:00+09:00"))
    due = cycles.enqueue_due_auto_requests(now=dt("2026-07-19T20:06:00-04:00"))

    assert series.created_at == "2026-07-20T00:00:00+00:00"
    assert settled.series.next_run_at == "2026-07-20T00:06:00+00:00"
    assert [request.slot_ordinal for request in due] == [2]


def test_explicit_naive_datetime_is_rejected(tmp_path: Path) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        cycles.enqueue_request(
            run.id,
            "manual",
            "client-1",
            "work",
            previous_cycle_id=None,
            now=dt("2026-07-20T00:00:00"),
        )


def test_pause_and_reconcile_preserve_or_requeue_dispatching_request(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path / "auto", target_slots=1)
    initial = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
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

    db, teams, cycles, run = make_auto_run(
        tmp_path / "interrupted",
        target_slots=1,
    )
    initial = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    assert request.id == initial.id
    cycle = teams.create_cycle(run.id, "auto", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "interrupted")

    paused = cycles.pause_interrupted(cycle.id)

    assert paused.series.status == "paused_interrupted"
    assert paused.request.status == "dispatching"


def test_reconcile_is_idempotent_after_terminal_cycle_settlement(
    tmp_path: Path,
) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    request = cycles.enqueue_request(run.id, "manual", "client-1", "work", previous_cycle_id=None)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "manual", request.source_id, request_id=request.id)
    teams.set_cycle_status(cycle.id, "completed", summary="done")

    first = cycles.reconcile(teams)
    second = cycles.reconcile(teams)

    assert first == []
    assert second == []
    assert len(cycles.list_requests(run.id)) == 1
    assert cycles.get_request(request.id).status == "settled"
    assert teams.get_cycle_for_request(request.id).id == cycle.id
    assert len(teams.list_cycles(run.id)) == 1


def test_a_contest_request_can_be_enqueued(tmp_path: Path) -> None:
    _db, teams, cycles, run = make_triggered_run(tmp_path)

    created = cycles.enqueue_request(
        run.id, "contest", "client-1", "T-04 has no owner", previous_cycle_id=None
    )

    assert created.source_type == "contest"
    assert created.status == "queued"


def test_the_same_contest_twice_returns_the_same_request(tmp_path: Path) -> None:
    """contest joins the idempotent group, so a double-submitted objection does
    not queue two adjudications of the same thing."""
    _db, teams, cycles, run = make_triggered_run(tmp_path)

    first = cycles.enqueue_request(
        run.id, "contest", "client-1", "T-04 has no owner", previous_cycle_id=None
    )
    second = cycles.enqueue_request(
        run.id, "contest", "client-1", "T-04 has no owner", previous_cycle_id=None
    )

    assert first.id == second.id


def test_a_contest_waits_while_another_request_is_dispatching(
    tmp_path: Path,
) -> None:
    """Serialization is already there -- claim_next refuses while a request is
    dispatching -- and it is why a contest cannot reproduce the mid-flight
    collision /add-work caused with cancel."""
    _db, teams, cycles, run = make_triggered_run(tmp_path)
    cycles.enqueue_request(run.id, "manual", "client-1", "do the work", previous_cycle_id=None)
    assert cycles.claim_next(run.id) is not None
    cycles.enqueue_request(
        run.id, "contest", "client-2", "T-04 has no owner", previous_cycle_id=None
    )

    assert cycles.claim_next(run.id) is None


def _applied_synthesis(db, teams, run, cycle, agent, payload):
    """합성 결과 하나를 원장에 적용된 상태로 남긴다.

    apply_synthesis 는 사이클에 종결된 필수 태스크가 최소 하나 있어야 하고,
    런이 summarizing, 사이클이 running 상태여야 한다 -- make_completed_
    synthesis_operation (tests/test_team_model_effects.py) 이 실제로 쓰는
    준비 과정을 그대로 따른다.
    """
    import hashlib

    from personal_agent_gateway.team_model_effects import (
        TeamModelEffectService,
        team_model_effect_result_validators,
    )
    from personal_agent_gateway.team_model_operations import (
        OperationSpec,
        TeamModelOperationService,
        ValidatedOperationResult,
    )

    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != agent.id
    )
    task = teams.create_task(
        run.id,
        "Draft",
        "Create a draft.",
        owner_agent_id=worker.id,
        cycle_id=cycle.id,
    )
    teams.start_task(task.id, worker.id)
    teams.finish_task(task.id, worker.id, "completed", result="Drafted.")
    teams.set_run_status(run.id, "summarizing")
    teams.set_agent_status(agent.id, "running")

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
        ValidatedOperationResult("synthesis", payload),
    )
    # 적용은 효과 서비스가 한다 -- tests/test_api_team_runs.py 가 같은 방식을
    # 쓴다. operations 에는 적용 메서드가 없다.
    TeamModelEffectService(db, teams, operations).apply_synthesis(
        completed.id, payload["summary"]
    )


def test_the_next_auto_cycle_uses_the_proposal_not_the_goal(tmp_path):
    """지난 사이클이 무엇을 알아냈든 다음 사이클이 처음과 같은 말을 듣는 것이
    지금 동작이고, 그것이 팀을 제자리에 돌린다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    cycle = teams.create_cycle(run.id, "auto", "slot-1")
    _applied_synthesis(
        db, teams, run, cycle, agent, {"summary": "끝", "next_cycle": "6문장을 다시 돌려라"}
    )
    teams.set_cycle_status(cycle.id, "completed")

    instruction = cycles._auto_instruction(db.connect(), run.id, cycle.id)

    assert instruction == "6문장을 다시 돌려라"


def test_the_first_slot_has_no_previous_cycle_and_uses_the_goal(tmp_path):
    """시리즈를 만들 때 나가는 요청은 읽을 제안이 아직 없다. 대체 경로가
    아니라 유일한 경로다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")

    assert cycles._auto_instruction(db.connect(), run.id, None) == "goal"


def test_a_cycle_with_no_proposal_yields_no_instruction(tmp_path):
    """리드가 더 할 일이 없다고 판단한 경우다. 시리즈는 여기서 끝난다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    cycle = teams.create_cycle(run.id, "auto", "slot-1")
    _applied_synthesis(db, teams, run, cycle, agent, {"summary": "끝"})
    teams.set_cycle_status(cycle.id, "completed")

    assert cycles._auto_instruction(db.connect(), run.id, cycle.id) is None
