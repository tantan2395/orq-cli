"""End-to-End Headless Multi-Agent Loop Verification."""

from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional
import pytest

from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.config import get_default_config
from orchestrator.dispatcher import Dispatcher
from orchestrator.mcp.ipc import IPCClient
from orchestrator.models import ReasoningEffort
from orchestrator.runner import WorkflowRunner


class ScriptedAgentAdapter(BaseAgentAdapter):
    """Simulates realistic agent behavior by invoking MCP tools over IPC during execution turns."""

    def __init__(self, agent_name: str, ipc_socket: Path):
        super().__init__(agent_name=agent_name)
        self.ipc_socket = ipc_socket

    async def execute_turn(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        client = IPCClient(socket_path=self.ipc_socket)

        if "role: decision_maker" in prompt.lower() and "stage: strategy" in prompt.lower():
            # Step 1: Decision Maker analyzes and hands off to Developer
            yield {"type": "status", "status": "Decomposing M1 milestone into bounded task"}
            yield {"type": "agent_message", "content": "I have created the task specification for the developer."}

            res = await client.call("agents_handoff", {
                "workflow_run_id": self._extract_run_id(prompt),
                "target_role": "developer",
                "task": "Implement local database core and accounting queries",
                "constraints": ["No cloud dependencies", "Offline only"],
                "acceptance_criteria": ["92 unit tests pass"],
                "idempotency_key": "handoff_m1",
            })
            yield {"type": "tool_call", "name": "agents_handoff", "args": res}
            yield {"type": "complete", "exit_code": 0}

        elif "role: developer" in prompt.lower():
            # Step 2: Developer implements and requests review
            yield {"type": "status", "status": "Writing local storage schema and test fixtures"}
            yield {"type": "agent_message", "content": "Implemented local accounting core. 92 tests pass locally."}

            res = await client.call("review_request", {
                "workflow_run_id": self._extract_run_id(prompt),
                "summary": "Completed local storage engine with byte-identical diff",
                "diff_or_patch": "3639ab290feba1450e6cdf13e70a88c6b8fc2c94",
                "evidence": {"tests_passed": 92, "lint": "clean"},
                "idempotency_key": "review_req_m1",
            })
            yield {"type": "tool_call", "name": "review_request", "args": res}
            yield {"type": "complete", "exit_code": 0}

        elif "role: code_reviewer" in prompt.lower():
            # Step 3: Code Reviewer evaluates and approves
            yield {"type": "status", "status": "Independently verifying 92 tests and diff hash"}
            yield {"type": "agent_message", "content": "Diff SHA-256 verified byte-identical. Gates retained."}

            res = await client.call("review_decision", {
                "workflow_run_id": self._extract_run_id(prompt),
                "reviewer_role": "code_reviewer",
                "decision": "approved",
                "summary": "M1 checkpoint approved with byte-identical verification",
                "evidence": ["92 passed"],
                "idempotency_key": "decision_m1_review",
            })
            yield {"type": "tool_call", "name": "review_decision", "args": res}
            yield {"type": "complete", "exit_code": 0}

        elif "stage: final_review" in prompt.lower():
            # Step 4: Decision Maker gives final milestone approval
            yield {"type": "status", "status": "Signing off completed checkpoint"}
            yield {"type": "agent_message", "content": "M1 milestone officially completed."}

            res = await client.call("review_decision", {
                "workflow_run_id": self._extract_run_id(prompt),
                "reviewer_role": "decision_maker",
                "decision": "approved",
                "summary": "Final sign-off complete",
                "idempotency_key": "decision_m1_final",
            })
            yield {"type": "tool_call", "name": "review_decision", "args": res}
            yield {"type": "complete", "exit_code": 0}

    def _extract_run_id(self, prompt: str) -> str:
        for line in prompt.splitlines():
            if "run_" in line:
                for part in line.split():
                    if part.startswith("run_"):
                        return part.strip("'\",")
        return "run_unknown"


@pytest.mark.asyncio
async def test_full_headless_workflow_e2e(tmp_path: Path):
    """Executes the complete Strategy -> Implementation -> Review -> Final Review autonomous loop."""
    socket_path = tmp_path / "e2e_ipc.sock"
    db_path = str(tmp_path / "e2e_state.db")

    cfg = get_default_config()
    cfg.sqlite_db_path = db_path
    cfg.ipc_socket_path = str(socket_path)

    # Initialize runner with scripted adapters that call real MCP tools over IPC
    codex_mock = ScriptedAgentAdapter("codex", socket_path)
    agy_mock = ScriptedAgentAdapter("agy", socket_path)

    runner = WorkflowRunner(
        config=cfg,
        workspace_root=str(tmp_path),
        db_path=db_path,
        socket_path=socket_path,
    )
    # Register mocked adapters on the runner dispatcher
    runner.dispatcher.register_adapter("codex", codex_mock)
    runner.dispatcher.register_adapter("agy", agy_mock)

    # Run the autonomous loop
    final_run = await runner.run(workflow_id="default_review_dev_loop", max_turns=6)

    # 1. Verify workflow completion
    assert final_run.status == "completed"
    assert final_run.current_stage == "final_review"

    # 2. Verify all tasks in SQLite
    tasks = await runner.db.list_tasks(final_run.run_id)
    task_types = [t.type for t in tasks]
    assert "handoff" in task_types
    assert "review_request" in task_types
    assert "review_decision" in task_types

    # 3. Verify event stream history in SQLite
    events = await runner.db.list_events(final_run.run_id, limit=100)
    event_types = [e.type for e in events]
    assert "workflow_started" in event_types
    assert "stage_started" in event_types
    assert "handoff_created" in event_types
    assert "review_requested" in event_types
    assert "review_completed" in event_types
    assert "workflow_completed" in event_types

    # 4. Verify correlation IDs on every event
    for e in events:
        assert e.workflow_run_id == final_run.run_id
        assert e.event_id is not None
        assert e.timestamp is not None
