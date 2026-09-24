"""Central Orchestration Engine managing state machine, events, and task lifecycle."""

import uuid
from typing import Any, Dict, List, Optional

from orchestrator.commands import (
    AgentMessageCommand,
    HandoffCommand,
    HumanInterventionCommand,
    ReviewDecisionCommand,
    ReviewRequestCommand,
    TaskCreateCommand,
    TaskDependencyCommand,
    TaskLifecycleCommand,
    TaskUpdateCommand,
    WorkflowControlCommand,
)
from orchestrator.config import OrchestratorConfig, get_default_config
from orchestrator.context_manager import ContextManager
from orchestrator.db import Database
from orchestrator.events import EventBus, create_event
from orchestrator.models import (
    Artifact,
    ArtifactType,
    OrchestrationTask,
    Project,
    ReviewDecision,
    StageConfig,
    TaskDependency,
    WorkflowDefinition,
    WorkflowRun,
    utc_now_iso,
)
from orchestrator.session_manager import SessionManager


class OrchestrationEngine:
    """The central authority for multi-agent coordination, routing, and state."""

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        db: Optional[Database] = None,
        events: Optional[EventBus] = None,
    ):
        self.config = config or get_default_config()
        self.db = db or Database(self.config.sqlite_db_path)
        self.events = events or EventBus()
        self.session_manager = SessionManager(self.db)
        self.context_manager = ContextManager()

    async def initialize(self) -> None:
        """Initializes database schema and storage, and wires event store persistence."""
        await self.db.initialize()
        self.events.add_callback(self.db.save_event)

    async def resolve_active_project(
        self, workspace_path: Optional[str] = None
    ) -> Optional[Project]:
        """Resolves active project via hybrid strategy: workspace match -> active_project_id setting."""
        if workspace_path:
            p = await self.db.get_project_by_workspace(workspace_path)
            if p:
                return p
        return await self.db.get_active_project()

    async def create_workflow_run(
        self,
        workflow_id: Optional[str] = None,
        workspace_root: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> WorkflowRun:
        """Instantiates a new workflow run from a registered definition."""
        wf_id = workflow_id or self.config.active_workflow
        if wf_id not in self.config.workflows:
            raise ValueError(f"Workflow '{wf_id}' not found in configuration.")

        definition: WorkflowDefinition = self.config.workflows[wf_id]
        ws_root = workspace_root or self.config.workspace.workspace_root

        # Auto-resolve project if not explicitly supplied
        effective_project_id = project_id
        if not effective_project_id:
            detected_project = await self.resolve_active_project(ws_root)
            if detected_project:
                effective_project_id = detected_project.id

        run_id = f"run_{uuid.uuid4().hex[:10]}"
        run = WorkflowRun(
            run_id=run_id,
            project_id=effective_project_id,
            definition_id=definition.id,
            definition_version=definition.version,
            workspace_root=ws_root,
            current_stage=definition.initial_stage,
            status="running",
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await self.db.save_workflow_run(run)

        # Publish initial events
        evt_wf = create_event(
            workflow_run_id=run.run_id,
            event_type="workflow_started",
            payload={"definition_id": definition.id, "initial_stage": run.current_stage},
            stage_id=run.current_stage,
        )
        await self.events.publish(evt_wf)

        evt_stage = create_event(
            workflow_run_id=run.run_id,
            event_type="stage_started",
            payload={"stage_name": run.current_stage},
            stage_id=run.current_stage,
        )
        await self.events.publish(evt_stage)

        return run

    async def handle_handoff(self, cmd: HandoffCommand) -> OrchestrationTask:
        """Processes an asynchronous handoff command with run-scoped idempotency."""
        if cmd.idempotency_key:
            existing = await self.db.get_task_by_idempotency(cmd.workflow_run_id, cmd.idempotency_key)
            if existing:
                return existing

        run = await self.db.get_workflow_run(cmd.workflow_run_id)
        if not run:
            raise ValueError(f"Workflow run '{cmd.workflow_run_id}' not found.")

        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            stage_id=run.current_stage,
            idempotency_key=cmd.idempotency_key,
            type="handoff",
            requested_by=cmd.requested_by,
            target_role=cmd.target_role,
            status="queued",
            created_at=utc_now_iso(),
            payload=cmd.model_dump(),
        )
        await self.db.save_task(task)

        # Publish task queued and handoff created events
        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="handoff_created",
            stage_id=run.current_stage,
            task_id=task.task_id,
            role=cmd.requested_by,
            payload={"target_role": cmd.target_role, "task": cmd.task},
        ))

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="task_queued",
            stage_id=run.current_stage,
            task_id=task.task_id,
            role=cmd.target_role,
            payload={"type": "handoff"},
        ))

        # Check stage transition
        definition = self.config.workflows.get(run.definition_id)
        if definition and run.current_stage in definition.stages:
            stage_cfg: StageConfig = definition.stages[run.current_stage]
            if stage_cfg.next_on_handoff:
                await self._transition_stage(run, stage_cfg.next_on_handoff)

        return task

    async def handle_review_request(self, cmd: ReviewRequestCommand) -> OrchestrationTask:
        """Processes a review request command with run-scoped idempotency."""
        if cmd.idempotency_key:
            existing = await self.db.get_task_by_idempotency(cmd.workflow_run_id, cmd.idempotency_key)
            if existing:
                return existing

        run = await self.db.get_workflow_run(cmd.workflow_run_id)
        if not run:
            raise ValueError(f"Workflow run '{cmd.workflow_run_id}' not found.")

        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            stage_id=run.current_stage,
            idempotency_key=cmd.idempotency_key,
            type="review_request",
            requested_by=cmd.requested_by,
            target_role=cmd.target_role,
            status="queued",
            created_at=utc_now_iso(),
            payload=cmd.model_dump(),
        )
        await self.db.save_task(task)

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="review_requested",
            stage_id=run.current_stage,
            task_id=task.task_id,
            role=cmd.requested_by,
            payload={"target_role": cmd.target_role, "summary": cmd.summary},
        ))

        # Check stage transition
        definition = self.config.workflows.get(run.definition_id)
        if definition and run.current_stage in definition.stages:
            stage_cfg: StageConfig = definition.stages[run.current_stage]
            if stage_cfg.next_on_review_request:
                await self._transition_stage(run, stage_cfg.next_on_review_request)

        return task

    async def handle_review_decision(self, cmd: ReviewDecisionCommand) -> OrchestrationTask:
        """Processes a reviewer's decision and evaluates state machine branch."""
        if cmd.idempotency_key:
            existing = await self.db.get_task_by_idempotency(cmd.workflow_run_id, cmd.idempotency_key)
            if existing:
                return existing

        run = await self.db.get_workflow_run(cmd.workflow_run_id)
        if not run:
            raise ValueError(f"Workflow run '{cmd.workflow_run_id}' not found.")

        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            stage_id=run.current_stage,
            idempotency_key=cmd.idempotency_key,
            type="review_decision",
            requested_by=cmd.reviewer_role,
            target_role="engine",
            status="completed",
            created_at=utc_now_iso(),
            completed_at=utc_now_iso(),
            payload=cmd.model_dump(),
            result={"decision": cmd.decision.value, "summary": cmd.summary},
        )
        await self.db.save_task(task)

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="review_completed",
            stage_id=run.current_stage,
            task_id=task.task_id,
            role=cmd.reviewer_role,
            payload={"decision": cmd.decision.value, "summary": cmd.summary},
        ))

        # Evaluate workflow transitions based on verdict
        definition = self.config.workflows.get(run.definition_id)
        if definition and run.current_stage in definition.stages:
            stage_cfg: StageConfig = definition.stages[run.current_stage]
            if cmd.decision == ReviewDecision.APPROVED:
                if stage_cfg.next_on_approved:
                    if stage_cfg.next_on_approved.lower() == "done":
                        await self._complete_workflow(run)
                    else:
                        await self._transition_stage(run, stage_cfg.next_on_approved)
            elif cmd.decision == ReviewDecision.CHANGES_REQUESTED:
                if stage_cfg.next_on_changes_requested:
                    await self._transition_stage(run, stage_cfg.next_on_changes_requested)

        return task

    async def handle_message(self, cmd: AgentMessageCommand) -> OrchestrationTask:
        """Processes conversational message between roles or with the human."""
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            type="agent_message",
            requested_by=cmd.sender_role,
            target_role=cmd.recipient_role,
            status="completed",
            payload=cmd.model_dump(),
        )
        await self.db.save_task(task)
        await self.events.publish(create_event(
            workflow_run_id=cmd.workflow_run_id,
            event_type="message_sent",
            task_id=task_id,
            role=cmd.sender_role,
            payload={"recipient": cmd.recipient_role, "message": cmd.message},
        ))
        return task

    async def handle_human_intervention(self, cmd: HumanInterventionCommand) -> OrchestrationTask:
        """Injects direct human instruction/inquiry into a role session."""
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            type="human_intervention",
            requested_by="human",
            target_role=cmd.target_role,
            status="queued",
            payload=cmd.model_dump(),
        )
        await self.db.save_task(task)
        await self.events.publish(create_event(
            workflow_run_id=cmd.workflow_run_id,
            event_type="human_intervention_received",
            task_id=task_id,
            role=cmd.target_role,
            payload={"message": cmd.message},
        ))
        return task

    async def handle_control(self, cmd: WorkflowControlCommand) -> None:
        """Handles pause, resume, cancel task/stage, or agent termination."""
        run = await self.db.get_workflow_run(cmd.workflow_run_id)
        if not run:
            return

        if cmd.action == "pause":
            run.status = "paused"
            run.updated_at = utc_now_iso()
            await self.db.save_workflow_run(run)
            await self.events.publish(create_event(
                workflow_run_id=run.run_id,
                event_type="workflow_paused",
                payload={"reason": cmd.reason},
            ))
        elif cmd.action == "resume":
            run.status = "running"
            run.updated_at = utc_now_iso()
            await self.db.save_workflow_run(run)
            await self.events.publish(create_event(
                workflow_run_id=run.run_id,
                event_type="workflow_resumed",
                payload={"reason": cmd.reason},
            ))
        elif cmd.action == "cancel_task" and cmd.target_id:
            task = await self.db.get_task(cmd.target_id)
            if task and task.status in ["queued", "running"]:
                task.status = "cancelled"
                task.completed_at = utc_now_iso()
                task.error = cmd.reason or "Cancelled by control command"
                await self.db.save_task(task)
                await self.events.publish(create_event(
                    workflow_run_id=run.run_id,
                    event_type="task_cancelled",
                    task_id=task.task_id,
                    payload={"reason": cmd.reason},
                ))

    async def record_artifact(
        self,
        workflow_run_id: str,
        artifact_type: ArtifactType,
        path: str,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Artifact:
        """Registers a newly emitted artifact."""
        artifact = Artifact(
            artifact_id=f"art_{uuid.uuid4().hex[:10]}",
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            type=artifact_type,
            path=path,
            description=description,
            created_at=utc_now_iso(),
        )
        await self.db.save_artifact(artifact)
        await self.events.publish(create_event(
            workflow_run_id=workflow_run_id,
            event_type="artifact_registered",
            task_id=task_id,
            payload={"artifact_id": artifact.artifact_id, "path": path, "type": artifact_type.value},
        ))
        return artifact

    async def _transition_stage(self, run: WorkflowRun, new_stage: str) -> None:
        """Transitions workflow run to a new stage."""
        old_stage = run.current_stage
        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="stage_completed",
            stage_id=old_stage,
            payload={"completed_stage": old_stage, "next_stage": new_stage},
        ))

        run.current_stage = new_stage
        run.updated_at = utc_now_iso()
        await self.db.save_workflow_run(run)

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="stage_started",
            stage_id=new_stage,
            payload={"new_stage": new_stage},
        ))

    async def _complete_workflow(self, run: WorkflowRun) -> None:
        """Marks workflow run complete."""
        run.status = "completed"
        run.updated_at = utc_now_iso()
        await self.db.save_workflow_run(run)

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="workflow_completed",
            stage_id=run.current_stage,
            payload={"final_stage": run.current_stage},
        ))

    async def evaluate_task_dependencies(self, task_id: str) -> tuple[bool, List[str]]:
        """Evaluates whether all prerequisite dependencies for task_id are satisfied."""
        prereq_ids = await self.db.get_task_dependencies(task_id)
        if not prereq_ids:
            return (True, [])

        blocking = []
        for pid in prereq_ids:
            prereq = await self.db.get_task(pid)
            if not prereq or (prereq.status != "completed" and prereq.kanban_column != "done"):
                blocking.append(pid)
        return (len(blocking) == 0, blocking)

    async def _check_cycle(self, start_node: str, target_node: str, visited: set) -> bool:
        """DFS to check if target_node is reachable from start_node."""
        if start_node == target_node:
            return True
        visited.add(start_node)
        prereqs = await self.db.get_task_dependencies(start_node)
        for p in prereqs:
            if p not in visited:
                if await self._check_cycle(p, target_node, visited):
                    return True
        return False

    async def handle_task_create(self, cmd: TaskCreateCommand) -> OrchestrationTask:
        """Creates a tracked task with dependency validation and kanban placement."""
        if cmd.idempotency_key:
            existing = await self.db.get_task_by_idempotency(cmd.workflow_run_id, cmd.idempotency_key)
            if existing:
                return existing

        run = await self.db.get_workflow_run(cmd.workflow_run_id)
        if not run:
            raise ValueError(f"Workflow run '{cmd.workflow_run_id}' not found.")

        # Validate initial dependencies
        for dep_id in cmd.dependencies:
            dep_task = await self.db.get_task(dep_id)
            if not dep_task:
                raise ValueError(f"Dependency task '{dep_id}' does not exist.")
            if dep_task.workflow_run_id != cmd.workflow_run_id:
                raise ValueError(f"Dependency task '{dep_id}' belongs to a different workflow run.")

        task_id = f"task_{uuid.uuid4().hex[:10]}"

        # Determine initial kanban column
        initial_column = cmd.kanban_column or "ready"
        if cmd.dependencies:
            incomplete = False
            for dep_id in cmd.dependencies:
                dep_task = await self.db.get_task(dep_id)
                if not dep_task or (dep_task.status != "completed" and dep_task.kanban_column != "done"):
                    incomplete = True
                    break
            if incomplete and initial_column != "backlog":
                initial_column = "blocked"

        task = OrchestrationTask(
            task_id=task_id,
            workflow_run_id=cmd.workflow_run_id,
            stage_id=cmd.stage_id or run.current_stage,
            idempotency_key=cmd.idempotency_key,
            title=cmd.title,
            description=cmd.description,
            type="task_item",
            requested_by=cmd.requested_by,
            target_role=cmd.target_role,
            status="queued",
            kanban_column=initial_column,
            created_at=utc_now_iso(),
            payload=cmd.payload,
        )
        await self.db.save_task(task)

        # Save dependency edges
        for dep_id in cmd.dependencies:
            await self.db.save_task_dependency(TaskDependency(
                task_id=task_id,
                depends_on_task_id=dep_id,
                workflow_run_id=cmd.workflow_run_id,
            ))

        await self.events.publish(create_event(
            workflow_run_id=run.run_id,
            event_type="task_created",
            stage_id=task.stage_id,
            task_id=task.task_id,
            role=task.target_role,
            payload={
                "title": task.title,
                "description": task.description,
                "kanban_column": task.kanban_column,
                "dependencies": cmd.dependencies,
            },
        ))
        return task

    async def handle_task_update(self, cmd: TaskUpdateCommand) -> OrchestrationTask:
        """Updates task metadata and column."""
        task = await self.db.get_task(cmd.task_id)
        if not task:
            raise ValueError(f"Task '{cmd.task_id}' not found.")

        if cmd.title is not None:
            task.title = cmd.title
        if cmd.description is not None:
            task.description = cmd.description
        if cmd.target_role is not None:
            task.target_role = cmd.target_role
        if cmd.kanban_column is not None:
            task.kanban_column = cmd.kanban_column
        if cmd.status is not None:
            task.status = cmd.status
        if cmd.payload is not None:
            task.payload.update(cmd.payload)

        await self.db.save_task(task)
        await self.events.publish(create_event(
            workflow_run_id=task.workflow_run_id,
            event_type="task_updated",
            stage_id=task.stage_id,
            task_id=task.task_id,
            role=task.target_role,
            payload={"kanban_column": task.kanban_column, "status": task.status},
        ))
        return task

    async def handle_task_lifecycle(self, cmd: TaskLifecycleCommand) -> Dict[str, Any]:
        """Applies authoritative lifecycle actions with dependency satisfaction."""
        task = await self.db.get_task(cmd.task_id)
        if not task:
            raise ValueError(f"Task '{cmd.task_id}' not found.")

        if cmd.action == "start":
            satisfied, blocking = await self.evaluate_task_dependencies(task.task_id)
            if not satisfied:
                task.kanban_column = "blocked"
                await self.db.save_task(task)
                await self.events.publish(create_event(
                    workflow_run_id=task.workflow_run_id,
                    event_type="task_blocked",
                    stage_id=task.stage_id,
                    task_id=task.task_id,
                    role=task.target_role,
                    payload={"blocking_tasks": blocking, "reason": "Task has incomplete dependencies"},
                ))
                return {
                    "accepted": False,
                    "status": "blocked",
                    "reason": "Task has incomplete dependencies",
                    "blocking_tasks": blocking,
                }

            task.status = "running"
            task.kanban_column = "in_progress"
            task.started_at = utc_now_iso()
            await self.db.save_task(task)
            await self.events.publish(create_event(
                workflow_run_id=task.workflow_run_id,
                event_type="task_started",
                stage_id=task.stage_id,
                task_id=task.task_id,
                role=task.target_role,
                payload={"title": task.title},
            ))
            return {"accepted": True, "status": "in_progress", "task_id": task.task_id}

        elif cmd.action == "complete":
            task.status = "completed"
            task.kanban_column = "done"
            task.completed_at = utc_now_iso()
            await self.db.save_task(task)
            await self.events.publish(create_event(
                workflow_run_id=task.workflow_run_id,
                event_type="task_completed",
                stage_id=task.stage_id,
                task_id=task.task_id,
                role=task.target_role,
                payload={"title": task.title},
            ))

            # Dynamic dependency promotion for all downstream tasks
            dependents = await self.db.get_task_dependents(task.task_id)
            promoted = []
            for dep_id in dependents:
                dep_task = await self.db.get_task(dep_id)
                if dep_task and dep_task.kanban_column == "blocked":
                    sat, _ = await self.evaluate_task_dependencies(dep_id)
                    if sat:
                        dep_task.kanban_column = "ready"
                        await self.db.save_task(dep_task)
                        promoted.append(dep_id)
                        await self.events.publish(create_event(
                            workflow_run_id=dep_task.workflow_run_id,
                            event_type="task_ready",
                            stage_id=dep_task.stage_id,
                            task_id=dep_task.task_id,
                            role=dep_task.target_role,
                            payload={"reason": f"Prerequisite '{task.task_id}' completed"},
                        ))
            return {"accepted": True, "status": "done", "task_id": task.task_id, "promoted_dependents": promoted}

        elif cmd.action == "block":
            task.kanban_column = "blocked"
            await self.db.save_task(task)
            await self.events.publish(create_event(
                workflow_run_id=task.workflow_run_id,
                event_type="task_blocked",
                stage_id=task.stage_id,
                task_id=task.task_id,
                role=task.target_role,
                payload={"reason": cmd.reason or "Explicitly blocked"},
            ))
            return {"accepted": True, "status": "blocked", "task_id": task.task_id}

        elif cmd.action == "unblock":
            sat, blocking = await self.evaluate_task_dependencies(task.task_id)
            if not sat:
                return {
                    "accepted": False,
                    "status": "blocked",
                    "reason": "Cannot unblock: dependencies still incomplete",
                    "blocking_tasks": blocking,
                }
            task.kanban_column = "ready"
            await self.db.save_task(task)
            await self.events.publish(create_event(
                workflow_run_id=task.workflow_run_id,
                event_type="task_ready",
                stage_id=task.stage_id,
                task_id=task.task_id,
                role=task.target_role,
                payload={"reason": "Unblocked by user or agent"},
            ))
            return {"accepted": True, "status": "ready", "task_id": task.task_id}

        elif cmd.action == "cancel":
            task.status = "cancelled"
            task.completed_at = utc_now_iso()
            task.error = cmd.reason or "Cancelled"
            await self.db.save_task(task)
            await self.events.publish(create_event(
                workflow_run_id=task.workflow_run_id,
                event_type="task_cancelled",
                stage_id=task.stage_id,
                task_id=task.task_id,
                role=task.target_role,
                payload={"reason": cmd.reason},
            ))
            return {"accepted": True, "status": "cancelled", "task_id": task.task_id}

        return {"accepted": False, "error": f"Unknown lifecycle action '{cmd.action}'"}

    async def handle_task_dependency(self, cmd: TaskDependencyCommand) -> Dict[str, Any]:
        """Adds or removes a dependency edge with cycle validation."""
        if cmd.action == "add":
            if cmd.task_id == cmd.depends_on_task_id:
                raise ValueError("A task cannot depend on itself.")

            task = await self.db.get_task(cmd.task_id)
            prereq = await self.db.get_task(cmd.depends_on_task_id)
            if not task:
                raise ValueError(f"Task '{cmd.task_id}' not found.")
            if not prereq:
                raise ValueError(f"Prerequisite task '{cmd.depends_on_task_id}' not found.")
            if task.workflow_run_id != prereq.workflow_run_id:
                raise ValueError("Cross-run task dependencies are not permitted.")

            # Cycle detection
            if await self._check_cycle(cmd.depends_on_task_id, cmd.task_id, set()):
                raise ValueError(f"Adding dependency introduces a circular cycle between '{cmd.task_id}' and '{cmd.depends_on_task_id}'.")

            await self.db.save_task_dependency(TaskDependency(
                task_id=cmd.task_id,
                depends_on_task_id=cmd.depends_on_task_id,
                workflow_run_id=cmd.workflow_run_id,
            ))

            # Re-evaluate task status
            if prereq.status != "completed" and prereq.kanban_column != "done" and task.kanban_column != "backlog":
                task.kanban_column = "blocked"
                await self.db.save_task(task)
                await self.events.publish(create_event(
                    workflow_run_id=task.workflow_run_id,
                    event_type="task_blocked",
                    stage_id=task.stage_id,
                    task_id=task.task_id,
                    role=task.target_role,
                    payload={"blocking_tasks": [cmd.depends_on_task_id], "reason": "New incomplete dependency added"},
                ))

            await self.events.publish(create_event(
                workflow_run_id=cmd.workflow_run_id,
                event_type="task_dependency_added",
                task_id=cmd.task_id,
                payload={"depends_on_task_id": cmd.depends_on_task_id},
            ))
            return {"accepted": True, "task_id": cmd.task_id, "depends_on_task_id": cmd.depends_on_task_id}

        elif cmd.action == "remove":
            await self.db.delete_task_dependency(cmd.task_id, cmd.depends_on_task_id)
            task = await self.db.get_task(cmd.task_id)
            if task and task.kanban_column == "blocked":
                sat, _ = await self.evaluate_task_dependencies(cmd.task_id)
                if sat:
                    task.kanban_column = "ready"
                    await self.db.save_task(task)
                    await self.events.publish(create_event(
                        workflow_run_id=task.workflow_run_id,
                        event_type="task_ready",
                        stage_id=task.stage_id,
                        task_id=task.task_id,
                        role=task.target_role,
                        payload={"reason": f"Prerequisite '{cmd.depends_on_task_id}' removed"},
                    ))
            await self.events.publish(create_event(
                workflow_run_id=cmd.workflow_run_id,
                event_type="task_dependency_removed",
                task_id=cmd.task_id,
                payload={"removed_dependency": cmd.depends_on_task_id},
            ))
            return {"accepted": True}

        return {"accepted": False, "error": f"Unknown dependency action '{cmd.action}'"}

    async def handle_ipc_command(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Routes an inbound IPC method call to the appropriate engine command handler."""
        normalized_method = method.replace(".", "_")
        try:
            if normalized_method == "agents_handoff":
                cmd = HandoffCommand(**params)
                task = await self.handle_handoff(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif normalized_method == "review_request":
                cmd = ReviewRequestCommand(**params)
                task = await self.handle_review_request(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif normalized_method == "review_decision":
                cmd = ReviewDecisionCommand(**params)
                task = await self.handle_review_decision(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif normalized_method == "agents_message":
                cmd = AgentMessageCommand(**params)
                task = await self.handle_message(cmd)
                return {"accepted": True, "task_id": task.task_id}

            elif normalized_method == "workflow_status":
                run_id = params.get("workflow_run_id", "")
                run = await self.db.get_workflow_run(run_id)
                if not run:
                    return {"error": f"Workflow run '{run_id}' not found", "status": "not_found"}
                return {"run_id": run.run_id, "current_stage": run.current_stage, "status": run.status}

            elif normalized_method == "workflow_pause":
                await self.handle_control(WorkflowControlCommand(
                    workflow_run_id=params["workflow_run_id"],
                    action="pause",
                    reason=params.get("reason"),
                ))
                return {"accepted": True, "status": "paused"}

            elif normalized_method == "workflow_resume":
                await self.handle_control(WorkflowControlCommand(
                    workflow_run_id=params["workflow_run_id"],
                    action="resume",
                ))
                return {"accepted": True, "status": "running"}

            # Task CRUD
            elif normalized_method == "task_create":
                cmd = TaskCreateCommand(**params)
                task = await self.handle_task_create(cmd)
                return {
                    "accepted": True,
                    "task_id": task.task_id,
                    "status": task.status,
                    "kanban_column": task.kanban_column,
                }

            elif normalized_method == "task_get":
                task_id = params.get("task_id", "")
                task = await self.db.get_task(task_id)
                if not task:
                    return {"error": f"Task '{task_id}' not found"}
                deps = await self.db.get_task_dependencies(task_id)
                dependents = await self.db.get_task_dependents(task_id)
                return {
                    "accepted": True,
                    "task": task.model_dump(),
                    "dependencies": deps,
                    "dependents": dependents,
                }

            elif normalized_method == "task_list":
                run_id = params.get("workflow_run_id", "")
                col = params.get("kanban_column")
                tasks = await self.db.list_tasks(run_id, kanban_column=col)
                role_filter = params.get("target_role")
                if role_filter:
                    tasks = [t for t in tasks if t.target_role == role_filter]
                return {"accepted": True, "tasks": [t.model_dump() for t in tasks]}

            elif normalized_method == "task_update":
                cmd = TaskUpdateCommand(**params)
                task = await self.handle_task_update(cmd)
                return {"accepted": True, "task_id": task.task_id, "kanban_column": task.kanban_column}

            elif normalized_method == "task_delete":
                task_id = params.get("task_id", "")
                ok = await self.db.delete_task(task_id)
                return {"accepted": ok, "task_id": task_id}

            # Task Lifecycle
            elif normalized_method in ["task_start", "task_complete", "task_cancel", "task_block", "task_unblock"]:
                action = normalized_method.replace("task_", "")
                cmd = TaskLifecycleCommand(
                    workflow_run_id=params.get("workflow_run_id", ""),
                    task_id=params["task_id"],
                    action=action,
                    reason=params.get("reason"),
                )
                return await self.handle_task_lifecycle(cmd)

            # Task Dependencies
            elif normalized_method == "task_add_dependency":
                cmd = TaskDependencyCommand(
                    workflow_run_id=params["workflow_run_id"],
                    task_id=params["task_id"],
                    depends_on_task_id=params["depends_on_task_id"],
                    action="add",
                )
                return await self.handle_task_dependency(cmd)

            elif normalized_method == "task_remove_dependency":
                cmd = TaskDependencyCommand(
                    workflow_run_id=params["workflow_run_id"],
                    task_id=params["task_id"],
                    depends_on_task_id=params["depends_on_task_id"],
                    action="remove",
                )
                return await self.handle_task_dependency(cmd)

            elif normalized_method == "task_dependencies":
                task_id = params.get("task_id", "")
                deps = await self.db.get_task_dependencies(task_id)
                return {"accepted": True, "task_id": task_id, "dependencies": deps}

            elif normalized_method == "task_dependents":
                task_id = params.get("task_id", "")
                dependents = await self.db.get_task_dependents(task_id)
                return {"accepted": True, "task_id": task_id, "dependents": dependents}

            elif normalized_method == "task_ready":
                task_id = params.get("task_id", "")
                sat, blocking = await self.evaluate_task_dependencies(task_id)
                return {"accepted": True, "task_id": task_id, "is_ready": sat, "blocking_tasks": blocking}

            else:
                return {"error": f"Unknown method '{method}'", "accepted": False}
        except Exception as e:
            return {"error": str(e), "accepted": False}
