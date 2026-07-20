import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor

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
    "session_metadata_set",
    "agent_session_link",
]
SessionStatus = Literal["idle", "waiting_approval", "failed"]
SessionOrigin = Literal["chat", "hook"]


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
    origin: SessionOrigin = "chat"
    hook_run_id: str | None = None


class TranscriptStore:
    def __init__(self, session_dir: Path, database: Database | None = None) -> None:
        self._session_dir = session_dir
        self._database = database
        if database is not None:
            self._rebuild_metadata_index()

    def attach_database(self, database: Database) -> None:
        self._database = database
        self._rebuild_metadata_index()

    def start_new(
        self,
        *,
        origin: SessionOrigin = "chat",
        hook_run_id: str | None = None,
        activate: bool = True,
    ) -> str:
        transcript_id = uuid4().hex
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._transcript_path(transcript_id).touch(exist_ok=True)
        if activate:
            self._write_active(transcript_id)
        if origin != "chat" or hook_run_id is not None:
            self.append_to(
                transcript_id,
                "session_metadata_set",
                {"origin": origin, "hook_run_id": hook_run_id},
            )
        else:
            self._refresh_metadata(transcript_id)
        return transcript_id

    def active_id(self) -> str | None:
        return self._read_active_id()

    def exists(self, transcript_id: str) -> bool:
        return self._transcript_path(transcript_id).exists()

    def list_sessions(self, origin: SessionOrigin | None = None) -> list[SessionSummary]:
        active_id = self._read_active_id()
        if self._database is not None:
            where = "where origin = ?" if origin is not None else ""
            parameters = (origin,) if origin is not None else ()
            rows = self._database.fetchall(
                f"select * from transcript_metadata {where} "
                "order by updated_at desc, id desc",
                parameters,
            )
            return [_metadata_summary(row, active_id) for row in rows]
        sessions = [
            self._session_summary(transcript_id, active_id)
            for transcript_id in self._known_session_ids(active_id)
        ]
        if origin is not None:
            sessions = [session for session in sessions if session.origin == origin]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def page_sessions(
        self,
        limit: int = 100,
        cursor: str | None = None,
        query: str | None = None,
        origin: SessionOrigin | None = None,
    ) -> tuple[list[SessionSummary], str | None]:
        normalized_limit = max(1, min(limit, 200))
        if self._database is None:
            sessions = (
                self.search_sessions(query, origin=origin)
                if query
                else self.list_sessions(origin=origin)
            )
            return sessions[:normalized_limit], None

        clauses: list[str] = []
        parameters: list[object] = []
        if origin is not None:
            clauses.append("origin = ?")
            parameters.append(origin)
        if query:
            clauses.append("lower(title) like ?")
            parameters.append(f"%{query.strip().lower()}%")
        if cursor:
            updated_at, transcript_id = decode_cursor(cursor, 2)
            if not isinstance(updated_at, str) or not isinstance(transcript_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(updated_at < ? or (updated_at = ? and id < ?))")
            parameters.extend((updated_at, updated_at, transcript_id))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        rows = self._database.fetchall(
            f"select * from transcript_metadata {where} "
            "order by updated_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        active_id = self._read_active_id()
        sessions = [_metadata_summary(row, active_id) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["updated_at"], last["id"])
        return sessions, next_cursor

    def search_sessions(
        self,
        query: str,
        origin: SessionOrigin | None = None,
    ) -> list[SessionSummary]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        if self._database is not None:
            clauses = ["lower(title) like ?"]
            parameters: list[object] = [f"%{normalized_query}%"]
            if origin is not None:
                clauses.append("origin = ?")
                parameters.append(origin)
            rows = self._database.fetchall(
                f"select * from transcript_metadata where {' and '.join(clauses)} "
                "order by updated_at desc, id desc",
                parameters,
            )
            active_id = self._read_active_id()
            return [_metadata_summary(row, active_id) for row in rows]

        active_id = self._read_active_id()
        matched_sessions: list[SessionSummary] = []
        for transcript_id in self._known_session_ids(active_id):
            events = self._load(transcript_id)
            haystack = "\n".join(
                json.dumps(event.payload, ensure_ascii=False).lower() for event in events
            )
            if normalized_query in haystack:
                summary = self._session_summary(transcript_id, active_id)
                if origin is None or summary.origin == origin:
                    matched_sessions.append(summary)
        return sorted(matched_sessions, key=lambda session: session.updated_at, reverse=True)

    def session_origin(self, transcript_id: str) -> SessionOrigin | None:
        if not self.exists(transcript_id):
            return None
        if self._database is not None:
            row = self._database.fetchone(
                "select origin from transcript_metadata where id = ?",
                (transcript_id,),
            )
            if row is not None:
                return row["origin"]
        origin, _hook_run_id = _session_metadata(self._load(transcript_id))
        return origin

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
        self._index_event(event)
        return True

    def delete(self, transcript_id: str) -> bool:
        transcript_path = self._transcript_path(transcript_id)
        active_id = self._read_active_id()
        if not transcript_path.exists():
            return False

        transcript_path.unlink()
        if self._database is not None:
            self._database.execute(
                "delete from transcript_metadata where id = ?", (transcript_id,)
            )
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
        self._index_event(event)
        return event

    def load_active(self) -> list[TranscriptEvent]:
        transcript_id = self._read_active_id()
        if transcript_id is None:
            return []

        return self._load(transcript_id)

    def load(self, transcript_id: str) -> list[TranscriptEvent]:
        return self._load(transcript_id)

    def page_events(
        self, transcript_id: str, limit: int = 500, cursor: str | None = None
    ) -> tuple[list[TranscriptEvent], str | None]:
        events = self._load(transcript_id)
        end = len(events)
        if cursor:
            values = decode_cursor(cursor, 1)
            if not isinstance(values[0], int) or values[0] < 0 or values[0] > end:
                raise ValueError("Invalid cursor")
            end = values[0]
        normalized_limit = max(1, min(limit, 1000))
        start = max(0, end - normalized_limit)
        next_cursor = encode_cursor(start) if start > 0 else None
        return events[start:end], next_cursor

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
        origin, hook_run_id = _session_metadata(events)
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
            origin=origin,
            hook_run_id=hook_run_id,
        )

    def _rebuild_metadata_index(self) -> None:
        if self._database is None:
            return
        active_id = self._read_active_id()
        session_ids = self._known_session_ids(active_id)
        with self._database.connection() as connection:
            if session_ids:
                placeholders = ", ".join("?" for _ in session_ids)
                connection.execute(
                    f"delete from transcript_metadata where id not in ({placeholders})",
                    session_ids,
                )
            else:
                connection.execute("delete from transcript_metadata")
            for transcript_id in session_ids:
                summary = self._session_summary(transcript_id, active_id)
                pending = _pending_shell_approval_ids(self._load(transcript_id))
                _upsert_metadata(connection, summary, pending)

    def _refresh_metadata(self, transcript_id: str) -> None:
        if self._database is None:
            return
        summary = self._session_summary(transcript_id, self._read_active_id())
        pending = _pending_shell_approval_ids(self._load(transcript_id))
        with self._database.connection() as connection:
            _upsert_metadata(connection, summary, pending)

    def _index_event(self, event: TranscriptEvent) -> None:
        if self._database is None:
            return
        row = self._database.fetchone(
            "select * from transcript_metadata where id = ?", (event.transcript_id,)
        )
        if row is None:
            self._refresh_metadata(event.transcript_id)
            return

        pending = set(json.loads(row["pending_approval_ids_json"]))
        tool_id = str(event.payload.get("id", ""))
        if event.kind == "tool_request" and event.payload.get("name") == "shell.run":
            pending.add(tool_id)
        elif event.kind in {"tool_result", "tool_denial"}:
            pending.discard(tool_id)

        title = str(row["title"])
        if event.kind == "session_rename":
            value = event.payload.get("title")
            if isinstance(value, str) and value.strip():
                title = _compact_title(value)
        elif event.kind == "user" and title == "Untitled session":
            value = event.payload.get("content") or event.payload.get("message")
            if isinstance(value, str) and value.strip():
                title = _compact_title(value)

        agent_id = str(row["agent_id"])
        model = str(row["model"])
        options = json.loads(row["options_json"])
        origin = str(row["origin"])
        hook_run_id = row["hook_run_id"]
        if event.kind == "session_config_set":
            value = event.payload.get("agent_id")
            agent_id = value if isinstance(value, str) else "codex"
            value = event.payload.get("model")
            model = value if isinstance(value, str) else "default"
            value = event.payload.get("options")
            options = dict(value) if isinstance(value, dict) else {}
        elif event.kind == "session_metadata_set":
            value = event.payload.get("origin")
            origin = value if value in {"chat", "hook"} else "chat"
            value = event.payload.get("hook_run_id")
            hook_run_id = value if isinstance(value, str) and value else None

        status: SessionStatus
        if event.kind == "runtime_error":
            status = "failed"
        elif pending:
            status = "waiting_approval"
        else:
            status = "idle"
        self._database.execute(
            """
            update transcript_metadata
            set title = ?, updated_at = ?, message_count = ?, status = ?,
                agent_id = ?, model = ?, options_json = ?, editable = ?,
                pending_approval_ids_json = ?, origin = ?, hook_run_id = ?
            where id = ?
            """,
            (
                title,
                event.created_at.isoformat(),
                int(row["message_count"]) + int(event.kind in {"user", "assistant"}),
                status,
                agent_id,
                model,
                json.dumps(options, ensure_ascii=False, sort_keys=True),
                int(bool(row["editable"]) and event.kind in {
                    "session_config_set", "session_metadata_set",
                    "session_rename", "agent_session_link"
                }),
                json.dumps(sorted(pending)),
                origin,
                hook_run_id,
                event.transcript_id,
            ),
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


def _session_metadata(
    events: list[TranscriptEvent],
) -> tuple[SessionOrigin, str | None]:
    for event in reversed(events):
        if event.kind != "session_metadata_set":
            continue
        origin = event.payload.get("origin")
        hook_run_id = event.payload.get("hook_run_id")
        return (
            origin if origin in {"chat", "hook"} else "chat",
            hook_run_id if isinstance(hook_run_id, str) and hook_run_id else None,
        )
    return "chat", None


def _is_session_editable(events: list[TranscriptEvent]) -> bool:
    return all(
        event.kind in {
            "session_config_set",
            "session_metadata_set",
            "session_rename",
            "agent_session_link",
        }
        for event in events
    )


def _has_pending_shell_approval(events: list[TranscriptEvent]) -> bool:
    return bool(_pending_shell_approval_ids(events))


def _pending_shell_approval_ids(events: list[TranscriptEvent]) -> set[str]:
    pending_by_tool_id: set[str] = set()
    for event in events:
        payload = event.payload
        if event.kind == "tool_request" and payload.get("name") == "shell.run":
            pending_by_tool_id.add(str(payload.get("id", "")))
        elif event.kind in {"tool_result", "tool_denial"}:
            pending_by_tool_id.discard(str(payload.get("id", "")))
    return pending_by_tool_id


def _upsert_metadata(connection, summary: SessionSummary, pending: set[str]) -> None:
    connection.execute(
        """
        insert into transcript_metadata (
            id, title, created_at, updated_at, message_count, status,
            agent_id, model, options_json, editable, pending_approval_ids_json,
            origin, hook_run_id
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(id) do update set
            title = excluded.title,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            message_count = excluded.message_count,
            status = excluded.status,
            agent_id = excluded.agent_id,
            model = excluded.model,
            options_json = excluded.options_json,
            editable = excluded.editable,
            pending_approval_ids_json = excluded.pending_approval_ids_json,
            origin = excluded.origin,
            hook_run_id = excluded.hook_run_id
        """,
        (
            summary.id,
            summary.title,
            summary.created_at.isoformat(),
            summary.updated_at.isoformat(),
            summary.message_count,
            summary.status,
            summary.agent_id,
            summary.model,
            json.dumps(summary.options, ensure_ascii=False, sort_keys=True),
            int(summary.editable),
            json.dumps(sorted(pending)),
            summary.origin,
            summary.hook_run_id,
        ),
    )


def _metadata_summary(row: object, active_id: str | None) -> SessionSummary:
    return SessionSummary(
        id=row["id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        message_count=int(row["message_count"]),
        status=row["status"],
        is_active=row["id"] == active_id,
        agent_id=row["agent_id"],
        model=row["model"],
        options=json.loads(row["options_json"]),
        editable=bool(row["editable"]),
        origin=row["origin"],
        hook_run_id=row["hook_run_id"],
    )
