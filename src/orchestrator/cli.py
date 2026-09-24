"""Command-line interface for the Agent Orchestration Runtime."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from rich.console import Console
from rich.table import Table

from orchestrator.config import load_config
from orchestrator.doctor import DiagnosticStatus, Doctor
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
    final_run = asyncio.run(runner.run(
        workflow_id=args.workflow,
        max_turns=args.max_turns,
        initial_task=initial_task,
    ))
    return 0 if final_run.status == "completed" else 1


def cmd_tui(args: argparse.Namespace) -> int:
    """Launches the interactive Textual TUI human control plane."""
    cfg = load_config(Path(args.config) if args.config else None)
    initial_task = getattr(args, "task", None) or getattr(args, "task_pos", None)
    app = OrchestratorTUI(
        config=cfg,
        workspace_root=args.workspace,
        workflow_id=args.workflow,
        initial_task=initial_task,
    )
    app.run()
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
    p_run.add_argument("--max-turns", type=int, default=10, help="Maximum turns to execute")
    p_run.add_argument("--config", default=None, help="Path to orchestrator.yaml")

    # tui
    p_tui = subparsers.add_parser("tui", help="Launch interactive Textual TUI control plane")
    p_tui.add_argument("task_pos", nargs="?", default=None, help="Initial task or question for the lead agent")
    p_tui.add_argument("--task", "-t", default=None, help="Initial task or question for the lead agent")
    p_tui.add_argument("--workflow", default=None, help="Workflow ID to run")
    p_tui.add_argument("--workspace", default=".", help="Target workspace root")
    p_tui.add_argument("--config", default=None, help="Path to orchestrator.yaml")

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
