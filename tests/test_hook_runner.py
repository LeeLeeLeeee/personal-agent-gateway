from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService


@dataclass
class FakeRuntimeResult:
    messages: list
    pending_approval: object


class FakeRuntime:
    def __init__(self, result: FakeRuntimeResult) -> None:
        self._result = result
        self.received_prompt: str | None = None

    async def handle_user_message(self, message: str) -> FakeRuntimeResult:
        self.received_prompt = message
        return self._result


class FakeFactory:
    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime
        self.calls: list[tuple] = []

    def create_headless_runtime(self, backend, model, options) -> FakeRuntime:
        self.calls.append((backend, model, options))
        return self._runtime


def _setup(tmp_path: Path, runtime: FakeRuntime):
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    hooks = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": object()})
    hook = hooks.create_hook(
        name="w", source_type="email",
        connection={"host": "h", "port": 993, "username": "u"}, secret="pw",
        filter={}, target_backend="codex", target_model="default",
        target_options={}, prompt_template="요약: {{subject}}", poll_interval_seconds=300,
    )
    runs = HookRunService(db)
    run = runs.create_run(hook.id, "k", "s", {"subject": "hi"})
    bus = EventBus()
    runner = HookRunner(hooks, runs, FakeFactory(runtime), bus)
    return runner, runs, run, hook, bus


@pytest.mark.asyncio
async def test_run_one_success_records_result_and_publishes(tmp_path: Path) -> None:
    runtime = FakeRuntime(FakeRuntimeResult([{"content": "done"}], None))
    runner, runs, run, hook, bus = _setup(tmp_path, runtime)

    await runner.run_one(run.id)

    updated = runs.get_run(run.id)
    assert updated.status == "succeeded"
    assert updated.result_text == "done"
    assert runtime.received_prompt == "요약: hi"
    events = bus.recent()
    assert events[-1]["type"] == "hook.run.updated"
    assert events[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_run_one_pending_approval_marks_failed(tmp_path: Path) -> None:
    runtime = FakeRuntime(FakeRuntimeResult([{"content": "partial"}], {"id": "a1"}))
    runner, runs, run, hook, bus = _setup(tmp_path, runtime)

    await runner.run_one(run.id)

    assert runs.get_run(run.id).status == "failed"
