"""Unit tests for orchestrator domain models."""

from orchestrator.models import (
    AgentSession,
    AgentTurnContext,
    Artifact,
    ArtifactType,
    ExecutionPolicy,
    ModelProfile,
    OrchestrationEvent,
    OrchestrationTask,
    ReasoningEffort,
    ReviewDecision,
    RoleConfig,
    StageConfig,
    StageType,
    WorkflowDefinition,
    WorkflowRun,
)


def test_model_profile_and_role_config():
    profile = ModelProfile(
        id="strategic",
        model="gpt-6-astra",
        reasoning=ReasoningEffort.HIGH,
        description="Lead strategy",
    )
    assert profile.id == "strategic"
    assert profile.reasoning == ReasoningEffort.HIGH

    role = RoleConfig(
        name="decision_maker",
        agent="codex",
        profile="strategic",
        prompt_file="prompts/decision_maker.md",
    )
    assert role.name == "decision_maker"
    assert role.agent == "codex"
    assert role.profile == "strategic"


def test_workflow_definition_and_run():
    stage = StageConfig(
        name="strategy",
        role="decision_maker",
        type=StageType.SEQUENTIAL,
        next_on_handoff="implementation",
    )
    defn = WorkflowDefinition(
        id="test_workflow",
        version=1,
        initial_stage="strategy",
        stages={"strategy": stage},
    )
    assert defn.version == 1
    assert defn.stages["strategy"].next_on_handoff == "implementation"

    run = WorkflowRun(
        run_id="run_001",
        definition_id="test_workflow",
        definition_version=1,
        workspace_root="/tmp/test",
        current_stage="strategy",
        status="running",
    )
    assert run.run_id == "run_001"
    assert run.status == "running"


def test_orchestration_task_with_idempotency():
    task = OrchestrationTask(
        task_id="task_123",
        workflow_run_id="run_001",
        stage_id="strategy",
        idempotency_key="unique_key_01",
        type="handoff",
        requested_by="decision_maker",
        target_role="developer",
        status="queued",
        payload={"task": "build feature"},
    )
    assert task.idempotency_key == "unique_key_01"
    assert task.target_role == "developer"
    assert task.status == "queued"


def test_artifact_model():
    artifact = Artifact(
        artifact_id="art_001",
        workflow_run_id="run_001",
        task_id="task_123",
        type=ArtifactType.REPORT,
        path="review-reports/m1-review.md",
        description="Accepted checkpoint report",
    )
    assert artifact.type == ArtifactType.REPORT
    assert artifact.path.endswith("m1-review.md")


def test_agent_turn_context_snapshot():
    turn_ctx = AgentTurnContext(
        turn_id="turn_01",
        workflow_run_id="run_001",
        stage_id="strategy",
        role="decision_maker",
        objective="Implement M1 checkpoint",
        role_contract="You are the lead architect.",
        bounded_task="Create bounded implementation slice",
        constraints=["No cloud DB", "Local only"],
        acceptance_criteria=["92 tests pass"],
        relevant_artifacts=["review-reports/m0.md"],
        previous_decisions=[{"decision": "approved"}],
    )
    assert turn_ctx.role == "decision_maker"
    assert len(turn_ctx.constraints) == 2
    assert len(turn_ctx.acceptance_criteria) == 1


def test_agent_session_normalization():
    session = AgentSession(
        id="canonical_sess_01",
        workflow_run_id="run_001",
        role="developer",
        agent_name="agy",
        native_session_id="conv_abc_123",
        workspace_root="/tmp/repo",
    )
    assert session.agent_name == "agy"
    assert session.native_session_id == "conv_abc_123"
    assert session.status == "idle"


def test_orchestration_event_correlation():
    evt = OrchestrationEvent(
        event_id="evt_001",
        workflow_run_id="run_001",
        stage_id="strategy",
        task_id="task_123",
        turn_id="turn_01",
        session_id="canonical_sess_01",
        role="decision_maker",
        type="agent_turn_started",
        payload={"message": "Starting planning turn"},
    )
    assert evt.task_id == "task_123"
    assert evt.turn_id == "turn_01"
    assert evt.role == "decision_maker"


def test_execution_policy():
    policy = ExecutionPolicy(workspace_root=".", use_git_worktree=True)
    assert policy.use_git_worktree is True
    assert "git_push" in policy.require_human_approval_for
