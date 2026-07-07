import asyncio

import pytest

from personal_agent_gateway.events import EventBus


@pytest.mark.asyncio
async def test_event_bus_fans_out_events_with_monotonic_ids() -> None:
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()

    try:
        published = await bus.publish({"type": "runtime.started", "session_id": "session-1"})

        assert published == {"id": 1, "type": "runtime.started", "session_id": "session-1"}
        assert await asyncio.wait_for(first.get(), timeout=1) == published
        assert await asyncio.wait_for(second.get(), timeout=1) == published
        assert bus.recent() == [published]
    finally:
        bus.unsubscribe(first)
        bus.unsubscribe(second)


@pytest.mark.asyncio
async def test_event_bus_replays_events_after_last_event_id() -> None:
    bus = EventBus()
    first = await bus.publish({"type": "runtime.started"})
    second = await bus.publish({"type": "runtime.completed"})

    subscriber = bus.subscribe(last_event_id=first["id"])

    try:
        assert await asyncio.wait_for(subscriber.get(), timeout=1) == second
    finally:
        bus.unsubscribe(subscriber)
