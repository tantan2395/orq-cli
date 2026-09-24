"""Central Orchestration Engine managing state machine, events, and task lifecycle."""

import uuid
from typing import Any, Dict, List, Optional

from orchestrator.commands import (
    AgentMessageCommand,
    HandoffCommand,
    HumanInterventionCommand,
    ReviewDecisionCommand,
    ReviewRequestCommand,
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
    ReviewDecision,
    StageConfig,
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
        """Initializes database schema and storage."""
        await self.db.initialize()

    async def create_workflow_run(
        self,
        workflow_id: Optional[str] = None,
        workspace_root: Optional[str] = None,
    ) -> WorkflowRun:
        """Instantiates a new workflow run from a registered definition."""
        wf_id = workflow_id or self.config.active_workflow
        if wf_id not in self.config.workflows:
            raise ValueError(f"Workflow '{wf_id}' not found in configuration.")

        definition: WorkflowDefinition = self.config.workflows[wf_id]
        ws_root = workspace_root or self.config.workspace.workspace_root

        run_id = f"run_{uuid.uuid4().hex[:10]}"
        run = WorkflowRun(
            run_id=run_id,
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
            status="completed",
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

    async def handle_ipc_command(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Routes an inbound IPC method call to the appropriate engine command handler."""
        try:
            if method == "agents_handoff":
                cmd = HandoffCommand(**params)
                task = await self.handle_handoff(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif method == "review_request":
                cmd = ReviewRequestCommand(**params)
                task = await self.handle_review_request(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif method == "review_decision":
                cmd = ReviewDecisionCommand(**params)
                task = await self.handle_review_decision(cmd)
                return {"accepted": True, "task_id": task.task_id, "status": task.status}

            elif method == "agents_message":
                cmd = AgentMessageCommand(**params)
                task = await self.handle_message(cmd)
                return {"accepted": True, "task_id": task.task_id}

            elif method == "workflow_status":
                run_id = params.get("workflow_run_id", "")
                run = await self.db.get_workflow_run(run_id)
                if not run:
                    return {"error": f"Workflow run '{run_id}' not found", "status": "not_found"}
                return {"run_id": run.run_id, "current_stage": run.current_stage, "status": run.status}

            elif method == "workflow_pause":
                await self.handle_control(WorkflowControlCommand(
                    workflow_run_id=params["workflow_run_id"],
                    action="pause",
                    reason=params.get("reason"),
                ))
                return {"accepted": True, "status": "paused"}

            elif method == "workflow_resume":
                await self.handle_control(WorkflowControlCommand(
                    workflow_run_id=params["workflow_run_id"],
                    action="resume",
                ))
                return {"accepted": True, "status": "running"}

            else:
                return {"error": f"Unknown method '{method}'", "accepted": False}
        except Exception as e:
            return {"error": str(e), "accepted": False}
