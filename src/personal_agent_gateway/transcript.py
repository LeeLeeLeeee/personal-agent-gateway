import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

TranscriptKind = Literal[
    "user",
    "assistant",
    "tool_request",
    "approval",
    "tool_result",
    "tool_denial",
    "runtime_error",
    "session_rename",
    "session_config_set",
    "agent_session_link",
]
SessionStatus = Literal["idle", "waiting_approval", "failed"]


class TranscriptEvent(BaseModel):
    id: str
    transcript_id: str
    kind: TranscriptKind
    payload: dict[str, object]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    status: SessionStatus
    is_active: bool
    agent_id: str = "codex"
    model: str = "default"
    options: dict[str, object] = Field(default_factory=dict)
    editable: bool = True


class TranscriptStore:
    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir

    def start_new(self) -> str:
        transcript_id = uuid4().hex
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._transcript_path(transcript_id).touch(exist_ok=True)
        self._write_active(transcript_id)
        return transcript_id

    def active_id(self) -> str | None:
        return self._read_active_id()

    def exists(self, transcript_id: str) -> bool:
        return self._transcript_path(transcript_id).exists()

    def list_sessions(self) -> list[SessionSummary]:
        active_id = self._read_active_id()
        sessions = [
            self._session_summary(transcript_id, active_id)
            for transcript_id in self._known_session_ids(active_id)
        ]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def search_sessions(self, query: str) -> list[SessionSummary]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        active_id = self._read_active_id()
        matched_sessions: list[SessionSummary] = []
        for transcript_id in self._known_session_ids(active_id):
            events = self._load(transcript_id)
            haystack = "\n".join(
                json.dumps(event.payload, ensure_ascii=False).lower() for event in events
            )
            if normalized_query in haystack:
                matched_sessions.append(self._session_summary(transcript_id, active_id))
        return sorted(matched_sessions, key=lambda session: session.updated_at, reverse=True)

    def activate(self, transcript_id: str) -> bool:
        if not self._transcript_path(transcript_id).exists():
            return False

        self._write_active(transcript_id)
        return True

    def set_title(self, transcript_id: str, title: str) -> bool:
        if not self._transcript_path(transcript_id).exists():
            return False

        event = TranscriptEvent(
            id=uuid4().hex,
            transcript_id=transcript_id,
            kind="session_rename",
            payload={"title": title},
        )
        with self._transcript_path(transcript_id).open("a", encoding="utf-8") as transcript_file:
            transcript_file.write(f"{event.model_dump_json()}\n")
        return True

    def delete(self, transcript_id: str) -> bool:
        transcript_path = self._transcript_path(transcript_id)
        active_id = self._read_active_id()
        if not transcript_path.exists():
            return False

        transcript_path.unlink()
        if active_id != transcript_id:
            return True

        active_path = self._active_path()
        if active_path.exists():
            active_path.unlink()
        return True

    def append(
        self,
        kind: TranscriptKind,
        payload: dict[str, object],
    ) -> TranscriptEvent:
        transcript_id = self._read_active_id()
        if transcript_id is None:
            transcript_id = self.start_new()

        return self.append_to(transcript_id, kind, payload)

    def append_to(
        self,
        transcript_id: str,
        kind: TranscriptKind,
        payload: dict[str, object],
    ) -> TranscriptEvent:
        event = TranscriptEvent(
            id=uuid4().hex,
            transcript_id=transcript_id,
            kind=kind,
            payload=payload,
        )
        self._session_dir.mkdir(parents=True, exist_ok=True)
        with self._transcript_path(transcript_id).open("a", encoding="utf-8") as transcript_file:
            transcript_file.write(f"{event.model_dump_json()}\n")
        return event

    def load_active(self) -> list[TranscriptEvent]:
        transcript_id = self._read_active_id()
        if transcript_id is None:
            return []

        return self._load(transcript_id)

    def load(self, transcript_id: str) -> list[TranscriptEvent]:
        return self._load(transcript_id)

    def reset(self) -> str:
        return self.start_new()

    def _active_path(self) -> Path:
        return self._session_dir / "active.json"

    def _transcript_path(self, transcript_id: str) -> Path:
        return self._session_dir / f"{transcript_id}.jsonl"

    def _load(self, transcript_id: str) -> list[TranscriptEvent]:
        transcript_path = self._transcript_path(transcript_id)
        if not transcript_path.exists():
            return []

        events: list[TranscriptEvent] = []
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            events.append(TranscriptEvent.model_validate(json.loads(line)))
        return events

    def _read_active_id(self) -> str | None:
        active_path = self._active_path()
        if not active_path.exists():
            return None

        active = json.loads(active_path.read_text(encoding="utf-8"))
        return active["transcript_id"]

    def _write_active(self, transcript_id: str) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._active_path().write_text(
            json.dumps({"transcript_id": transcript_id}),
            encoding="utf-8",
        )

    def _known_session_ids(self, active_id: str | None) -> list[str]:
        session_ids = {
            path.stem
            for path in self._session_dir.glob("*.jsonl")
            if path.is_file()
        }
        if active_id is not None:
            session_ids.add(active_id)
        return sorted(session_ids)

    def _session_summary(self, transcript_id: str, active_id: str | None) -> SessionSummary:
        events = self._load(transcript_id)
        created_at = _created_at(events, self._transcript_path(transcript_id))
        updated_at = events[-1].created_at if events else created_at
        agent_id, model, options = _session_agent_config(events)
        return SessionSummary(
            id=transcript_id,
            title=_session_title(events),
            created_at=created_at,
            updated_at=updated_at,
            message_count=sum(1 for event in events if event.kind in {"user", "assistant"}),
            status=_session_status(events),
            is_active=transcript_id == active_id,
            agent_id=agent_id,
            model=model,
            options=options,
            editable=_is_session_editable(events),
        )


