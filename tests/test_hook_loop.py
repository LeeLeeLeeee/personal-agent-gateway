from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_loop import HookLoop
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService
from personal_agent_gateway.sources.base import HookEvent, PollResult


class StubAdapter:
    def __init__(self, events: list[HookEvent]) -> None:
        self._events = events

    def poll(self, connection, secret, cursor, filter_config):
        return PollResult(events=self._events, cursor={"uidvalidity": 1, "last_uid": 9})


class RecordingRunner:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.enqueued.append(run_id)


@pytest.mark.asyncio
async def test_tick_creates_runs_and_enqueues(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    adapter = StubAdapter([HookEvent("email:1:2", "s", {"subject": "hi"})])
    hooks = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": adapter})
    hooks.create_hook(
        name="w", source_type="email",
        connection={"host": "h", "port": 993, "username": "u"}, secret="pw",
        filter={}, target_backend="codex", target_model="default",
        target_options={}, prompt_template="t", poll_interval_seconds=300,
    )
    runs = HookRunService(db)
    runner = RecordingRunner()
    loop = HookLoop(hooks, runs, runner)

    await loop.tick()

    assert len(runner.enqueued) == 1
    assert len(runs.list_runs(hooks.list_hooks()[0].id)) == 1
