import asyncio
from pathlib import Path

import pytest

from personal_agent_gateway.team_cycle_loop import TeamCycleLoop
from personal_agent_gateway.team_provider_recovery import (
    ProviderRecoveryClaim,
)
from team_cycle_helpers import dt, make_cycle_services


class RecordingDispatcher:
    def __init__(self) -> None:
        self.enqueued_run_ids: list[str] = []
        self.recovered_operation_ids: list[str] = []

    async def enqueue_run(self, team_run_id: str) -> None:
        self.enqueued_run_ids.append(team_run_id)

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
