"""Unit tests for inbound command models."""

from orchestrator.commands import (
    AgentMessageCommand,
    HandoffCommand,
    HumanInterventionCommand,
    ReviewDecisionCommand,
    ReviewRequestCommand,
    WorkflowControlCommand,
)
from orchestrator.models import ReviewDecision


def test_handoff_command():
    cmd = HandoffCommand(
        workflow_run_id="run_001",
        idempotency_key="handoff_slice_1",
        requested_by="decision_maker",
        target_role="developer",
        task="Implement local database migration",
        context="Local offline storage",
        constraints=["No remote calls"],
        acceptance_criteria=["Migrations run without error"],
        artifacts=["review-reports/m0-design.md"],
    )
    assert cmd.workflow_run_id == "run_001"
    assert cmd.idempotency_key == "handoff_slice_1"
    assert cmd.target_role == "developer"
    assert len(cmd.constraints) == 1


def test_review_request_command():
    cmd = ReviewRequestCommand(
        workflow_run_id="run_001",
        idempotency_key="rev_req_01",
        requested_by="developer",
        target_role="code_reviewer",
        summary="Completed migration logic with 12 tests",
        diff_or_patch="3639ab290feba1450e6cdf13e70a88c6b8fc2c94",
        evidence={"tests_passed": 12, "uncached_lint": "clean"},
        retained_gates=["Windows installer verification pending"],
        artifacts=["review-reports/m1-probes.json"],
    )
    assert cmd.requested_by == "developer"
    assert cmd.evidence["tests_passed"] == 12
    assert len(cmd.retained_gates) == 1


def test_review_decision_command():
    cmd = ReviewDecisionCommand(
        workflow_run_id="run_001",
        idempotency_key="decision_01",
        reviewer_role="code_reviewer",
        decision=ReviewDecision.APPROVED,
        summary="Independently verified: all tests pass, diff is byte-identical",
        findings=["Clean architecture", "No database leaks"],
        evidence=["92 tests passed", "probe checks clean"],
        retained_gates=["Windows qualification retained"],
        artifacts=["review-reports/m1-acceptance.md"],
    )
    assert cmd.decision == ReviewDecision.APPROVED
    assert len(cmd.findings) == 2


def test_workflow_control_and_intervention():
    ctrl = WorkflowControlCommand(
        workflow_run_id="run_001",
        action="pause",
        reason="Human inspecting state",
    )
    assert ctrl.action == "pause"

    human = HumanInterventionCommand(
        workflow_run_id="run_001",
        target_role="decision_maker",
        message="Why did you choose SQLite over DuckDB?",
    )
    assert human.target_role == "decision_maker"
    assert "DuckDB" in human.message
