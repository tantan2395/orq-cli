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
        self._text_buffers: Dict[int, str] = {}
        self._emitted_message: bool = False

    def build_command(
        self,
        prompt: str,
        model: str,
        reasoning: ReasoningEffort,
        workspace_root: str,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Constructs CLI arguments for agy -p."""
        cmd = [self.binary_path]
        effective_session = session_id or self.active_session_id
        if effective_session:
            cmd.extend(["--conversation", effective_session])

        cmd.extend([
            "--output-format", "stream-json",
            "--model", model,
            "--effort", reasoning.value,
            "--dangerously-skip-permissions",
            "--add-dir", os.path.abspath(workspace_root),
            "-p", prompt,
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
        self._text_buffers.clear()
        self._emitted_message = False
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

        # Capture conversation ID from any known location
        conv_id = (
            data.get("conversation_id")
            or data.get("session_id")
            or (data.get("init", {}).get("conversation_id") if isinstance(data.get("init"), dict) else None)
            or (data.get("step_update", {}).get("conversation_id") if isinstance(data.get("step_update"), dict) else None)
            or (data.get("result", {}).get("conversation_id") if isinstance(data.get("result"), dict) else None)
        )
        if conv_id:
            self.active_session_id = str(conv_id)

        # 1. Native agy stream-json format
        event_name = data.get("event")
        if event_name == "step_update":
            su = data.get("step_update") or {}
            step_type = su.get("step_type")
            state = su.get("state")
            step_idx = su.get("step_index", 0)

            if step_type == "tool":
                tool_info = su.get("tool_info") or {}
                tool_name = su.get("tool_name") or tool_info.get("name") or "unknown_tool"
                if state == "ACTIVE":
                    return {
                        "type": "tool_call",
                        "name": tool_name,
                        "args": tool_info.get("parameters") or {},
                    }
                elif state == "DONE":
                    output = tool_info.get("output", "")
                    return {
                        "type": "tool_result",
                        "output": str(output),
                    }

            elif step_type == "agent_response":
                delta = su.get("text_delta") or ""
                self._text_buffers[step_idx] = self._text_buffers.get(step_idx, "") + delta
                if state == "DONE":
                    content = self._text_buffers.pop(step_idx, "")
                    if content.strip():
                        self._emitted_message = True
                        return {"type": "agent_message", "content": content}
                return None

        elif event_name == "result":
            res = data.get("result") or {}
            resp = res.get("response")
            if not self._emitted_message and resp and str(resp).strip():
                self._emitted_message = True
                return {"type": "agent_message", "content": str(resp)}
            return None

        # 2. Fallback for generic format
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
