"""Core domain models and configuration schemas for agent-orchestrator."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RoleConfig(BaseModel):
    """Defines a role responsibility, decoupled from the agent and model."""
    name: str = Field(description="Role identifier (e.g. decision_maker, developer)")
    agent: str = Field(description="Underlying agent CLI (e.g. codex, agy)")
    model: str = Field(description="Specific model identifier")
    reasoning: ReasoningEffort = Field(default=ReasoningEffort.MEDIUM, description="Reasoning effort level")
    system_prompt: Optional[str] = Field(default=None, description="Optional system prompt overlay")
    session_id: Optional[str] = Field(default=None, description="Active or resumed conversation/session ID")


class StageConfig(BaseModel):
    """A stage in a workflow, referencing a role."""
    name: str = Field(description="Stage name (e.g. strategy, implementation, review)")
    role: str = Field(description="Role name assigned to this stage")
    description: Optional[str] = Field(default="", description="Stage description or purpose")
    next_on_handoff: Optional[str] = Field(default=None, description="Next stage on formal handoff")
    next_on_review_request: Optional[str] = Field(default=None, description="Next stage on review request")
    next_on_approved: Optional[str] = Field(default=None, description="Next stage when review is approved")
    next_on_changes_requested: Optional[str] = Field(default=None, description="Next stage when changes requested")


class WorkflowConfig(BaseModel):
    """Configures a multi-stage workflow pipeline."""
    name: str = Field(description="Workflow name")
    initial_stage: str = Field(description="Starting stage")
    stages: Dict[str, StageConfig] = Field(default_factory=dict, description="Mapped stages")


class HandoffPayload(BaseModel):
    """Formal transfer of responsibility between roles."""
    target_role: str = Field(description="Recipient role name")
    task: str = Field(description="Specific bounded task to execute")
    context: str = Field(default="", description="Relevant context and background")
    constraints: List[str] = Field(default_factory=list, description="Non-negotiable constraints")
    acceptance_criteria: List[str] = Field(default_factory=list, description="Explicit verification criteria")
    artifacts: List[str] = Field(default_factory=list, description="Referenced report, patch, or doc paths")


class ReviewRequestPayload(BaseModel):
    """Formal request from an implementer role for code/strategy review."""
    target_role: str = Field(description="Reviewer role name")
    summary: str = Field(description="Summary of changes and deliverables")
    diff_or_patch: Optional[str] = Field(default=None, description="Git commit, patch path, or diff summary")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Test output, probe JSON, build results")
    retained_gates: List[str] = Field(default_factory=list, description="Pending or deferred items")
    artifacts: List[str] = Field(default_factory=list, description="Files, evidence JSON, or screenshot paths")


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED = "blocked"


class ReviewDecisionPayload(BaseModel):
    """Reviewer's formal evaluation and decision."""
    decision: ReviewDecision
    feedback: str
    retained_gates: List[str] = Field(default_factory=list)
    suggested_handoff: Optional[HandoffPayload] = None


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class TaskItem(BaseModel):
    """Shared task item tracked by the orchestrator."""
    id: str
    title: str
    description: str = ""
    assigned_role: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentEventType(str, Enum):
    CHUNK = "chunk"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETE = "complete"
    ERROR = "error"


class AgentEvent(BaseModel):
    """Standardized event emitted during agent execution."""
    type: AgentEventType
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ChatMessage(BaseModel):
    """Message between human and agent or between agents."""
    id: str
    sender_role: str
    recipient_role: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_formal_handoff: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrchestratorConfig(BaseModel):
    """Root configuration structure for the orchestrator."""
    workspace_root: str = Field(default=".", description="Target workspace directory")
    roles: Dict[str, RoleConfig] = Field(default_factory=dict)
    workflows: Dict[str, WorkflowConfig] = Field(default_factory=dict)
    active_workflow: str = Field(default="default")
    review_reports_dir: str = Field(default="review-reports", description="Path to review reports")
