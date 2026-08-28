import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.team_cycle_dispatcher import (
    TeamCycleDispatcher,
)
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_provider_recovery import (
    TeamProviderRecovery,
)


class TeamCycleLoop:
    def __init__(
        self,
        cycles: TeamCycleService,
        dispatcher: TeamCycleDispatcher,
        provider_recovery: TeamProviderRecovery | None = None,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = (
            lambda: datetime.now(timezone.utc)
        ),
    ) -> None:
        self._cycles = cycles
        self._dispatcher = dispatcher
        self._provider_recovery = provider_recovery
        self._interval_seconds = interval_seconds
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    async def tick(self) -> None:
        now = self._now()
        if self._provider_recovery is not None:
            # recover_due reaches AgentRegistry.catalog(), which is synchronous
            # httpx with time.sleep retries under a lock. On the loop thread
            # that stalls every other run, not just this tick.
            claims = await asyncio.to_thread(
                self._provider_recovery.recover_due, now=now
            )
            for claim in claims:
                self._dispatcher.resume_recovered_operation(claim)
        outcome = self._cycles.enqueue_due_auto_requests(now=now)
        for request in outcome.requests:
            await self._dispatcher.enqueue_run(request.team_run_id)
        for series in outcome.completed_series:
            # 리드가 다음 할 일을 내지 않아 끝난 시리즈도 알려야 한다.
            # 알리지 않으면 열려 있는 화면은 다음 사이클을 기다리는 모습
            # 그대로 남는다.
            await self._dispatcher.announce_auto_series_completed(series)

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
