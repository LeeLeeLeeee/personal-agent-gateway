import asyncio
from collections import deque
from typing import Any
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

        ``operation_id`` identifies the run the event belongs to and
        ``step_index`` its position inside that run. Both are omitted from the
        payload when absent, so callers that have not been converted yet keep
        producing exactly the frames they produced before.
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


class EventScope:
    """Publishes events for one run, stamping operation id and step index.

    The step counter is per-scope, so two runs streaming at the same time
    number their steps independently — a global counter could not tell a
    consumer which run a step belonged to.
    """

    def __init__(self, bus: EventBus, operation_id: str) -> None:
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
        return await self._bus.publish(
            event,
            operation_id=self._operation_id,
            step_index=self._step_index,
        )


def _parse_event_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
