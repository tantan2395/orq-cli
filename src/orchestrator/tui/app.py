"""Textual TUI for the Agent Orchestration Runtime."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
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

from orchestrator.commands import (
    HumanInterventionCommand,
    TaskCreateCommand,
    TaskLifecycleCommand,
    WorkflowControlCommand,
)
from orchestrator.config import OrchestratorConfig, get_default_config
from orchestrator.db import Database
from orchestrator.dispatcher import Dispatcher
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus, OrchestrationEvent
from orchestrator.mcp.ipc import IPCServer
from orchestrator.models import OrchestrationTask, Project, StageConfig, WorkflowRun, utc_now_iso
from orchestrator.tui.kanban import KanbanBoard, TaskCard
from orchestrator.tui.modals import NewTaskModal, ProjectPickerModal, QuestionModal, TaskDetailModal


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

    #status-project-info {
        margin-right: 2;
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

    #control-row {
        height: 3;
        padding: 0 1;
        margin-top: 1;
        margin-bottom: 1;
        align-vertical: middle;
    }

    #control-row Button {
        margin-right: 1;
    }

    #tabs {
        height: 1fr;
    }

    #kanban-board {
        height: 1fr;
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

    #agents-container {
        padding: 1;
        height: 1fr;
    }

    .agent-card {
        padding: 1;
        margin-bottom: 1;
        border: solid #334155;
        background: #1e293b;
    }

    #activity-split {
        height: 1fr;
        layout: horizontal;
    }

    #activity-log-pane {
        width: 55%;
        height: 1fr;
        border-right: solid #334155;
        padding: 0 1;
    }

    #activity-log {
        height: 1fr;
        background: #090d16;
        border: solid #1e293b;
        padding: 0 1;
    }

    #chat-pane {
        width: 45%;
        height: 1fr;
        padding: 0 1;
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

    Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Pause / Resume"),
        Binding("n", "new_task", "New Task"),
        Binding("a", "answer_question", "Answer"),
        Binding("g", "grill_me", "Grill Me"),
        Binding("p", "open_project_picker", "Switch Project"),
        Binding("1", "switch_tab_1", "Kanban"),
        Binding("2", "switch_tab_2", "Workflow"),
        Binding("3", "switch_tab_3", "Agents"),
        Binding("4", "switch_tab_4", "Activity"),
        Binding("tab", "next_tab", "Switch Tab"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        workspace_root: Optional[str] = None,
        workflow_id: Optional[str] = None,
        initial_task: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        super().__init__()
        self.config = config or get_default_config()
        self.workspace_root = workspace_root or self.config.workspace.workspace_root
        self.workflow_id = workflow_id or self.config.active_workflow
        self.initial_task = initial_task
        self.project_id = project_id

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

        self.current_project: Optional[Project] = None
        self.workflow_run: Optional[WorkflowRun] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._pending_question: Optional[Dict[str, Any]] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="status-bar"):
            yield Label("Project: Default", id="status-project-info")
            yield Label("Initializing...", id="status-run-info")
            yield Label("Role: None", id="status-role-badge")
            yield Label("State: Idle", id="status-state-badge")

        with Horizontal(id="control-row"):
            yield Button("Pause", id="btn-pause", variant="warning")
            yield Button("Resume", id="btn-resume", variant="success")
            yield Button("New Task [n]", id="btn-new-task", variant="primary")
            yield Button("Answer [a]", id="btn-answer", variant="warning")
            yield Button("Grill Me [g]", id="btn-grill-me")
            yield Button("Projects [p]", id="btn-projects")
            yield Button("Cancel Run", id="btn-cancel", variant="error")

        with TabbedContent(id="tabs"):
            with TabPane("[1] Kanban", id="tab-kanban"):
                yield KanbanBoard(id="kanban-board")

            with TabPane("[2] Workflow", id="tab-workflow"):
                yield VerticalScroll(id="dag-container")

            with TabPane("[3] Agents", id="tab-agents"):
                yield VerticalScroll(id="agents-container")

            with TabPane("[4] Activity", id="tab-activity"):
                with Horizontal(id="activity-split"):
                    with Vertical(id="activity-log-pane"):
                        yield Label("[bold cyan]Agent Activity & Live Stream[/bold cyan]")
                        yield RichLog(id="activity-log", wrap=True, highlight=True, markup=True)

                    with Vertical(id="chat-pane"):
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

        yield Footer()

    async def on_mount(self) -> None:
        """Starts engine, IPC, events listener, and runner loop."""
        await self.engine.initialize()
        await self.ipc_server.start()

        self.events.add_callback(self._on_event)

        # Resolve Project
        if self.project_id:
            self.current_project = await self.db.get_project(self.project_id)
        else:
            self.current_project = await self.engine.resolve_active_project(self.workspace_root)

        # Create or load active workflow run
        self.workflow_run = await self.engine.create_workflow_run(
            workflow_id=self.workflow_id,
            workspace_root=self.workspace_root,
            project_id=self.current_project.id if self.current_project else None,
        )

        chat_log = self.query_one("#chat-log", RichLog)
        activity_log = self.query_one("#activity-log", RichLog)

        if self.initial_task and self.initial_task.strip():
            definition = self.config.workflows.get(self.workflow_run.definition_id)
            initial_role = "decision_maker"
            if definition and self.workflow_run.current_stage in definition.stages:
                initial_role = definition.stages[self.workflow_run.current_stage].role

            task = OrchestrationTask(
                task_id=f"human_init_{uuid.uuid4().hex[:8]}",
                workflow_run_id=self.workflow_run.run_id,
                stage_id=self.workflow_run.current_stage,
                title=self.initial_task.strip()[:60],
                description=self.initial_task.strip(),
                type="human_intervention",
                requested_by="human",
                target_role=initial_role,
                status="queued",
                kanban_column="ready",
                payload={"task": self.initial_task.strip()},
            )
            await self.db.save_task(task)
            chat_log.write(f"[bold cyan]Human (CLI):[/bold cyan] {escape(self.initial_task.strip())}")
            activity_log.write(f"[bold cyan]System:[/bold cyan] Initial task queued for [bold yellow]{escape(initial_role)}[/bold yellow]: {escape(self.initial_task.strip())}")
            self._update_header()
        else:
            chat_log.write("[bold cyan]System:[/bold cyan] Welcome to [bold]Orchestrator TUI[/bold].")
            chat_log.write("[dim]Workflow initialized. Type your task or question below in Direct Chat, or switch roles to converse directly.[/dim]\n")
            activity_log.write("[bold cyan]System:[/bold cyan] Ready & awaiting human task. Send a task via Direct Chat, press [bold white]'n'[/bold white] for New Task, or launch with: [bold white]orq tui \"<task>\"[/bold white].")
            self._update_header(state_override="AWAITING TASK")

        self._render_dag()
        self._render_agents()
        await self._refresh_kanban()

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
                tool_name = str(activity.get("name", ""))
                args = activity.get("args") or {}
                if isinstance(args, dict):
                    preview_items = []
                    for k in ["title", "target_role", "task", "reason", "decision", "summary", "workflow_run_id"]:
                        if k in args:
                            val = str(args[k]).replace("\n", " ").strip()
                            if len(val) > 40:
                                val = val[:37] + "..."
                            preview_items.append(f"{k}={repr(val)}")
                    if not preview_items:
                        for k, v in list(args.items())[:2]:
                            val = str(v).replace("\n", " ").strip()
                            if len(val) > 30:
                                val = val[:27] + "..."
                            preview_items.append(f"{k}={repr(val)}")
                    args_summary = ", ".join(preview_items)
                else:
                    args_summary = str(args)[:60].replace("\n", " ")
                log.write(f"[yellow]⚡ Tool Call:[/yellow] [bold]{escape(tool_name)}[/bold]({escape(args_summary)})")
            elif act_type == "tool_result":
                out_str = str(activity.get("output", "")).strip().replace("\n", " ")
                if out_str:
                    if len(out_str) > 120:
                        out_str = out_str[:117] + "..."
                    log.write(f"[dim yellow]↳ Output:[/dim yellow] {escape(out_str)}")
            elif act_type == "agent_message":
                msg_content = str(activity.get("content", ""))
                log.write(Text(msg_content))
                if event.role:
                    chat_log = self.query_one("#chat-log", RichLog)
                    prefix = Text.from_markup(f"[bold green]{escape(str(event.role))}:[/bold green] ")
                    prefix.append(msg_content)
                    chat_log.write(prefix)
                    chat_log.scroll_end(animate=False)
            elif act_type == "error":
                log.write(f"[bold red]✗ Error:[/bold red] {escape(str(activity.get('error', '')))}")
        elif event.type == "stage_started":
            log.write(f"\n[bold green]▶ Stage Started:[/bold green] [bold]{escape(str(event.payload.get('new_stage', '')))}[/bold]")
            self._update_header()
            self._render_dag()
            self._render_agents()
        elif event.type == "workflow_completed":
            log.write("\n[bold green]✓ Workflow Completed Successfully![/bold green]")
            self._update_header()
            self._render_dag()
            self._render_agents()
        elif event.type == "workflow_paused":
            reason = event.payload.get("reason") or "Workflow paused by agent or operator"
            log.write(f"\n[bold yellow]⏸ Workflow Paused:[/bold yellow] {escape(reason)}")
            chat_log = self.query_one("#chat-log", RichLog)
            pause_prefix = Text.from_markup(f"[bold yellow]⏸ {escape(str(event.role or 'Agent'))}:[/bold yellow] ")
            pause_prefix.append(reason)
            chat_log.write(pause_prefix)
            chat_log.scroll_end(animate=False)
            refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
            if refreshed:
                self.workflow_run = refreshed
            self._update_header()
        elif event.type == "workflow_resumed":
            log.write("\n[bold green]▶ Workflow Resumed[/bold green]")
            refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
            if refreshed:
                self.workflow_run = refreshed
            self._update_header()
        elif event.type in ["agent_message_sent", "message_sent"]:
            payload = event.payload or {}
            sender = event.role or "Agent"
            recipient = payload.get("recipient") or payload.get("target") or "human"
            message_text = payload.get("message") or payload.get("content") or ""
            chat_log = self.query_one("#chat-log", RichLog)
            if str(recipient).lower() == "human":
                prefix = Text.from_markup(f"[bold green]{escape(str(sender))}:[/bold green] ")
                prefix.append(str(message_text))
                chat_log.write(prefix)
            else:
                prefix = Text.from_markup(f"[bold green]{escape(str(sender))} → {escape(str(recipient))}:[/bold green] ")
                prefix.append(str(message_text))
                chat_log.write(prefix)
            chat_log.scroll_end(animate=False)

        elif event.type in ["interactive_question", "question_asked"]:
            payload = event.payload or {}
            self._pending_question = payload
            q_text = payload.get("question", "")
            q_role = payload.get("sender_role") or payload.get("role") or event.role or "decision_maker"
            options = payload.get("options", [])
            log.write(f"\n[bold magenta]❓ Interactive Question from {escape(q_role)}:[/bold magenta] [bold white]{escape(q_text)}[/bold white]")
            if options:
                opts_str = " | ".join(f"[{i+1}] {escape(opt)}" for i, opt in enumerate(options))
                log.write(f"[dim yellow]Choices: {opts_str}[/dim yellow]")

            chat_log = self.query_one("#chat-log", RichLog)
            q_prefix = Text.from_markup(f"[bold magenta]❓ {escape(q_role)} asks:[/bold magenta] ")
            q_prefix.append(q_text)
            if options:
                q_prefix.append(f"\n   Choices: {', '.join(options)}")
            chat_log.write(q_prefix)
            chat_log.scroll_end(animate=False)

            self.action_answer_question()

        # Refresh Kanban board whenever a task or review event occurs
        if event.type in [
            "task_created",
            "task_started",
            "task_completed",
            "task_ready",
            "task_blocked",
            "task_cancelled",
            "task_updated",
            "task_queued",
            "handoff_created",
            "review_requested",
            "review_completed",
        ]:
            await self._refresh_kanban()

    def _get_current_role(self) -> str:
        if not self.workflow_run:
            return "None"
        definition = self.config.workflows.get(self.workflow_run.definition_id)
        if definition and self.workflow_run.current_stage in definition.stages:
            return definition.stages[self.workflow_run.current_stage].role
        return "None"

    def _update_header(self, state_override: Optional[str] = None) -> None:
        if not self.workflow_run:
            return
        proj_info = self.query_one("#status-project-info", Label)
        run_info = self.query_one("#status-run-info", Label)
        role_badge = self.query_one("#status-role-badge", Label)
        state_badge = self.query_one("#status-state-badge", Label)

        proj_name = self.current_project.name if self.current_project else "Default"
        proj_info.update(f"Project: [bold magenta]{escape(proj_name)}[/bold magenta]")

        current_role = self._get_current_role()
        run_info.update(f"Run: [cyan]{escape(self.workflow_run.run_id)}[/cyan] | Stage: [bold]{escape(self.workflow_run.current_stage)}[/bold]")
        role_badge.update(f"Role: [bold yellow]{escape(current_role)}[/bold yellow]")

        current_state = state_override or self.workflow_run.status
        if current_state == "AWAITING TASK":
            status_color = "cyan"
        elif current_state == "running":
            status_color = "green"
        elif current_state == "paused":
            status_color = "yellow"
        else:
            status_color = "blue"
        state_badge.update(f"State: [bold {status_color}]{escape(current_state.upper())}[/bold {status_color}]")

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

    def _render_agents(self) -> None:
        container = self.query_one("#agents-container", VerticalScroll)
        container.remove_children()

        current_role = self._get_current_role()
        for role_name, role_cfg in self.config.roles.items():
            agent_name = role_cfg.agent
            profile_name = getattr(role_cfg, "profile", getattr(role_cfg, "model_profile", ""))
            profile = self.config.model_profiles.get(profile_name)
            model_info = profile.model if profile else "default"
            reasoning = profile.reasoning.value if profile and hasattr(profile.reasoning, "value") else str(getattr(profile, "reasoning", "none"))

            active_session = self.engine.session_manager.get_session_id(role_name) or "none"
            is_active = (self.workflow_run and self.workflow_run.status == "running" and role_name == current_role)

            status_badge = "[bold green]● ACTIVE[/bold green]" if is_active else "[dim]IDLE[/dim]"

            card = Static(
                f"[bold cyan]Role: {escape(role_name)}[/bold cyan] ({status_badge})\n"
                f"  • [bold]Agent Provider:[/bold] [yellow]{escape(agent_name)}[/yellow]\n"
                f"  • [bold]Model Profile:[/bold]  [white]{escape(profile_name)}[/white] ([dim]{escape(model_info)}, reasoning: {escape(reasoning)}[/dim])\n"
                f"  • [bold]Active Session:[/bold] [dim]{escape(active_session)}[/dim]",
                classes="agent-card",
            )
            container.mount(card)

    async def _refresh_kanban(self) -> None:
        if not self.workflow_run:
            return
        tasks = await self.db.list_tasks(self.workflow_run.run_id)
        try:
            board = self.query_one("#kanban-board", KanbanBoard)
            board.refresh_board(tasks)
        except Exception:
            pass

    async def on_task_card_selected(self, message: TaskCard.Selected) -> None:
        """Opens TaskDetailModal when a card is clicked or selected."""
        deps = await self.db.get_task_dependencies(message.task.task_id)
        dependents = await self.db.get_task_dependents(message.task.task_id)

        def on_detail_action(res: Optional[Dict[str, Any]]) -> None:
            if not res:
                return
            act = res.get("action")
            if act == "chat":
                tabs = self.query_one("#tabs", TabbedContent)
                tabs.active = "tab-activity"
                role_select = self.query_one("#chat-role-select", Select)
                target_role = res.get("role")
                if target_role and target_role in self.config.roles:
                    role_select.value = target_role
                self.query_one("#chat-input", Input).focus()
            elif act in ["start", "block", "cancel"]:
                self.run_worker(self._execute_task_lifecycle(res["task_id"], act))

        self.push_screen(TaskDetailModal(task=message.task, dependencies=deps, dependents=dependents), on_detail_action)

    async def _execute_task_lifecycle(self, task_id: str, action: str) -> None:
        if not self.workflow_run:
            return
        cmd = TaskLifecycleCommand(
            workflow_run_id=self.workflow_run.run_id,
            task_id=task_id,
            action=action,
            reason="Triggered by operator via Task Details",
        )
        res = await self.engine.handle_task_lifecycle(cmd)
        await self._refresh_kanban()
        log = self.query_one("#activity-log", RichLog)
        if res.get("accepted"):
            log.write(f"[bold green]✓ Task {task_id}:[/bold green] Action '{action}' accepted.")
        else:
            log.write(f"[bold red]✗ Task {task_id}:[/bold red] Action '{action}' rejected: {escape(str(res.get('reason')))}")

    def _ensure_runner_loop(self) -> None:
        """Ensures the background orchestrator execution loop is running."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run_orchestrator_loop())

    async def _run_orchestrator_loop(self) -> None:
        """Autonomous execution loop coordinating turns in background."""
        while self.workflow_run and self.workflow_run.status not in ["completed", "failed", "cancelled"]:
            if self.workflow_run.status == "paused":
                await asyncio.sleep(0.5)
                refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
                if refreshed:
                    self.workflow_run = refreshed
                continue

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

            # Check for direct human interventions queued for any role
            if not pending_task:
                human_task = next((t for t in reversed(tasks) if t.status == "queued" and t.type == "human_intervention"), None)
                if human_task and human_task.target_role in self.config.roles:
                    pending_task = human_task
                    role_name = human_task.target_role

            if not pending_task:
                # In interactive TUI mode, wait for user input or task queueing instead of fabricating unprompted turns
                self._update_header(state_override="AWAITING TASK")
                await asyncio.sleep(0.5)
                refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
                if refreshed:
                    self.workflow_run = refreshed
                continue

            self._update_header()
            self._render_agents()

            if pending_task.task_id and not pending_task.task_id.startswith("turn_task_"):
                pending_task.status = "running"
                if pending_task.kanban_column in ["ready", "backlog"]:
                    pending_task.kanban_column = "in_progress"
                pending_task.started_at = utc_now_iso()
                await self.db.save_task(pending_task)
                await self._refresh_kanban()

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

            # Mark task completed in DB if it was a stored task
            if pending_task and pending_task.task_id and not pending_task.task_id.startswith("turn_task_"):
                pending_task.status = "completed"
                pending_task.kanban_column = "done"
                pending_task.completed_at = utc_now_iso()
                await self.db.save_task(pending_task)
                await self._refresh_kanban()

            # Refresh status
            refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
            if refreshed:
                self.workflow_run = refreshed
                self._update_header()
                self._render_dag()
                self._render_agents()

            if self.workflow_run.status in ["completed", "failed", "cancelled"]:
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
            self._ensure_runner_loop()
        elif event.button.id == "btn-new-task":
            self.action_new_task()
        elif event.button.id == "btn-answer":
            self.action_answer_question()
        elif event.button.id == "btn-grill-me":
            self.run_worker(self.action_grill_me())
        elif event.button.id == "btn-projects":
            self.action_open_project_picker()
        elif event.button.id == "btn-cancel":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="cancel_stage", target_id=self.workflow_run.current_stage
            ))
            self.workflow_run = await self.db.get_workflow_run(self.workflow_run.run_id)
            self._update_header()
        elif event.button.id == "btn-chat-send":
            await self._send_chat_message()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key pressed in input fields."""
        if event.input.id == "chat-input":
            await self._send_chat_message()

    async def _send_chat_message(self) -> None:
        if not self.workflow_run:
            return
        chat_input = self.query_one("#chat-input", Input)
        role_select = self.query_one("#chat-role-select", Select)
        text = chat_input.value.strip()
        if text:
            target_role = str(role_select.value)
            chat_log = self.query_one("#chat-log", RichLog)
            me_prefix = Text.from_markup(f"[bold cyan]Me → {escape(target_role)}:[/bold cyan] ")
            me_prefix.append(text)
            chat_log.write(me_prefix)
            chat_log.scroll_end(animate=False)
            await self.engine.handle_human_intervention(HumanInterventionCommand(
                workflow_run_id=self.workflow_run.run_id,
                target_role=target_role,
                message=text,
            ))
            # If paused, auto-resume so the agent wakes up to respond to the human instruction
            if self.workflow_run.status == "paused":
                await self.engine.handle_control(WorkflowControlCommand(
                    workflow_run_id=self.workflow_run.run_id, action="resume"
                ))
            refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
            if refreshed:
                self.workflow_run = refreshed
            self._update_header()
            self._ensure_runner_loop()
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
        self._ensure_runner_loop()

    def action_new_task(self) -> None:
        """Opens the New Task modal dialog."""
        def on_new_task_submit(result: Optional[Dict[str, Any]]) -> None:
            if result:
                self.run_worker(self._create_task_from_modal(result))

        roles = list(self.config.roles.keys())
        self.push_screen(NewTaskModal(roles=roles), on_new_task_submit)

    async def _create_task_from_modal(self, data: Dict[str, Any]) -> None:
        if not self.workflow_run:
            return
        cmd = TaskCreateCommand(
            workflow_run_id=self.workflow_run.run_id,
            title=data["title"],
            description=data.get("description", ""),
            target_role=data.get("target_role", "developer"),
            kanban_column=data.get("kanban_column", "ready"),
            requested_by="human",
        )
        await self.engine.handle_task_create(cmd)
        await self._refresh_kanban()
        log = self.query_one("#activity-log", RichLog)
        log.write(f"[bold green]✓ Created Task:[/bold green] {escape(data['title'])} (Role: {escape(str(data.get('target_role')))}, Column: {escape(str(data.get('kanban_column')))})")

    def action_open_project_picker(self) -> None:
        """Opens the Project Picker modal dialog."""
        async def load_and_show() -> None:
            projects = await self.db.list_projects(include_archived=False)
            active_id = self.current_project.id if self.current_project else None

            def on_project_selected(selected_id: Optional[str]) -> None:
                if selected_id:
                    self.run_worker(self._switch_project(selected_id))

            self.push_screen(ProjectPickerModal(projects=projects, active_project_id=active_id), on_project_selected)

        self.run_worker(load_and_show())

    async def _switch_project(self, project_id: str) -> None:
        proj = await self.db.get_project(project_id)
        if proj:
            self.current_project = proj
            await self.db.set_active_project(proj.id)
            self._update_header()
            log = self.query_one("#activity-log", RichLog)
            log.write(f"[bold cyan]Switched active project to:[/bold cyan] {escape(proj.name)} ({escape(proj.id)})")

    def action_answer_question(self) -> None:
        """Pops up the interactive question modal for the active or pending question."""
        if not self._pending_question:
            # If no pending question from agent, provide a quick direction modal
            q_data = {
                "question": "Provide direction or response to the active agent:",
                "options": [
                    "Proceed with recommended plan",
                    "Pause workflow for manual code inspection",
                    "Decompose into smaller Kanban tasks first",
                ],
                "is_multi_select": False,
                "allow_write_in": True,
                "sender_role": self._get_current_role(),
            }
        else:
            q_data = self._pending_question

        def _on_modal_close(result: Optional[Dict[str, Any]]) -> None:
            self.run_worker(self._handle_question_modal_result(result))

        self.push_screen(
            QuestionModal(
                question=q_data.get("question"),
                options=q_data.get("options"),
                is_multi_select=bool(q_data.get("is_multi_select", False)),
                allow_write_in=bool(q_data.get("allow_write_in", True)),
                questions=q_data.get("questions"),
                sender_role=q_data.get("sender_role") or q_data.get("role") or "decision_maker",
            ),
            callback=_on_modal_close,
        )

    async def action_grill_me(self) -> None:
        """Triggers an interactive requirements & architecture interview with the decision maker."""
        if not self.workflow_run:
            return
        target_role = "decision_maker"
        instruction = (
            "Please grill/interview me (the human operator) on the project requirements, architecture choices, "
            "and constraints. Ask clarifying questions one at a time with structured choices using the `ask_question` tool."
        )
        chat_log = self.query_one("#chat-log", RichLog)
        prefix = Text.from_markup(f"[bold cyan]Me → {escape(target_role)} [Grill Me]:[/bold cyan] ")
        prefix.append(instruction)
        chat_log.write(prefix)
        chat_log.scroll_end(animate=False)

        activity_log = self.query_one("#activity-log", RichLog)
        activity_log.write(f"[bold cyan]Human initiated Grill Me interview with {escape(target_role)}.[/bold cyan]")

        await self.engine.handle_human_intervention(HumanInterventionCommand(
            workflow_run_id=self.workflow_run.run_id,
            target_role=target_role,
            message=instruction,
        ))
        if self.workflow_run.status == "paused":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="resume"
            ))
        refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
        if refreshed:
            self.workflow_run = refreshed
        self._update_header()
        self._ensure_runner_loop()

    async def _handle_question_modal_result(self, result: Optional[Dict[str, Any]]) -> None:
        if not result or not self.workflow_run:
            return

        target_role = result.get("sender_role") or "decision_maker"
        answers = result.get("answers", [])
        overall_selected = result.get("selected", [])
        write_in = result.get("write_in", "")

        # Clear pending question
        self._pending_question = None

        # Build formatted human response text
        if len(answers) > 1:
            lines = ["[HUMAN OPERATOR ANSWERS]"]
            for idx, a in enumerate(answers):
                lines.append(f"{idx+1}. Question: {a.get('question')}")
                if a.get("selected"):
                    lines.append(f"   Selected: {', '.join(a['selected'])}")
                if a.get("write_in"):
                    lines.append(f"   Notes: {a['write_in']}")
        else:
            q_title = result.get("question") or (answers[0].get("question") if answers else "Question")
            lines = [f"[ANSWER TO QUESTION: {q_title}]"]
            if overall_selected:
                if len(overall_selected) == 1:
                    lines.append(f"Selected: {overall_selected[0]}")
                else:
                    lines.append(f"Selected: {', '.join(overall_selected)}")
            if write_in:
                lines.append(f"Additional notes / Write-in: {write_in}")

        answer_text = "\n".join(lines)

        summary_parts = []
        if overall_selected:
            summary_parts.append(", ".join(overall_selected))
        if write_in:
            summary_parts.append(f"Notes: {write_in}")
        summary_short = " | ".join(summary_parts) if summary_parts else "Acknowledged"

        # Log to Direct Chat and Activity Log
        chat_log = self.query_one("#chat-log", RichLog)
        me_prefix = Text.from_markup(f"[bold cyan]Me → {escape(target_role)} [Answer]:[/bold cyan] ")
        me_prefix.append(summary_short)
        chat_log.write(me_prefix)
        chat_log.scroll_end(animate=False)

        activity_log = self.query_one("#activity-log", RichLog)
        activity_log.write(f"[bold green]✓ Answer Submitted to {escape(target_role)}:[/bold green] {escape(summary_short)}")

        # Dispatch human intervention to engine
        await self.engine.handle_human_intervention(HumanInterventionCommand(
            workflow_run_id=self.workflow_run.run_id,
            target_role=target_role,
            message=answer_text,
        ))

        # Auto-resume if workflow was paused
        if self.workflow_run.status == "paused":
            await self.engine.handle_control(WorkflowControlCommand(
                workflow_run_id=self.workflow_run.run_id, action="resume"
            ))

        refreshed = await self.db.get_workflow_run(self.workflow_run.run_id)
        if refreshed:
            self.workflow_run = refreshed
        self._update_header()
        self._ensure_runner_loop()

    def action_switch_tab_1(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-kanban"

    def action_switch_tab_2(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-workflow"

    def action_switch_tab_3(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-agents"

    def action_switch_tab_4(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-activity"

    def action_next_tab(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        tab_ids = ["tab-kanban", "tab-workflow", "tab-agents", "tab-activity"]
        current = tabs.active
        idx = (tab_ids.index(current) + 1) % len(tab_ids) if current in tab_ids else 0
        tabs.active = tab_ids[idx]

    async def on_unmount(self) -> None:
        """Clean shutdown of IPC and loop task."""
        if self._loop_task:
            self._loop_task.cancel()
        await self.ipc_server.stop()
