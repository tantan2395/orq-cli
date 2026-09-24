"""Tests for the 15 MCP Task and Kanban tools, IPC routing, and dot aliases."""

from pathlib import Path
import pytest
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.mcp.server import create_mcp_server


@pytest.fixture
def test_setup(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    db = Database(str(db_file))
    events = EventBus()
    cfg = get_default_config()
    engine = OrchestrationEngine(config=cfg, db=db, events=events)
    return db, events, engine


def test_mcp_server_registers_all_15_tools():
    """Verify that create_mcp_server registers all 15 task tools alongside workflow tools."""
    server = create_mcp_server()
    registered = {t.name for t in server._tool_manager.list_tools()}

    expected_tools = {
        "task_create",
        "task_get",
        "task_list",
        "task_update",
        "task_delete",
        "task_start",
        "task_complete",
        "task_cancel",
        "task_block",
        "task_unblock",
        "task_add_dependency",
        "task_remove_dependency",
        "task_dependencies",
        "task_dependents",
        "task_ready",
        "agents_handoff",
        "review_request",
        "review_decision",
        "agents_message",
        "workflow_status",
        "workflow_pause",
        "workflow_resume",
        "ask_question",
    }
    for expected in expected_tools:
        assert expected in registered, f"Tool '{expected}' not found in MCP server tools"


@pytest.mark.asyncio
async def test_ipc_task_crud_and_dot_aliases(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    # 1. task.create (dot alias)
    res_create = await engine.handle_ipc_command("task.create", {
        "workflow_run_id": run.run_id,
        "title": "Build inventory sync",
        "description": "Sync with vendor inventory API",
        "target_role": "developer",
        "kanban_column": "ready",
    })
    assert res_create["accepted"] is True
    task_id = res_create["task_id"]
    assert res_create["kanban_column"] == "ready"

    # 2. task_get (underscore name)
    res_get = await engine.handle_ipc_command("task_get", {"task_id": task_id})
    assert res_get["accepted"] is True
    assert res_get["task"]["title"] == "Build inventory sync"

    # 3. task.update (dot alias)
    res_update = await engine.handle_ipc_command("task.update", {
        "task_id": task_id,
        "workflow_run_id": run.run_id,
        "title": "Build inventory synchronization",
    })
    assert res_update["accepted"] is True

    # 4. task_list
    res_list = await engine.handle_ipc_command("task_list", {"workflow_run_id": run.run_id})
    assert res_list["accepted"] is True
    assert len(res_list["tasks"]) >= 1


@pytest.mark.asyncio
async def test_ipc_lifecycle_and_dependencies(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    # Create task 1
    t1 = await engine.handle_ipc_command("task_create", {
        "workflow_run_id": run.run_id,
        "title": "Task 1",
    })
    t1_id = t1["task_id"]

    # Create task 2
    t2 = await engine.handle_ipc_command("task_create", {
        "workflow_run_id": run.run_id,
        "title": "Task 2",
    })
    t2_id = t2["task_id"]

    # task_add_dependency: Task 2 depends on Task 1
    dep_res = await engine.handle_ipc_command("task_add_dependency", {
        "workflow_run_id": run.run_id,
        "task_id": t2_id,
        "depends_on_task_id": t1_id,
    })
    assert dep_res["accepted"] is True

    # Check task_dependencies and task_dependents
    deps = await engine.handle_ipc_command("task_dependencies", {"task_id": t2_id})
    assert t1_id in deps["dependencies"]

    dependents = await engine.handle_ipc_command("task_dependents", {"task_id": t1_id})
    assert t2_id in dependents["dependents"]

    # Check task_ready
    ready_t2 = await engine.handle_ipc_command("task_ready", {"task_id": t2_id})
    assert ready_t2["is_ready"] is False

    # Start Task 1
    start_t1 = await engine.handle_ipc_command("task_start", {"task_id": t1_id})
    assert start_t1["accepted"] is True
    assert start_t1["status"] == "in_progress"

    # Complete Task 1 -> Promotes Task 2
    comp_t1 = await engine.handle_ipc_command("task_complete", {"task_id": t1_id})
    assert comp_t1["accepted"] is True
    assert t2_id in comp_t1["promoted_dependents"]

    # Now task_ready on Task 2 is True
    ready_t2_after = await engine.handle_ipc_command("task_ready", {"task_id": t2_id})
    assert ready_t2_after["is_ready"] is True

    # Start Task 2
    start_t2 = await engine.handle_ipc_command("task.start", {"task_id": t2_id})
    assert start_t2["accepted"] is True

    # Cancel Task 2
    cancel_t2 = await engine.handle_ipc_command("task_cancel", {"task_id": t2_id, "reason": "Not needed"})
    assert cancel_t2["accepted"] is True
    assert cancel_t2["status"] == "cancelled"

    # Delete Task 2
    del_res = await engine.handle_ipc_command("task_delete", {"task_id": t2_id})
    assert del_res["accepted"] is True


@pytest.mark.asyncio
async def test_ipc_ask_question_pauses_and_emits_event(test_setup):
    import asyncio
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    published_events = []
    async def _capture(e):
        published_events.append(e)
    events.add_callback(_capture)

    res = await engine.handle_ipc_command("ask_question", {
        "workflow_run_id": run.run_id,
        "question": "Which architecture pattern do you want?",
        "options": ["Monolith", "Microservices"],
        "is_multi_select": False,
        "allow_write_in": True,
        "sender_role": "decision_maker",
    })

    assert res["accepted"] is True
    assert res["status"] == "awaiting_human_answer"

    # Verify workflow was paused
    refreshed_run = await db.get_workflow_run(run.run_id)
    assert refreshed_run.status == "paused"

    # Verify question_asked event was emitted
    q_events = [e for e in published_events if e.type == "question_asked"]
    assert len(q_events) == 1
    assert q_events[0].payload.get("question") == "Which architecture pattern do you want?"
    assert q_events[0].payload.get("options") == ["Monolith", "Microservices"]
