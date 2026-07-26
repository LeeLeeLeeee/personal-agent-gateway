import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from personal_agent_gateway.transcript import TranscriptStore


@dataclass(frozen=True)
class AgentSessionContext:
    agent_id: str
    model: str
    execution: dict[str, object]
    persona_id: str | None
    persona_snapshot: dict[str, object] | None
    system_prompt: str | None

    def fingerprint(self) -> str:
        encoded = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentSessionLink:
    session_id: str
    agent_id: str
    model: str
    context_fingerprint: str | None
    upstream_session_id: str
    updated_at: datetime


class AgentSessionLinkService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._transcript = transcript

    def links(self, session_id: str) -> list[AgentSessionLink]:
        links: list[AgentSessionLink] = []
        for event in self._transcript.load(session_id):
            if event.kind == "agent_session_unlink":
                agent_id = event.payload.get("agent_id")
                upstream_session_id = event.payload.get("upstream_session_id")
                if not isinstance(agent_id, str) or not isinstance(
                    upstream_session_id, str
                ):
                    continue
                links = [
                    link
                    for link in links
                    if not (
                        link.agent_id == agent_id
                        and link.upstream_session_id == upstream_session_id
                    )
                ]
                continue
            if event.kind != "agent_session_link":
                continue
            payload = event.payload
            upstream_session_id = payload.get("upstream_session_id")
            if not isinstance(upstream_session_id, str) or not upstream_session_id:
                continue
            agent_id = payload.get("agent_id")
            model = payload.get("model")
            context_fingerprint = payload.get("context_fingerprint")
            links.append(
                AgentSessionLink(
                    session_id=session_id,
                    agent_id=agent_id if isinstance(agent_id, str) else "",
                    model=model if isinstance(model, str) else "",
                    context_fingerprint=(
                        context_fingerprint
                        if isinstance(context_fingerprint, str) and context_fingerprint
                        else None
                    ),
                    upstream_session_id=upstream_session_id,
                    updated_at=event.created_at,
                )
            )
        return links

    def remove(self, session_id: str, link: AgentSessionLink) -> None:
        if not any(
            candidate.agent_id == link.agent_id
            and candidate.upstream_session_id == link.upstream_session_id
            for candidate in self.links(session_id)
        ):
            return
        self._transcript.append_to(
            session_id,
            "agent_session_unlink",
            {
                "agent_id": link.agent_id,
                "upstream_session_id": link.upstream_session_id,
            },
        )

    def upstream_session_ids(self, session_id: str) -> list[str]:
        upstream_session_ids: list[str] = []
        seen: set[str] = set()
        for link in self.links(session_id):
            if link.upstream_session_id in seen:
                continue
            seen.add(link.upstream_session_id)
            upstream_session_ids.append(link.upstream_session_id)
        return upstream_session_ids

    def inventory(self) -> list[AgentSessionLink]:
        latest_links: dict[tuple[str, str], AgentSessionLink] = {}
        for session in self._transcript.list_sessions(origin="chat"):
            for link in self.links(session.id):
                key = (link.agent_id, link.upstream_session_id)
                current = latest_links.get(key)
                if current is None or link.updated_at > current.updated_at:
                    latest_links[key] = link
        return sorted(
            latest_links.values(),
            key=lambda link: (
                link.agent_id,
                link.upstream_session_id,
                link.session_id,
            ),
        )

    def latest(
        self,
        session_id: str,
        context: AgentSessionContext,
    ) -> AgentSessionLink | None:
        expected = context.fingerprint()
        for link in reversed(self.links(session_id)):
            if link.agent_id != context.agent_id:
                continue
            if link.model != context.model:
                continue
            if link.context_fingerprint != expected:
                continue
            return link
        return None

    def record(
        self,
        session_id: str,
        context: AgentSessionContext,
        upstream_session_id: str,
    ) -> AgentSessionLink:
        context_fingerprint = context.fingerprint()
        for link in reversed(self.links(session_id)):
            if (
                link.agent_id == context.agent_id
                and link.upstream_session_id == upstream_session_id
                and link.context_fingerprint == context_fingerprint
            ):
                return link
        event = self._transcript.append_to(
            session_id,
            "agent_session_link",
            {
                "agent_id": context.agent_id,
                "model": context.model,
                "context_fingerprint": context_fingerprint,
                "upstream_session_id": upstream_session_id,
            },
        )
        return AgentSessionLink(
            session_id=session_id,
            agent_id=context.agent_id,
            model=context.model,
            context_fingerprint=context_fingerprint,
            upstream_session_id=upstream_session_id,
            updated_at=event.created_at,
        )
