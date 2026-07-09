# Session-Scoped Chat and SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make chat runtime state, SSE activity, running status, approvals, and history loading owned by each session instead of by one app-wide chat state.

**Architecture:** Keep native Codex/Claude resume behavior unchanged. Add a durable `session_activity_events` read model for UI activity, keep transcript JSONL as the model-context source of truth, expose session-explicit chat APIs, and refactor the React chat container around `activeSessionId` plus `sessionStateById`.

**Tech Stack:** FastAPI, Pydantic, SQLite through existing `Database`, JSONL transcript store, React/Vite, Vitest, pytest.

## Global Constraints

- Source spec: `docs/specs/2026-07-09-session-scoped-chat-and-sse-spec.md`.
- Do not change Codex/Claude native session resume semantics.
- Do not introduce Redis, Kafka, hosted brokers, or a new external database.
- Preserve existing active-session endpoints as compatibility wrappers while new frontend code moves to session-explicit endpoints.
- Chat/runtime SSE events must include `session_id`, server `id`, server `created_at`, `type`, `source`, and `payload`.
- Team Run SSE events may keep `team_run_id` routing and must not be forced into chat session state.
- Keep transcript JSONL as model context; do not store UI-only activity as model messages.
- Use TDD for each task: write failing tests, verify failure, implement, verify pass.

---

## File Map

- Create: `src/personal_agent_gateway/session_activity.py`
  - Owns durable session activity rows and conversion to API/SSE payloads.
- Create: `src/personal_agent_gateway/run_state.py`
  - Owns session-scoped running/waiting state.
- Modify: `src/personal_agent_gateway/db.py`
  - Adds `session_activity_events` schema and migrations.
- Modify: `src/personal_agent_gateway/app.py`
  - Wires activity persistence, session-explicit APIs, run state, and delete cleanup.
- Modify: `src/personal_agent_gateway/runtime_factory.py`
  - Adds `create_runtime_for_session(session_id)` and uses the passed session id when publishing Codex events.
- Modify: `src/personal_agent_gateway/transcript.py`
  - Adds `exists(transcript_id)` for explicit API validation.
- Modify: `frontend/src/api/client.js`
  - Adds session-explicit API methods.
- Modify: `frontend/src/lib/timeline.js`
  - Supports durable activity envelopes, deterministic merge, and stable keys.
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
  - Replaces global chat live state with `sessionStateById`.
- Tests:
  - `tests/test_session_activity.py`
  - `tests/test_app.py`
  - `tests/test_events.py`
  - `tests/test_model_client.py` only if existing native resume tests need import adjustment
  - `frontend/src/api/client.test.js`
  - `frontend/src/lib/timeline.test.js`
  - `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

---

### Task 1: Durable Session Activity Store

**Files:**
- Create: `src/personal_agent_gateway/session_activity.py`
- Modify: `src/personal_agent_gateway/db.py`
- Test: `tests/test_session_activity.py`

**Interfaces:**
- Produces: `SessionActivityService.record(session_id: str, event_type: str, source: str, payload: dict[str, object], transcript_event_id: str | None = None) -> SessionActivityEvent`
- Produces: `SessionActivityService.list(session_id: str) -> list[SessionActivityEvent]`
- Produces: `SessionActivityService.delete_session(session_id: str) -> None`
- Produces: `SessionActivityEvent.to_event_payload() -> dict[str, object]`

- [ ] **Step 1: Write the failing activity persistence tests**

Create `tests/test_session_activity.py`:

```python
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.session_activity import SessionActivityService


def test_session_activity_records_monotonic_sequence_per_session(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)

    first = service.record(
        session_id="session-a",
        event_type="runtime.user_message.started",
        source="runtime",
        payload={"message": "hello"},
    )
    second = service.record(
        session_id="session-a",
        event_type="runtime.completed",
        source="runtime",
        payload={"pending_approval": None},
    )
    other = service.record(
        session_id="session-b",
        event_type="runtime.user_message.started",
        source="runtime",
        payload={"message": "other"},
    )

    assert first.event_seq == 1
    assert second.event_seq == 2
    assert other.event_seq == 1
    assert [event.event_seq for event in service.list("session-a")] == [1, 2]


def test_session_activity_payload_is_api_ready_and_deletable(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)

    event = service.record(
        session_id="session-a",
        event_type="codex.event",
        source="codex",
        payload={"item": {"type": "agent_message", "text": "done"}},
        transcript_event_id="transcript-event-1",
    )

    assert event.to_event_payload() == {
        "id": event.id,
        "session_id": "session-a",
        "event_seq": 1,
        "created_at": event.created_at.isoformat().replace("+00:00", "Z"),
        "type": "codex.event",
        "source": "codex",
        "payload": {"item": {"type": "agent_message", "text": "done"}},
        "transcript_event_id": "transcript-event-1",
    }

    service.delete_session("session-a")

    assert service.list("session-a") == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_session_activity.py -q`

Expected: FAIL because `personal_agent_gateway.session_activity` does not exist.

- [ ] **Step 3: Add the SQLite schema**

Modify `SCHEMA_SQL` in `src/personal_agent_gateway/db.py` by appending this table before the closing triple quote:

```sql
create table if not exists session_activity_events (
    id integer primary key autoincrement,
    session_id text not null,
    event_seq integer not null,
    event_type text not null,
    source text not null,
    payload_json text not null,
    transcript_event_id text,
    created_at text not null,
    unique(session_id, event_seq)
);

create index if not exists idx_session_activity_events_session_seq
on session_activity_events(session_id, event_seq);
```

- [ ] **Step 4: Add `SessionActivityService`**

Create `src/personal_agent_gateway/session_activity.py`:

```python
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from personal_agent_gateway.db import Database


