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
