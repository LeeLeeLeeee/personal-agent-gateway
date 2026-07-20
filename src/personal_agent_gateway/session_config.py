from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel

from personal_agent_gateway.transcript import TranscriptEvent, TranscriptStore

AgentId = Literal["codex", "claude"]


class SessionAgentConfig(BaseModel):
    session_id: str | None
    persona_id: str | None = None
    persona_snapshot: dict[str, Any] | None = None
    agent_id: AgentId
    model: str
    options: dict[str, Any]
    editable: bool
    source: Literal["default", "explicit"] = "default"
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
                persona_id=None,
                persona_snapshot=None,
                agent_id="codex",
                model="default",
                options={},
                editable=editable,
                source="default",
                updated_at=None,
            )
        return SessionAgentConfig(
            session_id=resolved_id,
            persona_id=(
                str(latest.payload["persona_id"])
                if latest.payload.get("persona_id")
                else None
            ),
            persona_snapshot=(
                dict(latest.payload["persona_snapshot"])
                if isinstance(latest.payload.get("persona_snapshot"), dict)
                else None
            ),
            agent_id=cast(AgentId, latest.payload["agent_id"]),
            model=str(latest.payload["model"]),
            options=dict(latest.payload.get("options") or {}),
            editable=editable,
            source="explicit",
            updated_at=latest.created_at,
        )

    def set_config(
        self,
        session_id: str | None,
        agent_id: AgentId,
        model: str,
        options: dict[str, Any],
        persona_id: str | None = None,
        persona_snapshot: dict[str, Any] | None = None,
    ) -> SessionAgentConfig:
        resolved_id = session_id or self._transcript.active_id() or self._transcript.start_new()
        events = self._transcript.load(resolved_id)
        if not _is_editable(events):
            raise ValueError("Session config is locked")
        payload: dict[str, object] = {
            "agent_id": agent_id,
            "model": model,
            "options": dict(options),
        }
        if persona_id is not None:
            payload["persona_id"] = persona_id
        if persona_snapshot is not None:
            payload["persona_snapshot"] = dict(persona_snapshot)
        event = self._transcript.append_to(
            resolved_id,
            "session_config_set",
            payload,
        )
        return SessionAgentConfig(
            session_id=resolved_id,
            persona_id=persona_id,
            persona_snapshot=dict(persona_snapshot) if persona_snapshot is not None else None,
            agent_id=agent_id,
            model=model,
            options=dict(options),
            editable=True,
            source="explicit",
            updated_at=event.created_at,
        )


def _latest_config_event(events: list[TranscriptEvent]) -> TranscriptEvent | None:
    for event in reversed(events):
        if event.kind == "session_config_set":
            return event
    return None


def _is_editable(events: list[TranscriptEvent]) -> bool:
    return all(
        event.kind in {"session_config_set", "session_rename", "agent_session_link"}
        for event in events
    )
