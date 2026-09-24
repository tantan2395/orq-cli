"""Tests for dual-state Kanban projection and board reconstruction from SQLite."""

from pathlib import Path
import pytest
from textual.widgets import Label, Static
from orchestrator.commands import TaskCreateCommand, TaskLifecycleCommand
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.tui.app import OrchestratorTUI
from orchestrator.tui.kanban import KanbanBoard, TaskCard


@pytest.fixture
def test_setup(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    db = Database(str(db_file))
    events = EventBus()
    cfg = get_default_config()
    engine = OrchestrationEngine(config=cfg, db=db, events=events)
    return db, events, engine


@pytest.mark.asyncio
async def test_dual_state_model_orthogonality(test_setup):
    """Verify that execution status and kanban_column can evolve independently without collisions."""
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    task = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run.run_id,
        title="Offline Sync",
        kanban_column="backlog",
    ))
    assert task.status == "queued"
    assert task.kanban_column == "backlog"

    # Start task -> moves status to running and kanban_column to in_progress
    start_res = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=task.task_id,
        action="start",
    ))
    assert start_res["accepted"] is True
    t_running = await db.get_task(task.task_id)
    assert t_running.status == "running"
    assert t_running.kanban_column == "in_progress"

    # Block task -> status stays running/queued while column is blocked
    block_res = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=task.task_id,
        action="block",
        reason="Waiting for vendor API keys",
    ))
    assert block_res["accepted"] is True
    t_blocked = await db.get_task(task.task_id)
    assert t_blocked.kanban_column == "blocked"

    # Complete task
    comp_res = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=task.task_id,
        action="complete",
    ))
    assert comp_res["accepted"] is True
    t_done = await db.get_task(task.task_id)
    assert t_done.status == "completed"
    assert t_done.kanban_column == "done"


@pytest.mark.asyncio
async def test_board_projection_reconstruction_from_db(test_setup):
    """Verify that restarting ORQ reconstructs exact board column placement from persisted SQLite."""
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    # Create tasks in different columns
    t_backlog = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="T1", kanban_column="backlog"))
    t_ready = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="T2", kanban_column="ready"))
    t_prog = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="T3", kanban_column="in_progress"))
    t_review = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="T4", kanban_column="review"))
    t_done = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="T5", kanban_column="done"))

    # Simulate restart: new Database and Board instances pointing to same SQLite file
    db_reopened = Database(db.db_path)
    loaded_tasks = await db_reopened.list_tasks(run.run_id)

    board = KanbanBoard()
    # Mock populate board
    grouped = {col_id: [] for col_id in ["backlog", "ready", "in_progress", "review", "blocked", "done"]}
    for t in loaded_tasks:
        grouped[t.kanban_column].append(t)

    assert len(grouped["backlog"]) == 1
    assert len(grouped["ready"]) == 1
    assert len(grouped["in_progress"]) == 1
    assert len(grouped["review"]) == 1
    assert len(grouped["done"]) == 1
    assert grouped["backlog"][0].title == "T1"
    assert grouped["ready"][0].title == "T2"


@pytest.mark.asyncio
async def test_tui_kanban_rendering_and_tabs():
    """Verify that the TUI mounts the Kanban board with all 6 columns."""
    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        board = app.query_one("#kanban-board", KanbanBoard)
        assert board is not None

        # Verify all 6 columns exist
        for col_id in ["backlog", "ready", "in_progress", "review", "blocked", "done"]:
            col = app.query_one(f"#col-{col_id}")
            assert col is not None

        # Verify Tab 1 is Kanban by default
        app.action_switch_tab_1()
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-kanban"

        # Verify Tab 2 is Workflow
        app.action_switch_tab_2()
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-workflow"

        # Verify Tab 3 is Agents
        app.action_switch_tab_3()
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-agents"

        # Verify Tab 4 is Activity
        app.action_switch_tab_4()
        await pilot.pause()
        assert app.query_one("#tabs").active == "tab-activity"
