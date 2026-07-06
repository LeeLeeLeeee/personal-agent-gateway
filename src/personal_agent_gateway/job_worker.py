import asyncio
from pathlib import Path

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.runners.base import Runner


class JobWorker:
    def __init__(
        self,
        jobs: JobService,
        artifacts: ArtifactStore,
        runners: dict[str, Runner],
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._runners = runners
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
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

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def run_one(self, job_id: str) -> None:
        job = self._jobs.mark_running(job_id)
        runner = self._runners[self._jobs.runner_type_for(job)]
        result = await runner.run(job.capability_id, job.input_json)
        if result.exit_code != 0:
            self._jobs.mark_failed(job.id, result.stderr or result.stdout or "Job failed")
            return

        for artifact_path in result.artifact_paths:
            self._register_artifact(job.id, artifact_path)
        self._jobs.mark_succeeded(job.id)

    async def _run_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self.run_one(job_id)
            finally:
                self._queue.task_done()

    def _register_artifact(self, job_id: str, artifact_path: Path) -> None:
        self._artifacts.register_existing_file(
            artifact_type=_artifact_type(artifact_path),
            title=artifact_path.name,
            source_path=artifact_path,
            relative_path=f"files/{artifact_path.name}",
            mime_type=_mime_type(artifact_path),
            source_job_id=job_id,
        )


def _artifact_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".m4a", ".mp3", ".wav"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix in {".log", ".txt"}:
        return "text"
    return "other"


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".txt":
        return "text/plain"
    return "application/octet-stream"
