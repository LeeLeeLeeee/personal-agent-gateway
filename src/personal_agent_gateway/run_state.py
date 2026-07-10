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
