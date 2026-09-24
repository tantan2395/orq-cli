"""Unit tests for SQLite database persistence."""

import pytest
from orchestrator.db import Database
from orchestrator.models import (
    AgentSession,
    Artifact,
    ArtifactType,
    OrchestrationEvent,
    OrchestrationTask,
    WorkflowRun,
)


@pytest.mark.asyncio
async def test_database_lifecycle_and_idempotency(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    # 1. Test WorkflowRun
    run = WorkflowRun(
        run_id="run_001",
        definition_id="default_review_dev_loop",
        definition_version=1,
        workspace_root=str(tmp_path),
        current_stage="strategy",
        status="running",
    )
    await db.save_workflow_run(run)
    fetched_run = await db.get_workflow_run("run_001")
    assert fetched_run is not None
    assert fetched_run.current_stage == "strategy"

    # 2. Test OrchestrationTask with scoped idempotency
    task1 = OrchestrationTask(
        task_id="t1",
        workflow_run_id="run_001",
        idempotency_key="idemp_key_1",
        type="handoff",
        requested_by="decision_maker",
        target_role="developer",
        status="queued",
        payload={"task": "task 1"},
    )
    await db.save_task(task1)

    # Retrieval by idempotency
    existing = await db.get_task_by_idempotency("run_001", "idemp_key_1")
    assert existing is not None
    assert existing.task_id == "t1"

    # Same idempotency key in a DIFFERENT run should be allowed
    task2 = OrchestrationTask(
        task_id="t2",
        workflow_run_id="run_002",
        idempotency_key="idemp_key_1",
        type="handoff",
        requested_by="decision_maker",
        target_role="developer",
        status="queued",
        payload={"task": "task 2"},
    )
    await db.save_task(task2)
    existing_run2 = await db.get_task_by_idempotency("run_002", "idemp_key_1")
    assert existing_run2 is not None
    assert existing_run2.task_id == "t2"

    # 3. Test Artifact
    artifact = Artifact(
        artifact_id="art_01",
        workflow_run_id="run_001",
        type=ArtifactType.REPORT,
        path="review-reports/m1.md",
    )
    await db.save_artifact(artifact)
    artifacts = await db.list_artifacts("run_001")
    assert len(artifacts) == 1
    assert artifacts[0].type == ArtifactType.REPORT

    # 4. Test Event
    evt = OrchestrationEvent(
        event_id="e1",
        workflow_run_id="run_001",
        type="stage_started",
        payload={"stage": "strategy"},
    )
    await db.save_event(evt)
    events = await db.list_events("run_001")
    assert len(events) == 1
    assert events[0].type == "stage_started"

    # 5. Test Session
    sess = AgentSession(
        id="s1",
        workflow_run_id="run_001",
        role="developer",
        agent_name="agy",
        workspace_root=str(tmp_path),
    )
    await db.save_session(sess)
    fetched_sess = await db.get_session("run_001", "developer")
    assert fetched_sess is not None
    assert fetched_sess.agent_name == "agy"
