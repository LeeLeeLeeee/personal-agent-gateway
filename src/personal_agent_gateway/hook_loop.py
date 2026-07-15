import asyncio
from datetime import datetime, timezone

from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService


class HookLoop:
    def __init__(
        self,
        hooks: HookService,
        hook_runs: HookRunService,
        runner: HookRunner,
        interval_seconds: float = 30.0,
    ) -> None:
        self._hooks = hooks
        self._hook_runs = hook_runs
        self._runner = runner
        self._interval_seconds = interval_seconds
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
        for run in self._hooks.poll_due(
            self._hook_runs, now=datetime.now(timezone.utc)
        ):
            await self._runner.enqueue(run.id)

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
