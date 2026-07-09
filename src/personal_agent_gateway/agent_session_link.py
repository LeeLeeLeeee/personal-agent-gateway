import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from personal_agent_gateway.transcript import TranscriptStore


@dataclass(frozen=True)
class AgentSessionLink:
    session_id: str
    agent_id: str
    model: str
    options_fingerprint: str
    upstream_session_id: str
    updated_at: datetime


class AgentSessionLinkService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._transcript = transcript

    def latest(
        self,
        session_id: str,
        agent_id: str,
        model: str,
        options: dict[str, object],
    ) -> AgentSessionLink | None:
        expected = _fingerprint_options(options)
        for event in reversed(self._transcript.load(session_id)):
            if event.kind != "agent_session_link":
                continue
            payload = event.payload
            if payload.get("agent_id") != agent_id:
                continue
            if payload.get("model") != model:
                continue
            if payload.get("options_fingerprint") != expected:
                continue
            upstream_session_id = payload.get("upstream_session_id")
            if not isinstance(upstream_session_id, str) or not upstream_session_id:
                continue
            return AgentSessionLink(
                session_id=session_id,
                agent_id=agent_id,
                model=model,
                options_fingerprint=expected,
                upstream_session_id=upstream_session_id,
                updated_at=event.created_at,
            )
        return None

    def record(
        self,
        session_id: str,
        agent_id: str,
        model: str,
        options: dict[str, object],
        upstream_session_id: str,
    ) -> AgentSessionLink:
        options_fingerprint = _fingerprint_options(options)
        event = self._transcript.append_to(
            session_id,
            "agent_session_link",
            {
                "agent_id": agent_id,
                "model": model,
                "options_fingerprint": options_fingerprint,
                "upstream_session_id": upstream_session_id,
            },
        )
        return AgentSessionLink(
            session_id=session_id,
            agent_id=agent_id,
            model=model,
            options_fingerprint=options_fingerprint,
            upstream_session_id=upstream_session_id,
            updated_at=event.created_at,
        )


def _fingerprint_options(options: dict[str, object]) -> str:
    encoded = json.dumps(options, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
