"""Agy CLI agent adapter."""

import asyncio
import json
import os
import shutil
from typing import AsyncIterator, List, Optional

from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.models import AgentEvent, AgentEventType, RoleConfig


class AgyAdapter(BaseAgentAdapter):
    """Adapter for executing turns via the agy CLI."""

    def __init__(self, binary_path: Optional[str] = None):
        super().__init__(agent_name="agy")
        self.binary_path = binary_path or shutil.which("agy") or "/home/tantan/.local/bin/agy"

    def build_command(
        self,
        prompt: str,
        role: RoleConfig,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Constructs the CLI invocation args for agy print mode."""
        cmd = [self.binary_path, "-p"]
        effective_session = session_id or role.session_id or self.active_session_id

        if effective_session:
            cmd.extend(["--conversation", effective_session])

        cmd.extend([
            "--output-format", "stream-json",
            "--model", role.model,
            "--effort", role.reasoning.value,
            "--dangerously-skip-permissions",
            "--add-dir", os.path.abspath(workspace_root),
        ])

        full_prompt = prompt
        if role.system_prompt:
            full_prompt = f"[ROLE SYSTEM INSTRUCTIONS: {role.name}]\n{role.system_prompt}\n\n[TASK]:\n{prompt}"

        cmd.append(full_prompt)
        return cmd

    async def execute_turn(
        self,
        prompt: str,
        role: RoleConfig,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Runs agy -p non-interactively and yields standardized AgentEvents."""
        cmd = self.build_command(prompt, role, workspace_root, session_id)

        try:
            self._current_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_root,
            )
        except Exception as e:
            yield AgentEvent(
                type=AgentEventType.ERROR,
                content=f"Failed to launch agy CLI: {e}",
            )
            return

        final_content = []

        async def read_stream():
            while True:
                line = await self._current_process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                event = self._parse_agy_line(decoded)
                if event:
                    if event.type == AgentEventType.CHUNK:
                        final_content.append(event.content)
                    yield event

        async for event in read_stream():
            yield event

        return_code = await self._current_process.wait()
        stderr_bytes = await self._current_process.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if return_code != 0 and stderr_text:
            yield AgentEvent(
                type=AgentEventType.ERROR,
                content=f"Agy process exited with code {return_code}: {stderr_text}",
            )
        else:
            yield AgentEvent(
                type=AgentEventType.COMPLETE,
                content="".join(final_content),
                metadata={"exit_code": return_code, "session_id": self.active_session_id},
            )

    def _parse_agy_line(self, line: str) -> Optional[AgentEvent]:
        """Parses a single stdout line from agy --output-format stream-json."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return AgentEvent(type=AgentEventType.CHUNK, content=line + "\n")

        if not isinstance(data, dict):
            return AgentEvent(type=AgentEventType.CHUNK, content=str(data) + "\n")

        # Capture conversation ID
        conv_id = data.get("conversation_id") or data.get("session_id")
        if conv_id:
            self.active_session_id = str(conv_id)

        event_type = data.get("type", "")

        # Thought events
        if event_type == "thought" or "thinking" in event_type:
            content = data.get("content") or data.get("thought") or ""
            return AgentEvent(type=AgentEventType.THOUGHT, content=str(content), metadata=data)

        # Tool calls
        if event_type == "tool_call":
            tool_name = data.get("name") or data.get("tool_name") or ""
            return AgentEvent(
                type=AgentEventType.TOOL_CALL,
                content=f"Tool Call: {tool_name}",
                metadata={"tool_name": tool_name, "args": data.get("args") or data.get("parameters")},
            )

        # Tool results
        if event_type == "tool_result":
            return AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                content=str(data.get("output") or data.get("result") or ""),
                metadata=data,
            )

        # Content chunks
        if event_type == "chunk" or "content" in data:
            content = data.get("content") or ""
            if isinstance(content, str):
                return AgentEvent(type=AgentEventType.CHUNK, content=content, metadata=data)

        if "text" in data and isinstance(data["text"], str):
            return AgentEvent(type=AgentEventType.CHUNK, content=data["text"], metadata=data)

        return AgentEvent(type=AgentEventType.CHUNK, content="", metadata=data)
