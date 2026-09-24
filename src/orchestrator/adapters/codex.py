"""Codex CLI agent adapter supporting streaming NDJSON output and session resumption."""

import asyncio
import json
import os
import shutil
from typing import Any, AsyncIterator, Dict, List, Optional
from orchestrator.adapters.base import BaseAgentAdapter
from orchestrator.models import ReasoningEffort


class CodexAdapter(BaseAgentAdapter):
    """Adapter for driving the codex CLI in headless mode."""

    def __init__(self, binary_path: Optional[str] = None):
        super().__init__(agent_name="codex")
        self.binary_path = binary_path or shutil.which("codex") or "/home/tantan/.npm-global/bin/codex"

    def build_command(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Constructs CLI arguments for codex exec."""
        cmd = [
            self.binary_path,
            "-C", os.path.abspath(workspace_root),
            "exec",
        ]
        effective_session = session_id or self.active_session_id
        if effective_session:
            cmd.extend(["resume", effective_session])

        cmd.extend([
            "--json",
            "-m", model,
            "-c", f'model_reasoning_effort="{reasoning.value}"',
            "--dangerously-bypass-approvals-and-sandbox",
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
        """Spawns codex exec and yields structured activity items."""
        cmd = self.build_command(prompt, model, reasoning, workspace_root, session_id)

        try:
            self._current_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_root,
            )
        except Exception as e:
            yield {"type": "error", "error": f"Failed to launch codex CLI: {e}"}
            return

        yield {"type": "status", "status": f"Codex spawned with model {model} (effort: {reasoning.value})"}

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
            yield {"type": "error", "error": f"Codex exited with code {return_code}: {stderr_text}"}
        else:
            yield {
                "type": "complete",
                "exit_code": return_code,
                "session_id": self.active_session_id,
            }

    def _parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Maps an NDJSON line to the standardized activity taxonomy."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return {"type": "agent_message", "content": line}

        if not isinstance(data, dict):
            return {"type": "agent_message", "content": str(data)}

        # Capture native session / thread ID
        sess = data.get("thread_id") or data.get("session_id") or data.get("id")
        if sess and ("thread" in str(data.get("type", "")) or "session" in str(data.get("type", ""))):
            self.active_session_id = str(sess)

        ev_type = str(data.get("type", "")).lower()

        # Tool calls
        if "tool_call" in ev_type or "tool_use" in ev_type:
            return {
                "type": "tool_call",
                "name": data.get("name") or data.get("tool") or "unknown_tool",
                "args": data.get("args") or data.get("input") or {},
            }

        # Tool results
        if "tool_result" in ev_type:
            return {
                "type": "tool_result",
                "output": str(data.get("output") or data.get("content") or ""),
            }

        # Status / Progress
        if "status" in ev_type or "progress" in ev_type:
            return {
                "type": "status",
                "status": data.get("status") or data.get("message") or str(data),
            }

        # Text chunks / messages
        if "delta" in data and isinstance(data["delta"], dict):
            text = data["delta"].get("text") or data["delta"].get("content") or ""
            if text:
                return {"type": "agent_message", "content": text}

        if "content" in data and isinstance(data["content"], str):
            return {"type": "agent_message", "content": data["content"]}

        if "text" in data and isinstance(data["text"], str):
            return {"type": "agent_message", "content": data["text"]}

        return None
