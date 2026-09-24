"""Textual TUI for the Agent Orchestration Runtime."""

import asyncio
from pathlib import Path
from typing import Optional
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from orchestrator.commands import HumanInterventionCommand, WorkflowControlCommand
from orchestrator.config import OrchestratorConfig, get_default_config
from orchestrator.db import Database
from orchestrator.dispatcher import Dispatcher
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus, OrchestrationEvent
from orchestrator.mcp.ipc import IPCServer
from orchestrator.models import OrchestrationTask, StageConfig, WorkflowRun


class OrchestratorTUI(App):
    """Interactive Textual TUI human control plane for agent orchestration."""

    CSS = """
    Screen {
        layout: vertical;
        background: #0f172a;
        color: #f8fafc;
    }

    #status-bar {
        height: 3;
        background: #1e293b;
        border-bottom: solid #334155;
        padding: 0 1;
        align-vertical: middle;
    }

    #status-run-info {
        width: 1fr;
    }

    #status-role-badge {
        margin-right: 1;
        padding: 0 1;
        background: #334155;
        border: solid #475569;
    }

    #status-state-badge {
        margin-right: 1;
        padding: 0 1;
        background: #1e3a5f;
        border: solid #0284c7;
    }

    #main-split {
        height: 1fr;
        layout: horizontal;
    }

    #left-pane {
        width: 55%;
        border-right: solid #334155;
        padding: 0 1;
    }

    #right-pane {
        width: 45%;
        padding: 0 1;
    }

    #activity-log {
        height: 1fr;
        background: #090d16;
        border: solid #1e293b;
        padding: 0 1;
    }

    #tabs {
        height: 1fr;
    }

    #chat-container {
        height: 1fr;
    }

    #chat-role-row {
        height: 3;
        margin-bottom: 1;
        align-vertical: middle;
    }

    #chat-role-label {
        margin-right: 1;
        padding-top: 1;
        color: #94a3b8;
    }

    #chat-role-select {
        width: 1fr;
    }

    .chat-box {
        height: 1fr;
        background: #090d16;
        border: solid #1e293b;
        padding: 0 1;
    }

    #chat-input-row {
        height: 3;
        margin-top: 1;
    }

    #chat-input {
        width: 1fr;
    }

    #dag-container {
        padding: 1;
        height: 1fr;
    }

    .stage-card {
        padding: 1;
        margin-bottom: 1;
        border: solid #334155;
        background: #1e293b;
    }

    .stage-active {
        border: solid #38bdf8;
        background: #0369a1;
    }

    .stage-done {
        border: solid #22c55e;
        background: #14532d;
    }

    #tasks-log, #artifacts-log {
        height: 1fr;
        background: #090d16;
        border: solid #1e293b;
        padding: 0 1;
    }

    #control-row {
        height: 3;
        margin-bottom: 1;
    }

    Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Pause / Resume"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("tab", "next_tab", "Switch Tab"),
    ]

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        workspace_root: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ):
        super().__init__()
        self.config = config or get_default_config()
        self.workspace_root = workspace_root or self.config.workspace.workspace_root
        self.workflow_id = workflow_id or self.config.active_workflow

        self.db = Database(self.config.sqlite_db_path)
        self.events = EventBus()
        self.engine = OrchestrationEngine(config=self.config, db=self.db, events=self.events)
        self.ipc_server = IPCServer(
            handler=self.engine.handle_ipc_command,
            socket_path=Path(self.config.ipc_socket_path),
        )
        self.dispatcher = Dispatcher(
            config=self.config,
            session_manager=self.engine.session_manager,
            event_bus=self.events,
        )

        self.workflow_run: Optional[WorkflowRun] = None
        self._loop_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="status-bar"):
            yield Label("Initializing...", id="status-run-info")
            yield Label("Role: None", id="status-role-badge")
            yield Label("State: Idle", id="status-state-badge")

        with Horizontal(id="main-split"):
            # Left pane: Live Activity Log
            with Vertical(id="left-pane"):
                yield Label("[bold cyan]Agent Activity & Live Stream[/bold cyan]")
                yield RichLog(id="activity-log", wrap=True, highlight=True, markup=True)

            # Right pane: Tabs (Chat, DAG, Tasks, Artifacts)
            with Vertical(id="right-pane"):
                with Horizontal(id="control-row"):
                    yield Button("Pause", id="btn-pause", variant="warning")
                    yield Button("Resume", id="btn-resume", variant="success")
                    yield Button("Cancel Run", id="btn-cancel", variant="error")

                with TabbedContent(id="tabs"):
                    with TabPane("Direct Chat", id="tab-chat"):
                        with Vertical(id="chat-container"):
                            with Horizontal(id="chat-role-row"):
                                yield Label("Role:", id="chat-role-label")
                                yield Select(
                                    [(role, role) for role in self.config.roles.keys()],
                                    value="decision_maker",
                                    id="chat-role-select",
                                )
                            yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True, classes="chat-box")
                            with Horizontal(id="chat-input-row"):
                                yield Input(placeholder="Send direct instruction to role...", id="chat-input")
                                yield Button("Send", id="btn-chat-send", variant="primary")

                    with TabPane("Workflow DAG", id="tab-dag"):
                        yield VerticalScroll(id="dag-container")

                    with TabPane("Tasks & Handoffs", id="tab-tasks"):
                        yield RichLog(id="tasks-log", wrap=True, highlight=True, markup=True)

                    with TabPane("Artifacts", id="tab-artifacts"):
                        yield RichLog(id="artifacts-log", wrap=True, highlight=True, markup=True)

        yield Footer()

    async def on_mount(self) -> None:
        """Starts engine, IPC, events listener, and runner loop."""
        await self.engine.initialize()
        await self.ipc_server.start()

        self.events.add_callback(self._on_event)

        # Create or load active workflow run
        self.workflow_run = await self.engine.create_workflow_run(
            workflow_id=self.workflow_id,
            workspace_root=self.workspace_root,
        )
        self._update_header()
        self._render_dag()

        # Start autonomous runner in background
        self._loop_task = asyncio.create_task(self._run_orchestrator_loop())

    async def _on_event(self, event: OrchestrationEvent) -> None:
        """Reactive UI updates on incoming orchestration events."""
        log = self.query_one("#activity-log", RichLog)

        if event.type == "agent_turn_started":
            payload = event.payload
            log.write(f"\n[bold blue]━━━ Turn Started: Role '{escape(str(event.role))}' ({escape(str(payload.get('agent')))}/{escape(str(payload.get('model')))}) ━━━[/bold blue]")
        elif event.type == "agent_activity":
            activity = event.payload
            act_type = activity.get("type")
            if act_type == "status":
                log.write(f"[dim]• {escape(str(activity.get('status', '')))}[/dim]")
            elif act_type == "tool_call":
                log.write(f"[yellow]⚡ Tool Call:[/yellow] [bold]{escape(str(activity.get('name', '')))}[/bold]({escape(str(activity.get('args', '')))})")
            elif act_type == "agent_message":
                log.write(activity.get("content", ""), markup=False)
            elif act_type == "error":
                log.write(f"[bold red]✗ Error:[/bold red] {escape(str(activity.get('error', '')))}")
        elif event.type == "stage_started":
            log.write(f"\n[bold green]▶ Stage Started:[/bold green] [bold]{escape(str(event.payload.get('new_stage', '')))}[/bold]")
            self._update_header()
            self._render_dag()
        elif event.type == "workflow_completed":
            log.write("\n[bold green]✓ Workflow Completed Successfully![/bold green]")
            self._update_header()
            self._render_dag()

        # Refresh tasks & artifacts tabs
        if event.type in ["task_queued", "handoff_created", "review_requested", "review_completed"]:
            await self._refresh_tasks()
        if event.type == "artifact_registered":
            await self._refresh_artifacts()

    def _update_header(self) -> None:
        if not self.workflow_run:
            return
        run_info = self.query_one("#status-run-info", Label)
        role_badge = self.query_one("#status-role-badge", Label)
        state_badge = self.query_one("#status-state-badge", Label)

        definition = self.config.workflows.get(self.workflow_run.definition_id)
        current_role = "None"
        if definition and self.workflow_run.current_stage in definition.stages:
            current_role = definition.stages[self.workflow_run.current_stage].role

        run_info.update(f"Run: [cyan]{escape(self.workflow_run.run_id)}[/cyan] | Stage: [bold]{escape(self.workflow_run.current_stage)}[/bold]")
        role_badge.update(f"Role: [bold yellow]{escape(current_role)}[/bold yellow]")

        status_color = "green" if self.workflow_run.status == "running" else "yellow" if self.workflow_run.status == "paused" else "blue"
        state_badge.update(f"State: [bold {status_color}]{escape(self.workflow_run.status.upper())}[/bold {status_color}]")

    def _render_dag(self) -> None:
        dag = self.query_one("#dag-container", VerticalScroll)
        dag.remove_children()

        if not self.workflow_run:
            return

        definition = self.config.workflows.get(self.workflow_run.definition_id)
        if not definition:
            return

        for name, stage in definition.stages.items():
            classes = "stage-card"
            badge = "○ Pending"
            if name == self.workflow_run.current_stage:
                classes += " stage-active"
                badge = "▶ Active"
            elif self.workflow_run.status == "completed":
                classes += " stage-done"
                badge = "✓ Complete"

            card = Static(
                f"[bold]{escape(name.upper())}[/bold] ({badge})\n"
                f"Role: [yellow]{escape(stage.role)}[/yellow] ({escape(stage.type.value)})\n"
                f"[dim]{escape(stage.description)}[/dim]",
                classes=classes,
            )
            dag.mount(card)

    async def _refresh_tasks(self) -> None:
        tasks_log = self.query_one("#tasks-log", RichLog)
        tasks_log.clear()
        if not self.workflow_run:
            return
        tasks = await self.db.list_tasks(self.workflow_run.run_id)
        for t in tasks:
            status_color = "green" if t.status == "completed" else "yellow" if t.status == "running" else "dim"
            tasks_log.write(
                f"[{status_color}]● {escape(t.status.upper())}[/{status_color}] [bold]{escape(t.type)}[/bold] "
                f"({escape(t.requested_by)} → {escape(t.target_role or 'engine')})\n"
                f"  Task: {escape(str(t.payload.get('task') or t.payload.get('summary') or ''))}\n"
            )

    async def _refresh_artifacts(self) -> None:
        artifacts_log = self.query_one("#artifacts-log", RichLog)
        artifacts_log.clear()
        if not self.workflow_run:
            return
        arts = await self.db.list_artifacts(self.workflow_run.run_id)
        for a in arts:
            artifacts_log.write(f"[bold cyan]{escape(a.type.value.upper())}[/bold cyan]: {escape(a.path)} [dim]({escape(a.description or '')})[/dim]")

    async def _run_orchestrator_loop(self) -> None:
        """Autonomous execution loop coordinating turns in background."""
        while self.workflow_run and self.workflow_run.status == "running":
            definition = self.config.workflows.get(self.workflow_run.definition_id)
            if not definition or self.workflow_run.current_stage not in definition.stages:
                break

            stage_cfg: StageConfig = definition.stages[self.workflow_run.current_stage]
            role_name = stage_cfg.role

            # Find latest task or stage task
            tasks = await self.db.list_tasks(self.workflow_run.run_id)
            pending_task = next(
                (t for t in reversed(tasks) if t.status == "queued" and (t.target_role == role_name or not t.target_role)),
                None,
            )

            if not pending_task:
                pending_task = OrchestrationTask(
                    task_id=f"turn_task_{self.workflow_run.current_stage}",
                    workflow_run_id=self.workflow_run.run_id,
                    stage_id=self.workflow_run.current_stage,
                    type="stage_task",
                    requested_by="engine",
                    target_role=role_name,
                    status="running",
                    payload={"task": stage_cfg.description or f"Execute stage {self.workflow_run.current_stage}"},
                )

            turn_ctx = self.engine.context_manager.create_turn_context(
                workflow_run=self.workflow_run,
                role_config=self.config.roles[role_name],
                task=pending_task,
            )
            effective_prompt = self.engine.context_manager.assemble_effective_prompt(turn_ctx)

            async for _ in self.dispatcher.execute_turn(
                turn_context=turn_ctx,
                effective_prompt=effective_prompt,
                workspace_root=self.workspace_root,
            ):
                if self.workflow_run.status != "running":
                    break

            # Refresh status
            refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
            if refreshed:
                self.workflow_run = refreshed
                self._update_header()
                self._render_dag()

            if self.workflow_run.status == "completed":
                break

            await asyncio.sleep(1.0)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button actions."""
        if not self.workflow_run:
            return

        if event.button.id == "btn-pause":
            await self.action_toggle_pause()
        elif event.button.id == "btn-resume":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="resume"
            ))
            self.workflow_run = await self.db.get_workflow_run(self.workflow_run.run_id)
            self._update_header()
        elif event.button.id == "btn-cancel":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="cancel_stage", target_id=self.workflow_run.current_stage
            ))
            self.workflow_run = await self.db.get_workflow_run(self.workflow_run.run_id)
            self._update_header()
        elif event.button.id == "btn-chat-send":
            chat_input = self.query_one("#chat-input", Input)
            role_select = self.query_one("#chat-role-select", Select)
            text = chat_input.value.strip()
            if text:
                target_role = str(role_select.value)
                chat_log = self.query_one("#chat-log", RichLog)
                chat_log.write(f"[bold cyan]Me → {escape(target_role)}:[/bold cyan] {escape(text)}")
                await self.engine.handle_human_intervention(HumanInterventionCommand(
                    workflow_run_id=self.workflow_run.run_id,
                    target_role=target_role,
                    message=text,
                ))
                chat_input.value = ""

    async def action_toggle_pause(self) -> None:
        """Toggles between paused and running states."""
        if not self.workflow_run:
            return
        if self.workflow_run.status == "running":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="pause", reason="Human intervention from TUI"
            ))
        elif self.workflow_run.status == "paused":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="resume"
            ))
        self.workflow_run = await self.db.get_workflow_run(self.workflow_run.run_id)
        self._update_header()

    async def action_next_tab(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        # Cycle through tabs
        tab_ids = ["tab-chat", "tab-dag", "tab-tasks", "tab-artifacts"]
        current = tabs.active
        idx = (tab_ids.index(current) + 1) % len(tab_ids) if current in tab_ids else 0
        tabs.active = tab_ids[idx]

    async def on_unmount(self) -> None:
        """Clean shutdown of IPC and loop task."""
        if self._loop_task:
            self._loop_task.cancel()
        await self.ipc_server.stop()
