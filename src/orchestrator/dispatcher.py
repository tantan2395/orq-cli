"""Dispatcher layer resolving Role -> ModelProfile -> Agent -> Adapter."""

from typing import Any, AsyncIterator, Dict, Optional, Tuple
from orchestrator.adapters.agy import AgyAdapter
from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.adapters.codex import CodexAdapter
from orchestrator.config import OrchestratorConfig
from orchestrator.events import EventBus, create_event
from orchestrator.models import AgentTurnContext, ModelProfile, RoleConfig
from orchestrator.session_manager import SessionManager


class Dispatcher:
    """Resolves execution parameters and dispatches turns to CLI adapters."""

    def __init__(
        self,
        config: OrchestratorConfig,
        session_manager: SessionManager,
        event_bus: EventBus,
        adapters: Optional[Dict[str, BaseAgentAdapter]] = None,
    ):
        self.config = config
        self.session_manager = session_manager
        self.events = event_bus
        self._adapters: Dict[str, BaseAgentAdapter] = adapters or {
            "codex": CodexAdapter(),
            "agy": AgyAdapter(),
        }

    def register_adapter(self, agent_name: str, adapter: BaseAgentAdapter) -> None:
        """Registers a custom agent adapter."""
        self._adapters[agent_name.lower()] = adapter

    def resolve(self, role_name: str) -> Tuple[RoleConfig, ModelProfile, BaseAgentAdapter]:
        """Resolves ROLE -> RoleConfig -> ModelProfile -> Adapter."""
        if role_name not in self.config.roles:
            raise ValueError(f"Role '{role_name}' is not configured.")

        role_cfg: RoleConfig = self.config.roles[role_name]

        if role_cfg.profile not in self.config.model_profiles:
            raise ValueError(f"Model profile '{role_cfg.profile}' not found for role '{role_name}'.")

        profile: ModelProfile = self.config.model_profiles[role_cfg.profile]

        agent_key = role_cfg.agent.lower()
        if agent_key not in self._adapters:
            raise ValueError(f"No adapter available for agent '{role_cfg.agent}'.")

        adapter = self._adapters[agent_key]
        return role_cfg, profile, adapter

    async def execute_turn(
        self,
        turn_context: AgentTurnContext,
        effective_prompt: str,
        workspace_root: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Executes a single agent turn with full event streaming and session normalization."""
        role_cfg, profile, adapter = self.resolve(turn_context.role)

        session = await self.session_manager.get_or_create_session(
            workflow_run_id=turn_context.workflow_run_id,
            role=turn_context.role,
            agent_name=role_cfg.agent,
            workspace_root=workspace_root,
            initial_native_id=role_cfg.seed_session_id,
        )

        await self.events.publish(create_event(
            workflow_run_id=turn_context.workflow_run_id,
            event_type="agent_turn_started",
            stage_id=turn_context.stage_id,
            turn_id=turn_context.turn_id,
            session_id=session.id,
            role=turn_context.role,
            payload={
                "agent": role_cfg.agent,
                "model": profile.model,
                "reasoning": profile.reasoning.value,
                "task": turn_context.bounded_task,
            },
        ))

        await self.session_manager.set_status(turn_context.workflow_run_id, turn_context.role, "running")

        turn_failed = False
        error_msg = None

        try:
            async for activity in adapter.execute_turn(
                prompt=effective_prompt,
                model=profile.model,
                reasoning=profile.reasoning,
                workspace_root=workspace_root,
                session_id=session.native_session_id,
            ):
                # Update native session ID if newly discovered
                current_native_id = adapter.get_active_session_id()
                if current_native_id and current_native_id != session.native_session_id:
                    session = await self.session_manager.update_native_id(
                        turn_context.workflow_run_id, turn_context.role, current_native_id
                    ) or session

                # Publish activity event
                await self.events.publish(create_event(
                    workflow_run_id=turn_context.workflow_run_id,
                    event_type="agent_activity",
                    stage_id=turn_context.stage_id,
                    turn_id=turn_context.turn_id,
                    session_id=session.id,
                    role=turn_context.role,
                    payload=activity,
                ))

                if activity.get("type") == "error":
                    turn_failed = True
                    error_msg = activity.get("error")

                yield activity
        except Exception as e:
            turn_failed = True
            error_msg = str(e)
            yield {"type": "error", "error": error_msg}

        status_after = "idle" if not turn_failed else "failed"
        await self.session_manager.set_status(turn_context.workflow_run_id, turn_context.role, status_after)

        if turn_failed:
            await self.events.publish(create_event(
                workflow_run_id=turn_context.workflow_run_id,
                event_type="agent_turn_failed",
                stage_id=turn_context.stage_id,
                turn_id=turn_context.turn_id,
                session_id=session.id,
                role=turn_context.role,
                payload={"error": error_msg},
            ))
        else:
            await self.events.publish(create_event(
                workflow_run_id=turn_context.workflow_run_id,
                event_type="agent_turn_completed",
                stage_id=turn_context.stage_id,
                turn_id=turn_context.turn_id,
                session_id=session.id,
                role=turn_context.role,
                payload={"native_session_id": session.native_session_id},
            ))
