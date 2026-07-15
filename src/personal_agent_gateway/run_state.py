import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal


RunStatus = Literal["idle", "running", "waiting_approval", "failed"]


@dataclass(frozen=True)
class SessionRunState:
    session_id: str
    request_id: str
    started_at: datetime


class SessionAlreadyRunningError(RuntimeError):
    pass


class SessionRunRegistry:
    def __init__(self) -> None:
        self._running: dict[str, SessionRunState] = {}
        self._tasks: dict[str, tuple[str, asyncio.Task]] = {}
        self._lock = Lock()

    def start(self, session_id: str, request_id: str) -> None:
        with self._lock:
            if session_id in self._running:
                raise SessionAlreadyRunningError(session_id)
            self._running[session_id] = SessionRunState(
                session_id=session_id,
                request_id=request_id,
                started_at=datetime.now(UTC),
            )

    def start_if_exists(self, session_id: str, request_id: str, exists: Callable[[], bool]) -> bool:
        with self._lock:
            if session_id in self._running:
                raise SessionAlreadyRunningError(session_id)
            if not exists():
                return False
            self._running[session_id] = SessionRunState(
                session_id=session_id,
                request_id=request_id,
                started_at=datetime.now(UTC),
            )
            return True

    def finish(self, session_id: str, request_id: str | None = None) -> None:
        with self._lock:
            current = self._running.get(session_id)
            if current is None:
                return
            if request_id is not None and current.request_id != request_id:
                return
            self._running.pop(session_id, None)
            self._tasks.pop(session_id, None)

    def attach_task(self, session_id: str, request_id: str, task: asyncio.Task) -> None:
        with self._lock:
            current = self._running.get(session_id)
            if current is not None and current.request_id == request_id:
                self._tasks[session_id] = (request_id, task)

    def interrupt(self, session_id: str) -> bool:
        with self._lock:
            entry = self._tasks.get(session_id)
        if entry is None:
            return False
        entry[1].cancel()
        return True

    async def cancel_all(self) -> list[str]:
        with self._lock:
            entries = [
                (session_id, request_id, task)
                for session_id, (request_id, task) in self._tasks.items()
            ]
        for _session_id, _request_id, task in entries:
            task.cancel()
        if entries:
            await asyncio.gather(
                *(task for _session_id, _request_id, task in entries),
                return_exceptions=True,
            )
        for session_id, request_id, _task in entries:
            self.finish(session_id, request_id)
        return [session_id for session_id, _request_id, _task in entries]

    def is_running(self, session_id: str | None) -> bool:
        with self._lock:
            return isinstance(session_id, str) and session_id in self._running

    def delete_if_idle(self, session_id: str, delete: Callable[[], bool]) -> bool:
        with self._lock:
            if session_id in self._running:
                raise SessionAlreadyRunningError(session_id)
            return delete()

    def status(self, session_id: str | None, has_pending: bool, has_failed: bool) -> RunStatus:
        if self.is_running(session_id):
            return "running"
        if has_failed:
            return "failed"
        if has_pending:
            return "waiting_approval"
        return "idle"


class TeamRunRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._lock = Lock()

    def register(self, team_run_id: str, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks[team_run_id] = task
            self._cancel_reasons.pop(team_run_id, None)

    def is_running(self, team_run_id: str) -> bool:
        with self._lock:
            return team_run_id in self._tasks

    def cancel(self, team_run_id: str, reason: str = "user") -> bool:
        with self._lock:
            task = self._tasks.get(team_run_id)
            if task is not None:
                self._cancel_reasons[team_run_id] = reason
        if task is None:
            return False
        task.cancel()
        return True

    async def cancel_all(self, reason: str) -> list[str]:
        with self._lock:
            entries = list(self._tasks.items())
            for team_run_id, _task in entries:
                self._cancel_reasons[team_run_id] = reason
        for _team_run_id, task in entries:
            task.cancel()
        if entries:
            await asyncio.gather(*(task for _team_run_id, task in entries), return_exceptions=True)
        return [team_run_id for team_run_id, _task in entries]

    def cancel_reason(self, team_run_id: str) -> str | None:
        with self._lock:
            return self._cancel_reasons.get(team_run_id)

    def finish(self, team_run_id: str) -> None:
        with self._lock:
            self._tasks.pop(team_run_id, None)
            self._cancel_reasons.pop(team_run_id, None)
