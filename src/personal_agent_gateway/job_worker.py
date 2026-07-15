import asyncio
import os
from pathlib import Path

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.jobs import JobService, JobStatusError
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
        self._last_error: str | None = None
        self._interruption: tuple[str, bool] | None = None

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
        await self._stop_loop("Gateway shutdown interrupted job", canceled=False)

    async def emergency_stop(self) -> list[str]:
        targets = [
            job.id
            for job in self._jobs.list_jobs(statuses=["queued", "running"])
        ]
        was_alive = self.alive
        await self._stop_loop("Emergency stop canceled job", canceled=True)
        self._drain_queue()
        for job_id in targets:
            try:
                job = self._jobs.get_job(job_id)
                if job.status in {"queued", "running"}:
                    self._jobs.cancel_job(job_id, "Emergency stop canceled job")
            except (JobStatusError, KeyError):
                continue
        if was_alive:
            await self.start()
        return targets

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def run_one(self, job_id: str) -> None:
        job = self._jobs.mark_running(job_id)
        runner = self._runners[self._jobs.runner_type_for(job)]
        result = await runner.run(job.capability_id, job.input_json)
        if result.exit_code != 0:
            self._jobs.mark_failed(
                job.id,
                _redact_text(result.stderr or result.stdout or "Job failed"),
            )
            return

        for artifact_path in result.artifact_paths:
            self._register_artifact(job.id, artifact_path)
        self._jobs.mark_succeeded(job.id)

    async def _run_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self.run_one(job_id)
            except asyncio.CancelledError:
                message, canceled = self._interruption or (
                    "Gateway shutdown interrupted job",
                    False,
                )
                if canceled:
                    self._mark_canceled_if_active(job_id, message)
                else:
                    self._mark_failed_if_active(job_id, message)
                raise
            except Exception as exc:
                message = _redact_text(str(exc) or type(exc).__name__)
                self._last_error = message
                self._mark_failed_if_active(job_id, message)
            finally:
                self._queue.task_done()

    async def _stop_loop(self, message: str, *, canceled: bool) -> None:
        if self._task is None:
            return
        task = self._task
        self._task = None
        self._interruption = (message, canceled)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._interruption = None

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()

    def _mark_failed_if_active(self, job_id: str, message: str) -> None:
        try:
            job = self._jobs.get_job(job_id)
            if job.status in {"queued", "running"}:
                self._jobs.mark_failed(job_id, message)
        except (JobStatusError, KeyError):
            return

    def _mark_canceled_if_active(self, job_id: str, message: str) -> None:
        try:
            job = self._jobs.get_job(job_id)
            if job.status in {"queued", "running"}:
                self._jobs.cancel_job(job_id, message)
        except (JobStatusError, KeyError):
            return

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


def _redact_text(value: str) -> str:
    redacted = value
    for name in ("AGENT_WEB_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        redacted = redacted.replace(name, "[redacted]")
        secret = os.getenv(name)
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted[:2000]
