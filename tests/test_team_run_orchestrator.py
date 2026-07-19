import asyncio
from dataclasses import dataclass

import pytest

from personal_agent_gateway.run_state import TeamRunRegistry
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator


@dataclass
class FakeRuntime:
    gate: asyncio.Event
    calls: list[tuple[str, str, str | None]]

    async def start(self, team_run_id: str, cycle_id: str | None = None):
        self.calls.append(("start", team_run_id, cycle_id))
        await self.gate.wait()
        return object()

    async def resume(self, team_run_id: str, cycle_id: str | None = None):
        self.calls.append(("resume", team_run_id, cycle_id))
        await self.gate.wait()
        return object()


@pytest.mark.asyncio
async def test_orchestrator_registers_and_finishes_start() -> None:
    registry = TeamRunRegistry()
    gate = asyncio.Event()
    runtime = FakeRuntime(gate, [])
    orchestrator = TeamRunOrchestrator(registry, lambda: runtime)

    task = orchestrator.start("run-1", "cycle-1")
    await asyncio.sleep(0)

    assert registry.is_running("run-1") is True
    assert runtime.calls == [("start", "run-1", "cycle-1")]

    gate.set()
    await task

    assert registry.is_running("run-1") is False


@pytest.mark.asyncio
async def test_orchestrator_finishes_registry_when_resume_fails() -> None:
    class FailingRuntime:
        async def resume(self, team_run_id: str, cycle_id: str | None = None):
            raise RuntimeError(f"failed {team_run_id} {cycle_id}")

    registry = TeamRunRegistry()
    orchestrator = TeamRunOrchestrator(registry, lambda: FailingRuntime())

    task = orchestrator.resume("run-1", "cycle-1")

    with pytest.raises(RuntimeError, match="failed run-1 cycle-1"):
        await task
    assert registry.is_running("run-1") is False
