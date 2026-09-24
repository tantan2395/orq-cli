"""Tests for the ContextManager prompt loading and effective prompt assembly."""

from pathlib import Path
from orchestrator.context_manager import ContextManager
from orchestrator.models import OrchestrationTask, RoleConfig, WorkflowRun, utc_now_iso


def test_context_manager_prompt_loading_with_arbitrary_cwd(monkeypatch, tmp_path):
    # Simulate running CLI from an arbitrary directory with no prompts folder
    monkeypatch.chdir(tmp_path)

    cm = ContextManager()
    contract = cm.load_role_contract("prompts/developer.md")
    assert contract != ""
    assert "Core Developer" in contract
    assert "review_request" in contract

    # Also test with just the filename
    contract_filename_only = cm.load_role_contract("developer.md")
    assert contract_filename_only != ""
    assert "Core Developer" in contract_filename_only


def test_context_manager_assemble_effective_prompt():
    cm = ContextManager()
    run = WorkflowRun(
        run_id="run_test_123",
        definition_id="default_review_dev_loop",
        definition_version=1,
        workspace_root="/tmp/test",
        current_stage="implementation",
        status="running",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    role_cfg = RoleConfig(
        name="developer",
        agent="agy",
        profile="implementation",
        prompt_file="prompts/developer.md",
    )
    task = OrchestrationTask(
        task_id="task_test_456",
        workflow_run_id=run.run_id,
        stage_id="implementation",
        type="handoff",
        requested_by="decision_maker",
        target_role="developer",
        status="queued",
        created_at=utc_now_iso(),
        payload={
            "task": "Update README.md with MCP tools",
            "constraints": ["Modify README.md only"],
            "acceptance_criteria": ["README contains MCP tools section"],
        },
    )

    ctx = cm.create_turn_context(workflow_run=run, role_config=role_cfg, task=task)
    assert ctx.role_contract != ""
    assert "review_request" in ctx.role_contract

    effective = cm.assemble_effective_prompt(ctx)
    assert "[AGENT IDENTITY CONTRACT]" in effective
    assert "ROLE: developer" in effective
    assert "[ROLE BEHAVIORAL CONTRACT]" in effective
    assert "## Communication & Handoff Protocol" in effective
    assert "[BOUNDED TASK]" in effective
    assert "Update README.md with MCP tools" in effective
    assert "[CONSTRAINTS]" in effective
    assert "- Modify README.md only" in effective
    assert "[ACCEPTANCE CRITERIA]" in effective
    assert "- README contains MCP tools section" in effective
