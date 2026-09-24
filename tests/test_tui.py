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
