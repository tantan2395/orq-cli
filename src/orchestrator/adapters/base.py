"""Base agent adapter interface with cooperative interruption and forceful termination."""

import asyncio
import os
import signal
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, Optional
from orchestrator.models import ReasoningEffort


class BaseAgentAdapter(ABC):
    """Abstract base class for all CLI agent backends."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.active_session_id: Optional[str] = None
        self._current_process: Optional[asyncio.subprocess.Process] = None

    @abstractmethod
    async def execute_turn(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Executes a single turn with the CLI, yielding activity dictionaries."""
        pass

    async def interrupt(self) -> None:
        """Cooperative cancellation: sends SIGINT to the process."""
        if self._current_process and self._current_process.returncode is None:
            try:
                self._current_process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._current_process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                await self.terminate()

    async def terminate(self) -> None:
        """Forceful cancellation: sends SIGTERM, then SIGKILL if still running."""
        if self._current_process and self._current_process.returncode is None:
            try:
                self._current_process.terminate()
                await asyncio.wait_for(self._current_process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self._current_process.kill()
                    await self._current_process.wait()
                except Exception:
                    pass
            finally:
                self._current_process = None

    def get_active_session_id(self) -> Optional[str]:
        """Returns the native CLI session ID captured during execution."""
        return self.active_session_id
