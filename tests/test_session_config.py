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


def test_config_is_locked_after_first_user_message(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    store.append("user", {"content": "hello"})
    service = SessionAgentConfigService(store)

    config = service.effective_config(session_id)

    assert config.editable is False
    with pytest.raises(ValueError, match="Session config is locked"):
        service.set_config(session_id, "claude", "sonnet", {})
