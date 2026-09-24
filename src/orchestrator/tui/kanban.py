"""Kanban board projection widget for the Textual TUI."""

from typing import List, Optional
from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from orchestrator.models import OrchestrationTask


KANBAN_COLUMNS = [
    ("backlog", "BACKLOG", "#94a3b8"),
    ("ready", "READY", "#38bdf8"),
    ("in_progress", "IN PROGRESS", "#f59e0b"),
    ("review", "REVIEW", "#c084fc"),
    ("blocked", "BLOCKED", "#f87171"),
    ("done", "DONE", "#4ade80"),
]


class TaskCard(Static):
    """Visual card representing a single task on the Kanban board."""

    DEFAULT_CSS = """
    TaskCard {
        background: #1e293b;
        border: solid #334155;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }
    TaskCard:hover {
        border: solid #38bdf8;
        background: #243249;
    }
    TaskCard:focus {
        border: solid #38bdf8;
        background: #1e293b;
    }
    """

    class Selected(Message):
        """Emitted when a task card is clicked or selected."""
        def __init__(self, task: OrchestrationTask) -> None:
            super().__init__()
            self.task = task

    def __init__(self, task: OrchestrationTask, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task = task
        self.can_focus = True

    def on_mount(self) -> None:
        self.render_card()

    def render_card(self) -> None:
        task = self.task
        title = task.title or task.task_id
        role = task.target_role or "unassigned"
        stage = task.stage_id or ""
        column = task.kanban_column or "ready"

        role_color = "yellow"
        if role == "decision_maker":
            role_color = "magenta"
        elif role == "developer":
            role_color = "cyan"
        elif role == "code_reviewer":
            role_color = "green"

        desc = task.description or ""
        if len(desc) > 80:
            desc = desc[:77] + "..."

        lines = [
            f"[bold white]{escape(title)}[/bold white]",
        ]
        if desc:
            lines.append(f"[dim]{escape(desc)}[/dim]")

        meta_line = f"[dim]{escape(task.task_id)}[/dim] • [{role_color}]{escape(role)}[/{role_color}]"
        if stage:
            meta_line += f" • [dim]{escape(stage)}[/dim]"

        lines.append(meta_line)

        if column == "blocked":
            lines.append("[bold red]⚠ BLOCKED[/bold red]")
        elif column == "review":
            lines.append("[bold magenta]🔍 UNDER REVIEW[/bold magenta]")

        self.update("\n".join(lines))

    def on_click(self) -> None:
        self.post_message(self.Selected(self.task))

    def key_enter(self) -> None:
        self.post_message(self.Selected(self.task))


class KanbanColumn(Vertical):
    """A vertical column representing a Kanban state."""

    DEFAULT_CSS = """
    KanbanColumn {
        width: 1fr;
        height: 1fr;
        background: #0b1120;
        border: solid #1e293b;
        margin: 0 1;
        padding: 0;
    }
    .col-header {
        height: 3;
        padding: 0 1;
        content-align: center middle;
        text-style: bold;
        border-bottom: solid #334155;
    }
    .col-cards-container {
        height: 1fr;
        padding: 1 1;
    }
    """

    def __init__(self, column_id: str, title: str, color: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.column_id = column_id
        self.column_title = title
        self.color = color
        self.tasks: List[OrchestrationTask] = []

    def compose(self) -> ComposeResult:
        yield Static(f"[{self.color}]{self.column_title} (0)[/{self.color}]", id=f"header-{self.column_id}", classes="col-header")
        yield VerticalScroll(id=f"cards-{self.column_id}", classes="col-cards-container")

    def update_tasks(self, tasks: List[OrchestrationTask]) -> None:
        self.tasks = tasks
        header = self.query_one(f"#header-{self.column_id}", Static)
        header.update(f"[{self.color}]{self.column_title} ({len(tasks)})[/{self.color}]")

        cards_container = self.query_one(f"#cards-{self.column_id}", VerticalScroll)
        cards_container.remove_children()
        for t in tasks:
            cards_container.mount(TaskCard(t))


class KanbanBoard(Container):
    """The full 6-column Kanban board container."""

    DEFAULT_CSS = """
    KanbanBoard {
        height: 1fr;
        width: 100%;
        layout: vertical;
    }
    #kanban-columns-row {
        height: 1fr;
        width: 100%;
        layout: horizontal;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.columns: dict[str, KanbanColumn] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="kanban-columns-row"):
            for col_id, title, color in KANBAN_COLUMNS:
                col = KanbanColumn(col_id, title, color, id=f"col-{col_id}")
                self.columns[col_id] = col
                yield col

    def refresh_board(self, tasks: List[OrchestrationTask]) -> None:
        grouped: dict[str, List[OrchestrationTask]] = {col_id: [] for col_id, _, _ in KANBAN_COLUMNS}
        for t in tasks:
            col = t.kanban_column or "ready"
            if col in grouped:
                grouped[col].append(t)
            else:
                grouped["ready"].append(t)

        for col_id, col_widget in self.columns.items():
            col_widget.update_tasks(grouped.get(col_id, []))
