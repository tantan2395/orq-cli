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

        top_type = str(data.get("type", "")).lower()
        payload = data.get("payload", {}) if isinstance(data.get("payload"), dict) else {}

        # 1. Capture native session / thread ID
        sess = (
            data.get("thread_id")
            or data.get("session_id")
            or payload.get("id")
            or payload.get("session_id")
            or payload.get("thread_id")
            or data.get("id")
        )
        if sess and ("thread" in top_type or "session" in top_type):
            self.active_session_id = str(sess)

        # 2. Native codex exec stdout stream & nested event_msg
        effective_type = (top_type + " " + str(payload.get("type", ""))).lower()
        item = data.get("item") or payload.get("item") or {}
        if isinstance(item, dict) and item:
            itype = str(item.get("type", "")).lower()
            if itype in ["agent_message", "agentmessage"]:
                text = item.get("text")
                if text:
                    return {"type": "agent_message", "content": str(text)}
                contents = item.get("content", [])
                if isinstance(contents, list):
                    texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("text")]
                    if texts:
                        return {"type": "agent_message", "content": "\n".join(texts)}
            elif itype in ["mcp_tool_call", "mcptoolcall"]:
                server = item.get("server") or "mcp"
                tool = item.get("tool") or item.get("name") or "tool"
                res = item.get("result")
                if "completed" in effective_type and res is not None:
                    res_str = ""
                    if isinstance(res, dict) and "content" in res and isinstance(res["content"], list):
                        res_str = "\n".join(c.get("text", "") for c in res["content"] if isinstance(c, dict) and c.get("text"))
                    if not res_str:
                        res_str = str(res)
                    return {"type": "tool_result", "output": res_str}
                return {
                    "type": "tool_call",
                    "name": f"{server}:{tool}",
                    "args": item.get("arguments") or item.get("args") or {},
                }
            elif itype in ["command_execution", "commandexecution"]:
                cmd = item.get("command")
                out = item.get("aggregated_output")
                if "completed" in effective_type:
                    if out:
                        return {"type": "status", "status": f"Executed command `{cmd}`: {out.strip()}"}
                    elif cmd:
                        return {"type": "status", "status": f"Executed command `{cmd}`"}
                elif "started" in effective_type and cmd:
                    return {"type": "status", "status": f"Running command `{cmd}`..."}
                elif cmd:
                    return {"type": "status", "status": f"Executed command: {cmd}"}

        # 3. Handle Codex CLI nested event_msg structure
        if top_type == "event_msg":
            p_type = str(payload.get("type", "")).lower()
            if p_type == "task_complete":
                last_msg = payload.get("last_agent_message")
                if last_msg:
                    return {"type": "agent_message", "content": str(last_msg)}

        # 4. Handle response_item
        if top_type == "response_item":
            p_type = str(payload.get("type", "")).lower()
            if p_type == "message" and payload.get("role") == "assistant":
                contents = payload.get("content", [])
                texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("text")]
                if texts:
                    return {"type": "agent_message", "content": "\n".join(texts)}
            elif "tool_call" in p_type:
                return {
                    "type": "tool_call",
                    "name": payload.get("name") or "unknown_tool",
                    "args": payload.get("input") or payload.get("args") or {},
                }

        # 5. Standard / legacy flat event structures
        if "tool_call" in top_type or "tool_use" in top_type:
            return {
                "type": "tool_call",
                "name": data.get("name") or data.get("tool") or "unknown_tool",
                "args": data.get("args") or data.get("input") or {},
            }

        if "tool_result" in top_type:
            return {
                "type": "tool_result",
                "output": str(data.get("output") or data.get("content") or ""),
            }

        if "status" in top_type or "progress" in top_type:
            return {
                "type": "status",
                "status": data.get("status") or data.get("message") or str(data),
            }

        # Text chunks / deltas
        if "delta" in data and isinstance(data["delta"], dict):
            text = data["delta"].get("text") or data["delta"].get("content") or ""
            if text:
                return {"type": "agent_message", "content": text}

        if "content" in data and isinstance(data["content"], str):
            return {"type": "agent_message", "content": data["content"]}

        if "text" in data and isinstance(data["text"], str):
            return {"type": "agent_message", "content": data["text"]}

        return None
