"""Unit tests for EventBus and event creation."""

import pytest
from orchestrator.events import EventBus, create_event


@pytest.mark.asyncio
async def test_event_bus_pub_sub():
    bus = EventBus()
    queue = bus.subscribe()

    evt = create_event(
        workflow_run_id="run_001",
        event_type="test_event",
        payload={"message": "hello"},
    )
    await bus.publish(evt)

    received = await queue.get()
    assert received.event_id == evt.event_id
    assert received.payload["message"] == "hello"

    bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_event_bus_callback():
    bus = EventBus()
    received_events = []

    async def callback(e):
        received_events.append(e)

    bus.add_callback(callback)

    evt = create_event(
        workflow_run_id="run_001",
        event_type="callback_event",
        payload={"key": "val"},
    )
    await bus.publish(evt)

    assert len(received_events) == 1
    assert received_events[0].type == "callback_event"
