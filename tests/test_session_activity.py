from pathlib import Path
import threading
import asyncio

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.session_activity import SessionActivityPublisher, SessionActivityService


def test_session_activity_records_monotonic_sequence_per_session(
    tmp_path: Path,
) -> None:
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


def test_session_activity_payload_is_api_ready_and_deletable(
    tmp_path: Path,
) -> None:
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


def test_session_activity_record_serializes_same_session_sequence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)

    original_connect = db.connect
    lock = threading.Lock()
    select_started = threading.Event()
    select_count = 0

    class ConnectionProxy:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self._connection.__exit__(exc_type, exc, tb)

        def execute(self, sql: str, parameters=()):
            nonlocal select_count
            cursor = self._connection.execute(sql, parameters)
            if "select coalesce(max(event_seq), 0) + 1 as next_seq" in sql:
                with lock:
                    select_count += 1
                    if select_count == 1:
                        select_started.wait(timeout=0.2)
                    else:
                        select_started.set()
            return cursor

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    def connect_with_proxy():
        return ConnectionProxy(original_connect())

    monkeypatch.setattr(db, "connect", connect_with_proxy)

    errors: list[Exception] = []
    recorded = []

    def worker(index: int) -> None:
        try:
            event = service.record(
                session_id="session-a",
                event_type="runtime.user_message.started",
                source="runtime",
                payload={"index": index},
            )
            recorded.append(event)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(index,))
        for index in range(1, 3)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [event.event_seq for event in sorted(recorded, key=lambda event: event.event_seq)] == [1, 2]
    assert [event.event_seq for event in service.list("session-a")] == [1, 2]


def test_session_activity_persists_across_service_instances(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()

    SessionActivityService(db).record(
        session_id="session-a",
        event_type="runtime.user_message.started",
        source="runtime",
        payload={"message": "hello"},
    )

    second_service = SessionActivityService(db)
    events = second_service.list("session-a")

    assert [event.event_seq for event in events] == [1]


def test_session_activity_pages_latest_events_without_duplicates(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    for index in range(5):
        service.record(
            session_id="session-a",
            event_type="runtime.event",
            source="runtime",
            payload={"index": index},
        )

    first, cursor = service.page("session-a", limit=2)
    second, next_cursor = service.page("session-a", limit=2, cursor=cursor)
    third, final_cursor = service.page("session-a", limit=2, cursor=next_cursor)

    assert [event.event_seq for event in first] == [4, 5]
    assert [event.event_seq for event in second] == [2, 3]
    assert [event.event_seq for event in third] == [1]
    assert len({event.id for event in first + second + third}) == 5
    assert final_cursor is None


def test_session_activity_publisher_persists_before_fanout(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    bus = EventBus()
    publisher = SessionActivityPublisher(service, bus)

    published = asyncio.run(
        publisher.publish(
            {
                "type": "runtime.user_message.started",
                "session_id": "session-a",
                "message": "hello",
            }
        )
    )

    assert published["id"] == 1
    assert published["activity_id"] == 1
    assert published["event_seq"] == 1
    assert published["source"] == "runtime"
    assert published["payload"] == {"message": "hello"}
    assert published["message"] == "hello"
    assert bus.recent() == [published]
    assert service.list("session-a")[0].to_event_payload()["payload"] == {"message": "hello"}


def test_session_activity_publisher_keeps_sse_ids_monotonic_when_team_events_interleave(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    bus = EventBus()
    publisher = SessionActivityPublisher(service, bus)

    team_event = asyncio.run(
        publisher.publish(
            {
                "type": "team.run.started",
                "team_run_id": "run-1",
            }
        )
    )
    chat_event = asyncio.run(
        publisher.publish(
            {
                "type": "runtime.user_message.started",
                "session_id": "session-a",
                "message": "hello",
            }
        )
    )

    assert team_event == {"id": 1, "type": "team.run.started", "team_run_id": "run-1"}
    assert chat_event["id"] == 2
    assert chat_event["activity_id"] == 1
    assert chat_event["event_seq"] == 1
    assert chat_event["session_id"] == "session-a"
    assert chat_event["created_at"]
    assert chat_event["type"] == "runtime.user_message.started"
    assert chat_event["source"] == "runtime"
    assert chat_event["payload"] == {"message": "hello"}
    assert bus.recent() == [team_event, chat_event]


def test_session_activity_publisher_keeps_team_events_out_of_chat_activity(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    service = SessionActivityService(db)
    bus = EventBus()
    publisher = SessionActivityPublisher(service, bus)

    published = asyncio.run(
        publisher.publish(
            {
                "type": "team.run.started",
                "team_run_id": "run-1",
            }
        )
    )

    assert published == {"id": 1, "type": "team.run.started", "team_run_id": "run-1"}
    assert service.list("run-1") == []
