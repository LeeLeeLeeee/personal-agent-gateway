import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.team_cycles import (
    TeamCycleRequest,
    TeamCycleService,
)
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.teams import TeamRun, TeamRunCycle, TeamRunService


CyclePreparer = Callable[
    [TeamCycleRequest, TeamRunCycle],
    Awaitable[str | None],
]

_TERMINAL_CYCLE_STATUSES = {
    "completed",
    "completed_with_failures",
    "failed",
    "canceled",
}
_PAUSE_ACTIONS = {
    "paused_failure": ["retry", "continue"],
    "paused_user": ["answer"],
    "paused_interrupted": ["resume"],
}


class TeamCycleDispatcher:
    def __init__(
        self,
        cycles: TeamCycleService,
        teams: TeamRunService,
        orchestrator: TeamRunOrchestrator,
        event_bus: EventBus,
    ) -> None:
        self._cycles = cycles
        self._teams = teams
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._preparers: list[CyclePreparer] = []
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def add_preparer(self, preparer: CyclePreparer) -> None:
        self._preparers.append(preparer)

    async def enqueue_run(self, team_run_id: str) -> None:
        await self._queue.put(team_run_id)

    async def run_one(self, team_run_id: str) -> None:
        request = self._cycles.claim_next(team_run_id)
        if request is None:
            return
        try:
            cycle = self._teams.create_cycle(
                team_run_id,
                request.source_type,
                request.source_id,
                request_id=request.id,
            )
        except Exception:
            self._cycles.requeue_claim(request.id)
            raise
        try:
            instruction = request.instruction
            for preparer in self._preparers:
                replacement = await preparer(request, cycle)
                if replacement is not None:
                    instruction = replacement
            if request.previous_summary_text:
                instruction += (
                    "\n\nPREVIOUS CYCLE SUMMARY\n"
                    + request.previous_summary_text
                )
            await self._event_bus.publish(
                {
                    "type": "team.cycle.started",
                    "team_run_id": team_run_id,
                    "request_id": request.id,
                    "cycle_id": cycle.id,
                    "series_id": request.auto_series_id,
                    "slot_ordinal": request.slot_ordinal,
                }
            )
            await self._orchestrator.run_cycle(
                team_run_id,
                cycle.id,
                instruction,
            )
        except asyncio.CancelledError:
            await self._interrupt_cycle(team_run_id, cycle.id)
            raise
        except Exception as exc:
            if self._teams.get_cycle(cycle.id).status in _TERMINAL_CYCLE_STATUSES:
                current_request = self._cycles.get_request(request.id)
                if current_request.status == "dispatching":
                    await self.on_team_run_settled(
                        self._teams.get_team_run(team_run_id),
                        cycle.id,
                    )
                raise
            self._teams.set_cycle_status(
                cycle.id,
                "failed",
                error_message=str(exc),
            )
            await self.on_team_run_settled(
                self._teams.get_team_run(team_run_id),
                cycle.id,
            )

    async def on_team_run_settled(
        self,
        run: TeamRun,
        cycle_id: str | None,
    ) -> None:
        if cycle_id is None:
            return
        cycle = self._teams.get_cycle(cycle_id)
        result = self._cycles.settle_cycle(cycle_id)
        if not result.transitioned:
            return
        if cycle.status in _TERMINAL_CYCLE_STATUSES:
            await self._event_bus.publish(
                {
                    "type": "team.cycle.settled",
                    **_cycle_lineage(cycle, result.request),
                    "status": cycle.status,
                    "duration_seconds": _duration_seconds(cycle),
                }
            )
        if (
            result.series is not None
            and result.series.status
            in {
                "paused_failure",
                "paused_user",
                "paused_interrupted",
            }
        ):
            await self._event_bus.publish(
                {
                    "type": "team.auto_series.paused",
                    "team_run_id": run.id,
                    "series_id": result.series.id,
                    "reason": result.series.pause_reason,
                    "available_actions": _PAUSE_ACTIONS[result.series.status],
                }
            )
        if (
            result.series is not None
            and result.series.status == "auto_completed"
        ):
            await self._event_bus.publish(
                {
                    "type": "team.auto_series.completed",
                    "team_run_id": run.id,
                    "series_id": result.series.id,
                    "settled_slots": result.series.settled_slots,
                    "target_slots": result.series.target_slots,
                }
            )
        if result.queue_ready:
            await self.enqueue_run(run.id)

    def reconcile(self) -> list[str]:
        for request in self._cycles.list_dispatching_requests():
            cycle = self._teams.get_cycle_for_request(request.id)
            if cycle is not None and cycle.status in {"queued", "running"}:
                self._teams.set_cycle_status(cycle.id, "interrupted")
        return self._cycles.reconcile(self._teams)

    async def _interrupt_cycle(
        self,
        team_run_id: str,
        cycle_id: str,
    ) -> None:
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status in {"completed", "completed_with_failures", "failed"}:
            request = self._cycles.get_request(cycle.request_id)
            if request.status == "dispatching":
                await self.on_team_run_settled(
                    self._teams.get_team_run(team_run_id),
                    cycle_id,
                )
            return
        if cycle.status not in {"waiting_for_user", "interrupted"}:
            self._teams.set_cycle_status(cycle_id, "interrupted")
        await self.on_team_run_settled(
            self._teams.get_team_run(team_run_id),
            cycle_id,
        )

    async def _run_loop(self) -> None:
        while True:
            team_run_id = await self._queue.get()
            try:
                await self.run_one(team_run_id)
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = (
                    redact_text(exc)
                    or type(exc).__name__
                )
            finally:
                self._queue.task_done()


def _cycle_lineage(
    cycle: TeamRunCycle,
    request: TeamCycleRequest,
) -> dict[str, object]:
    return {
        "team_run_id": cycle.team_run_id,
        "request_id": request.id,
        "cycle_id": cycle.id,
        "series_id": request.auto_series_id,
        "slot_ordinal": request.slot_ordinal,
    }


def _duration_seconds(cycle: TeamRunCycle) -> float:
    started_at = datetime.fromisoformat(cycle.started_at or cycle.created_at)
    finished_at = datetime.fromisoformat(cycle.finished_at or cycle.updated_at)
    return max(0.0, (finished_at - started_at).total_seconds())
