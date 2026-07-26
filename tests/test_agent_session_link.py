from pathlib import Path

import pytest

from personal_agent_gateway.agent_session_link import (
    AgentSessionContext,
    AgentSessionLinkService,
)
from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.transcript import TranscriptStore


def test_session_link_records_and_reads_matching_upstream_session(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    context = AgentSessionContext(
        agent_id="codex",
        model="gpt-5.5",
        execution={"sandbox": "workspace-write", "effort": "high"},
        persona_id=None,
        persona_snapshot=None,
        system_prompt=None,
    )
    recorded = service.record(
        session_id=session_id,
        context=context,
        upstream_session_id="codex-thread-1",
    )

    latest = service.latest(
        session_id=session_id,
        context=context,
    )

    assert latest == recorded
    assert latest is not None
    assert latest.upstream_session_id == "codex-thread-1"


def test_session_link_ignores_different_agent_model_or_options(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    original = AgentSessionContext(
        agent_id="claude",
        model="sonnet",
        execution={"effort": "medium"},
        persona_id="reviewer",
        persona_snapshot={"name": "검토자"},
        system_prompt="비판적으로 검토한다.",
    )
    service.record(
        session_id=session_id,
        context=original,
        upstream_session_id="claude-session-1",
    )

    variants = [
        AgentSessionContext(**{**original.__dict__, "agent_id": "codex"}),
        AgentSessionContext(**{**original.__dict__, "model": "opus"}),
        AgentSessionContext(**{**original.__dict__, "execution": {"effort": "high"}}),
        AgentSessionContext(**{**original.__dict__, "persona_id": "builder"}),
        AgentSessionContext(**{**original.__dict__, "persona_snapshot": {"name": "작성자"}}),
        AgentSessionContext(**{**original.__dict__, "system_prompt": "작성한다."}),
    ]
    for context in variants:
        assert service.latest(session_id, context) is None


def test_session_link_fingerprint_is_canonical_unicode_and_rejects_nan(tmp_path: Path) -> None:
    context = AgentSessionContext(
        agent_id="claude",
        model="sonnet",
        execution={"read_roots": ["문서"], "nested": {"b": 2, "a": 1}},
        persona_id=None,
        persona_snapshot=None,
        system_prompt="한글",
    )
    reordered = AgentSessionContext(
        agent_id="claude",
        model="sonnet",
        execution={"nested": {"a": 1, "b": 2}, "read_roots": ["문서"]},
        persona_id=None,
        persona_snapshot=None,
        system_prompt="한글",
    )

    assert context.fingerprint() == reordered.fingerprint()

    invalid = AgentSessionContext(
        agent_id="codex",
        model="default",
        execution={"effort": float("nan")},
        persona_id=None,
        persona_snapshot=None,
        system_prompt=None,
    )
    with pytest.raises(ValueError):
        invalid.fingerprint()


def test_session_link_deduplicates_same_context_provider_and_upstream(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)
    context = AgentSessionContext("codex", "default", {}, None, None, None)

    first = service.record(session_id, context, "thread-1")
    second = service.record(session_id, context, "thread-1")

    assert second == first
    assert len(service.links(session_id)) == 1


def test_removed_session_link_is_not_resumable_or_retried(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)
    context = AgentSessionContext("codex", "default", {}, None, None, None)
    link = service.record(session_id, context, "thread-1")

    service.remove(session_id, link)

    assert service.links(session_id) == []
    assert service.latest(session_id, context) is None
    assert service.upstream_session_ids(session_id) == []


def test_legacy_link_is_deletable_but_never_resumable(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    transcript.append_to(
        session_id,
        "agent_session_link",
        {
            "agent_id": "codex",
            "model": "default",
            "options_fingerprint": "legacy",
            "upstream_session_id": "thread-legacy",
        },
    )
    service = AgentSessionLinkService(transcript)
    context = AgentSessionContext("codex", "default", {}, None, None, None)

    assert service.upstream_session_ids(session_id) == ["thread-legacy"]
    assert service.latest(session_id, context) is None


def test_inventory_returns_latest_link_per_provider_and_upstream(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    first_session = transcript.start_new()
    second_session = transcript.start_new()
    hook_session = transcript.start_new(origin="hook", activate=False)
    service = AgentSessionLinkService(transcript)
    context = AgentSessionContext("codex", "default", {}, None, None, None)

    service.record(first_session, context, "shared")
    latest = service.record(second_session, context, "shared")
    service.record(hook_session, context, "hook-only")

    assert service.inventory() == [latest]


def test_session_link_keeps_session_and_config_editable(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    transcript.append(
        "agent_session_link",
        {
            "agent_id": "codex",
            "model": "gpt-5.5",
            "options_fingerprint": "fingerprint",
            "upstream_session_id": "codex-thread-1",
        },
    )

    session = transcript.list_sessions()[0]
    config = SessionAgentConfigService(transcript).effective_config(session_id)

    assert session.editable is True
    assert config.editable is True
