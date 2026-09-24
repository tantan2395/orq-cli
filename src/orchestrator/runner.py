"""Headless workflow runner executing autonomous agent team loops."""

from pathlib import Path
from typing import Optional
from rich.console import Console

from orchestrator.config import OrchestratorConfig, get_default_config
from orchestrator.db import Database
from orchestrator.dispatcher import Dispatcher
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.mcp.ipc import IPCServer
from orchestrator.models import OrchestrationTask, StageConfig, WorkflowRun, utc_now_iso

console = Console()


class WorkflowRunner:
    """Executes a workflow pipeline headlessly from start to finish."""

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        workspace_root: Optional[str] = None,
        db_path: Optional[str] = None,
        socket_path: Optional[Path] = None,
        dispatcher: Optional[Dispatcher] = None,
    ):
        self.config = config or get_default_config()
        self.workspace_root = workspace_root or self.config.workspace.workspace_root
        self.db = Database(db_path or self.config.sqlite_db_path)
        self.events = EventBus()
        self.engine = OrchestrationEngine(config=self.config, db=self.db, events=self.events)

        self.socket_path = socket_path or Path(self.config.ipc_socket_path)
        self.ipc_server = IPCServer(handler=self.engine.handle_ipc_command, socket_path=self.socket_path)

        self.dispatcher = dispatcher or Dispatcher(
            config=self.config,
            session_manager=self.engine.session_manager,
            event_bus=self.events,
        )

    async def run(
        self,
        workflow_id: Optional[str] = None,
        max_turns: int = 10,
    ) -> WorkflowRun:
        """Executes the autonomous loop across stages until completed, paused, or max_turns reached."""
        await self.engine.initialize()
        await self.ipc_server.start()

        run = await self.engine.create_workflow_run(
            workflow_id=workflow_id,
            workspace_root=self.workspace_root,
        )
        console.print(f"[bold green]✓ Started Workflow Run:[/bold green] [cyan]{run.run_id}[/cyan] (Stage: [bold]{run.current_stage}[/bold])")

        turn_count = 0

        try:
            while run.status == "running" and turn_count < max_turns:
                turn_count += 1
                definition = self.config.workflows.get(run.definition_id)
                if not definition or run.current_stage not in definition.stages:
                    console.print(f"[red]Stage '{run.current_stage}' not defined in workflow.[/red]")
                    break

                stage_cfg: StageConfig = definition.stages[run.current_stage]
                role_name = stage_cfg.role

                console.print(f"\n[bold blue]━━━ Turn {turn_count}: Stage '{run.current_stage}' -> Role '{role_name}' ━━━[/bold blue]")

                # Find latest task or generate initial task for this stage
                tasks = await self.db.list_tasks(run.run_id)
                pending_task = next(
                    (t for t in reversed(tasks) if t.status == "queued" and (t.target_role == role_name or not t.target_role)),
                    None,
                )

                if not pending_task:
                    pending_task = OrchestrationTask(
                        task_id=f"init_task_{turn_count}",
                        workflow_run_id=run.run_id,
                        stage_id=run.current_stage,
                        type="stage_task",
                        requested_by="engine",
                        target_role=role_name,
                        status="running",
                        payload={"task": stage_cfg.description or f"Execute stage {run.current_stage}"},
                    )
                else:
                    pending_task.status = "running"
                    pending_task.started_at = utc_now_iso()
                    await self.db.save_task(pending_task)

                # Generate immutable turn context
                turn_ctx = self.engine.context_manager.create_turn_context(
                    workflow_run=run,
                    role_config=self.config.roles[role_name],
                    task=pending_task,
                )
                effective_prompt = self.engine.context_manager.assemble_effective_prompt(turn_ctx)

                # Dispatch turn to adapter
                async for activity in self.dispatcher.execute_turn(
                    turn_context=turn_ctx,
                    effective_prompt=effective_prompt,
                    workspace_root=self.workspace_root,
                ):
                    act_type = activity.get("type")
                    if act_type == "status":
                        console.print(f"[dim]• {activity.get('status')}[/dim]")
                    elif act_type == "tool_call":
                        console.print(f"[yellow]⚡ Tool Call:[/yellow] [bold]{activity.get('name')}[/bold]({activity.get('args')})")
                    elif act_type == "agent_message":
                        console.print(f"[white]{activity.get('content')}[/white]", end="")
                    elif act_type == "error":
                        console.print(f"[bold red]✗ Error:[/bold red] {activity.get('error')}")

                # Mark task completed in DB if it was a stored task
                if pending_task and not pending_task.task_id.startswith("init_task_"):
                    pending_task.status = "completed"
                    pending_task.completed_at = utc_now_iso()
                    await self.db.save_task(pending_task)

                # Refresh run state from DB
                refreshed = await self.db.get_workflow_run(run.run_id)
                if refreshed:
                    run = refreshed

                if run.status == "completed":
                    console.print(f"\n[bold green]✓ Workflow Run Completed Successfully![/bold green] (Final Stage: [bold]{run.current_stage}[/bold])")
                    break

            return run
        finally:
            await self.ipc_server.stop()
