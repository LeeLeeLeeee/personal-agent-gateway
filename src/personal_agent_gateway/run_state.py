from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


RunStatus = Literal["idle", "running", "waiting_approval", "failed"]


@dataclass(frozen=True)
class SessionRunState:
    session_id: str
    request_id: str
    started_at: datetime


class SessionRunRegistry:
    def __init__(self) -> None:
        self._running: dict[str, SessionRunState] = {}

    def start(self, session_id: str, request_id: str) -> None:
        self._running[session_id] = SessionRunState(
            session_id=session_id,
            request_id=request_id,
            started_at=datetime.now(UTC),
        )

    def finish(self, session_id: str) -> None:
        self._running.pop(session_id, None)

    def is_running(self, session_id: str | None) -> bool:
        return isinstance(session_id, str) and session_id in self._running

    def status(self, session_id: str | None, has_pending: bool, has_failed: bool) -> RunStatus:
        if self.is_running(session_id):
            return "running"
        if has_failed:
            return "failed"
        if has_pending:
            return "waiting_approval"
        return "idle"
