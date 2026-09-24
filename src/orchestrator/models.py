"""Core domain models and configuration schemas for agent-orchestrator."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelProfile(BaseModel):
    """Reusable model & reasoning allocation profile."""
    id: str = Field(description="Profile identifier (e.g. strategic, implementation, adversarial, lightweight)")
    model: str = Field(description="Model identifier supported by CLI")
    reasoning: ReasoningEffort = Field(default=ReasoningEffort.MEDIUM, description="Reasoning effort level")
    description: Optional[str] = Field(default=None, description="Purpose or cost tier note")


class RoleConfig(BaseModel):
    """Defines a role responsibility, decoupled from the agent and model."""
    name: str = Field(description="Role identifier (e.g. decision_maker, developer)")
    agent: str = Field(description="Underlying agent CLI (e.g. codex, agy)")
    profile: str = Field(description="Referenced ModelProfile id")
    prompt_file: Optional[str] = Field(default=None, description="Path to role behavioral contract markdown")
    description: Optional[str] = Field(default="", description="Role summary")
    seed_session_id: Optional[str] = Field(default=None, description="Optional native session ID to seed/resume")


class StageType(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    LOOP = "loop"
    HUMAN_GATE = "human_gate"


class StageConfig(BaseModel):
    """A stage in a workflow definition, referencing a role."""
    name: str = Field(description="Stage name")
    role: str = Field(description="Primary role name assigned to this stage")
    type: StageType = Field(default=StageType.SEQUENTIAL, description="Stage execution topology")
    description: Optional[str] = Field(default="", description="Stage description")
    next_on_handoff: Optional[str] = Field(default=None, description="Next stage on formal handoff")
    next_on_review_request: Optional[str] = Field(default=None, description="Next stage on review request")
    next_on_approved: Optional[str] = Field(default=None, description="Next stage when review is approved")
    next_on_changes_requested: Optional[str] = Field(default=None, description="Next stage when changes requested")
    max_loop_iterations: Optional[int] = Field(default=3, description="Safety bound for loop stages")


class WorkflowDefinition(BaseModel):
    """Declarative definition of a workflow pipeline."""
    id: str = Field(description="Workflow identifier")
    version: int = Field(default=1, description="Workflow definition version")
    description: Optional[str] = Field(default="", description="Workflow purpose")
    initial_stage: str = Field(description="Starting stage name")
    stages: Dict[str, StageConfig] = Field(default_factory=dict, description="Mapped stages")


class Project(BaseModel):
    """First-class project representing a persistent codebase/workspace."""
    id: str = Field(description="Unique project identifier or slug")
    name: str = Field(description="Human-readable project name")
    workspace_root: str = Field(description="Absolute path to project workspace directory")
    description: Optional[str] = Field(default="", description="Project overview")
    default_workflow: str = Field(default="default_review_dev_loop", description="Default workflow definition ID")
    config: Dict[str, Any] = Field(default_factory=dict, description="Project-level configuration overrides")
    status: Literal["active", "archived"] = Field(default="active", description="Project status")
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class WorkflowRun(BaseModel):
    """Runtime instance of a workflow definition."""
    run_id: str = Field(description="Unique run instance identifier")
    project_id: Optional[str] = Field(default=None, description="Associated Project ID")
    definition_id: str = Field(description="Associated WorkflowDefinition id")
    definition_version: int = Field(default=1, description="Associated WorkflowDefinition version")
    workspace_root: str = Field(description="Target repository root path")
    worktree_path: Optional[str] = Field(default=None, description="Isolated git worktree path if enabled")
    current_stage: str = Field(description="Active stage name")
    status: Literal["running", "paused", "completed", "failed", "cancelled"] = Field(default="running")
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ArtifactType(str, Enum):
    REPORT = "report"
    PATCH = "patch"
    DIFF = "diff"
    TEST_RESULT = "test_result"
    LOG = "log"
    EVIDENCE = "evidence"
    OTHER = "other"


class Artifact(BaseModel):
    """First-class artifact tracked by the orchestrator."""
    artifact_id: str = Field(description="Unique artifact ID")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    task_id: Optional[str] = Field(default=None, description="Task that produced this artifact")
    type: ArtifactType = Field(description="Artifact type classification")
    path: str = Field(description="Filesystem path relative to workspace or absolute")
    description: Optional[str] = Field(default=None, description="Summary or notes")
    created_at: str = Field(default_factory=utc_now_iso)


class TaskDependency(BaseModel):
    """Directional dependency edge between tasks (task_id depends on depends_on_task_id)."""
    task_id: str = Field(description="Dependent task ID")
    depends_on_task_id: str = Field(description="Prerequisite task ID that must complete first")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    created_at: str = Field(default_factory=utc_now_iso)


class OrchestrationTask(BaseModel):
    """Durable task managed by the orchestrator engine with run-scoped idempotency."""
    task_id: str = Field(description="Task identifier")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    stage_id: Optional[str] = Field(default=None, description="Workflow stage where task was created")
    idempotency_key: Optional[str] = Field(default=None, description="Optional key to prevent duplicate runs")
    title: str = Field(default="", description="Human-readable task title")
    description: Optional[str] = Field(default="", description="Detailed objective or scope")
    type: str = Field(description="Task category (handoff, review_request, review_decision, direct_chat, task_item)")
    requested_by: str = Field(description="Requesting role or 'human'")
    target_role: Optional[str] = Field(default=None, description="Target role to execute task")
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = Field(
        default="queued", description="Execution engine state"
    )
    kanban_column: Literal["backlog", "ready", "in_progress", "review", "blocked", "done"] = Field(
        default="ready", description="Authoritative Kanban board column"
    )
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    payload: Dict[str, Any] = Field(default_factory=dict, description="Input parameters")
    result: Optional[Dict[str, Any]] = Field(default=None, description="Execution outcome")
    error: Optional[str] = Field(default=None, description="Error details if failed")


class AgentTurnContext(BaseModel):
    """Immutable projected context snapshot provided to an agent for a specific turn."""
    turn_id: str = Field(description="Unique turn ID")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    stage_id: str = Field(description="Active stage name")
    role: str = Field(description="Executing role name")
    objective: str = Field(description="Overall workflow run objective")
    role_contract: str = Field(default="", description="Behavioral contract instructions from prompt file")
    bounded_task: str = Field(description="Specific bounded task to execute")
    constraints: List[str] = Field(default_factory=list, description="Non-negotiable constraints")
    acceptance_criteria: List[str] = Field(default_factory=list, description="Verification criteria")
    relevant_artifacts: List[str] = Field(default_factory=list, description="Referenced artifact paths")
    previous_decisions: List[Dict[str, Any]] = Field(default_factory=list, description="Relevant prior decisions")


class AgentSession(BaseModel):
    """Normalizes native CLI sessions into a canonical orchestrator session."""
    id: str = Field(description="Canonical orchestrator session UUID")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    role: str = Field(description="Role bound to this session")
    agent_name: str = Field(description="CLI backend name (codex, agy)")
    native_session_id: Optional[str] = Field(default=None, description="Native CLI ID (e.g. Codex thread UUID, Agy conv ID)")
    workspace_root: str = Field(description="Workspace or worktree path")
    status: Literal["idle", "running", "paused", "closed"] = Field(default="idle")
    created_at: str = Field(default_factory=utc_now_iso)
    last_activity: str = Field(default_factory=utc_now_iso)


class ExecutionPolicy(BaseModel):
    """Containment and permission safety policy."""
    workspace_root: str = Field(default=".", description="Default target repository path")
    use_git_worktree: bool = Field(default=True, description="Run in isolated git worktree by default")
    filesystem_mode: Literal["workspace_only", "full"] = Field(default="workspace_only")
    allow_network: bool = Field(default=True)
    allow_shell: bool = Field(default=True)
    allow_git: bool = Field(default=True)
    allow_destructive_operations: bool = Field(default=False)
    require_human_approval_for: List[str] = Field(
        default_factory=lambda: ["git_push", "force_push", "destructive_git", "deployment"]
    )


class OrchestrationEvent(BaseModel):
    """Standardized event recorded in event store and broadcast to subscribers."""
    event_id: str = Field(description="Unique event ID")
    workflow_run_id: str = Field(description="Associated workflow run ID")
    stage_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    turn_id: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None)
    role: Optional[str] = Field(default=None)
    timestamp: str = Field(default_factory=utc_now_iso)
    type: str = Field(description="Event classification (e.g. stage_started, agent_turn_started, review_requested)")
    payload: Dict[str, Any] = Field(default_factory=dict)


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
