from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.session_activity import SessionActivityService


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
