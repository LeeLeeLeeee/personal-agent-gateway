import asyncio
from datetime import datetime, timezone

from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.schedules import ScheduleService


class SchedulerLoop:
    def __init__(
        self,
        schedules: ScheduleService,
        jobs: JobService,
        worker: JobWorker,
        interval_seconds: float = 30.0,
    ) -> None:
        self._schedules = schedules
        self._jobs = jobs
        self._worker = worker
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
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
        for job in self._schedules.create_due_jobs(
            self._jobs,
            now=datetime.now(timezone.utc),
        ):
            if job.status == "queued":
                await self._worker.enqueue(job.id)

    async def _run_loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._interval_seconds)
