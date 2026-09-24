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


@pytest.mark.asyncio
async def test_review_decision_auto_queues_developer_fix_task(tmp_path):
    """Verify that CHANGES_REQUESTED automatically transitions to implementation and queues a task for developer."""
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "review_loop.db")
    engine = OrchestrationEngine(config=cfg)
    await engine.initialize()

    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    # Move to implementation -> review
    await engine.handle_handoff(HandoffCommand(
        workflow_run_id=run.run_id,
        target_role="developer",
        task="Build feature",
    ))
    await engine.handle_review_request(ReviewRequestCommand(
        workflow_run_id=run.run_id,
        summary="Feature ready for review",
    ))
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.current_stage == "review"

    # Reviewer requests changes
    decision_cmd = ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        reviewer_role="code_reviewer",
        decision=ReviewDecision.CHANGES_REQUESTED,
        summary="Found 2 failing assertions in telemetry parsing",
        findings=["Parsing error in timestamp", "Missing boundary check"],
    )
    await engine.handle_review_decision(decision_cmd)

    # Verify stage transition back to implementation
    refreshed_run = await engine.db.get_workflow_run(run.run_id)
    assert refreshed_run.current_stage == "implementation"

    # Verify auto-queued fix task for developer
    tasks = await engine.db.list_tasks(run.run_id)
    dev_fix_tasks = [t for t in tasks if t.target_role == "developer" and t.status == "queued"]
    assert len(dev_fix_tasks) == 1
    assert "Fix Review Findings (Round 1)" in dev_fix_tasks[0].title
    assert "Missing boundary check" in dev_fix_tasks[0].description
    assert dev_fix_tasks[0].kanban_column == "ready"


@pytest.mark.asyncio
async def test_review_decision_auto_queues_decision_maker_signoff_task(tmp_path):
    """Verify that APPROVED automatically transitions to final_review and queues an acceptance task for decision_maker."""
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "review_approve.db")
    engine = OrchestrationEngine(config=cfg)
    await engine.initialize()

    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    await engine.handle_handoff(HandoffCommand(
        workflow_run_id=run.run_id,
        target_role="developer",
        task="Build feature",
    ))
    await engine.handle_review_request(ReviewRequestCommand(
        workflow_run_id=run.run_id,
        summary="Feature ready for review",
    ))

    # Reviewer approves
    decision_cmd = ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        reviewer_role="code_reviewer",
        decision=ReviewDecision.APPROVED,
        summary="All tests passed and verified with 0 defects",
        artifacts=["/path/to/report.md"],
    )
    await engine.handle_review_decision(decision_cmd)

    # Verify stage transition to final_review
    refreshed_run = await engine.db.get_workflow_run(run.run_id)
    assert refreshed_run.current_stage == "final_review"

    # Verify auto-queued task for decision_maker
    tasks = await engine.db.list_tasks(run.run_id)
    signoff_tasks = [t for t in tasks if t.target_role == "decision_maker" and t.status == "queued"]
    assert len(signoff_tasks) == 1
    assert "Final Acceptance & Sign-off" in signoff_tasks[0].title
    assert signoff_tasks[0].kanban_column == "ready"


@pytest.mark.asyncio
async def test_review_rework_max_iterations_pauses_workflow(tmp_path):
    """Verify that exceeding max_loop_iterations pauses the workflow run for operator review."""
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "max_loop.db")
    engine = OrchestrationEngine(config=cfg)
    await engine.initialize()

    run = await engine.create_workflow_run(workspace_root=str(tmp_path))

    # Simulate 3 review rejections
    for i in range(3):
        # Move to review
        run.current_stage = "review"
        await engine.db.save_workflow_run(run)

        await engine.handle_review_decision(ReviewDecisionCommand(
            workflow_run_id=run.run_id,
            reviewer_role="code_reviewer",
            decision=ReviewDecision.CHANGES_REQUESTED,
            summary=f"Rejection #{i+1}",
        ))

    # After 3 rejections, the workflow must be paused
    refreshed_run = await engine.db.get_workflow_run(run.run_id)
    assert refreshed_run.status == "paused"


@pytest.mark.asyncio
async def test_idempotency_key_does_not_collide_between_request_and_decision(tmp_path):
    """Verify that a review_decision does not match a review_request sharing the same idempotency key."""
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "idemp.db")
    engine = OrchestrationEngine(config=cfg)
    await engine.initialize()

    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    shared_key = "round-1-shared-key"

    # Developer creates review request
    req = await engine.handle_review_request(ReviewRequestCommand(
        workflow_run_id=run.run_id,
        idempotency_key=shared_key,
        summary="Review request with key",
    ))
    assert req.type == "review_request"

    # Reviewer responds with the same key
    decision = await engine.handle_review_decision(ReviewDecisionCommand(
        workflow_run_id=run.run_id,
        idempotency_key=shared_key,
        reviewer_role="code_reviewer",
        decision=ReviewDecision.APPROVED,
        summary="Decision with same key",
    ))
    assert decision.type == "review_decision"
    assert decision.task_id != req.task_id
