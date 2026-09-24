"""Adapter registry for dynamically instantiating agent CLI adapters."""

from typing import Dict, Optional

from orchestrator.adapters.agy import AgyAdapter
from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.adapters.codex import CodexAdapter


class AdapterRegistry:
    """Maintains and provides agent adapter instances."""

    def __init__(self):
        self._adapters: Dict[str, BaseAgentAdapter] = {
            "codex": CodexAdapter(),
            "agy": AgyAdapter(),
        }

    def register(self, agent_name: str, adapter: BaseAgentAdapter) -> None:
        """Registers a custom or new agent adapter."""
        self._adapters[agent_name.lower()] = adapter

    def get(self, agent_name: str) -> BaseAgentAdapter:
        """Retrieves an adapter for the given agent CLI name."""
        name = agent_name.lower()
        if name not in self._adapters:
            raise ValueError(f"No adapter registered for agent '{agent_name}'. Available: {list(self._adapters.keys())}")
        return self._adapters[name]

    def has(self, agent_name: str) -> bool:
        """Checks if an agent adapter is registered."""
        return agent_name.lower() in self._adapters