class SessionActivityEvent(BaseModel):
    id: int
    session_id: str
    event_seq: int
    type: str
    source: str
    payload: dict[str, object]
    transcript_event_id: str | None
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["created_at"] = self.created_at.isoformat().replace("+00:00", "Z")
        return payload


class SessionActivityService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        session_id: str,
        event_type: str,
        source: str,
        payload: dict[str, object],
        transcript_event_id: str | None = None,
    ) -> SessionActivityEvent:
        created_at = datetime.now(UTC)
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._db.connect() as connection:
            row = connection.execute(
                "select coalesce(max(event_seq), 0) + 1 as next_seq "
                "from session_activity_events where session_id = ?",
                (session_id,),
            ).fetchone()
            event_seq = int(row["next_seq"])
            cursor = connection.execute(
                """
                insert into session_activity_events
                  (session_id, event_seq, event_type, source, payload_json, transcript_event_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_seq,
                    event_type,
                    source,
                    payload_json,
                    transcript_event_id,
                    created_at.isoformat(),
                ),
            )
            event_id = int(cursor.lastrowid)
        return SessionActivityEvent(
            id=event_id,
            session_id=session_id,
            event_seq=event_seq,
            type=event_type,
            source=source,
            payload=dict(payload),
            transcript_event_id=transcript_event_id,
            created_at=created_at,
        )

    def list(self, session_id: str) -> list[SessionActivityEvent]:
        rows = self._db.fetchall(
            """
            select id, session_id, event_seq, event_type, source, payload_json,
                   transcript_event_id, created_at
            from session_activity_events
            where session_id = ?
            order by event_seq asc
            """,
            (session_id,),
        )
        return [_event_from_row(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        self._db.execute("delete from session_activity_events where session_id = ?", (session_id,))


def _event_from_row(row: Any) -> SessionActivityEvent:
    return SessionActivityEvent(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        event_seq=int(row["event_seq"]),
        type=str(row["event_type"]),
        source=str(row["source"]),
        payload=json.loads(str(row["payload_json"])),
        transcript_event_id=row["transcript_event_id"],
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_session_activity.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/session_activity.py tests/test_session_activity.py
git commit -m "feat: add session activity persistence"
```

---

### Task 2: Persist Chat Runtime Events Before SSE Fanout

**Files:**
- Modify: `src/personal_agent_gateway/session_activity.py`
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `tests/test_session_activity.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `SessionActivityService.record(...)`
- Produces: `SessionActivityPublisher.publish(event: dict[str, object]) -> dict[str, object]`
- Produces: `app.state.session_activity_service`

- [ ] **Step 1: Write failing publisher tests**

Append to `tests/test_session_activity.py`:

```python
import asyncio

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.session_activity import SessionActivityPublisher


def test_session_activity_publisher_persists_before_fanout(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    bus = EventBus()
    publisher = SessionActivityPublisher(service, bus)

    published = asyncio.run(publisher.publish({
        "type": "runtime.user_message.started",
        "session_id": "session-a",
        "message": "hello",
    }))

    assert published["id"] == 1
    assert published["event_seq"] == 1
    assert published["source"] == "runtime"
    assert published["payload"] == {"message": "hello"}
    assert published["message"] == "hello"
    assert bus.recent() == [published]
    assert service.list("session-a")[0].to_event_payload()["payload"] == {"message": "hello"}


def test_session_activity_publisher_keeps_team_events_out_of_chat_activity(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    bus = EventBus()
    publisher = SessionActivityPublisher(service, bus)

    published = asyncio.run(publisher.publish({
        "type": "team.run.started",
        "team_run_id": "run-1",
    }))

    assert published == {"id": 1, "type": "team.run.started", "team_run_id": "run-1"}
    assert service.list("run-1") == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_session_activity.py -q`

Expected: FAIL because `SessionActivityPublisher` does not exist.

- [ ] **Step 3: Implement the publisher**

Append to `src/personal_agent_gateway/session_activity.py`:

```python
from personal_agent_gateway.events import EventBus


class SessionActivityPublisher:
    def __init__(self, service: SessionActivityService, event_bus: EventBus) -> None:
        self._service = service
        self._event_bus = event_bus

    async def publish(self, event: dict[str, object]) -> dict[str, object]:
        event_type = str(event.get("type", ""))
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or event_type.startswith("team."):
            return await self._event_bus.publish(event)

        payload = {
            key: value
            for key, value in event.items()
            if key not in {"id", "type", "session_id", "event_seq", "created_at", "source"}
        }
        source = _source_for_event_type(event_type)
        activity = self._service.record(
            session_id=session_id,
            event_type=event_type,
            source=source,
            payload=payload,
        )
        normalized = activity.to_event_payload()
        legacy = {key: value for key, value in payload.items() if key not in {"payload"}}
        return await self._event_bus.publish({**normalized, **legacy})


def _source_for_event_type(event_type: str) -> str:
    if event_type.startswith("codex."):
        return "codex"
    if event_type.startswith("claude."):
        return "claude"
    if event_type.startswith("artifact."):
        return "system"
    if event_type.startswith("approval."):
        return "runtime"
    return "runtime"
```

- [ ] **Step 4: Wire publisher into app runtime only**

Modify `_attach_local_services(...)` in `src/personal_agent_gateway/app.py`:

```python
from personal_agent_gateway.session_activity import SessionActivityPublisher, SessionActivityService
```

Inside `_attach_local_services`, after `db.initialize()`:

```python
    session_activity_service = SessionActivityService(db)
    session_activity_publisher = SessionActivityPublisher(session_activity_service, event_bus)
```

Then change runtime factory construction:

```python
    runtime_factory = AgentRuntimeFactory(config, transcript, job_service, session_activity_publisher)
```

And expose the service:

```python
    app.state.session_activity_service = session_activity_service
    app.state.session_activity_publisher = session_activity_publisher
```

Leave `TeamRuntime(..., event_bus)` unchanged in `create_app()`.

- [ ] **Step 5: Wire injected runtime to publisher**

Modify `create_app()` so injected chat runtime uses activity publisher after `_attach_local_services(...)` has run:

```python
    injected_runtime = runtime
    if injected_runtime is not None and hasattr(injected_runtime, "attach_event_bus"):
        injected_runtime.attach_event_bus(app.state.session_activity_publisher)
```

- [ ] **Step 6: Update runtime event test expectations**

In `tests/test_app.py`, update `test_chat_records_runtime_events_for_sse_subscribers` to assert the normalized envelope:

```python
    assert [event["type"] for event in recent] == [
        "runtime.user_message.started",
        "runtime.completed",
    ]
    assert recent[0]["session_id"] == recent[1]["session_id"]
    assert recent[0]["source"] == "runtime"
    assert recent[0]["event_seq"] == 1
    assert recent[0]["payload"] == {"message": "remember this"}
    assert recent[0]["message"] == "remember this"
    assert recent[1]["event_seq"] == 2
    assert recent[1]["payload"] == {"pending_approval": None}
```

- [ ] **Step 7: Run tests to verify pass**

Run:

```bash
python -m pytest tests/test_session_activity.py tests/test_app.py::test_chat_records_runtime_events_for_sse_subscribers -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/session_activity.py src/personal_agent_gateway/app.py tests/test_session_activity.py tests/test_app.py
git commit -m "feat: persist session activity before sse fanout"
```

---

### Task 3: Session-Scoped Running State and Explicit Backend APIs

**Files:**
- Create: `src/personal_agent_gateway/run_state.py`
- Modify: `src/personal_agent_gateway/transcript.py`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `TranscriptStore.exists(transcript_id: str) -> bool`
- Produces: `AgentRuntimeFactory.create_runtime_for_session(session_id: str) -> AgentRuntime`
- Produces: `SessionRunRegistry.start(session_id: str, request_id: str) -> None`
- Produces: `SessionRunRegistry.finish(session_id: str) -> None`
- Produces: `SessionRunRegistry.status(session_id: str, has_pending: bool, has_failed: bool) -> str`
- Produces: `GET /api/sessions/{session_id}/history`
- Produces: `GET /api/sessions/{session_id}/activity`
- Produces: `GET /api/sessions/{session_id}/status`
- Produces: `POST /api/sessions/{session_id}/chat`
- Produces: `POST /api/sessions/{session_id}/approvals/{approval_id}/approve`
- Produces: `POST /api/sessions/{session_id}/approvals/{approval_id}/deny`

- [ ] **Step 1: Write failing backend API tests**

Append to `tests/test_app.py`:

```python
def test_session_explicit_history_status_and_activity_do_not_activate_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    first_id = store.start_new()
    store.append_to(first_id, "user", {"content": "first"})
    second_id = store.start_new()
    store.append_to(second_id, "user", {"content": "second"})
    client = auth_client(config, FakeRuntime())

    history = client.get(f"/api/sessions/{first_id}/history")
    status = client.get(f"/api/sessions/{first_id}/status")
    activity = client.get(f"/api/sessions/{first_id}/activity")

    assert history.status_code == 200
    assert history.json()["session_id"] == first_id
    assert history.json()["events"][0]["payload"] == {"content": "first"}
    assert status.status_code == 200
    assert status.json()["session_id"] == first_id
    assert status.json()["status"] == "idle"
    assert activity.status_code == 200
    assert activity.json() == {"session_id": first_id, "events": []}
    assert store.active_id() == second_id


def test_session_explicit_chat_writes_only_target_session(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    first_id = store.start_new()
    second_id = store.start_new()

    class FakeCodexModelClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def complete(self, messages):
            return ModelResponse(content=f"reply to {messages[-1]['content']}", tool_calls=[])

    monkeypatch.setattr("personal_agent_gateway.runtime_factory.CodexModelClient", FakeCodexModelClient)
    client = auth_client(config, runtime=None)

    response = client.post(f"/api/sessions/{first_id}/chat", json={"message": "targeted"})

    assert response.status_code == 200
    assert response.json()["session_id"] == first_id
    assert store.active_id() == second_id
    assert [(event.kind, event.payload) for event in store.load(first_id)] == [
        ("user", {"content": "targeted"}),
        ("assistant", {"content": "reply to targeted"}),
    ]
    assert store.load(second_id) == []
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_app.py::test_session_explicit_history_status_and_activity_do_not_activate_session tests/test_app.py::test_session_explicit_chat_writes_only_target_session -q
```

Expected: FAIL because session-explicit endpoints and `create_runtime_for_session` do not exist.

- [ ] **Step 3: Add transcript existence check**

Modify `src/personal_agent_gateway/transcript.py`:

```python
    def exists(self, transcript_id: str) -> bool:
        return self._transcript_path(transcript_id).exists()
```

- [ ] **Step 4: Add run state registry**

Create `src/personal_agent_gateway/run_state.py`:

```python
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
```

- [ ] **Step 5: Add runtime factory method for explicit session**

Modify `src/personal_agent_gateway/runtime_factory.py`:

```python
    def create_runtime_for_session(self, session_id: str) -> AgentRuntime:
        return self._create_runtime_for_session_id(session_id)
```

Rename the body of `create_runtime_for_active_session()` into a private method and make active call delegate:

```python
    def create_runtime_for_active_session(self) -> AgentRuntime:
        session_id = self._transcript.active_id()
        if session_id is None:
            return self._create_runtime_for_app_config()
        return self._create_runtime_for_session_id(session_id)

    def _create_runtime_for_session_id(self, session_id: str) -> AgentRuntime:
        events = self._transcript.load(session_id)
        has_explicit_session_config = any(event.kind == "session_config_set" for event in events)
        if not has_explicit_session_config and self._config.model_provider != "codex":
            return self._create_runtime_for_app_config(session_id=session_id)
        ...
```

Change `_create_runtime_for_app_config` signature and body:

```python
    def _create_runtime_for_app_config(self, session_id: str | None = None) -> AgentRuntime:
        config = self._config
        effective_session_id = session_id if session_id is not None else self._transcript.active_id()
```

Use `effective_session_id` in Codex event publishing and `_runtime(..., session_id=effective_session_id)`.

- [ ] **Step 6: Add explicit API helpers in `app.py`**

Import:

```python
from uuid import uuid4

from personal_agent_gateway.run_state import SessionRunRegistry
```

In `create_app()` after `event_bus = EventBus()`:

```python
    run_registry = SessionRunRegistry()
    app.state.run_registry = run_registry
```

Add helpers inside `create_app()`:

```python
    def require_session_id(session_id: str) -> str:
        if not transcript.exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return session_id

    def runtime_for_session(session_id: str) -> AgentRuntime:
        if injected_runtime is not None:
            return injected_runtime
        return runtime_factory.create_runtime_for_session(session_id)

    async def chat_for_session(session_id: str, message: str) -> dict[str, object]:
        request_id = uuid4().hex
        run_registry.start(session_id, request_id)
        try:
            result = await runtime_for_session(session_id).handle_user_message(message)
            return {**_runtime_response(result), "session_id": session_id, "request_id": request_id}
        finally:
            run_registry.finish(session_id)
```

- [ ] **Step 7: Add explicit endpoints in `app.py`**

Add routes near existing session routes:

```python
    @app.get("/api/sessions/{session_id}/history")
    def session_history(session_id: str, _session: None = session_dependency) -> dict[str, object]:
        require_session_id(session_id)
        return {"session_id": session_id, "events": [_event_payload(event) for event in transcript.load(session_id)]}

    @app.get("/api/sessions/{session_id}/activity")
    def session_activity(session_id: str, _session: None = session_dependency) -> dict[str, object]:
        require_session_id(session_id)
        return {
            "session_id": session_id,
            "events": [
                event.to_event_payload()
                for event in app.state.session_activity_service.list(session_id)
            ],
        }

    @app.get("/api/sessions/{session_id}/status")
    def session_status(session_id: str, _session: None = session_dependency) -> dict[str, object]:
        require_session_id(session_id)
        events = transcript.load(session_id)
        effective_config = SessionAgentConfigService(transcript).effective_config(session_id)
        return {
            "session_id": session_id,
            "status": _session_status(events, session_id, run_registry),
            "pending_approval": _has_pending_shell_approval(events),
            "message_count": sum(1 for event in events if event.kind in {"user", "assistant"}),
            "last_event_id": _last_activity_event_id(app.state.session_activity_service.list(session_id)),
            "session_config": effective_config.model_dump(mode="json"),
        }

    @app.post("/api/sessions/{session_id}/chat")
    async def session_chat(
        session_id: str,
        request: ChatRequest,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        return await chat_for_session(session_id, request.message)
```

Add helper outside `create_app()`:

```python
def _last_activity_event_id(events: list[object]) -> int | None:
    if not events:
        return None
    return int(getattr(events[-1], "id"))
```

- [ ] **Step 8: Replace global running session helpers**

Change `_session_payload` and `_session_status` signatures:

```python
def _session_payload(session: BaseModel, run_registry: SessionRunRegistry) -> dict[str, object]:
    payload = session.model_dump(mode="json")
    payload["status"] = run_registry.status(
        str(payload["id"]),
        payload.get("status") == "waiting_approval",
        payload.get("status") == "failed",
    )
    return {str(key): value for key, value in payload.items()}


def _session_status(events: list[object], session_id: str | None, run_registry: SessionRunRegistry) -> str:
    return run_registry.status(
        session_id,
        _has_pending_shell_approval(events),
        bool(events and getattr(events[-1], "kind", "") == "runtime_error"),
    )
```

Update `/api/status`, `/api/sessions`, and `/api/sessions/search` to pass `run_registry`.

- [ ] **Step 9: Make active `/api/chat` a compatibility wrapper**

Replace the existing `/api/chat` body:

```python
        session_id = transcript.active_id() or transcript.start_new()
        return await chat_for_session(session_id, request.message)
```

- [ ] **Step 10: Delete activity with session delete**

Modify `delete_session()`:

```python
        app.state.session_activity_service.delete_session(session_id)
```

Call it after `transcript.delete(session_id)` succeeds.

- [ ] **Step 11: Run focused backend tests**

Run:

```bash
python -m pytest tests/test_app.py::test_session_explicit_history_status_and_activity_do_not_activate_session tests/test_app.py::test_session_explicit_chat_writes_only_target_session tests/test_app.py::test_sessions_api_lists_activate_delete_and_searches_sessions -q
```

Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/run_state.py src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/transcript.py tests/test_app.py
git commit -m "feat: add session explicit chat APIs"
```

---

### Task 4: Frontend API and Timeline Model for Session Activity

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`
- Modify: `frontend/src/lib/timeline.js`
- Modify: `frontend/src/lib/timeline.test.js`

**Interfaces:**
- Produces: `api.sessionHistory(id)`
- Produces: `api.sessionActivity(id)`
- Produces: `api.sessionStatus(id)`
- Produces: `api.sendSessionChat(id, message)`
- Produces: `api.approveSession(id, approvalId)`
- Produces: `api.denySession(id, approvalId)`
- Produces: `timelineFromSession(historyEvents, activityEvents)`

- [ ] **Step 1: Write failing API client tests**

Append to `frontend/src/api/client.test.js`:

```js
it("calls session-explicit chat APIs", async () => {
  installFetch({
    "GET /api/sessions/session-1/history": { events: [{ kind: "user" }] },
    "GET /api/sessions/session-1/activity": { events: [{ type: "runtime.completed" }] },
    "GET /api/sessions/session-1/status": { status: "idle" },
    "POST /api/sessions/session-1/chat": { messages: [] },
    "POST /api/sessions/session-1/approvals/approval-1/approve": { messages: [] },
    "POST /api/sessions/session-1/approvals/approval-1/deny": { messages: [] }
  });

  expect(await api.sessionHistory("session-1")).toEqual([{ kind: "user" }]);
  expect(await api.sessionActivity("session-1")).toEqual([{ type: "runtime.completed" }]);
  expect(await api.sessionStatus("session-1")).toEqual({ status: "idle" });
  await api.sendSessionChat("session-1", "hello");
  await api.approveSession("session-1", "approval-1");
  await api.denySession("session-1", "approval-1");

  expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/chat", expect.objectContaining({ method: "POST" }));
  expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/approve", expect.objectContaining({ method: "POST" }));
  expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/deny", expect.objectContaining({ method: "POST" }));
});
```

- [ ] **Step 2: Add client methods**

Append methods to `api` in `frontend/src/api/client.js`:

```js
  async sessionHistory(id) {
    return jsonList(await fetch(`/api/sessions/${encodeURIComponent(id)}/history`), "events");
  },
  async sessionActivity(id) {
    return jsonList(await fetch(`/api/sessions/${encodeURIComponent(id)}/activity`), "events");
  },
  async sessionStatus(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/status`));
  },
  async sendSessionChat(id, message) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    }));
  },
  async approveSession(id, approvalId) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/approve`, { method: "POST" }));
  },
  async denySession(id, approvalId) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/deny`, { method: "POST" }));
  },
```

- [ ] **Step 3: Write failing timeline merge tests**

Append to `frontend/src/lib/timeline.test.js`:

```js
it("merges transcript and durable activity in deterministic order", () => {
  const timeline = timelineFromSession(
    [{ kind: "user", created_at: "2026-07-09T01:00:00Z", payload: { content: "hello" } }],
    [
      {
        id: 10,
        event_seq: 1,
        type: "runtime.user_message.started",
        created_at: "2026-07-09T01:00:01Z",
        payload: { message: "hello" }
      },
      {
        id: 11,
        event_seq: 2,
        type: "codex.event",
        created_at: "2026-07-09T01:00:02Z",
        payload: { item: { type: "agent_message", id: "agent-1", text: "done" } }
      }
    ]
  );

  expect(timeline.map((entry) => entry.type)).toEqual(["user", "event_row", "agent"]);
  expect(timeline.map((entry) => entry.order)).toEqual([0, 1, 2]);
  expect(timeline[1].key).toBe("event:1");
  expect(timeline[2].key).toBe("agent:agent-1");
});

it("maps normalized SSE envelopes and keeps legacy raw Codex events working", () => {
  expect(entryFromSse({
    session_id: "session-1",
    event_seq: 3,
    type: "codex.event",
    created_at: "2026-07-09T01:00:02Z",
    payload: { item: { type: "agent_message", id: "agent-1", text: "done" } }
  })).toEqual(expect.objectContaining({
    type: "agent",
    key: "agent:agent-1",
    text: "done"
  }));

  expect(entryFromSse({ item: { type: "agent_message", text: "legacy" } }))
    .toEqual(expect.objectContaining({ type: "agent", text: "legacy" }));
});
```

Update the import:

```js
import { deriveLive, entryFromSse, timelineFromHistory, timelineFromSession } from "./timeline.js";
```

- [ ] **Step 4: Implement normalized timeline support**

Modify `frontend/src/lib/timeline.js`:

```js
export function timelineFromSession(historyEvents, activityEvents) {
  const historyEntries = timelineFromHistory(historyEvents);
  const activityEntries = activityEvents
    .map((event) => entryFromSse(event))
    .filter(Boolean);
  return [...historyEntries, ...activityEntries]
    .sort((left, right) => (left.serverOrder ?? left.order ?? 0) - (right.serverOrder ?? right.order ?? 0))
    .map((entry, index) => ({ ...entry, order: index }));
}
```

Change `entryFromSse(event)` to read normalized payload first:

```js
  const payload = event.payload && typeof event.payload === "object" ? event.payload : event;
  const item = payload.item;
```

Add stable keys and server order:

```js
      return {
        type: "command",
        key: `command:${event.session_id || ""}:${item.id || item.command || ""}`,
        ...
        serverOrder: event.event_seq
      };
```

For agent messages:

```js
      return {
        type: "agent",
        key: `agent:${item.id || event.event_seq || ""}`,
        text: item.text || "",
        time: fmtTime(event.created_at, false) || nowHM(),
        streaming: false,
        serverOrder: event.event_seq
      };
```

For runtime rows:

```js
    return {
      type: "event_row",
      key: `event:${event.event_seq || event.id || event.type}`,
      label: "runtime.user_message.started",
      detail: "message accepted",
      dotColor: "#000",
      time: fmtTime(event.created_at, true) || nowHMS(),
      serverOrder: event.event_seq
    };
```

- [ ] **Step 5: Run frontend focused tests**

Run:

```bash
cd frontend
npm test -- --run src/api/client.test.js src/lib/timeline.test.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/lib/timeline.js frontend/src/lib/timeline.test.js
git commit -m "feat: add session explicit frontend timeline model"
```

---

### Task 5: Frontend Session-Owned Chat State

**Files:**
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**Interfaces:**
- Consumes: `api.sessionHistory`, `api.sessionActivity`, `api.sessionStatus`, `api.sendSessionChat`
- Consumes: `timelineFromSession`, `entryFromSse`, `deriveLive`
- Produces: `sessionStateById` state in `GatewayApp`
- Produces: active ChatView and Statusbar derived only from active session state

- [ ] **Step 1: Write failing non-active SSE retention test**

Append to `GatewayApp.test.jsx`:

```jsx
it("keeps non-active session SSE entries in that session cache and shows them after activation", async () => {
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions: [
      sessions[0],
      { ...sessions[0], id: "session-2", title: "Background chat", is_active: false }
    ] },
    "GET /api/history": { events: [] },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/sessions/session-1/history": { events: [] },
    "GET /api/sessions/session-1/activity": { events: [] },
    "GET /api/sessions/session-1/status": { status: "idle", session_id: "session-1" },
    "GET /api/sessions/session-2/history": { events: [] },
    "GET /api/sessions/session-2/activity": { events: [] },
    "GET /api/sessions/session-2/status": { status: "idle", session_id: "session-2" },
    "POST /api/sessions/session-2/activate": { session_id: "session-2", events: [] }
  });

  render(<GatewayApp />);

  await screen.findByLabelText("Agent Gateway");
  const source = MockEventSource.instances[0];
  act(() => {
    source.emit({
      id: 50,
      session_id: "session-2",
      event_seq: 1,
      type: "codex.event",
      payload: { item: { type: "agent_message", id: "agent-2", text: "background answer" } }
    });
  });

  expect(screen.queryByText("background answer")).not.toBeInTheDocument();
  await userEvent.click(await screen.findByText("Background chat"));

  expect(await screen.findByText("background answer")).toBeInTheDocument();
});
```

- [ ] **Step 2: Write failing busy isolation test**

Append:

```jsx
it("does not disable active composer when another session is busy", async () => {
  installFetch({
    "GET /api/auth/status": { authenticated: true, totp_configured: true },
    "GET /api/status": status,
    "GET /api/sessions": { sessions: [
      sessions[0],
      { ...sessions[0], id: "session-2", title: "Background chat", status: "running", is_active: false }
    ] },
    "GET /api/history": { events: [] },
    "GET /api/agents": { agents: [] },
    "GET /api/sessions/active/config": { config: null },
    "GET /api/sessions/session-1/history": { events: [] },
    "GET /api/sessions/session-1/activity": { events: [] },
    "GET /api/sessions/session-1/status": { status: "idle", session_id: "session-1" }
  });

  render(<GatewayApp />);

  await screen.findByLabelText("Agent Gateway");
  const source = MockEventSource.instances[0];
  act(() => {
    source.emit({
      id: 51,
      session_id: "session-2",
      event_seq: 1,
      type: "runtime.user_message.started",
      payload: { message: "background work" }
    });
  });

  expect(screen.getByPlaceholderText("Message the agent, or describe a local action...")).not.toBeDisabled();
  expect(screen.getByText("AGENT IDLE")).toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd frontend
npm test -- --run src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: the new tests fail because non-active SSE entries are ignored and `busy` is global.

- [ ] **Step 4: Add session state helpers in `GatewayApp/index.jsx`**

Add near top-level helper functions:

```jsx
function emptyChatSessionState() {
  return {
    entries: [],
    pendingApproval: null,
    busy: false,
    turnStart: null,
    turnEnd: null,
    turnStreamed: false,
    nextLocalOrder: 0,
    lastServerEventId: null,
    lastLoadedAt: null
  };
}

function ensureSessionState(current, sessionId) {
  if (!sessionId) return current;
  if (current[sessionId]) return current;
  return { ...current, [sessionId]: emptyChatSessionState() };
}

function updateOneSession(current, sessionId, updater) {
  const base = current[sessionId] || emptyChatSessionState();
  return { ...current, [sessionId]: updater(base) };
}

function appendOrReconcileEntry(entries, entry) {
  if (!entry.key) return appendOrReconcileCommand(entries, entry);
  const index = entries.findIndex((candidate) => candidate.key === entry.key);
  if (index < 0) return appendOrReconcileCommand(entries, entry);
  const next = entries.slice();
  next[index] = { ...entries[index], ...entry, order: entries[index].order ?? entry.order };
  return next;
}
```

- [ ] **Step 5: Replace global chat state declarations**

Replace:

```jsx
  const [entries, setEntries] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [busy, setBusy] = useState(false);
  const [turnStart, setTurnStart] = useState(null);
  const [turnEnd, setTurnEnd] = useState(null);
  const [turnStreamed, setTurnStreamed] = useState(false);
```

With:

```jsx
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [sessionStateById, setSessionStateById] = useState({});
```

Then derive:

```jsx
  const activeSessionState = activeSessionId ? (sessionStateById[activeSessionId] || emptyChatSessionState()) : emptyChatSessionState();
  const entries = activeSessionState.entries;
  const pendingApproval = activeSessionState.pendingApproval;
  const busy = activeSessionState.busy;
  const turnStart = activeSessionState.turnStart;
  const turnEnd = activeSessionState.turnEnd;
  const turnStreamed = activeSessionState.turnStreamed;
```

- [ ] **Step 6: Replace single entry order ref with per-session local order**

Remove `entryOrderRef`. Add:

```jsx
  function stampSessionEntry(sessionId, state, entry) {
    if (entry.order != null) return { entry, nextLocalOrder: state.nextLocalOrder };
    const order = state.nextLocalOrder;
    return { entry: { ...entry, order }, nextLocalOrder: order + 1 };
  }
```

- [ ] **Step 7: Load active session into `sessionStateById`**

In `loadApp`, after `nextStatus`:

```jsx
    const sessionId = nextStatus?.session_id || null;
    setActiveSessionId(sessionId);
    activeSessionIdRef.current = sessionId;
    let nextEntries = timelineFromHistory(history);
    if (sessionId && api.sessionHistory && api.sessionActivity) {
      const [sessionHistory, sessionActivity] = await Promise.all([
        api.sessionHistory(sessionId),
        api.sessionActivity(sessionId)
      ]);
      nextEntries = timelineFromSession(sessionHistory, sessionActivity);
    }
    setSessionStateById((current) => sessionId
      ? { ...current, [sessionId]: { ...emptyChatSessionState(), entries: nextEntries, nextLocalOrder: nextEntries.length, lastLoadedAt: Date.now() } }
      : current
    );
```

Keep `api.history()` as compatibility fallback for the no-active-session boot path.

- [ ] **Step 8: Route SSE by session id without dropping non-active events**

Replace `shouldIgnoreScopedEvent` usage with:

```jsx
      if (parsed.session_id) {
        const sessionId = parsed.session_id;
        const entry = entryFromSse(parsed);
        setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
          const started = parsed.type === "runtime.user_message.started" ? Date.now() : state.turnStart;
          const ended = parsed.type === "runtime.completed" || parsed.type === "runtime.error" ? Date.now() : state.turnEnd;
          const busyNext = parsed.type === "runtime.user_message.started"
            ? true
            : parsed.type === "runtime.completed" || parsed.type === "runtime.error"
              ? false
              : state.busy;
          if (!entry) {
            return { ...state, busy: busyNext, turnStart: started, turnEnd: ended, lastServerEventId: parsed.id ?? state.lastServerEventId };
          }
          const stamped = stampSessionEntry(sessionId, state, entry);
          return {
            ...state,
            entries: appendOrReconcileEntry(state.entries, stamped.entry),
            busy: busyNext,
            turnStart: started,
            turnEnd: ended,
            turnStreamed: true,
            nextLocalOrder: stamped.nextLocalOrder,
            lastServerEventId: parsed.id ?? state.lastServerEventId
          };
        }));
        return;
      }
```

Keep Team Run routing after this block:

```jsx
      if (parsed.type?.startsWith("team.") && parsed.team_run_id === selectedTeamRunIdRef.current) {
        api.teamRunDetail(selectedTeamRunIdRef.current).then(setTeamRunDetail);
      }
```

- [ ] **Step 9: Send chat using active session id**

Change `handleSend(message)`:

```jsx
    let sessionId = activeSessionId;
    if (!sessionId) {
      const reset = await api.reset();
      sessionId = reset?.session_id || null;
      setActiveSessionId(sessionId);
      activeSessionIdRef.current = sessionId;
    }
    if (!sessionId) return;
```

Use per-session state update:

```jsx
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
      const stamped = stampSessionEntry(sessionId, state, { type: "user", text: message, time: nowHM(), key: `local:user:${started}` });
      return {
        ...state,
        entries: [...state.entries, stamped.entry],
        busy: true,
        turnStart: started,
        turnEnd: null,
        turnStreamed: false,
        nextLocalOrder: stamped.nextLocalOrder
      };
    }));
```

Call:

```jsx
      await postTurn(sessionId, await api.sendSessionChat(sessionId, message));
```

In `finally`, clear busy only for that session:

```jsx
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
        ...state,
        busy: false,
        turnEnd: Date.now()
      })));
```

- [ ] **Step 10: Make `postTurn` session-aware**

Change signature:

```jsx
  async function postTurn(sessionId, data) {
```

Update only that session:

```jsx
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
      const pending = data ? normalizeApproval(data.pending_approval) : null;
      const streamed = state.turnStreamed;
      const agentEntries = !streamed && data && Array.isArray(data.messages)
        ? data.messages.filter((message) => typeof message.content === "string")
            .map((message) => ({ type: "agent", text: message.content, time: nowHM(), key: `fallback:${sessionId}:${message.content}` }))
        : [];
      let nextState = { ...state, pendingApproval: pending };
      for (const entry of agentEntries) {
        const stamped = stampSessionEntry(sessionId, nextState, entry);
        nextState = {
          ...nextState,
          entries: appendOrReconcileEntry(nextState.entries, stamped.entry),
          nextLocalOrder: stamped.nextLocalOrder
        };
      }
      return nextState;
    }));
```

Then refresh status/sessions as current code already does.

- [ ] **Step 11: Activate sessions from cache plus explicit reload**

Change `handleActivate(id)`:

```jsx
    const data = await api.activate(id);
    if (!data) return;
    setActiveSessionId(id);
    activeSessionIdRef.current = id;
    setSessionConfigError("");
    const [historyEvents, activityEvents, nextSessionStatus] = await Promise.all([
      api.sessionHistory(id),
      api.sessionActivity(id),
      api.sessionStatus(id)
    ]);
    const nextEntries = timelineFromSession(historyEvents, activityEvents);
    setSessionStateById((current) => updateOneSession(current, id, (state) => ({
      ...state,
      entries: nextEntries.length ? nextEntries : state.entries,
      pendingApproval: normalizeApproval(nextSessionStatus?.pending_approval),
      busy: nextSessionStatus?.status === "running",
      turnStart: state.turnStart,
      turnEnd: nextSessionStatus?.status === "running" ? null : state.turnEnd,
      nextLocalOrder: Math.max(state.nextLocalOrder, nextEntries.length),
      lastLoadedAt: Date.now()
    })));
    await refreshStatusAndSessions();
```

- [ ] **Step 12: Pass active session state to shell and chat**

Keep `AppShell` props using derived active values:

```jsx
      entries={entries}
      busy={busy}
      turnStart={turnStart}
      turnEnd={turnEnd}
```

Keep `ChatView` props using derived active values.

- [ ] **Step 13: Run frontend tests**

Run:

```bash
cd frontend
npm test -- --run src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: PASS.

- [ ] **Step 14: Commit**

```bash
git add frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "feat: isolate chat state by session"
```

---

### Task 6: Session-Scoped Approval Endpoints and UI Calls

**Files:**
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `tests/test_app.py`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**Interfaces:**
- Consumes: `api.approveSession(id, approvalId)`
- Consumes: `api.denySession(id, approvalId)`
- Produces: session-scoped approve/deny behavior that cannot resolve a pending approval in a different session.

- [ ] **Step 1: Write failing backend approval isolation test**

Append to `tests/test_app.py`:

```python
def test_session_scoped_approval_cannot_resolve_other_session_pending_request(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    command = write_file_command("ran.txt", "ran")
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient([
            ModelResponse(
                content="",
                tool_calls=[ToolCall(id="shell-call", name="shell.run", arguments={"command": command})],
            ),
            ModelResponse(content="done", tool_calls=[]),
        ]),
    )
    client = auth_client(config, runtime)
    session_a = TranscriptStore(config.session_dir).active_id() or TranscriptStore(config.session_dir).start_new()
    pending = client.post(f"/api/sessions/{session_a}/chat", json={"message": "run it"}).json()["pending_approval"]
    session_b = TranscriptStore(config.session_dir).start_new()

    response = client.post(f"/api/sessions/{session_b}/approvals/{pending['id']}/approve")

    assert response.status_code == 200
    assert response.json()["messages"][0]["content"].startswith("Error: No pending approval")
    assert not (config.workspace_root / "ran.txt").exists()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_app.py::test_session_scoped_approval_cannot_resolve_other_session_pending_request -q
```

Expected: FAIL because session-scoped approval endpoints do not exist.

- [ ] **Step 3: Add explicit approval endpoints**

In `src/personal_agent_gateway/app.py`:

```python
    @app.post("/api/sessions/{session_id}/approvals/{approval_id}/approve")
    async def session_approve(
        session_id: str,
        approval_id: str,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        request_id = uuid4().hex
        run_registry.start(session_id, request_id)
        try:
            result = await runtime_for_session(session_id).approve(approval_id)
            return {**_runtime_response(result), "session_id": session_id, "request_id": request_id}
        finally:
            run_registry.finish(session_id)

    @app.post("/api/sessions/{session_id}/approvals/{approval_id}/deny")
    async def session_deny(
        session_id: str,
        approval_id: str,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        request_id = uuid4().hex
        run_registry.start(session_id, request_id)
        try:
            result = await runtime_for_session(session_id).deny(approval_id)
            return {**_runtime_response(result), "session_id": session_id, "request_id": request_id}
        finally:
            run_registry.finish(session_id)
```

- [ ] **Step 4: Update frontend approval call**

In `handleResolveApproval(action)`, capture active session:

```jsx
    const sessionId = activeSessionId;
    if (!sessionId || !pendingApproval || busy) return;
```

Call explicit APIs:

```jsx
      const data = action === "approve"
        ? await api.approveSession(sessionId, pendingApproval.id)
        : await api.denySession(sessionId, pendingApproval.id);
      await postTurn(sessionId, data);
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_app.py::test_session_scoped_approval_cannot_resolve_other_session_pending_request -q
cd frontend
npm test -- --run src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/app.py tests/test_app.py frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "feat: scope approvals to chat sessions"
```

---

### Task 7: Final Verification, Build, and Dev Server Restart

**Files:**
- Modify: no source files unless verification reveals a defect in this plan's changes.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified local build served by the running gateway.

- [ ] **Step 1: Run full backend tests**

Run:

```bash
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run full frontend tests**

Run:

```bash
cd frontend
npm test -- --run
```

Expected: PASS.

- [ ] **Step 3: Build frontend assets**

Run:

```bash
cd frontend
npm run build
```

Expected: Vite build succeeds and updates `src/personal_agent_gateway/frontend_dist`.

- [ ] **Step 4: Restart local server**

Run from repo root:

```powershell
$listener = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  $listener | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
}
$process = Start-Process powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","scripts/run_local.ps1" -WindowStyle Hidden -PassThru
"STARTED pid=$($process.Id)"
```

Expected: prints `STARTED pid=<number>`.

- [ ] **Step 5: Smoke the HTTP shell**

Run:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/).StatusCode
```

Expected: `200`.

- [ ] **Step 6: Manual smoke scenario**

Use the browser at `http://127.0.0.1:8787/`:

```text
1. Open session A.
2. Send a long-running prompt.
3. Switch to session B before it completes.
4. Verify session B composer is usable and not marked busy.
5. Verify session A row shows running.
6. When session A completes, switch back.
7. Verify session A contains all activity in chronological order.
8. Hard refresh.
9. Verify session A transcript and durable activity still appear.
```

- [ ] **Step 7: Commit verification build if dist changed**

```bash
git status --short
git add src/personal_agent_gateway/frontend_dist
git commit -m "build: refresh frontend assets"
```

Only run the commit if `git status --short` shows changed files under `src/personal_agent_gateway/frontend_dist`.

---

## Self-Review

**Spec coverage:**  
Task 1 and Task 2 cover durable session activity and normalized SSE envelopes. Task 3 covers session-scoped running state and session-explicit history/status/chat APIs. Task 4 covers deterministic timeline ordering and envelope compatibility. Task 5 covers frontend `sessionStateById`, non-active SSE retention, active-only Statusbar/ChatView state, and EventSource lifecycle preservation. Task 6 covers session-scoped approvals. Task 7 covers full verification and rebuilt assets.

**Scan result:**  
This plan contains concrete file paths, test commands, expected failure/pass states, and implementation snippets for each code task.

**Type consistency:**  
The names `SessionActivityService`, `SessionActivityPublisher`, `SessionRunRegistry`, `timelineFromSession`, `api.sessionHistory`, `api.sessionActivity`, `api.sessionStatus`, `api.sendSessionChat`, `api.approveSession`, and `api.denySession` are introduced before downstream tasks consume them.
