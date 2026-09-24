"""Unit tests for the Dispatcher layer."""

from typing import Any, AsyncIterator, Dict, Optional
import pytest
from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.dispatcher import Dispatcher
from orchestrator.events import EventBus
from orchestrator.models import AgentTurnContext, ReasoningEffort
from orchestrator.session_manager import SessionManager


class MockAdapter(BaseAgentAdapter):
    """Mock agent adapter for unit testing."""

    def __init__(self, agent_name: str = "mock"):
        super().__init__(agent_name=agent_name)
        self.executed_prompts = []

    async def execute_turn(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        self.executed_prompts.append(prompt)
        self.active_session_id = "mock_native_id_99"
        yield {"type": "status", "status": "running mock"}
        yield {"type": "agent_message", "content": "I am working on the task"}
        yield {"type": "complete", "exit_code": 0}


@pytest.mark.asyncio
async def test_dispatcher_resolution_and_execution(tmp_path):
    cfg = get_default_config()
    db = Database(str(tmp_path / "dispatcher.db"))
    await db.initialize()

    events = EventBus()
    event_log = []
    events.add_callback(lambda e: event_log.append(e))

    sess_mgr = SessionManager(db)
    mock_adapter = MockAdapter("codex")

    dispatcher = Dispatcher(
        config=cfg,
        session_manager=sess_mgr,
        event_bus=events,
        adapters={"codex": mock_adapter, "agy": MockAdapter("agy")},
    )

    # 1. Test resolution
    role_cfg, profile, adapter = dispatcher.resolve("decision_maker")
    assert role_cfg.name == "decision_maker"
    assert profile.model == "gpt-6-astra"
    assert adapter.agent_name == "codex"

    # 2. Test execution turn
    turn_ctx = AgentTurnContext(
        turn_id="turn_01",
        workflow_run_id="run_001",
        stage_id="strategy",
        role="decision_maker",
        objective="Test run",
        bounded_task="Create architecture plan",
    )

    activities = []
    async for act in dispatcher.execute_turn(
        turn_context=turn_ctx,
        effective_prompt="Plan the system",
        workspace_root=str(tmp_path),
    ):
        activities.append(act)

    assert len(activities) == 3
    assert activities[1]["type"] == "agent_message"

    # Verify session normalization captured native id
    session = await db.get_session("run_001", "decision_maker")
    assert session is not None
    assert session.native_session_id == "mock_native_id_99"

    # Verify lifecycle events were published
    event_types = [e.type for e in event_log]
    assert "agent_turn_started" in event_types
    assert "agent_activity" in event_types
    assert "agent_turn_completed" in event_types
