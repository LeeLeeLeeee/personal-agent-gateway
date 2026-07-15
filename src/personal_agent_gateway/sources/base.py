from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HookEvent:
    dedup_key: str
    summary: str
    payload: dict[str, object]


@dataclass(frozen=True)
class PollResult:
    events: list[HookEvent]
    cursor: dict[str, object]


class SourceAdapter(Protocol):
    def poll(
        self,
        connection: dict[str, object],
        secret: str,
        cursor: dict[str, object] | None,
        filter_config: dict[str, object],
    ) -> PollResult: ...
