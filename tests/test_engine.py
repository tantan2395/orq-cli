"""Unit tests for the OrchestrationEngine state machine and task processing."""

import pytest
from orchestrator.commands import (
    AgentMessageCommand,
    HandoffCommand,
    HumanInterventionCommand,
    ReviewDecisionCommand,
    ReviewRequestCommand,
    WorkflowControlCommand,
)
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.models import ReviewDecision


@pytest.mark.asyncio
async def test_engine_workflow_transitions(tmp_path):
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "engine_test.db")
    db = Database(cfg.sqlite_db_path)
    events = EventBus()

    engine = OrchestrationEngine(config=cfg, db=db, events=events)
    await engine.initialize()

    # 1. Create run (starts in 'strategy')
    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    assert run.current_stage == "strategy"
    assert run.status == "running"

    # 2. Handoff from decision_maker -> developer (should transition stage to 'implementation')
    handoff_cmd = HandoffCommand(
        workflow_run_id=run.run_id,
        idempotency_key="handoff_1",
        requested_by="decision_maker",
        target_role="developer",
        task="Implement local database schemas",
        constraints=["No cloud DB"],
        acceptance_criteria=["92 tests pass"],
    )
    task1 = await engine.handle_handoff(handoff_cmd)
    assert task1.status == "queued"
    assert task1.target_role == "developer"

    run = await db.get_workflow_run(run.run_id)
    assert run.current_stage == "implementation"

    # Idempotency check: duplicate handoff call returns same task
    task1_duplicate = await engine.handle_handoff(handoff_cmd)
    assert task1_duplicate.task_id == task1.task_id

    # 3. Review request from developer -> code_reviewer (should transition stage to 'review')
    rev_req_cmd = ReviewRequestCommand(
        workflow_run_id=run.run_id,
        idempotency_key="review_req_1",
        requested_by="developer",
        target_role="code_reviewer",
        summary="Completed schemas and 92 tests pass",
        diff_or_patch="3639ab29",
        evidence={"tests_passed": 92},
    )
    task2 = await engine.handle_review_request(rev_req_cmd)
    assert task2.status == "queued"

    run = await db.get_workflow_run(run.run_id)
    assert run.current_stage == "review"

    # 4. Review decision: changes requested -> transitions back to 'implementation'
    decision_changes = ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        idempotency_key="decision_1",
        reviewer_role="code_reviewer",
        decision=ReviewDecision.CHANGES_REQUESTED,
        summary="Missing error boundary in DB worker",
        findings=["Uncaught exception in worker"],
    )
    task3 = await engine.handle_review_decision(decision_changes)
    assert task3.status == "completed"

    run = await db.get_workflow_run(run.run_id)
    assert run.current_stage == "implementation"

    # 5. Developer re-submits review request -> transitions back to 'review'
    rev_req_cmd2 = ReviewRequestCommand(
        workflow_run_id=run.run_id,
        idempotency_key="review_req_2",
        requested_by="developer",
        target_role="code_reviewer",
        summary="Fixed error boundary",
    )
    await engine.handle_review_request(rev_req_cmd2)
    run = await db.get_workflow_run(run.run_id)
    assert run.current_stage == "review"

    # 6. Review decision: approved -> transitions to 'final_review'
    decision_approved = ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        idempotency_key="decision_2",
        reviewer_role="code_reviewer",
        decision=ReviewDecision.APPROVED,
        summary="All checks pass",
    )
    await engine.handle_review_decision(decision_approved)
    run = await db.get_workflow_run(run.run_id)
    assert run.current_stage == "final_review"

    # 7. Final review decision: approved -> workflow marks completed
    final_decision = ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        idempotency_key="decision_final",
        reviewer_role="decision_maker",
        decision=ReviewDecision.APPROVED,
        summary="Milestone signed off",
    )
    await engine.handle_review_decision(final_decision)
    run = await db.get_workflow_run(run.run_id)
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_engine_pause_resume_and_cancellation(tmp_path):
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "control_test.db")
    engine = OrchestrationEngine(config=cfg)
    await engine.initialize()

    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    assert run.status == "running"

    # Pause
    await engine.handle_control(WorkflowControlCommand(
        workflow_run_id=run.run_id,
        action="pause",
        reason="Human inspect",
    ))
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.status == "paused"

    # Resume
    await engine.handle_control(WorkflowControlCommand(
        workflow_run_id=run.run_id,
        action="resume",
    ))
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.status == "running"

    # Create task and cancel it
    task = await engine.handle_handoff(HandoffCommand(
        workflow_run_id=run.run_id,
        requested_by="decision_maker",
        target_role="developer",
        task="Cancelled task",
    ))
    assert task.status == "queued"

    await engine.handle_control(WorkflowControlCommand(
        workflow_run_id=run.run_id,
        action="cancel_task",
        target_id=task.task_id,
        reason="No longer needed",
    ))
    task_fetched = await engine.db.get_task(task.task_id)
    assert task_fetched.status == "cancelled"
