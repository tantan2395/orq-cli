"""Event bus and typed orchestration event definitions."""

import asyncio
import logging
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional
from orchestrator.models import OrchestrationEvent, utc_now_iso

logger = logging.getLogger(__name__)


class EventBus:
    """Asynchronous pub/sub event bus with subscription queues and callbacks."""

    def __init__(self):
        self._subscribers: List[asyncio.Queue[OrchestrationEvent]] = []
        self._callbacks: List[Callable[[OrchestrationEvent], Coroutine[Any, Any, None]]] = []

    def subscribe(self) -> asyncio.Queue[OrchestrationEvent]:
        """Creates and returns an async Queue that receives all published events."""
        q: asyncio.Queue[OrchestrationEvent] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[OrchestrationEvent]) -> None:
        """Removes a subscription queue."""
        if q in self._subscribers:
            self._subscribers.remove(q)

    def add_callback(self, cb: Callable[[OrchestrationEvent], Coroutine[Any, Any, None]]) -> None:
        """Adds an async callback invoked on every event."""
        self._callbacks.append(cb)

    async def publish(self, event: OrchestrationEvent) -> None:
        """Publishes an event to all subscriber queues and callbacks."""
        for q in list(self._subscribers):
            await q.put(event)

        for cb in self._callbacks:
            try:
                await cb(event)
            except Exception as e:
                logger.exception("Error in EventBus callback: %s", e)


def create_event(
    workflow_run_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    stage_id: Optional[str] = None,
    task_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    session_id: Optional[str] = None,
    role: Optional[str] = None,
) -> OrchestrationEvent:
    """Helper to create an OrchestrationEvent with a new UUID and timestamp."""
    return OrchestrationEvent(
        event_id=f"evt_{uuid.uuid4().hex[:12]}",
        workflow_run_id=workflow_run_id,
        stage_id=stage_id,
        task_id=task_id,
        turn_id=turn_id,
        session_id=session_id,
        role=role,
        timestamp=utc_now_iso(),
        type=event_type,
        payload=payload or {},
    )
