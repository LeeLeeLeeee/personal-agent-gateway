import pytest

from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.transcript import TranscriptStore


def test_effective_config_defaults_to_codex_for_empty_session(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    service = SessionAgentConfigService(store)

    config = service.effective_config(session_id)

    assert config.session_id == session_id
    assert config.agent_id == "codex"
    assert config.model == "default"
    assert config.options == {}
    assert config.editable is True


def test_set_config_appends_session_config_event(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    service = SessionAgentConfigService(store)

    config = service.set_config(session_id, "claude", "sonnet", {"effort": "high"})

    assert config.agent_id == "claude"
    assert config.model == "sonnet"
    assert config.options == {"effort": "high"}
    events = store.load(session_id)
    assert events[-1].kind == "session_config_set"
    assert events[-1].payload["agent_id"] == "claude"


def test_set_config_writes_to_requested_empty_non_active_session(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    first_id = store.start_new()
    store.append("user", {"content": "hello"})
    second_id = store.start_new()
    store.activate(first_id)
    service = SessionAgentConfigService(store)

    config = service.set_config(second_id, "claude", "sonnet", {})

    second_events = store.load(second_id)
    first_events = store.load(first_id)

    assert config.session_id == second_id
    assert [event.kind for event in second_events] == ["session_config_set"]
    assert second_events[0].payload == {
        "agent_id": "claude",
        "model": "sonnet",
        "options": {},
    }
    assert all(event.kind != "session_config_set" for event in first_events)


def test_config_is_locked_after_first_user_message(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    store.append("user", {"content": "hello"})
    service = SessionAgentConfigService(store)

    config = service.effective_config(session_id)

    assert config.editable is False
    with pytest.raises(ValueError, match="Session config is locked"):
        service.set_config(session_id, "claude", "sonnet", {})
