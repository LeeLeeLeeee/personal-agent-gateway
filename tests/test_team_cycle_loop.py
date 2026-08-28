import asyncio
import threading
from pathlib import Path

import pytest

from personal_agent_gateway.team_cycle_loop import TeamCycleLoop
from personal_agent_gateway.team_cycles import AutoEnqueueOutcome
from personal_agent_gateway.team_provider_recovery import (
    ProviderRecoveryClaim,
)
from team_cycle_helpers import dt, make_cycle_services, seed_next_cycle_proposal


class RecordingDispatcher:
    def __init__(self) -> None:
        self.enqueued_run_ids: list[str] = []
        self.recovered_operation_ids: list[str] = []
        self.completed_series_ids: list[str] = []

    async def enqueue_run(self, team_run_id: str) -> None:
        self.enqueued_run_ids.append(team_run_id)

    async def announce_auto_series_completed(self, series) -> None:
        self.completed_series_ids.append(series.id)

    def resume_recovered_operation(
        self,
        claim: ProviderRecoveryClaim,
    ) -> None:
        self.recovered_operation_ids.append(claim.operation_id)


class RecordingProviderRecovery:
    def __init__(self, claims: list[ProviderRecoveryClaim]) -> None:
        self._claims = claims
        self.checked_at = []

    def recover_due(self, *, now):
        self.checked_at.append(now)
        return self._claims


@pytest.mark.asyncio
async def test_loop_enqueues_due_auto_slot_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "personal_agent_gateway.teams._now",
        lambda: "2026-07-20T00:50:00+00:00",
    )
    _db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    series = cycles.get_active_series(run.id)
    assert series is not None
    first = cycles.list_requests(run.id)[0]
    first = cycles.claim_next(run.id)
    assert first is not None
    first_cycle = teams.create_cycle(
        run.id,
        "auto",
        first.source_id,
        request_id=first.id,
    )
    teams.set_cycle_status(
        first_cycle.id,
        "completed",
        summary="done",
    )
    leader = teams.get_agent(run.leader_agent_id)
    seed_next_cycle_proposal(_db, run, first_cycle, leader, "continue")
    cycles.settle_cycle(
        first_cycle.id,
        now=dt("2026-07-20T00:55:00+00:00"),
    )
    dispatcher = RecordingDispatcher()
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        now=lambda: dt("2026-07-20T01:00:00+00:00"),
    )

    await loop.tick()
    await loop.tick()

    requests = cycles.list_requests(run.id)
    assert [request.slot_ordinal for request in requests] == [1, 2]
    assert dispatcher.enqueued_run_ids == [run.id]


@pytest.mark.asyncio
async def test_loop_resumes_claimed_provider_recovery(
    tmp_path: Path,
) -> None:
    _db, _teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    dispatcher = RecordingDispatcher()
    claim = ProviderRecoveryClaim(
        team_run_id=run.id,
        cycle_id="cycle-id",
        task_id="task-id",
        operation_id="operation-id",
    )
    recovery = RecordingProviderRecovery([claim])
    now = dt("2026-07-20T01:00:00+00:00")
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        provider_recovery=recovery,
        now=lambda: now,
    )

    await loop.tick()

    assert recovery.checked_at == [now]
    assert dispatcher.recovered_operation_ids == ["operation-id"]


@pytest.mark.asyncio
async def test_loop_lifecycle_reports_redacted_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db, _teams, cycles, _run = make_cycle_services(
        tmp_path,
        "triggered",
    )
    dispatcher = RecordingDispatcher()
    attempted = asyncio.Event()
    monkeypatch.setenv("OPENAI_API_KEY", "loop-secret")

    def fail_enqueue_due_auto_requests(*, now):
        attempted.set()
        raise RuntimeError(f"leaked loop-secret at {now.isoformat()}")

    monkeypatch.setattr(
        cycles,
        "enqueue_due_auto_requests",
        fail_enqueue_due_auto_requests,
    )
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        interval_seconds=60,
        now=lambda: dt("2026-07-20T01:00:00+00:00"),
    )

    await loop.start()
    await asyncio.wait_for(attempted.wait(), timeout=1)
    await asyncio.sleep(0)

    assert loop.alive is True
    assert loop.last_error == (
        "leaked [redacted] at 2026-07-20T01:00:00+00:00"
    )

    await loop.stop()

    assert loop.alive is False


@pytest.mark.asyncio
async def test_tick_runs_provider_recovery_off_the_event_loop() -> None:
    """recover_due reaches AgentRegistry.catalog(), which does synchronous
    httpx calls with time.sleep retries under a threading lock. Running it on
    the loop froze every other run, not just this tick."""

    class NoCycles:
        def enqueue_due_auto_requests(self, *, now):
            return AutoEnqueueOutcome(requests=[], completed_series=[])

    class ThreadRecordingRecovery:
        def __init__(self) -> None:
            self.thread_ids: list[int] = []

        def recover_due(self, *, now):
            self.thread_ids.append(threading.get_ident())
            return []

    recovery = ThreadRecordingRecovery()
    loop = TeamCycleLoop(
        NoCycles(),
        RecordingDispatcher(),
        provider_recovery=recovery,
        now=lambda: dt("2026-07-20T01:00:00+00:00"),
    )

    await loop.tick()

    assert recovery.thread_ids, "recover_due was never called"
    assert recovery.thread_ids[0] != threading.get_ident(), (
        "recover_due ran on the event loop thread; blocking gateway I/O there "
        "freezes every other run, not just this tick"
    )


@pytest.mark.asyncio
async def test_loop_announces_a_series_the_lead_ended(tmp_path: Path) -> None:
    """리드가 다음 할 일을 내지 않아 끝난 시리즈도 알림을 낸다.

    마지막 슬롯까지 쓴 종료는 team.auto_series.completed 를 내보내고 화면이
    그 신호로 다시 읽는다. 이쪽만 조용히 끝나면 열려 있는 화면은 다음
    사이클을 기다리는 모습 그대로 남는다.
    """
    _db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    series = cycles.get_active_series(run.id)
    assert series is not None
    first = cycles.claim_next(run.id)
    first_cycle = teams.create_cycle(
        run.id,
        "auto",
        first.source_id,
        request_id=first.id,
    )
    # 제안을 남기지 않는다 -- 리드가 더 할 일이 없다고 본 경우다.
    teams.set_cycle_status(first_cycle.id, "completed", summary="done")
    cycles.settle_cycle(first_cycle.id, now=dt("2026-07-20T00:55:00+00:00"))
    dispatcher = RecordingDispatcher()
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        now=lambda: dt("2026-07-20T01:00:00+00:00"),
    )

    await loop.tick()

    assert dispatcher.completed_series_ids == [series.id]
    assert dispatcher.enqueued_run_ids == []
    assert cycles.get_latest_series(run.id).status == "auto_completed"
