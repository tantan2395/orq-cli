"""Modal dialog screens for task inspection, creation, and project selection."""

from typing import Any, Dict, List, Optional
from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet, Select, Static

from orchestrator.models import OrchestrationTask, Project


class TaskDetailModal(ModalScreen[Optional[Dict[str, Any]]]):
    """Detailed inspection modal for an OrchestrationTask."""

    DEFAULT_CSS = """
    TaskDetailModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #detail-container {
        width: 80;
        height: auto;
        max-height: 85%;
        background: #0f172a;
        border: solid #38bdf8;
        padding: 1 2;
    }
    #detail-header {
        text-style: bold;
        color: #38bdf8;
        margin-bottom: 1;
        border-bottom: solid #334155;
        padding-bottom: 1;
    }
    .detail-section {
        margin-bottom: 1;
    }
    .detail-label {
        color: #94a3b8;
        text-style: bold;
    }
    #detail-actions {
        margin-top: 1;
        height: 3;
        align-horizontal: right;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        task: OrchestrationTask,
        dependencies: Optional[List[str]] = None,
        dependents: Optional[List[str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.task = task
        self.dependencies = dependencies or []
        self.dependents = dependents or []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="detail-container"):
            title = self.task.title or self.task.task_id
            yield Label(f"[bold cyan]Task Details:[/bold cyan] [bold white]{escape(title)}[/bold white]", id="detail-header")

            # Meta Row
            meta_text = (
                f"[bold]ID:[/bold] [dim]{self.task.task_id}[/dim]  |  "
                f"[bold]Role:[/bold] [yellow]{self.task.target_role}[/yellow]  |  "
                f"[bold]Column:[/bold] [cyan]{self.task.kanban_column.upper()}[/cyan]  |  "
                f"[bold]Status:[/bold] [green]{self.task.status}[/green]"
            )
            yield Static(meta_text, classes="detail-section")

            # Objective / Scope
            yield Label("Objective / Description:", classes="detail-label")
            desc = self.task.description or "[dim]No detailed objective specified.[/dim]"
            yield Static(f"[white]{escape(desc)}[/white]", classes="detail-section")

            # Dependencies
            yield Label("Prerequisite Dependencies:", classes="detail-label")
            if self.dependencies:
                dep_text = "\n".join(f"  • [dim]{escape(dep)}[/dim]" for dep in self.dependencies)
            else:
                dep_text = "  [dim]None (Independent task)[/dim]"
            yield Static(dep_text, classes="detail-section")

            # Dependents
            yield Label("Downstream Dependents:", classes="detail-label")
            if self.dependents:
                down_text = "\n".join(f"  • [dim]{escape(dep)}[/dim]" for dep in self.dependents)
            else:
                down_text = "  [dim]None[/dim]"
            yield Static(down_text, classes="detail-section")

            # Acceptance Criteria
            ac = self.task.payload.get("acceptance_criteria") if isinstance(self.task.payload, dict) else None
            if ac and isinstance(ac, list) and len(ac) > 0:
                yield Label("Acceptance Criteria:", classes="detail-label")
                ac_text = "\n".join(f"  ✓ {escape(str(item))}" for item in ac)
                yield Static(ac_text, classes="detail-section")

            # Artifacts
            artifacts = self.task.payload.get("artifacts") if isinstance(self.task.payload, dict) else None
            if artifacts and isinstance(artifacts, list) and len(artifacts) > 0:
                yield Label("Artifacts:", classes="detail-label")
                art_text = "\n".join(f"  • {escape(str(item))}" for item in artifacts)
                yield Static(art_text, classes="detail-section")

            # Action Buttons
            with Horizontal(id="detail-actions"):
                yield Button("Chat", id="btn-modal-chat", variant="primary")
                if self.task.kanban_column not in ["in_progress", "done"]:
                    yield Button("Start", id="btn-modal-start", variant="success")
                if self.task.kanban_column != "blocked":
                    yield Button("Block", id="btn-modal-block", variant="warning")
                if self.task.status not in ["completed", "cancelled"]:
                    yield Button("Cancel", id="btn-modal-cancel", variant="error")
                yield Button("Close", id="btn-modal-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-modal-chat":
            self.dismiss({"action": "chat", "role": self.task.target_role, "task_id": self.task.task_id})
        elif button_id == "btn-modal-start":
            self.dismiss({"action": "start", "task_id": self.task.task_id})
        elif button_id == "btn-modal-block":
            self.dismiss({"action": "block", "task_id": self.task.task_id})
        elif button_id == "btn-modal-cancel":
            self.dismiss({"action": "cancel", "task_id": self.task.task_id})
        else:
            self.dismiss(None)


class NewTaskModal(ModalScreen[Optional[Dict[str, Any]]]):
    """Modal dialog for creating a new task from the TUI."""

    DEFAULT_CSS = """
    NewTaskModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #new-task-container {
        width: 70;
        height: auto;
        background: #0f172a;
        border: solid #22c55e;
        padding: 1 2;
    }
    #new-task-header {
        text-style: bold;
        color: #22c55e;
        margin-bottom: 1;
        border-bottom: solid #334155;
        padding-bottom: 1;
    }
    .form-row {
        margin-bottom: 1;
    }
    .form-label {
        color: #94a3b8;
        margin-bottom: 0;
    }
    #new-task-actions {
        margin-top: 1;
        height: 3;
        align-horizontal: right;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(self, roles: List[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.roles = roles or ["developer", "decision_maker", "code_reviewer", "tester"]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-task-container"):
            yield Label("[bold green]+ Create New Task[/bold green]", id="new-task-header")

            yield Label("Task Title:", classes="form-label")
            yield Input(placeholder="e.g. Implement offline synchronization for inventory", id="input-task-title", classes="form-row")

            yield Label("Objective / Description:", classes="form-label")
            yield Input(placeholder="Detailed acceptance criteria or implementation plan...", id="input-task-desc", classes="form-row")

            yield Label("Target Role:", classes="form-label")
            default_role = "developer" if "developer" in self.roles else self.roles[0]
            yield Select([(r, r) for r in self.roles], value=default_role, id="select-task-role", classes="form-row")

            yield Label("Initial Kanban Column:", classes="form-label")
            yield Select([("READY", "ready"), ("BACKLOG", "backlog")], value="ready", id="select-task-col", classes="form-row")

            with Horizontal(id="new-task-actions"):
                yield Button("Cancel", id="btn-cancel-task")
                yield Button("Create Task", id="btn-submit-task", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit-task":
            title_input = self.query_one("#input-task-title", Input)
            desc_input = self.query_one("#input-task-desc", Input)
            role_select = self.query_one("#select-task-role", Select)
            col_select = self.query_one("#select-task-col", Select)

            title = title_input.value.strip()
            if not title:
                title_input.placeholder = "Title cannot be empty!"
                return

            self.dismiss({
                "title": title,
                "description": desc_input.value.strip(),
                "target_role": str(role_select.value),
                "kanban_column": str(col_select.value),
            })
        else:
            self.dismiss(None)


class ProjectPickerModal(ModalScreen[Optional[str]]):
    """Modal dialog to inspect and switch active projects."""

    DEFAULT_CSS = """
    ProjectPickerModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #project-container {
        width: 75;
        height: auto;
        max-height: 80%;
        background: #0f172a;
        border: solid #a855f7;
        padding: 1 2;
    }
    #project-header {
        text-style: bold;
        color: #a855f7;
        margin-bottom: 1;
        border-bottom: solid #334155;
        padding-bottom: 1;
    }
    #project-actions {
        margin-top: 1;
        height: 3;
        align-horizontal: right;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(self, projects: List[Project], active_project_id: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.projects = projects
        self.active_project_id = active_project_id

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="project-container"):
            yield Label("[bold magenta]Select Orchestrated Project[/bold magenta]", id="project-header")

            if self.projects:
                options = [(f"{p.name} ({p.id})", p.id) for p in self.projects if p.status == "active"]
                default_val = self.active_project_id if any(p.id == self.active_project_id for p in self.projects) else (options[0][1] if options else None)
                yield Label("Active Project:", classes="form-label")
                yield Select(options, value=default_val, id="select-project")

                # Project info summaries
                for p in self.projects:
                    marker = "[bold green]● ACTIVE[/bold green]" if p.id == self.active_project_id else f"[dim]{p.status}[/dim]"
                    yield Static(f"\n[bold cyan]{p.name}[/bold cyan] ({p.id}) - {marker}\n  [dim]Workspace:[/dim] {p.workspace_root}\n  [dim]Workflow:[/dim] {p.default_workflow}")
            else:
                yield Static("[dim]No projects registered yet. Use 'orq project create <name> --workspace <path>'[/dim]")

            with Horizontal(id="project-actions"):
                yield Button("Close", id="btn-close-project")
                if self.projects:
                    yield Button("Switch Project", id="btn-switch-project", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-switch-project":
            select = self.query_one("#select-project", Select)
            if select.value != Select.BLANK:
                self.dismiss(str(select.value))
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)


class QuestionModal(ModalScreen[Optional[Dict[str, Any]]]):
    """Interactive question modal supporting multiple choice, multi-select, and write-in answers (Grill-Me style)."""

    DEFAULT_CSS = """
    QuestionModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #question-container {
        width: 86;
        height: auto;
        max-height: 85%;
        background: #0f172a;
        border: solid #c084fc;
        padding: 1 2;
    }
    #question-header {
        text-style: bold;
        color: #c084fc;
        margin-bottom: 1;
        border-bottom: solid #334155;
        padding-bottom: 1;
    }
    .question-block {
        margin-bottom: 1;
        background: #1e293b;
        padding: 1;
        border: solid #334155;
    }
    .question-title {
        color: #f8fafc;
        text-style: bold;
        margin-bottom: 1;
    }
    .question-sub {
        color: #94a3b8;
        margin-bottom: 1;
    }
    .options-scroll {
        margin: 1 0;
        max-height: 10;
    }
    .writein-field {
        margin-top: 0;
        margin-bottom: 1;
    }
    #question-actions {
        margin-top: 1;
        height: 3;
        align-horizontal: right;
    }
    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_cancel", "Skip / Cancel"),
    ]

    def __init__(
        self,
        question: Optional[str] = None,
        options: Optional[List[str]] = None,
        is_multi_select: bool = False,
        allow_write_in: bool = True,
        questions: Optional[List[Dict[str, Any]]] = None,
        sender_role: str = "decision_maker",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.sender_role = sender_role
        self.allow_write_in = allow_write_in

        if questions:
            self.question_specs = questions
        else:
            self.question_specs = [{
                "question": question or "Please provide direction or feedback:",
                "options": options or [],
                "is_multi_select": is_multi_select,
            }]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="question-container"):
            yield Label(f"[bold magenta]❓ Interactive Question from {escape(self.sender_role)}[/bold magenta]", id="question-header")

            for q_idx, spec in enumerate(self.question_specs):
                q_text = spec.get("question", "")
                opts = spec.get("options", [])
                multi = spec.get("is_multi_select", False)

                with Vertical(classes="question-block"):
                    num_prefix = f"[{q_idx+1}] " if len(self.question_specs) > 1 else ""
                    yield Static(f"[bold white]{num_prefix}{escape(q_text)}[/bold white]", classes="question-title")

                    if opts:
                        hint = "Select all options that apply (multiple choice):" if multi else "Select one option:"
                        yield Label(f"[dim]{hint}[/dim]", classes="question-sub")
                        if multi:
                            with VerticalScroll(classes="options-scroll"):
                                for opt_idx, opt in enumerate(opts):
                                    yield Checkbox(label=opt, id=f"chk_{q_idx}_{opt_idx}")
                        else:
                            with VerticalScroll(classes="options-scroll"):
                                with RadioSet(id=f"radios_{q_idx}"):
                                    for opt_idx, opt in enumerate(opts):
                                        yield RadioButton(label=opt, id=f"opt_{q_idx}_{opt_idx}", value=(opt_idx == 0))

                    if self.allow_write_in:
                        yield Label("[dim]Write-in response / custom feedback (optional):[/dim]", classes="question-sub")
                        yield Input(
                            placeholder="Type your custom answer or notes here...",
                            id=f"writein_{q_idx}",
                            classes="writein-field",
                        )

            with Horizontal(id="question-actions"):
                yield Button("Skip / Cancel", id="btn-skip")
                yield Button("Submit Answer", id="btn-submit", variant="primary")

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        elif event.button.id == "btn-skip":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        answers = []
        overall_selected = []
        overall_write_ins = []

        for q_idx, spec in enumerate(self.question_specs):
            q_text = spec.get("question", "")
            opts = spec.get("options", [])
            multi = spec.get("is_multi_select", False)

            selected_opts: List[str] = []
            if opts:
                if multi:
                    for opt_idx, _ in enumerate(opts):
                        try:
                            cb = self.query_one(f"#chk_{q_idx}_{opt_idx}", Checkbox)
                            if cb.value:
                                selected_opts.append(str(cb.label))
                        except Exception:
                            pass
                else:
                    try:
                        rs = self.query_one(f"#radios_{q_idx}", RadioSet)
                        if rs.pressed_button:
                            selected_opts.append(str(rs.pressed_button.label))
                    except Exception:
                        pass

            write_in_val = ""
            if self.allow_write_in:
                try:
                    inp = self.query_one(f"#writein_{q_idx}", Input)
                    write_in_val = inp.value.strip()
                except Exception:
                    pass

            answers.append({
                "question": q_text,
                "selected": selected_opts,
                "write_in": write_in_val,
            })
            overall_selected.extend(selected_opts)
            if write_in_val:
                overall_write_ins.append(write_in_val)

        # If user provided nothing at all on an options question, prompt them
        if not overall_selected and not overall_write_ins and any(spec.get("options") for spec in self.question_specs):
            self.notify("Please select an option or provide a write-in response.", severity="warning")
            return

        self.dismiss({
            "sender_role": self.sender_role,
            "answers": answers,
            "question": self.question_specs[0].get("question", ""),
            "selected": answers[0]["selected"] if answers else [],
            "write_in": answers[0]["write_in"] if answers else "",
        })
