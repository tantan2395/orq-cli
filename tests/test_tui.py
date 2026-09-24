"""Tests for the Textual TUI control plane."""

import pytest
from textual.widgets import Label, RichLog, Select, Button, Input
from orchestrator.tui.app import OrchestratorTUI


@pytest.mark.asyncio
async def test_tui_compose_and_mount():
    """Verify that OrchestratorTUI composes and mounts without Label or markup errors."""
    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        # Check status bar labels
        run_info = app.query_one("#status-run-info", Label)
        role_badge = app.query_one("#status-role-badge", Label)
        state_badge = app.query_one("#status-state-badge", Label)

        assert run_info is not None
        assert role_badge is not None
        assert state_badge is not None

        # Check logs and controls
        activity_log = app.query_one("#activity-log", RichLog)
        assert activity_log is not None

        btn_pause = app.query_one("#btn-pause", Button)
        btn_resume = app.query_one("#btn-resume", Button)
        btn_cancel = app.query_one("#btn-cancel", Button)
        assert btn_pause is not None
        assert btn_resume is not None
        assert btn_cancel is not None

        # Check chat controls
        chat_role = app.query_one("#chat-role-select", Select)
        chat_input = app.query_one("#chat-input", Input)
        assert chat_role is not None
        assert chat_input is not None


@pytest.mark.asyncio
async def test_tui_chat_submit():
    """Verify that submitting text in chat sends a human intervention command."""
    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        chat_input = app.query_one("#chat-input", Input)
        chat_input.value = "Focus on auth tests first"
        await chat_input.action_submit()
        await pilot.pause()

        # Input should be cleared after submit
        assert chat_input.value == ""

        # Verify task was saved in DB
        tasks = await app.db.list_tasks(app.workflow_run.run_id)
        human_tasks = [t for t in tasks if t.type == "human_intervention"]
        assert len(human_tasks) >= 1
        assert human_tasks[-1].payload.get("message") == "Focus on auth tests first"
        assert human_tasks[-1].status == "queued"


@pytest.mark.asyncio
async def test_tui_chat_auto_resumes_paused_workflow():
    """Verify that chatting while paused automatically resumes the workflow."""
    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        # Pause the workflow run first
        app.workflow_run.status = "paused"
        await app.db.save_workflow_run(app.workflow_run)

        chat_input = app.query_one("#chat-input", Input)
        chat_input.value = "Here is the answer you requested"
        await chat_input.action_submit()
        await pilot.pause()

        assert app.workflow_run.status == "running"

