"""Base agent adapter interface."""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from orchestrator.models import AgentEvent, RoleConfig


class BaseAgentAdapter(ABC):
    """Abstract interface for CLI agent adapters."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.active_session_id: Optional[str] = None
        self._current_process = None

    @abstractmethod
    async def execute_turn(
        self,
        prompt: str,
        role: RoleConfig,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Executes a single turn with the underlying agent CLI, streaming events."""
        pass

    async def interrupt(self) -> None:
        """Interrupts any currently running CLI process."""
        if self._current_process and self._current_process.returncode is None:
            try:
                self._current_process.terminate()
                await self._current_process.wait()
            except Exception:
                pass
            finally:
                self._current_process = None

    def get_active_session_id(self) -> Optional[str]:
        """Returns the most recent session or thread ID for this adapter."""
        return self.active_session_id
