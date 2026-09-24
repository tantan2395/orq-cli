"""Tests for task dependencies, cycle detection, gated execution, and dynamic promotion."""

from pathlib import Path
import pytest
from orchestrator.commands import TaskCreateCommand, TaskDependencyCommand, TaskLifecycleCommand
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus


@pytest.fixture
def test_setup(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    db = Database(str(db_file))
    events = EventBus()
    cfg = get_default_config()
    engine = OrchestrationEngine(config=cfg, db=db, events=events)
    return db, events, engine


@pytest.mark.asyncio
async def test_self_dependency_rejection(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    t1 = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run.run_id,
        title="Task 1",
    ))

    with pytest.raises(ValueError, match="cannot depend on itself"):
        await engine.handle_task_dependency(TaskDependencyCommand(
            workflow_run_id=run.run_id,
            task_id=t1.task_id,
            depends_on_task_id=t1.task_id,
            action="add",
        ))


@pytest.mark.asyncio
async def test_cross_run_dependency_rejection(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run1 = await engine.create_workflow_run()
    run2 = await engine.create_workflow_run()

    t1 = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run1.run_id,
        title="Task Run 1",
    ))
    t2 = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run2.run_id,
        title="Task Run 2",
    ))

    with pytest.raises(ValueError, match="Cross-run"):
        await engine.handle_task_dependency(TaskDependencyCommand(
            workflow_run_id=run1.run_id,
            task_id=t1.task_id,
            depends_on_task_id=t2.task_id,
            action="add",
        ))


@pytest.mark.asyncio
async def test_cycle_detection(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    # Create A, B, C
    tA = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="Task A"))
    tB = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="Task B"))
    tC = await engine.handle_task_create(TaskCreateCommand(workflow_run_id=run.run_id, title="Task C"))

    # B depends on A (A -> B)
    await engine.handle_task_dependency(TaskDependencyCommand(
        workflow_run_id=run.run_id,
        task_id=tB.task_id,
        depends_on_task_id=tA.task_id,
        action="add",
    ))

    # C depends on B (A -> B -> C)
    await engine.handle_task_dependency(TaskDependencyCommand(
        workflow_run_id=run.run_id,
        task_id=tC.task_id,
        depends_on_task_id=tB.task_id,
        action="add",
    ))

    # Attempt cycle: A depends on C (C -> A would create A -> B -> C -> A)
    with pytest.raises(ValueError, match="circular cycle"):
        await engine.handle_task_dependency(TaskDependencyCommand(
            workflow_run_id=run.run_id,
            task_id=tA.task_id,
            depends_on_task_id=tC.task_id,
            action="add",
        ))


@pytest.mark.asyncio
async def test_gated_start_and_dynamic_promotion(test_setup):
    db, events, engine = test_setup
    await engine.initialize()
    run = await engine.create_workflow_run()

    # Track emitted events
    emitted = []
    events.add_callback(lambda e: emitted.append(e))

    # Task A (Independent)
    tA = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run.run_id,
        title="Database Migration",
    ))
    assert tA.kanban_column == "ready"

    # Task B (Depends on Task A)
    tB = await engine.handle_task_create(TaskCreateCommand(
        workflow_run_id=run.run_id,
        title="API Endpoints",
        dependencies=[tA.task_id],
    ))
    # Since Task A is not completed, Task B is automatically placed into BLOCKED
    assert tB.kanban_column == "blocked"

    # Attempt to start Task B before Task A finishes -> Gated rejection
    res = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=tB.task_id,
        action="start",
    ))
    assert res["accepted"] is False
    assert res["status"] == "blocked"
    assert tA.task_id in res["blocking_tasks"]

    # Start and Complete Task A
    await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=tA.task_id,
        action="start",
    ))
    complete_res = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=tA.task_id,
        action="complete",
    ))
    assert complete_res["accepted"] is True
    assert tB.task_id in complete_res["promoted_dependents"]

    # Verify Task B was dynamically promoted from BLOCKED to READY!
    refreshed_B = await db.get_task(tB.task_id)
    assert refreshed_B.kanban_column == "ready"

    # Verify task_ready event was published
    ready_events = [e for e in emitted if e.type == "task_ready" and e.task_id == tB.task_id]
    assert len(ready_events) == 1

    # Now Task B can successfully start!
    start_B = await engine.handle_task_lifecycle(TaskLifecycleCommand(
        workflow_run_id=run.run_id,
        task_id=tB.task_id,
        action="start",
    ))
    assert start_B["accepted"] is True
    assert start_B["status"] == "in_progress"
