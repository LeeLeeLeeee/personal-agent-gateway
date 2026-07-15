import asyncio
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.runners.base import RunResult


class FakeRunner:
    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        return ["fake", capability_id]

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        output = Path(str(input_json["output_file"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("done", encoding="utf-8")
        return RunResult(exit_code=0, stdout="ok", stderr="", artifact_paths=[output])


@pytest.mark.asyncio
async def test_worker_marks_job_succeeded_and_registers_artifact(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    output_file = tmp_path / "temp" / "done.txt"
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="fake",
        input_json={"source_file": "demo.mov", "output_file": str(output_file)},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": FakeRunner()})

    await worker.run_one(job.id)

    updated = service.get_job(job.id)
    assert updated.status == "succeeded"
    assert len(artifacts.list()) == 1


@pytest.mark.asyncio
async def test_worker_marks_job_failed_on_runner_failure(tmp_path: Path) -> None:
    class FailingRunner(FakeRunner):
        async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
            return RunResult(exit_code=1, stdout="", stderr="failed", artifact_paths=[])

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="fake",
        input_json={"source_file": "demo.mov"},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": FailingRunner()})

    await worker.run_one(job.id)

    updated = service.get_job(job.id)
    assert updated.status == "failed"
    assert updated.error_message == "failed"


@pytest.mark.asyncio
async def test_worker_survives_runner_exception_and_executes_next_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlakyRunner(FakeRunner):
        def __init__(self) -> None:
            self.calls = 0

        async def run(
            self,
            capability_id: str,
            input_json: dict[str, object],
        ) -> RunResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("runner exposed top-secret")
            return RunResult(exit_code=0, stdout="ok", stderr="", artifact_paths=[])

    monkeypatch.setenv("OPENAI_API_KEY", "top-secret")
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    first = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="first",
        input_json={"source_file": "first.mov"},
    )
    second = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="second",
        input_json={"source_file": "second.mov"},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": FlakyRunner()})

    await worker.start()
    await worker.enqueue(first.id)
    await worker.enqueue(second.id)
    await asyncio.wait_for(worker._queue.join(), timeout=1)

    assert service.get_job(first.id).status == "failed"
    assert service.get_job(first.id).error_message == "runner exposed [redacted]"
    assert service.get_job(second.id).status == "succeeded"
    assert worker.alive is True
    await worker.stop()


@pytest.mark.asyncio
async def test_worker_marks_active_job_as_shutdown_interrupted(tmp_path: Path) -> None:
    class BlockingRunner(FakeRunner):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.block = asyncio.Event()

        async def run(
            self,
            capability_id: str,
            input_json: dict[str, object],
        ) -> RunResult:
            self.started.set()
            await self.block.wait()
            return RunResult(exit_code=0, stdout="ok", stderr="", artifact_paths=[])

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    runner = BlockingRunner()
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="blocking",
        input_json={"source_file": "demo.mov"},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": runner})

    await worker.start()
    await worker.enqueue(job.id)
    await asyncio.wait_for(runner.started.wait(), timeout=1)
    await worker.stop()

    updated = service.get_job(job.id)
    assert updated.status == "failed"
    assert updated.error_message == "Gateway shutdown interrupted job"


@pytest.mark.asyncio
async def test_emergency_stop_cancels_running_and_queued_jobs_then_keeps_worker_alive(
    tmp_path: Path,
) -> None:
    class BlockingRunner(FakeRunner):
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def run(
            self,
            capability_id: str,
            input_json: dict[str, object],
        ) -> RunResult:
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    runner = BlockingRunner()
    running = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="running",
        input_json={"source_file": "running.mov"},
    )
    queued = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="queued",
        input_json={"source_file": "queued.mov"},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": runner})

    await worker.start()
    await worker.enqueue(running.id)
    await worker.enqueue(queued.id)
    await asyncio.wait_for(runner.started.wait(), timeout=1)

    canceled = await worker.emergency_stop()

    assert set(canceled) == {running.id, queued.id}
    assert service.get_job(running.id).status == "canceled"
    assert service.get_job(queued.id).status == "canceled"
    assert worker.alive is True
    await worker.stop()
