from dataclasses import asdict, dataclass

from personal_agent_gateway.agent_session_link import AgentSessionLinkService
from personal_agent_gateway.transcript import TranscriptStore


@dataclass(frozen=True)
class MissingLMGSession:
    provider: str
    upstream_session_id: str
    consumer_session_id: str
    context_fingerprint: str | None


@dataclass(frozen=True)
class UnlinkedPAGSession:
    provider: str
    upstream_session_id: str
    consumer_session_id: str | None
    context_fingerprint: str | None


@dataclass(frozen=True)
class ContextMismatch:
    provider: str
    upstream_session_id: str
    consumer_session_id: str
    lmg_consumer_session_id: str | None
    pag_context_fingerprint: str
    lmg_context_fingerprint: str


@dataclass(frozen=True)
class SessionConsistencyReport:
    missing_in_lmg: tuple[MissingLMGSession, ...]
    unlinked_in_pag: tuple[UnlinkedPAGSession, ...]
    context_mismatch: tuple[ContextMismatch, ...]

    def payload(self) -> dict[str, object]:
        return {
            "missing_in_lmg": [asdict(item) for item in self.missing_in_lmg],
            "unlinked_in_pag": [asdict(item) for item in self.unlinked_in_pag],
            "context_mismatch": [asdict(item) for item in self.context_mismatch],
            "counts": {
                "missing_in_lmg": len(self.missing_in_lmg),
                "unlinked_in_pag": len(self.unlinked_in_pag),
                "context_mismatch": len(self.context_mismatch),
            },
        }


class SessionConsistencyService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._links = AgentSessionLinkService(transcript)

    def report(
        self,
        lmg_sessions: list[dict[str, object]],
    ) -> SessionConsistencyReport:
        pag_inventory = {
            (link.agent_id, link.upstream_session_id): link
            for link in self._links.inventory()
        }
        lmg_inventory = {
            (provider, upstream_session_id): row
            for row in lmg_sessions
            if row.get("consumer") == "personal-agent-gateway"
            and (provider := _nonempty_string(row.get("provider"))) is not None
            and (
                upstream_session_id := _nonempty_string(row.get("upstream_id"))
            )
            is not None
        }

        missing_in_lmg = tuple(
            MissingLMGSession(
                provider=provider,
                upstream_session_id=upstream_session_id,
                consumer_session_id=link.session_id,
                context_fingerprint=link.context_fingerprint,
            )
            for (provider, upstream_session_id), link in sorted(pag_inventory.items())
            if (provider, upstream_session_id) not in lmg_inventory
        )
        unlinked_in_pag = tuple(
            UnlinkedPAGSession(
                provider=provider,
                upstream_session_id=upstream_session_id,
                consumer_session_id=_nonempty_string(
                    row.get("consumer_session_id")
                ),
                context_fingerprint=_nonempty_string(
                    row.get("consumer_context_fingerprint")
                ),
            )
            for (provider, upstream_session_id), row in sorted(lmg_inventory.items())
            if (provider, upstream_session_id) not in pag_inventory
        )

        context_mismatch: list[ContextMismatch] = []
        for identity in sorted(pag_inventory.keys() & lmg_inventory.keys()):
            link = pag_inventory[identity]
            row = lmg_inventory[identity]
            lmg_fingerprint = _nonempty_string(
                row.get("consumer_context_fingerprint")
            )
            if link.context_fingerprint is None or lmg_fingerprint is None:
                continue
            if link.context_fingerprint == lmg_fingerprint:
                continue
            provider, upstream_session_id = identity
            context_mismatch.append(
                ContextMismatch(
                    provider=provider,
                    upstream_session_id=upstream_session_id,
                    consumer_session_id=link.session_id,
                    lmg_consumer_session_id=_nonempty_string(
                        row.get("consumer_session_id")
                    ),
                    pag_context_fingerprint=link.context_fingerprint,
                    lmg_context_fingerprint=lmg_fingerprint,
                )
            )

        return SessionConsistencyReport(
            missing_in_lmg=missing_in_lmg,
            unlinked_in_pag=unlinked_in_pag,
            context_mismatch=tuple(context_mismatch),
        )


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value
