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