def _created_at(events: list[TranscriptEvent], transcript_path: Path) -> datetime:
    if events:
        return events[0].created_at
    if transcript_path.exists():
        return datetime.fromtimestamp(transcript_path.stat().st_mtime, UTC)
    return datetime.now(UTC)


def _session_title(events: list[TranscriptEvent]) -> str:
    for event in reversed(events):
        if event.kind == "session_rename":
            title = event.payload.get("title")
            if isinstance(title, str) and title.strip():
                return _compact_title(title)
    for event in events:
        if event.kind != "user":
            continue
        content = event.payload.get("content") or event.payload.get("message")
        if isinstance(content, str) and content.strip():
            return _compact_title(content)
    return "Untitled session"


def _compact_title(content: str) -> str:
    title = " ".join(content.split())
    if len(title) <= 64:
        return title
    return f"{title[:61]}..."


def _session_status(events: list[TranscriptEvent]) -> SessionStatus:
    if events and events[-1].kind == "runtime_error":
        return "failed"
    if _has_pending_shell_approval(events):
        return "waiting_approval"
    return "idle"


def _session_agent_config(events: list[TranscriptEvent]) -> tuple[str, str, dict[str, object]]:
    for event in reversed(events):
        if event.kind != "session_config_set":
            continue
        agent_id = event.payload.get("agent_id")
        model = event.payload.get("model")
        options = event.payload.get("options")
        return (
            agent_id if isinstance(agent_id, str) else "codex",
            model if isinstance(model, str) else "default",
            dict(options) if isinstance(options, dict) else {},
        )
    return "codex", "default", {}


def _is_session_editable(events: list[TranscriptEvent]) -> bool:
    return all(
        event.kind in {"session_config_set", "session_rename", "agent_session_link"}
        for event in events
    )


def _has_pending_shell_approval(events: list[TranscriptEvent]) -> bool:
    pending_by_tool_id: set[str] = set()
    for event in events:
        payload = event.payload
        if event.kind == "tool_request" and payload.get("name") == "shell.run":
            pending_by_tool_id.add(str(payload.get("id", "")))
        elif event.kind in {"tool_result", "tool_denial"}:
            pending_by_tool_id.discard(str(payload.get("id", "")))
    return bool(pending_by_tool_id)
