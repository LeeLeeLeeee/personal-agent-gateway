from pathlib import Path

from personal_agent_gateway.agent_session_link import (
    AgentSessionContext,
    AgentSessionLinkService,
)
from personal_agent_gateway.session_consistency import SessionConsistencyService
from personal_agent_gateway.transcript import TranscriptStore


def _context(provider: str, marker: str) -> AgentSessionContext:
    return AgentSessionContext(
        agent_id=provider,
        model="default",
        execution={"marker": marker},
        persona_id=None,
        persona_snapshot=None,
        system_prompt=None,
    )


def test_report_classifies_missing_unlinked_and_context_mismatch_read_only(
    tmp_path: Path,
) -> None:
    transcript = TranscriptStore(tmp_path)
    matched_chat = transcript.start_new()
    missing_chat = transcript.start_new()
    mismatch_chat = transcript.start_new()
    links = AgentSessionLinkService(transcript)
    matched_context = _context("codex", "matched")
    mismatch_context = _context("claude", "pag")
    links.record(matched_chat, matched_context, "matched")
    links.record(missing_chat, _context("codex", "missing"), "missing")
    links.record(mismatch_chat, mismatch_context, "mismatch")
    before_events = {
        session.id: transcript.load(session.id)
        for session in transcript.list_sessions(origin="chat")
    }
    lmg_sessions = [
        {
            "provider": "codex",
            "upstream_id": "matched",
            "consumer": "personal-agent-gateway",
            "consumer_session_id": matched_chat,
            "consumer_context_fingerprint": matched_context.fingerprint(),
        },
        {
            "provider": "claude",
            "upstream_id": "mismatch",
            "consumer": "personal-agent-gateway",
            "consumer_session_id": mismatch_chat,
            "consumer_context_fingerprint": "different",
        },
        {
            "provider": "codex",
            "upstream_id": "unlinked",
            "consumer": "personal-agent-gateway",
            "consumer_session_id": "deleted-chat",
        },
        {
            "provider": "codex",
            "upstream_id": "other-consumer",
            "consumer": "some-other-service",
        },
    ]
    before_lmg = list(lmg_sessions)

    report = SessionConsistencyService(transcript).report(lmg_sessions).payload()

    assert report["missing_in_lmg"] == [
        {
            "provider": "codex",
            "upstream_session_id": "missing",
            "consumer_session_id": missing_chat,
            "context_fingerprint": _context("codex", "missing").fingerprint(),
        }
    ]
    assert report["unlinked_in_pag"] == [
        {
            "provider": "codex",
            "upstream_session_id": "unlinked",
            "consumer_session_id": "deleted-chat",
            "context_fingerprint": None,
        }
    ]
    assert report["context_mismatch"] == [
        {
            "provider": "claude",
            "upstream_session_id": "mismatch",
            "consumer_session_id": mismatch_chat,
            "lmg_consumer_session_id": mismatch_chat,
            "pag_context_fingerprint": mismatch_context.fingerprint(),
            "lmg_context_fingerprint": "different",
        }
    ]
    assert report["counts"] == {
        "missing_in_lmg": 1,
        "unlinked_in_pag": 1,
        "context_mismatch": 1,
    }
    assert {
        session.id: transcript.load(session.id)
        for session in transcript.list_sessions(origin="chat")
    } == before_events
    assert lmg_sessions == before_lmg


def test_report_uses_provider_with_upstream_identity(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    chat = transcript.start_new()
    context = _context("codex", "same-id")
    AgentSessionLinkService(transcript).record(chat, context, "shared")

    report = SessionConsistencyService(transcript).report(
        [
            {
                "provider": "claude",
                "upstream_id": "shared",
                "consumer": "personal-agent-gateway",
            }
        ]
    ).payload()

    assert report["counts"] == {
        "missing_in_lmg": 1,
        "unlinked_in_pag": 1,
        "context_mismatch": 0,
    }
