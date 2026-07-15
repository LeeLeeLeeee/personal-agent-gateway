import asyncio
from datetime import datetime, timezone

from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.intake import IntakeGate
from personal_agent_gateway.schedules import ScheduleService


class SchedulerLoop:
    def __init__(
        self,
        schedules: ScheduleService,
        jobs: JobService,
        worker: JobWorker,
        interval_seconds: float = 30.0,
        intake_gate: IntakeGate | None = None,
    ) -> None:
        self._schedules = schedules
        self._jobs = jobs
        self._worker = worker
        self._interval_seconds = interval_seconds
        self._intake_gate = intake_gate
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

    async def tick(self) -> None:
        if self._intake_gate is not None and not self._intake_gate.is_open:
            return
        for job in self._schedules.create_due_jobs(
            self._jobs,
            now=datetime.now(timezone.utc),
        ):
            if job.status == "queued":
                await self._worker.enqueue(job.id)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)[:2000] or type(exc).__name__
            await asyncio.sleep(self._interval_seconds)
