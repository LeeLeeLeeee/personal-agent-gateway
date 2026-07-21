import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.runners.base import RunResult
from personal_agent_gateway.scheduler_loop import SchedulerLoop


class SuccessfulRunner:
    def preview_command(
        self,
        capability_id: str,
        input_json: dict[str, object],
    ) -> list[str]:
        return ["fake", capability_id]

    async def run(
        self,
        capability_id: str,
        input_json: dict[str, object],
    ) -> RunResult:
        return RunResult(exit_code=0, stdout="ok", stderr="", artifact_paths=[])


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        app_db_path=tmp_path / "data" / "app.sqlite",
    )


def wait_for_status(app, job_id: str, expected: str) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if app.state.job_service.get_job(job_id).status == expected:
            return
        time.sleep(0.01)
    assert app.state.job_service.get_job(job_id).status == expected


def test_lifespan_starts_and_stops_worker_and_scheduler(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))

    observers = app.state.team_run_orchestrator._observers
    preparers = app.state.team_cycle_dispatcher._preparers
    assert observers[0].__self__ is app.state.team_cycle_dispatcher
    assert observers[1].__self__ is app.state.hook_runner
    assert preparers[0].__self__ is app.state.hook_runner
    assert app.state.job_worker.alive is False
    assert app.state.scheduler_loop.alive is False
    assert app.state.team_cycle_dispatcher.alive is False
    assert app.state.team_cycle_loop.alive is False

    with TestClient(app):
        assert app.state.job_worker.alive is True
        assert app.state.scheduler_loop.alive is True
        assert app.state.team_cycle_dispatcher.alive is True
        assert app.state.team_cycle_loop.alive is True

    assert app.state.job_worker.alive is False
    assert app.state.scheduler_loop.alive is False
    assert app.state.team_cycle_dispatcher.alive is False
    assert app.state.team_cycle_loop.alive is False


def test_lifespan_reconciles_and_orders_cycle_background_services(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    calls: list[str] = []

    app.state.team_run_service.interrupt_active_runs = lambda: calls.append("interrupt")
    app.state.team_cycle_dispatcher.reconcile = lambda: (
        calls.append("reconcile") or ["recovered"]
    )

    async def dispatcher_start() -> None:
        calls.append("dispatcher.start")

    async def dispatcher_enqueue(team_run_id: str) -> None:
        calls.append(f"dispatcher.enqueue:{team_run_id}")

    async def loop_start() -> None:
        calls.append("loop.start")

    async def loop_stop() -> None:
        calls.append("loop.stop")

    async def dispatcher_stop() -> None:
        calls.append("dispatcher.stop")

    async def registry_cancel_all(*, reason: str) -> list[str]:
        calls.append(f"registry.cancel_all:{reason}")
        return []

    app.state.team_cycle_dispatcher.start = dispatcher_start
    app.state.team_cycle_dispatcher.enqueue_run = dispatcher_enqueue
    app.state.team_cycle_dispatcher.stop = dispatcher_stop
    app.state.team_cycle_loop.start = loop_start
    app.state.team_cycle_loop.stop = loop_stop
    app.state.team_run_registry.cancel_all = registry_cancel_all

    with TestClient(app):
        assert calls[:5] == [
            "interrupt",
            "reconcile",
            "dispatcher.start",
            "dispatcher.enqueue:recovered",
            "loop.start",
        ]

    assert calls.index("loop.stop") < calls.index("dispatcher.stop")
    assert calls.index("dispatcher.stop") < calls.index("registry.cancel_all:shutdown")


@pytest.mark.asyncio
async def test_lifespan_stops_cycle_tasks_when_later_startup_step_fails(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))

    def fail_recovery() -> None:
        raise RuntimeError("recovery failed")

    app.state.job_service.recover_interrupted_jobs = fail_recovery

    with pytest.raises(RuntimeError, match="recovery failed"):
        async with app.router.lifespan_context(app):
            pass

    try:
        assert app.state.team_cycle_dispatcher.alive is False
        assert app.state.team_cycle_loop.alive is False
    finally:
        await app.state.team_cycle_loop.stop()
        await app.state.team_cycle_dispatcher.stop()


def test_lifespan_recovers_running_and_reenqueues_non_chat_queued_jobs(
    tmp_path: Path,
) -> None:
    app = create_app(make_config(tmp_path))
    app.state.job_worker._runners["ffmpeg"] = SuccessfulRunner()
    interrupted = app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="interrupted",
        input_json={"source_file": "old.mov"},
    )
    app.state.job_service.mark_running(interrupted.id)
    queued = app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="queued",
        input_json={"source_file": "next.mov"},
    )
    chat_mirror = app.state.job_service.create_job(
        capability_id="ffmpeg.inspect",
        source="chat",
        title="chat mirror",
        input_json={"source_file": "chat.mov"},
    )

    with TestClient(app):
        wait_for_status(app, queued.id, "succeeded")
        assert app.state.job_service.get_job(interrupted.id).status == "failed"
        assert app.state.job_service.get_job(chat_mirror.id).status == "queued"


def test_scheduler_claims_due_schedule_and_worker_executes_job(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    app.state.job_worker._runners["ffmpeg"] = SuccessfulRunner()
    app.state.scheduler_loop._interval_seconds = 0.01
    schedule = app.state.schedule_service.create_schedule(
        name="due",
        capability_id="ffmpeg.inspect",
        cron_expression="* * * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
    )
    app.state.database.execute(
        "update schedules set next_run_at = ? where id = ?",
        ("2020-01-01T00:00:00+00:00", schedule.id),
    )

    with TestClient(app):
        deadline = time.monotonic() + 2
        jobs = []
        while time.monotonic() < deadline:
            jobs = app.state.job_service.list_jobs(sources=["schedule"])
            if jobs and jobs[0].status == "succeeded":
                break
            time.sleep(0.01)

        assert len(jobs) == 1
        assert jobs[0].status == "succeeded"


@pytest.mark.asyncio
async def test_scheduler_survives_one_tick_exception() -> None:
    class FlakySchedules:
        def __init__(self) -> None:
            self.calls = 0
            self.recovered = asyncio.Event()

        def create_due_jobs(self, _jobs, now):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first tick failed")
            self.recovered.set()
            return []

    schedules = FlakySchedules()
    loop = SchedulerLoop(schedules, object(), object(), interval_seconds=0.01)

    await loop.start()
    await asyncio.wait_for(schedules.recovered.wait(), timeout=1)

    assert schedules.calls >= 2
    assert loop.alive is True
    await loop.stop()


@pytest.mark.asyncio
async def test_scheduler_redacts_environment_secret_from_last_error(
    monkeypatch,
) -> None:
    class FailingSchedules:
        def create_due_jobs(self, _jobs, now):
            raise RuntimeError("scheduler leaked scheduler-secret")

    monkeypatch.setenv("OPENAI_API_KEY", "scheduler-secret")
    loop = SchedulerLoop(
        FailingSchedules(),
        object(),
        object(),
        interval_seconds=60,
    )

    await loop.start()
    await asyncio.sleep(0.01)

    assert "scheduler-secret" not in (loop.last_error or "")
    assert "[redacted]" in (loop.last_error or "")
    await loop.stop()
