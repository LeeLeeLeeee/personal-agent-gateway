import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.team_cycles import (
    TeamCycleRequest,
    TeamCycleService,
)
from personal_agent_gateway.team_model_invoker import AmbiguousModelOperation
from personal_agent_gateway.team_provider_recovery import (
    ProviderOperationWaiting,
    TeamProviderRecovery,
)
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.teams import (
    ProviderRecoveryClaim,
    TeamRun,
    TeamRunCycle,
    TeamRunService,
)


@dataclass(frozen=True)
class CyclePreparation:
    instruction: str
    output_contract_id: str | None = None


CyclePreparer = Callable[
    [TeamCycleRequest, TeamRunCycle],
    Awaitable[CyclePreparation | None],
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
        provider_recovery: TeamProviderRecovery,
        *,
        concurrency: int = 1,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self._cycles = cycles
        self._teams = teams
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._provider_recovery = provider_recovery
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._preparers: list[CyclePreparer] = []
        self._concurrency = concurrency
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._worker_errors: dict[int, str] = {}
        self._last_error: str | None = None
        self._interrupt_on_stop = True
        self._startup_operation_cycles: list[str] = []

    @property
    def alive(self) -> bool:
        return len(self._workers) == self._concurrency and all(
            not task.done() for task in self._workers.values()
        )

    @property
    def last_error(self) -> str | None:
        if self._worker_errors:
            worker_id = next(reversed(self._worker_errors))
            return self._worker_errors[worker_id]
        return self._last_error

    async def start(self) -> None:
        if self.alive:
            return
        for worker_id in range(self._concurrency):
            task = self._workers.get(worker_id)
            if task is not None and not task.done():
                continue
            self._workers[worker_id] = asyncio.create_task(
                self._worker_loop(worker_id),
                name=f"team-cycle-dispatcher-{worker_id}",
            )
        startup_cycles = self._startup_operation_cycles
        self._startup_operation_cycles = []
        for cycle_id in startup_cycles:
            cycle = self._teams.get_cycle(cycle_id)
            operation = self._provider_recovery.get_open_operation(cycle.id)
            self._resume_operation(cycle, operation)

    async def stop(self, *, interrupt_active: bool = True) -> None:
        if not self._workers:
            return
        self._interrupt_on_stop = interrupt_active
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        try:
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            self._workers.clear()
            self._interrupt_on_stop = True

    def discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()

    def add_preparer(self, preparer: CyclePreparer) -> None:
        self._preparers.append(preparer)

    async def enqueue_run(self, team_run_id: str) -> None:
        await self._queue.put(team_run_id)

    def resume(
        self,
        team_run_id: str,
        cycle_id: str | None = None,
    ):
        return self._observe_scheduled(
            self._orchestrator.resume(team_run_id, cycle_id),
            team_run_id,
            cycle_id,
        )

    def resume_recovered_operation(
        self,
        claim: ProviderRecoveryClaim,
    ):
        cycle = self._teams.get_cycle(claim.cycle_id)
        operation = self._provider_recovery.get_open_operation(cycle.id)
        if operation is None or operation.id != claim.operation_id:
            raise RuntimeError("Recovered operation claim no longer matches")
        return self._resume_operation(cycle, operation)

    async def run_one(self, team_run_id: str) -> None:
        if self._teams.get_team_run(team_run_id).status == "canceled":
            return
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
            cycle = self._provider_recovery.freeze_cycle(cycle.id)
        except Exception:
            self._cycles.requeue_claim(request.id)
            raise
        try:
            instruction = request.instruction
            output_contract_id: str | None = None
            for preparer in self._preparers:
                replacement = await preparer(request, cycle)
                if replacement is not None:
                    instruction = replacement.instruction
                    output_contract_id = replacement.output_contract_id
            if request.previous_summary_text:
                instruction += (
                    "\n\nPREVIOUS CYCLE CONTEXT\n"
                    + request.previous_summary_text
                )
            self._teams.set_cycle_effective_instruction(
                cycle.id,
                instruction,
                output_contract_id,
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
            task = asyncio.current_task()
            dispatcher_stopping = task is not None and task.cancelling()
            if not dispatcher_stopping or self._interrupt_on_stop:
                await self._interrupt_cycle(team_run_id, cycle.id)
            if dispatcher_stopping:
                raise
        except ProviderOperationWaiting:
            return
        except AmbiguousModelOperation:
            await self.on_team_run_settled(
                self._teams.get_team_run(team_run_id),
                cycle.id,
            )
            return
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
        if cycle.status == "waiting_for_provider":
            return
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
        operation_result = self._provider_recovery.reconcile_startup()
        startup_cycle_ids = {
            *operation_result.runnable_cycle_ids,
            *operation_result.locally_applicable_cycle_ids,
        }
        self._startup_operation_cycles = sorted(startup_cycle_ids)
        for request in self._cycles.list_dispatching_requests():
            cycle = self._teams.get_cycle_for_request(request.id)
            if (
                cycle is not None
                and cycle.id not in startup_cycle_ids
                and cycle.status in {"queued", "running"}
            ):
                self._teams.set_cycle_status(cycle.id, "interrupted")
        return self._cycles.reconcile(self._teams)

    async def _interrupt_cycle(
        self,
        team_run_id: str,
        cycle_id: str,
    ) -> None:
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status in {
            "completed",
            "completed_with_failures",
            "failed",
            "canceled",
        }:
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

    def _resume_operation(self, cycle: TeamRunCycle, operation):
        add_work_operation = (
            operation is not None
            and (
                operation.stage == "cycle_add_work"
                or (
                    operation.stage == "cycle_planning_repair"
                    and operation.stage_ordinal == 2
                )
            )
        )
        if add_work_operation:
            instruction = (
                self._teams.get_cycle_effective_instruction(cycle.id)
                or self._teams.get_cycle_objective(cycle.id)
            )
            if instruction is None:
                raise RuntimeError(
                    "Recovered add-work operation has no instruction"
                )
            scheduled = self._orchestrator.continue_cycle(
                cycle.team_run_id,
                cycle.id,
                instruction,
            )
        else:
            scheduled = self._orchestrator.resume(
                cycle.team_run_id,
                cycle.id,
            )
        return self._observe_scheduled(
            scheduled,
            cycle.team_run_id,
            cycle.id,
        )

    def _observe_scheduled(
        self,
        scheduled,
        team_run_id: str,
        cycle_id: str | None,
    ):
        if isinstance(scheduled, asyncio.Future):
            asyncio.create_task(
                self._consume_operation_marker(
                    scheduled,
                    team_run_id,
                    cycle_id,
                )
            )
        return scheduled

    async def _consume_operation_marker(
        self,
        scheduled,
        team_run_id: str,
        cycle_id: str | None,
    ) -> None:
        try:
            await scheduled
        except asyncio.CancelledError:
            return
        except ProviderOperationWaiting:
            return
        except AmbiguousModelOperation:
            await self.on_team_run_settled(
                self._teams.get_team_run(team_run_id),
                cycle_id,
            )
        except Exception as exc:
            self._last_error = redact_text(exc) or type(exc).__name__

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            team_run_id = await self._queue.get()
            try:
                await self.run_one(team_run_id)
                self._worker_errors.pop(worker_id, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._worker_errors.pop(worker_id, None)
                self._worker_errors[worker_id] = redact_text(exc) or type(exc).__name__
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
