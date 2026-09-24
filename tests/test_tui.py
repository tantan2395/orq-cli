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


@pytest.mark.asyncio
async def test_tui_message_sent_renders_in_chat():
    """Verify that message_sent events from agents render in Direct Chat."""
    from orchestrator.events import create_event

    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        chat_log = app.query_one("#chat-log", RichLog)

        # Publish a message_sent event as emitted by engine.handle_message
        evt = create_event(
            workflow_run_id=app.workflow_run.run_id,
            event_type="message_sent",
            role="decision_maker",
            payload={
                "recipient": "human",
                "message": "Here is the list of Orq MCP tools: agents_handoff, review_request...",
            },
        )
        await app.events.publish(evt)
        app.action_switch_tab_4()
        await pilot.pause()

        # Check that the message was rendered to chat_log
        lines = [line.text for line in chat_log.lines]
        matching = any("Here is the list of Orq MCP tools" in line for line in lines)
        assert matching, f"Expected message in chat_log lines, got: {lines}"


@pytest.mark.asyncio
async def test_tui_defaults_to_awaiting_task():
    """Verify that launching TUI with no initial task enters AWAITING TASK state."""
    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        state_badge = app.query_one("#status-state-badge", Label)
        # Give on_mount a brief moment
        await pilot.pause()
        assert "AWAITING TASK" in str(state_badge.render())

        # Verify welcome instruction in chat log
        app.action_switch_tab_4()
        await pilot.pause()
        chat_log = app.query_one("#chat-log", RichLog)
        lines = [line.text for line in chat_log.lines]
        assert any("Welcome to Orchestrator TUI" in line for line in lines)


@pytest.mark.asyncio
async def test_tui_initial_task_from_cli():
    """Verify that launching TUI with initial_task queues it immediately on mount."""
    app = OrchestratorTUI(initial_task="Audit security configuration")
    async with app.run_test() as pilot:
        await pilot.pause()

        # Check task was queued in DB
        tasks = await app.db.list_tasks(app.workflow_run.run_id)
        init_tasks = [t for t in tasks if t.type == "human_intervention"]
        assert len(init_tasks) >= 1
        assert init_tasks[0].payload.get("task") == "Audit security configuration"
        assert init_tasks[0].target_role == "decision_maker"

        # Check it is logged in chat
        app.action_switch_tab_4()
        await pilot.pause()
        chat_log = app.query_one("#chat-log", RichLog)
        lines = [line.text for line in chat_log.lines]
        assert any("Audit security configuration" in line for line in lines)


@pytest.mark.asyncio
async def test_tui_agent_activity_message_renders_in_chat_and_activity():
    """Verify that agent_activity events of type agent_message render cleanly in chat and activity log without TypeError."""
    from orchestrator.events import create_event

    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        chat_log = app.query_one("#chat-log", RichLog)
        activity_log = app.query_one("#activity-log", RichLog)

        response_text = "Orq currently exposes seven MCP tools: [1] agents_handoff, [2] review_request"
        evt = create_event(
            workflow_run_id=app.workflow_run.run_id,
            event_type="agent_activity",
            role="decision_maker",
            payload={
                "type": "agent_message",
                "content": response_text,
            },
        )
        await app.events.publish(evt)
        app.action_switch_tab_4()
        await pilot.pause()

        # Check chat_log lines
        chat_lines = [line.text for line in chat_log.lines]
        assert any("Orq currently exposes seven MCP tools" in line for line in chat_lines), f"Expected response in chat_log: {chat_lines}"
        assert any("decision_maker:" in line for line in chat_lines)

        # Check activity_log lines
        activity_lines = [line.text for line in activity_log.lines]
        assert any("Orq currently exposes seven MCP tools" in line for line in activity_lines), f"Expected response in activity_log: {activity_lines}"


@pytest.mark.asyncio
async def test_tui_question_modal_submission():
    """Verify that QuestionModal handles selections and write-ins, and submits human intervention."""
    from orchestrator.tui.modals import QuestionModal
    from textual.widgets import Input, Button

    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        # Pause workflow first to test auto-resume
        app.workflow_run.status = "paused"
        await app.db.save_workflow_run(app.workflow_run)

        # Push QuestionModal
        app.action_answer_question()
        await pilot.pause()

        # Check that QuestionModal is the active screen
        assert isinstance(app.screen, QuestionModal)

        # Type in write-in response
        writein = app.screen.query_one("#writein_0", Input)
        writein.value = "Use SQLite for local embedded storage"

        # Submit modal
        await pilot.click("#btn-submit")
        await pilot.pause()

        # Modal should be dismissed
        assert not isinstance(app.screen, QuestionModal)

        # Verify task was saved in DB as human intervention
        tasks = await app.db.list_tasks(app.workflow_run.run_id)
        human_tasks = [t for t in tasks if t.type == "human_intervention"]
        assert len(human_tasks) >= 1
        last_msg = human_tasks[-1].payload.get("message", "")
        assert "Use SQLite for local embedded storage" in last_msg

        # Verify workflow was auto-resumed
        assert app.workflow_run.status == "running"


@pytest.mark.asyncio
async def test_tui_question_asked_event_triggers_modal():
    """Verify that a question_asked event from an agent pushes QuestionModal automatically."""
    from orchestrator.events import create_event
    from orchestrator.tui.modals import QuestionModal

    app = OrchestratorTUI()
    async with app.run_test() as pilot:
        evt = create_event(
            workflow_run_id=app.workflow_run.run_id,
            event_type="question_asked",
            role="decision_maker",
            payload={
                "question": "Which database engine would you prefer?",
                "options": ["PostgreSQL", "SQLite", "DuckDB"],
                "is_multi_select": False,
                "allow_write_in": True,
            },
        )
        await app.events.publish(evt)
        await pilot.pause()

        # Modal should be active
        assert isinstance(app.screen, QuestionModal)
        assert app.screen.question_specs[0]["question"] == "Which database engine would you prefer?"

        # Dismiss modal with skip
        await pilot.click("#btn-skip")
        await pilot.pause()
        assert not isinstance(app.screen, QuestionModal)



