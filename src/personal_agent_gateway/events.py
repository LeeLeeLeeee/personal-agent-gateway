import asyncio
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history_limit: int = 200) -> None:
        self._next_id = 1
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, object]) -> dict[str, Any]:
        published = {"id": self._next_id, **event}
        self._next_id += 1
        self._history.append(published)
        for subscriber in list(self._subscribers):
            await subscriber.put(published)
        return published

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


def _parse_event_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
