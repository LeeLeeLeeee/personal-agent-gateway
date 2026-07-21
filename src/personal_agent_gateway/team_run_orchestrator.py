import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol

from personal_agent_gateway.run_state import TeamRunRegistry
from personal_agent_gateway.teams import TeamRun


class TeamRuntimeProtocol(Protocol):
    async def start(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamRun: ...

    async def resume(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamRun: ...

    async def add_work(
        self, team_run_id: str, instruction: str, cycle_id: str | None = None
    ) -> list[object]: ...


class TeamRunOrchestrator:
    def __init__(
        self,
        registry: TeamRunRegistry,
        runtime_provider: Callable[[], TeamRuntimeProtocol],
    ) -> None:
        self._registry = registry
        self._runtime_provider = runtime_provider
        self._observers: list[
            Callable[[TeamRun, str | None], Awaitable[None]]
        ] = []

    def add_observer(
        self, observer: Callable[[TeamRun, str | None], Awaitable[None]]
    ) -> None:
        self._observers.append(observer)

    def is_running(self, team_run_id: str) -> bool:
        return self._registry.is_running(team_run_id)

    def start(self, team_run_id: str, cycle_id: str | None = None) -> asyncio.Task:
        runtime = self._runtime_provider()
        return self._schedule(
            team_run_id,
            cycle_id,
            lambda: runtime.start(team_run_id, cycle_id),
        )

    def resume(self, team_run_id: str, cycle_id: str | None = None) -> asyncio.Task:
        runtime = self._runtime_provider()
        return self._schedule(
            team_run_id,
            cycle_id,
            lambda: runtime.resume(team_run_id, cycle_id),
        )

    async def run_cycle(
        self, team_run_id: str, cycle_id: str, instruction: str
    ) -> TeamRun:
        runtime = self._runtime_provider()

        async def execute_cycle() -> TeamRun:
            await runtime.add_work(team_run_id, instruction, cycle_id)
            return await runtime.resume(team_run_id, cycle_id)

        return await self._schedule(team_run_id, cycle_id, execute_cycle)

    def _schedule(
        self,
        team_run_id: str,
        cycle_id: str | None,
        action: Callable[[], Coroutine[object, object, TeamRun]],
    ) -> asyncio.Task:
        async def run_and_finish() -> TeamRun:
            try:
                result = await action()
                for observer in self._observers:
                    await observer(result, cycle_id)
                return result
            finally:
                self._registry.finish(team_run_id)

        task = asyncio.create_task(run_and_finish())
        self._registry.register(team_run_id, task)
        return task
