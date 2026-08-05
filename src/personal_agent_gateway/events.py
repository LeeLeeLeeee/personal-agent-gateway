import asyncio
from collections import deque
from typing import Any, Protocol
from uuid import uuid4


class EventBus:
    def __init__(self, history_limit: int = 200) -> None:
        self.stream_id = uuid4().hex
        self._next_id = 1
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(
        self,
        event: dict[str, object],
        *,
        operation_id: str | None = None,
        step_index: int | None = None,
    ) -> dict[str, Any]:
        """Publish one event.

        ``operation_id`` and ``step_index`` are stamped onto the payload when
        given, and omitted otherwise, so callers that pass neither keep
        producing exactly the frames they produced before. As wired today,
        ``operation_id`` is always the session id (``runtime.py`` uses
        ``session_id`` as the scope key), and every frame that gets stamped
        already carried ``session_id`` on its own — so the key adds no new
        information yet. It also only reaches three lifecycle frames per run;
        the token-level ``model.event`` stream published by
        ``runtime_factory.py`` goes straight to the bus and is never stamped.
        """
        published = {
            **event,
            "stream_id": self.stream_id,
            "id": self._next_id,
        }
        if operation_id is not None:
            published["operation_id"] = operation_id
        if step_index is not None:
            published["step_index"] = step_index
        self._next_id += 1
        self._history.append(published)
        for subscriber in list(self._subscribers):
            await subscriber.put(published)
        return published

    def scope(self, operation_id: str) -> "EventScope":
        """Return a per-run publisher that numbers its own steps."""
        return EventScope(self, operation_id)

    def subscribe(self, last_event_id: int | str | None = None) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        after_id = _parse_event_id(last_event_id)
        for event in self._history:
            if after_id is None or int(event["id"]) > after_id:
                queue.put_nowait(event)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._history)


class _PublishTarget(Protocol):
    async def publish(
        self,
        event: dict[str, object],
        *,
        operation_id: str | None = None,
        step_index: int | None = None,
    ) -> dict[str, Any]: ...


class EventScope:
    """Publishes events for one run, stamping the operation id.

    The step counter is per-scope, so two runs streaming at the same time
    number their steps independently — a global counter could not tell a
    consumer which run a step belonged to. Step numbering is deferred,
    though: the counter is maintained here but deliberately not emitted
    into the published payload until a stepping policy exists.

    As wired today, ``operation_id`` is the session id, which every
    stamped frame already carried before scoping existed, and only three
    lifecycle frames per run go through a scope at all — the token-level
    ``model.event`` stream published by ``runtime_factory.py`` bypasses
    scoping entirely.
    """

    def __init__(self, bus: _PublishTarget, operation_id: str) -> None:
        self._bus = bus
        self._operation_id = operation_id
        self._step_index = 0

    @property
    def operation_id(self) -> str:
        return self._operation_id

    @property
    def step_index(self) -> int:
        return self._step_index

    async def publish(
        self,
        event: dict[str, object],
        *,
        advance_step: bool = False,
    ) -> dict[str, Any]:
        if advance_step:
            self._step_index += 1
        return await self._bus.publish(event, operation_id=self._operation_id)


def _parse_event_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
