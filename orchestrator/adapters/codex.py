"""Codex CLI agent adapter."""

import asyncio
import json
import os
import shutil
from typing import AsyncIterator, List, Optional

from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.models import AgentEvent, AgentEventType, RoleConfig


class CodexAdapter(BaseAgentAdapter):
    """Adapter for executing turns via the codex CLI."""

    def __init__(self, binary_path: Optional[str] = None):
        super().__init__(agent_name="codex")
        self.binary_path = binary_path or shutil.which("codex") or "/home/tantan/.npm-global/bin/codex"

    def build_command(
        self,
        prompt: str,
        role: RoleConfig,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Constructs the CLI invocation args for codex exec."""
        cmd = [self.binary_path, "exec"]
        effective_session = session_id or role.session_id or self.active_session_id

        if effective_session:
            cmd.extend(["resume", effective_session])

        cmd.extend([
            "--json",
            "-m", role.model,
            "-c", f'model_reasoning_effort="{role.reasoning.value}"',
            "-C", os.path.abspath(workspace_root),
            "--dangerously-bypass-approvals-and-sandbox",
        ])

        # Prepend role system prompt overlay if defined
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
        """Runs codex exec non-interactively and yields standardized AgentEvents."""
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
                content=f"Failed to launch codex CLI: {e}",
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

                event = self._parse_codex_line(decoded)
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
                content=f"Codex process exited with code {return_code}: {stderr_text}",
            )
        else:
            yield AgentEvent(
                type=AgentEventType.COMPLETE,
                content="".join(final_content),
                metadata={"exit_code": return_code, "session_id": self.active_session_id},
            )

    def _parse_codex_line(self, line: str) -> Optional[AgentEvent]:
        """Parses a single stdout line from codex exec --json."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Fallback for non-JSON lines
            return AgentEvent(type=AgentEventType.CHUNK, content=line + "\n")

        if not isinstance(data, dict):
            return AgentEvent(type=AgentEventType.CHUNK, content=str(data) + "\n")

        # Capture session or thread IDs
        session_candidate = data.get("thread_id") or data.get("session_id") or data.get("id")
        if session_candidate and ("thread" in str(data.get("type", "")) or "session" in str(data.get("type", ""))):
            self.active_session_id = str(session_candidate)

        event_type = data.get("type", "")

        # Thoughts / Reasoning events
        if "thought" in event_type or "reasoning" in event_type:
            content = data.get("thought") or data.get("content") or data.get("text") or ""
            return AgentEvent(type=AgentEventType.THOUGHT, content=str(content), metadata=data)

        # Tool calls
        if "tool_call" in event_type or "tool_use" in event_type:
            tool_name = data.get("name") or data.get("tool") or ""
            return AgentEvent(
                type=AgentEventType.TOOL_CALL,
                content=f"Tool Call: {tool_name}",
                metadata={"tool_name": tool_name, "args": data.get("args") or data.get("input")},
            )

        # Tool results
        if "tool_result" in event_type:
            return AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                content=str(data.get("output") or data.get("content") or ""),
                metadata=data,
            )

        # Text chunks / deltas
        if "delta" in data:
            delta = data["delta"]
            text = delta.get("text") or delta.get("content") or ""
            if text:
                return AgentEvent(type=AgentEventType.CHUNK, content=text, metadata=data)

        if "content" in data and isinstance(data["content"], str):
            return AgentEvent(type=AgentEventType.CHUNK, content=data["content"], metadata=data)

        if "text" in data and isinstance(data["text"], str):
            return AgentEvent(type=AgentEventType.CHUNK, content=data["text"], metadata=data)

        # Generic message or event
        return AgentEvent(type=AgentEventType.CHUNK, content="", metadata=data)
