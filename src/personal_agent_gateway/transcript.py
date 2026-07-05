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
]


class TranscriptEvent(BaseModel):
    id: str
    transcript_id: str
    kind: TranscriptKind
    payload: dict[str, object]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TranscriptStore:
    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir

    def start_new(self) -> str:
        transcript_id = uuid4().hex
        self._write_active(transcript_id)
        return transcript_id

    def active_id(self) -> str | None:
        return self._read_active_id()

    def append(
        self,
        kind: TranscriptKind,
        payload: dict[str, object],
    ) -> TranscriptEvent:
        transcript_id = self._read_active_id()
        if transcript_id is None:
            transcript_id = self.start_new()

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

        transcript_path = self._transcript_path(transcript_id)
        if not transcript_path.exists():
            return []

        events: list[TranscriptEvent] = []
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            events.append(TranscriptEvent.model_validate(json.loads(line)))
        return events

    def reset(self) -> str:
        return self.start_new()

    def _active_path(self) -> Path:
        return self._session_dir / "active.json"

    def _transcript_path(self, transcript_id: str) -> Path:
        return self._session_dir / f"{transcript_id}.jsonl"

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
