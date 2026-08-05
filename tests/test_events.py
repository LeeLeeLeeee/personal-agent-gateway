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

        assert published == {
            "stream_id": bus.stream_id,
            "id": 1,
            "type": "runtime.started",
            "session_id": "session-1",
        }
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


@pytest.mark.asyncio
async def test_event_bus_uses_a_new_stream_identity_after_restart() -> None:
    first_bus = EventBus()
    second_bus = EventBus()

    first = await first_bus.publish({"type": "runtime.started"})
    second = await second_bus.publish({"type": "runtime.started"})

    assert first["id"] == second["id"] == 1
    assert first["stream_id"] != second["stream_id"]


@pytest.mark.asyncio
async def test_event_bus_owns_stream_and_event_identity_fields() -> None:
    bus = EventBus()

    published = await bus.publish(
        {"type": "runtime.started", "stream_id": "caller", "id": "caller"}
    )

    assert published["stream_id"] == bus.stream_id
    assert published["id"] == 1


@pytest.mark.asyncio
async def test_publish_without_scope_keeps_legacy_payload() -> None:
    bus = EventBus()

    published = await bus.publish({"type": "runtime.started"})

    assert published == {"stream_id": bus.stream_id, "id": 1, "type": "runtime.started"}


@pytest.mark.asyncio
async def test_publish_stamps_operation_and_step_when_given() -> None:
    bus = EventBus()

    published = await bus.publish(
        {"type": "runtime.started"}, operation_id="op-1", step_index=3
    )

    assert published["operation_id"] == "op-1"
    assert published["step_index"] == 3


@pytest.mark.asyncio
async def test_scope_numbers_steps_independently_per_operation() -> None:
    bus = EventBus()
    first = bus.scope("op-1")
    second = bus.scope("op-2")

    a = await first.publish({"type": "runtime.started"})
    b = await first.publish({"type": "item.completed"}, advance_step=True)
    c = await second.publish({"type": "runtime.started"})

    assert (a["operation_id"], a["step_index"]) == ("op-1", 0)
    assert (b["operation_id"], b["step_index"]) == ("op-1", 1)
    assert (c["operation_id"], c["step_index"]) == ("op-2", 0)


@pytest.mark.asyncio
async def test_scope_events_still_reach_subscribers_and_history() -> None:
    bus = EventBus()
    subscriber = bus.subscribe()
    scope = bus.scope("op-1")

    try:
        published = await scope.publish({"type": "runtime.started"})

        assert await asyncio.wait_for(subscriber.get(), timeout=1) == published
        assert bus.recent() == [published]
    finally:
        bus.unsubscribe(subscriber)
