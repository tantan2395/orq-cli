"""Inbound command models for agent-orchestrator."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from orchestrator.models import ReviewDecision


class BaseCommand(BaseModel):
    """Base class for all inbound commands to the orchestrator engine."""
    workflow_run_id: str = Field(description="Target workflow run identifier")


class HandoffCommand(BaseCommand):
    """Formal transfer of execution responsibility between roles."""
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key scoped to workflow_run_id")
    requested_by: str = Field(default="agent", description="Role initiating the handoff")
    target_role: str = Field(description="Target role to assume responsibility")
    task: str = Field(description="Specific bounded task to execute")
    context: str = Field(default="", description="Relevant context and background")
    constraints: List[str] = Field(default_factory=list, description="Non-negotiable constraints")
    acceptance_criteria: List[str] = Field(default_factory=list, description="Verification criteria")
    artifacts: List[str] = Field(default_factory=list, description="Referenced report, patch, or doc paths")


class ReviewRequestCommand(BaseCommand):
    """Formal request from an implementer role for independent review."""
    idempotency_key: Optional[str] = Field(default=None)
    requested_by: str = Field(default="developer", description="Implementer role name")
    target_role: str = Field(default="code_reviewer", description="Reviewer role name")
    summary: str = Field(description="Summary of changes and deliverables")
    diff_or_patch: Optional[str] = Field(default=None, description="Commit hash, patch path, or diff")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Test output, probe JSON, build metrics")
    retained_gates: List[str] = Field(default_factory=list, description="Pending or deferred items")
    artifacts: List[str] = Field(default_factory=list, description="Associated artifact paths")


class ReviewDecisionCommand(BaseCommand):
    """Reviewer's formal evaluation verdict and findings."""
    idempotency_key: Optional[str] = Field(default=None)
    reviewer_role: str = Field(description="Reviewer role submitting the decision")
    decision: ReviewDecision = Field(description="Evaluation outcome")
    summary: str = Field(description="High-level evaluation summary")
    findings: List[str] = Field(default_factory=list, description="List of defects, praises, or issues")
    evidence: List[str] = Field(default_factory=list, description="Verification evidence collected")
    retained_gates: List[str] = Field(default_factory=list, description="Deferred items")
    artifacts: List[str] = Field(default_factory=list, description="Paths to review report artifacts")


class AgentMessageCommand(BaseCommand):
    """Conversational exchange between roles or with the human."""
    sender_role: str = Field(description="Sender role or 'human'")
    recipient_role: str = Field(description="Recipient role or 'human'")
    message: str = Field(description="Message body")


class HumanInterventionCommand(BaseCommand):
    """Direct human command or inquiry injected into a role session."""
    target_role: str = Field(description="Role being addressed by the human")
    message: str = Field(description="Human prompt or instruction")


class WorkflowControlCommand(BaseCommand):
    """Engine control actions: pause, resume, cancel task/stage, terminate agent."""
    action: Literal["pause", "resume", "cancel_task", "cancel_stage", "terminate_agent"]
    target_id: Optional[str] = Field(default=None, description="Task ID, stage name, or session ID")
    reason: Optional[str] = Field(default=None, description="Reason for cancellation or pause")


class TaskCreateCommand(BaseCommand):
    """Creates a durable tracked task."""
    idempotency_key: Optional[str] = Field(default=None)
    title: str = Field(description="Task title")
    description: str = Field(default="", description="Task details")
    assigned_role: str = Field(description="Role responsible for the task")


class TaskUpdateCommand(BaseCommand):
    """Updates status or notes of an existing task."""
    task_id: str = Field(description="Task identifier")
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    notes: Optional[str] = Field(default=None)
