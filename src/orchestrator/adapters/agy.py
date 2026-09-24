"""Agy CLI agent adapter supporting stream-json output and conversation resumption."""

import asyncio
import json
import os
import shutil
from typing import Any, AsyncIterator, Dict, List, Optional
from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.models import ReasoningEffort


class AgyAdapter(BaseAgentAdapter):
    """Adapter for driving the agy CLI in headless mode."""

    def __init__(self, binary_path: Optional[str] = None):
        super().__init__(agent_name="agy")
        self.binary_path = binary_path or shutil.which("agy") or "/home/tantan/.local/bin/agy"

    def build_command(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Constructs CLI arguments for agy -p."""
        cmd = [self.binary_path, "-p"]
        effective_session = session_id or self.active_session_id
        if effective_session:
            cmd.extend(["--conversation", effective_session])

        cmd.extend([
            "--output-format", "stream-json",
            "--model", model,
            "--effort", reasoning.value,
            "--dangerously-skip-permissions",
            "--add-dir", os.path.abspath(workspace_root),
            prompt,
        ])
        return cmd

    async def execute_turn(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Spawns agy -p and yields structured activity items."""
        cmd = self.build_command(prompt, model, reasoning, workspace_root, session_id)

        try:
            self._current_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_root,
            )
        except Exception as e:
            yield {"type": "error", "error": f"Failed to launch agy CLI: {e}"}
            return

        yield {"type": "status", "status": f"Agy spawned with model {model} (effort: {reasoning.value})"}

        while True:
            line = await self._current_process.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            event = self._parse_line(decoded)
            if event:
                yield event

        return_code = await self._current_process.wait()
        stderr_bytes = await self._current_process.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if return_code != 0 and stderr_text:
            yield {"type": "error", "error": f"Agy exited with code {return_code}: {stderr_text}"}
        else:
            yield {
                "type": "complete",
                "exit_code": return_code,
                "session_id": self.active_session_id,
            }

    def _parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Maps a stream-json line to the activity taxonomy."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return {"type": "agent_message", "content": line}

        if not isinstance(data, dict):
            return {"type": "agent_message", "content": str(data)}

        # Capture conversation ID
        conv_id = data.get("conversation_id") or data.get("session_id")
        if conv_id:
            self.active_session_id = str(conv_id)

        ev_type = str(data.get("type", "")).lower()

        # Tool calls
        if ev_type == "tool_call":
            return {
                "type": "tool_call",
                "name": data.get("name") or data.get("tool_name") or "unknown_tool",
                "args": data.get("args") or data.get("parameters") or {},
            }

        # Tool results
        if ev_type == "tool_result":
            return {
                "type": "tool_result",
                "output": str(data.get("output") or data.get("result") or ""),
            }

        # Status / progress
        if ev_type in ["status", "progress"]:
            return {
                "type": "status",
                "status": data.get("status") or data.get("message") or str(data),
            }

        # Content chunks
        if "content" in data and isinstance(data["content"], str):
            return {"type": "agent_message", "content": data["content"]}

        if "text" in data and isinstance(data["text"], str):
            return {"type": "agent_message", "content": data["text"]}

        return None
