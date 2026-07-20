import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.team_cycle_dispatcher import (
    TeamCycleDispatcher,
)
from personal_agent_gateway.team_cycles import TeamCycleService


class TeamCycleLoop:
    def __init__(
        self,
        cycles: TeamCycleService,
        dispatcher: TeamCycleDispatcher,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = (
            lambda: datetime.now(timezone.utc)
        ),
    ) -> None:
        self._cycles = cycles
        self._dispatcher = dispatcher
        self._interval_seconds = interval_seconds
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    async def tick(self) -> None:
        for request in self._cycles.enqueue_due_auto_requests(
            now=self._now()
        ):
            await self._dispatcher.enqueue_run(request.team_run_id)

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

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = (
                    redact_text(exc)
                    or type(exc).__name__
                )
            await asyncio.sleep(self._interval_seconds)
