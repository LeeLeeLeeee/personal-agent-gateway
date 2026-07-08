from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel

from personal_agent_gateway.transcript import TranscriptEvent, TranscriptStore

AgentId = Literal["codex", "claude"]


class SessionAgentConfig(BaseModel):
    session_id: str | None
    agent_id: AgentId
    model: str
    options: dict[str, Any]
    editable: bool
    updated_at: datetime | None = None


class SessionAgentConfigService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._transcript = transcript

    def effective_config(self, session_id: str | None = None) -> SessionAgentConfig:
        resolved_id = session_id or self._transcript.active_id() or self._transcript.start_new()
        events = self._transcript.load(resolved_id)
        latest = _latest_config_event(events)
        editable = _is_editable(events)
        if latest is None:
            return SessionAgentConfig(
                session_id=resolved_id,
                agent_id="codex",
                model="default",
                options={},
                editable=editable,
                updated_at=None,
            )
        return SessionAgentConfig(
            session_id=resolved_id,
            agent_id=cast(AgentId, latest.payload["agent_id"]),
            model=str(latest.payload["model"]),
            options=dict(latest.payload.get("options") or {}),
            editable=editable,
            updated_at=latest.created_at,
        )

    def set_config(
        self,
        session_id: str | None,
        agent_id: AgentId,
        model: str,
        options: dict[str, Any],
    ) -> SessionAgentConfig:
        resolved_id = session_id or self._transcript.active_id() or self._transcript.start_new()
        events = self._transcript.load(resolved_id)
        if not _is_editable(events):
            raise ValueError("Session config is locked")
        event = self._transcript.append_to(
            resolved_id,
            "session_config_set",
            {"agent_id": agent_id, "model": model, "options": dict(options)},
        )
        return SessionAgentConfig(
            session_id=resolved_id,
            agent_id=agent_id,
            model=model,
            options=dict(options),
            editable=True,
            updated_at=event.created_at,
        )


def _latest_config_event(events: list[TranscriptEvent]) -> TranscriptEvent | None:
    for event in reversed(events):
        if event.kind == "session_config_set":
            return event
    return None


def _is_editable(events: list[TranscriptEvent]) -> bool:
    return all(event.kind in {"session_config_set", "session_rename"} for event in events)
