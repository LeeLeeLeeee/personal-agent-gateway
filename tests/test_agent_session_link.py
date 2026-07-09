from pathlib import Path

from personal_agent_gateway.agent_session_link import AgentSessionLinkService
from personal_agent_gateway.transcript import TranscriptStore


def test_session_link_records_and_reads_matching_upstream_session(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    recorded = service.record(
        session_id=session_id,
        agent_id="codex",
        model="gpt-5.5",
        options={"effort": "high", "sandbox": "workspace-write"},
        upstream_session_id="codex-thread-1",
    )

    latest = service.latest(
        session_id=session_id,
        agent_id="codex",
        model="gpt-5.5",
        options={"sandbox": "workspace-write", "effort": "high"},
    )

    assert latest == recorded
    assert latest is not None
    assert latest.upstream_session_id == "codex-thread-1"


def test_session_link_ignores_different_agent_model_or_options(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    service.record(
        session_id=session_id,
        agent_id="claude",
        model="sonnet",
        options={"effort": "medium"},
        upstream_session_id="claude-session-1",
    )

    assert service.latest(session_id, "codex", "sonnet", {"effort": "medium"}) is None
    assert service.latest(session_id, "claude", "opus", {"effort": "medium"}) is None
    assert service.latest(session_id, "claude", "sonnet", {"effort": "high"}) is None
