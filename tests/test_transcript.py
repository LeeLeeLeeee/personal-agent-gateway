import json
from pathlib import Path

from personal_agent_gateway.transcript import TranscriptStore


def test_start_new_creates_new_active_transcript_id(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)

    transcript_id = store.start_new()

    assert store.active_id() == transcript_id
    assert transcript_id
    assert json.loads((tmp_path / "active.json").read_text()) == {
        "transcript_id": transcript_id
    }


def test_active_id_returns_none_before_session_exists(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)

    assert store.active_id() is None


def test_set_title_overrides_derived_title(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    transcript_id = store.start_new()
    store.append("user", {"content": "original first message"})

    assert store.list_sessions()[0].title == "original first message"
    assert store.set_title(transcript_id, "Renamed session") is True

    summary = store.list_sessions()[0]
    assert summary.title == "Renamed session"
    assert summary.message_count == 1  # rename event is not counted as a message


def test_set_title_unknown_session_returns_false(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)

    assert store.set_title("missing", "x") is False


def test_append_writes_jsonl_events_in_order(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    transcript_id = store.start_new()

    first = store.append("user", {"message": "hello"})
    second = store.append("assistant", {"message": "hi"})

    lines = (tmp_path / f"{transcript_id}.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines]

    assert [event["id"] for event in events] == [first.id, second.id]
    assert [event["transcript_id"] for event in events] == [transcript_id, transcript_id]
    assert [event["kind"] for event in events] == ["user", "assistant"]
    assert [event["payload"] for event in events] == [
        {"message": "hello"},
        {"message": "hi"},
    ]


def test_append_before_start_new_creates_active_transcript(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)

    event = store.append("user", {"message": "hello"})

    assert event.transcript_id
    assert json.loads((tmp_path / "active.json").read_text()) == {
        "transcript_id": event.transcript_id
    }
    assert (tmp_path / f"{event.transcript_id}.jsonl").exists()


def test_load_active_restores_events_from_existing_directory(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    transcript_id = store.start_new()
    store.append("tool_request", {"name": "shell", "args": ["pwd"]})
    store.append("tool_result", {"exit_code": 0, "stdout": "/tmp"})

    reloaded_store = TranscriptStore(tmp_path)

    events = reloaded_store.load_active()

    assert [event.transcript_id for event in events] == [transcript_id, transcript_id]
    assert [event.kind for event in events] == ["tool_request", "tool_result"]
    assert [event.payload for event in events] == [
        {"name": "shell", "args": ["pwd"]},
        {"exit_code": 0, "stdout": "/tmp"},
    ]


def test_reset_starts_new_active_transcript_without_deleting_old_file(
    tmp_path: Path,
) -> None:
    store = TranscriptStore(tmp_path)
    old_transcript_id = store.start_new()
    store.append("user", {"message": "before reset"})
    old_transcript_path = tmp_path / f"{old_transcript_id}.jsonl"

    new_transcript_id = store.reset()

    assert new_transcript_id != old_transcript_id
    assert old_transcript_path.exists()
    assert json.loads((tmp_path / "active.json").read_text()) == {
        "transcript_id": new_transcript_id
    }


def test_list_sessions_returns_metadata_sorted_by_recent_activity(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    first_id = store.start_new()
    store.append("user", {"content": "first long request title"})
    store.append("assistant", {"content": "first answer"})
    second_id = store.reset()
    store.append("user", {"content": "second request"})

    sessions = store.list_sessions()

    assert [session.id for session in sessions] == [second_id, first_id]
    assert sessions[0].title == "second request"
    assert sessions[0].message_count == 1
    assert sessions[0].status == "idle"
    assert sessions[0].is_active is True
    assert sessions[0].created_at <= sessions[0].updated_at
    assert sessions[1].title == "first long request title"
    assert sessions[1].message_count == 2
    assert sessions[1].is_active is False


def test_list_sessions_reports_waiting_and_failed_status(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    waiting_id = store.start_new()
    store.append(
        "tool_request",
        {
            "id": "shell-call",
            "name": "shell.run",
            "approval_id": "approval-1",
            "arguments": {"command": "printf ok"},
        },
    )
    failed_id = store.reset()
    store.append("runtime_error", {"message": "Codex exited"})

    sessions = {session.id: session for session in store.list_sessions()}

    assert sessions[waiting_id].status == "waiting_approval"
    assert sessions[failed_id].status == "failed"


def test_list_sessions_includes_agent_config_metadata(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    store.append(
        "session_config_set",
        {"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )

    session = store.list_sessions()[0]

    assert session.id == session_id
    assert session.agent_id == "claude"
    assert session.model == "sonnet"
    assert session.options == {"effort": "high"}
    assert session.editable is True


def test_list_sessions_marks_session_config_locked_after_user_message(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    store.start_new()
    store.append(
        "session_config_set",
        {"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )
    store.append("user", {"content": "hello"})

    session = store.list_sessions()[0]

    assert session.editable is False


def test_activate_session_switches_active_transcript(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    first_id = store.start_new()
    store.append("user", {"content": "first"})
    store.reset()

    assert store.activate(first_id) is True

    assert store.active_id() == first_id
    assert [event.payload for event in store.load_active()] == [{"content": "first"}]


def test_activate_missing_session_returns_false(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)

    assert store.activate("missing") is False


def test_delete_session_removes_transcript_and_clears_active_pointer(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    transcript_id = store.start_new()
    store.append("user", {"content": "delete me"})

    assert store.delete(transcript_id) is True

    assert store.active_id() is None
    assert not (tmp_path / f"{transcript_id}.jsonl").exists()
    assert not (tmp_path / "active.json").exists()


def test_search_sessions_matches_transcript_payload_text(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    first_id = store.start_new()
    store.append("user", {"content": "find the billing regression"})
    store.reset()
    store.append("user", {"content": "unrelated message"})

    results = store.search_sessions("billing")

    assert [result.id for result in results] == [first_id]
    assert results[0].title == "find the billing regression"
