"""Command-line interface for the Agent Orchestration Runtime."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from rich.console import Console
from rich.table import Table

from orchestrator.config import load_config
from orchestrator.db import Database
from orchestrator.doctor import DiagnosticStatus, Doctor
from orchestrator.models import Project
from orchestrator.runner import WorkflowRunner
from orchestrator.tui.app import OrchestratorTUI

console = Console()


def cmd_doctor(args: argparse.Namespace) -> int:
    """Runs the 4-level diagnostic suite."""
    console.print("\n[bold]Agent Orchestrator Capability Diagnostics[/bold]\n")
    doctor = Doctor(workspace_root=args.workspace)
    results = doctor.run_all()

    table = Table(title="Diagnostic Checks", show_header=True, header_style="bold cyan")
    table.add_column("Status", width=14)
    table.add_column("Capability / Environment", width=38)
    table.add_column("Details", width=42)

    has_failures = False
    for r in results:
        if r.status == DiagnosticStatus.PASS:
            status_text = "[bold green]✓ PASS[/bold green]"
        elif r.status == DiagnosticStatus.WARN:
            status_text = "[bold yellow]⚠ WARN[/bold yellow]"
        elif r.status == DiagnosticStatus.FAIL:
            status_text = "[bold red]✗ FAIL[/bold red]"
            has_failures = True
        else:
            status_text = "[dim]○ UNSUPPORTED[/dim]"

        table.add_row(status_text, r.name, r.details)

    console.print(table)
    return 1 if has_failures else 0


def cmd_run(args: argparse.Namespace) -> int:
    """Runs a workflow headlessly to completion."""
    console.print("\n[bold]Launching Headless Multi-Agent Workflow Runner...[/bold]\n")
    cfg = load_config(Path(args.config) if args.config else None)
    runner = WorkflowRunner(
        config=cfg,
        workspace_root=args.workspace,
    )
    initial_task = getattr(args, "task", None) or getattr(args, "task_pos", None)
    project_id = getattr(args, "project", None)
    final_run = asyncio.run(runner.run(
        workflow_id=args.workflow,
        max_turns=args.max_turns,
        initial_task=initial_task,
        project_id=project_id,
    ))
    return 0 if final_run.status == "completed" else 1


def cmd_tui(args: argparse.Namespace) -> int:
    """Launches the interactive Textual TUI human control plane."""
    cfg = load_config(Path(args.config) if args.config else None)
    initial_task = getattr(args, "task", None) or getattr(args, "task_pos", None)
    project_id = getattr(args, "project", None)
    app = OrchestratorTUI(
        config=cfg,
        workspace_root=args.workspace,
        workflow_id=args.workflow,
        initial_task=initial_task,
        project_id=project_id,
    )
    app.run()
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    """Registers a new project."""
    cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    db = Database(cfg.sqlite_db_path)
    ws_path = Path(args.workspace).resolve()
    ws_path.mkdir(parents=True, exist_ok=True)

    slug = args.name.lower().replace(" ", "-")
    existing = asyncio.run(db.get_project(slug))
    if existing:
        console.print(f"[bold red]✗ Project '{args.name}' (ID: {slug}) already exists.[/bold red]")
        return 1

    by_ws = asyncio.run(db.get_project_by_workspace(str(ws_path)))
    if by_ws:
        console.print(f"[bold red]✗ Workspace '{ws_path}' is already assigned to project '{by_ws.name}' ({by_ws.id}).[/bold red]")
        return 1

    proj = Project(
        id=slug,
        name=args.name,
        workspace_root=str(ws_path),
        description=args.description or "",
        default_workflow=args.workflow or cfg.active_workflow,
        config={},
        status="active",
    )
    asyncio.run(db.save_project(proj))
    active = asyncio.run(db.get_active_project())
    if not active:
        asyncio.run(db.set_active_project(proj.id))

    console.print(f"[bold green]✓ Project created:[/bold green] [bold cyan]{proj.name}[/bold cyan] ({proj.id})")
    console.print(f"  Workspace: [white]{proj.workspace_root}[/white]")
    console.print(f"  Default Workflow: [white]{proj.default_workflow}[/white]")
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    """Lists registered projects."""
    cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    db = Database(cfg.sqlite_db_path)
    include_archived = getattr(args, "all", False)
    projects = asyncio.run(db.list_projects(include_archived=include_archived))
    active = asyncio.run(db.get_active_project())
    active_id = active.id if active else None

    if not projects:
        console.print("[dim]No projects registered. Create one with 'orq project create <name> --workspace <path>'[/dim]")
        return 0

    table = Table(title="Orchestrated Projects", show_header=True, header_style="bold cyan")
    table.add_column("Active", justify="center")
    table.add_column("Name (ID)")
    table.add_column("Workspace Root")
    table.add_column("Status")
    table.add_column("Default Workflow")

    for p in projects:
        is_active = "[bold green]*[/bold green]" if p.id == active_id else ""
        status_style = "[green]active[/green]" if p.status == "active" else "[dim]archived[/dim]"
        table.add_row(
            is_active,
            f"{p.name} ([dim]{p.id}[/dim])",
            p.workspace_root,
            status_style,
            p.default_workflow,
        )

    console.print(table)
    return 0


def cmd_project_inspect(args: argparse.Namespace) -> int:
    """Displays detailed project information and recent runs."""
    cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    db = Database(cfg.sqlite_db_path)
    target = args.name.lower().replace(" ", "-")
    proj = asyncio.run(db.get_project(target))
    if not proj:
        all_projs = asyncio.run(db.list_projects(include_archived=True))
        for p in all_projs:
            if p.name.lower() == args.name.lower() or p.id == args.name:
                proj = p
                break
    if not proj:
        console.print(f"[bold red]✗ Project '{args.name}' not found.[/bold red]")
        return 1

    runs = asyncio.run(db.list_workflow_runs(project_id=proj.id))
    active = asyncio.run(db.get_active_project())
    is_active = active and active.id == proj.id

    console.print(f"\n[bold cyan]Project: {proj.name}[/bold cyan] ({'[bold green]ACTIVE[/bold green]' if is_active else proj.status})\n")
    console.print(f"  [bold]ID:[/bold]               {proj.id}")
    console.print(f"  [bold]Workspace:[/bold]        {proj.workspace_root}")
    console.print(f"  [bold]Description:[/bold]      {proj.description or 'None'}")
    console.print(f"  [bold]Default Workflow:[/bold] {proj.default_workflow}")
    console.print(f"  [bold]Created At:[/bold]       {proj.created_at}")
    console.print(f"  [bold]Updated At:[/bold]       {proj.updated_at}\n")

    if runs:
        table = Table(title=f"Runs for {proj.name}", show_header=True, header_style="bold magenta")
        table.add_column("Run ID", width=18)
        table.add_column("Stage", width=18)
        table.add_column("Status", width=12)
        table.add_column("Started", width=22)
        for r in runs:
            table.add_row(r.run_id, r.current_stage, r.status, r.created_at)
        console.print(table)
    else:
        console.print("  [dim]No workflow runs recorded for this project.[/dim]\n")
    return 0


def cmd_project_use(args: argparse.Namespace) -> int:
    """Sets the active project."""
    cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    db = Database(cfg.sqlite_db_path)
    target = args.name.lower().replace(" ", "-")
    proj = asyncio.run(db.get_project(target))
    if not proj:
        all_projs = asyncio.run(db.list_projects(include_archived=True))
        for p in all_projs:
            if p.name.lower() == args.name.lower() or p.id == args.name:
                proj = p
                break
    if not proj:
        console.print(f"[bold red]✗ Project '{args.name}' not found.[/bold red]")
        return 1
    if proj.status == "archived":
        console.print(f"[bold red]✗ Cannot use archived project '{proj.name}'.[/bold red]")
        return 1

    asyncio.run(db.set_active_project(proj.id))
    console.print(f"[bold green]✓ Active project set to:[/bold green] [bold cyan]{proj.name}[/bold cyan] ({proj.id})")
    return 0


def cmd_project_remove(args: argparse.Namespace) -> int:
    """Soft-deletes (archives) a project."""
    cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    db = Database(cfg.sqlite_db_path)
    target = args.name.lower().replace(" ", "-")
    proj = asyncio.run(db.get_project(target))
    if not proj:
        all_projs = asyncio.run(db.list_projects(include_archived=True))
        for p in all_projs:
            if p.name.lower() == args.name.lower() or p.id == args.name:
                proj = p
                break
    if not proj:
        console.print(f"[bold red]✗ Project '{args.name}' not found.[/bold red]")
        return 1

    asyncio.run(db.archive_project(proj.id))
    active = asyncio.run(db.get_active_project())
    if active and active.id == proj.id:
        other_projects = asyncio.run(db.list_projects(include_archived=False))
        if other_projects:
            asyncio.run(db.set_active_project(other_projects[0].id))
        else:
            asyncio.run(db.set_active_project(""))

    console.print(f"[bold green]✓ Project '{proj.name}' ({proj.id}) archived.[/bold green]")
    console.print(f"[dim]Note: Workspace directory at '{proj.workspace_root}' was preserved.[/dim]")
    return 0


def cmd_profile_import(args: argparse.Namespace) -> int:
    """Imports available metadata and context from an existing session into a candidate profile."""
    console.print(f"[bold cyan]Importing session context from [white]{args.source}[/white] -> [white]{args.target}[/white]...[/bold cyan]")
    profile_dir = Path("profiles") / args.target
    profile_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "profile_id": args.target,
        "source": args.source,
        "conventions": {
            "handoff_schema": "5-point bounded slice specification",
            "review_schema": "Independent verification with retained gates",
            "evidence_format": "JSON probes and diff hash",
        },
        "extracted_context": f"Candidate profile imported from {args.source}. Review and edit before use.",
    }
    meta_path = profile_dir / "profile.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    console.print(f"[bold green]✓ Candidate profile created at:[/bold green] [cyan]{meta_path}[/cyan]")
    console.print(f"Run [bold white]'orq profile inspect {args.target}'[/bold white] to review.")
    return 0


def cmd_profile_inspect(args: argparse.Namespace) -> int:
    """Displays an imported candidate profile for human inspection and editing."""
    meta_path = Path("profiles") / args.target / "profile.json"
    if not meta_path.exists():
        console.print(f"[red]Profile '{args.target}' not found at {meta_path}[/red]")
        return 1

    content = meta_path.read_text(encoding="utf-8")
    console.print(f"\n[bold cyan]Profile: {args.target}[/bold cyan]\n")
    console.print_json(content)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orq",
        description="orq: Local multi-agent orchestration runtime for coding agents",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # doctor
    p_doc = subparsers.add_parser("doctor", help="Run capability and environment diagnostics")
    p_doc.add_argument("--workspace", default=".", help="Target workspace path")

    # run
    p_run = subparsers.add_parser("run", help="Run workflow headlessly")
    p_run.add_argument("task_pos", nargs="?", default=None, help="Initial task or goal for the workflow")
    p_run.add_argument("--task", "-t", default=None, help="Initial task or goal for the workflow")
    p_run.add_argument("--workflow", default=None, help="Workflow ID to run")
    p_run.add_argument("--workspace", default=".", help="Target workspace root")
    p_run.add_argument("--project", default=None, help="Project name or ID to associate run with")
    p_run.add_argument("--max-turns", type=int, default=10, help="Maximum turns to execute")
    p_run.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    # tui
    p_tui = subparsers.add_parser("tui", help="Launch interactive Textual TUI control plane")
    p_tui.add_argument("task_pos", nargs="?", default=None, help="Initial task or question for the lead agent")
    p_tui.add_argument("--task", "-t", default=None, help="Initial task or question for the lead agent")
    p_tui.add_argument("--workflow", default=None, help="Workflow ID to run")
    p_tui.add_argument("--workspace", default=".", help="Target workspace root")
    p_tui.add_argument("--project", default=None, help="Project name or ID to associate run with")
    p_tui.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    # project
    p_proj = subparsers.add_parser("project", help="Manage orchestrated projects")
    proj_subs = p_proj.add_subparsers(dest="project_action", help="Project actions")

    p_proj_create = proj_subs.add_parser("create", help="Create and register a new project")
    p_proj_create.add_argument("name", help="Human-readable project name")
    p_proj_create.add_argument("--workspace", required=True, help="Path to project workspace directory")
    p_proj_create.add_argument("--description", "-d", default="", help="Optional project description")
    p_proj_create.add_argument("--workflow", default=None, help="Default workflow definition for this project")
    p_proj_create.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    p_proj_list = proj_subs.add_parser("list", help="List registered projects")
    p_proj_list.add_argument("--all", "-a", action="store_true", help="Include archived projects")
    p_proj_list.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    p_proj_inspect = proj_subs.add_parser("inspect", help="Inspect a project and its workflow runs")
    p_proj_inspect.add_argument("name", help="Project name or ID to inspect")
    p_proj_inspect.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    p_proj_use = proj_subs.add_parser("use", help="Set the active project")
    p_proj_use.add_argument("name", help="Project name or ID to activate")
    p_proj_use.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    p_proj_remove = proj_subs.add_parser("remove", help="Archive a project (preserves files on disk)")
    p_proj_remove.add_argument("name", help="Project name or ID to archive")
    p_proj_remove.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    # profile
    p_prof = subparsers.add_parser("profile", help="Manage and inspect workflow profiles")
    prof_subs = p_prof.add_subparsers(dest="profile_action", help="Profile actions")

    p_imp = prof_subs.add_parser("import-session", help="Import metadata from an existing session")
    p_imp.add_argument("--source", required=True, help="Session source (e.g. codex:<uuid>)")
    p_imp.add_argument("--target", required=True, help="Target profile name")

    p_insp = prof_subs.add_parser("inspect", help="Inspect candidate profile")
    p_insp.add_argument("target", help="Profile name to inspect")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(cmd_doctor(args))
    elif args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "tui":
        sys.exit(cmd_tui(args))
    elif args.command == "project":
        if args.project_action == "create":
            sys.exit(cmd_project_create(args))
        elif args.project_action == "list":
            sys.exit(cmd_project_list(args))
        elif args.project_action == "inspect":
            sys.exit(cmd_project_inspect(args))
        elif args.project_action == "use":
            sys.exit(cmd_project_use(args))
        elif args.project_action == "remove":
            sys.exit(cmd_project_remove(args))
        else:
            parser.parse_args(["project", "--help"])
            sys.exit(1)
    elif args.command == "profile":
        if args.profile_action == "import-session":
            sys.exit(cmd_profile_import(args))
        elif args.profile_action == "inspect":
            sys.exit(cmd_profile_inspect(args))
        else:
            parser.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
