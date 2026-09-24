"""Logical Orchestrator MCP server and tool handler definitions."""

import json
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from orchestrator.mcp.ipc import IPCClient


def create_mcp_server(ipc_client: Optional[IPCClient] = None) -> MCPServer:
    """Creates and configures the Orchestrator MCP server."""
    client = ipc_client or IPCClient()
    server = MCPServer(
        name="orchestrator",
        instructions=(
            "Orchestrator MCP provides multi-agent coordination. "
            "Use agents_handoff to delegate tasks, review_request to submit work for review, "
            "and review_decision to evaluate submitted work."
        ),
    )

    @server.tool(
        name="agents_handoff",
        description="Formally hand off task execution to another role in the workflow.",
    )
    async def agents_handoff(
        workflow_run_id: str,
        target_role: str,
        task: str,
        requested_by: str = "agent",
        context: str = "",
        constraints: Optional[List[str]] = None,
        acceptance_criteria: Optional[List[str]] = None,
        artifacts: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        payload = {
            "workflow_run_id": workflow_run_id,
            "target_role": target_role,
            "requested_by": requested_by,
            "task": task,
            "context": context,
            "constraints": constraints or [],
            "acceptance_criteria": acceptance_criteria or [],
            "artifacts": artifacts or [],
            "idempotency_key": idempotency_key,
        }
        res = await client.call("agents_handoff", payload)
        return json.dumps(res)

    @server.tool(
        name="review_request",
        description="Submit implemented work, test evidence, and diffs for independent review.",
    )
    async def review_request(
        workflow_run_id: str,
        summary: str,
        requested_by: str = "developer",
        target_role: str = "code_reviewer",
        diff_or_patch: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        retained_gates: Optional[List[str]] = None,
        artifacts: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        payload = {
            "workflow_run_id": workflow_run_id,
            "summary": summary,
            "requested_by": requested_by,
            "target_role": target_role,
            "diff_or_patch": diff_or_patch,
            "evidence": evidence or {},
            "retained_gates": retained_gates or [],
            "artifacts": artifacts or [],
            "idempotency_key": idempotency_key,
        }
        res = await client.call("review_request", payload)
        return json.dumps(res)

    @server.tool(
        name="review_decision",
        description="Submit formal evaluation verdict (approved, changes_requested, blocked) and findings.",
    )
    async def review_decision(
        workflow_run_id: str,
        reviewer_role: str,
        decision: str,
        summary: str,
        findings: Optional[List[str]] = None,
        evidence: Optional[List[str]] = None,
        retained_gates: Optional[List[str]] = None,
        artifacts: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        payload = {
            "workflow_run_id": workflow_run_id,
            "reviewer_role": reviewer_role,
            "decision": decision,
            "summary": summary,
            "findings": findings or [],
            "evidence": evidence or [],
            "retained_gates": retained_gates or [],
            "artifacts": artifacts or [],
            "idempotency_key": idempotency_key,
        }
        res = await client.call("review_decision", payload)
        return json.dumps(res)

    @server.tool(
        name="agents_message",
        description="Send a conversational message or question to another role or the human.",
    )
    async def agents_message(
        workflow_run_id: str,
        sender_role: str,
        recipient_role: str,
        message: str,
    ) -> str:
        payload = {
            "workflow_run_id": workflow_run_id,
            "sender_role": sender_role,
            "recipient_role": recipient_role,
            "message": message,
        }
        res = await client.call("agents_message", payload)
        return json.dumps(res)

    @server.tool(
        name="workflow_status",
        description="Get current workflow run stage, active tasks, and status.",
    )
    async def workflow_status(workflow_run_id: str) -> str:
        res = await client.call("workflow_status", {"workflow_run_id": workflow_run_id})
        return json.dumps(res)

    @server.tool(
        name="workflow_pause",
        description="Request workflow pause for human clarification or external dependency.",
    )
    async def workflow_pause(workflow_run_id: str, reason: str = "") -> str:
        res = await client.call("workflow_pause", {"workflow_run_id": workflow_run_id, "reason": reason})
        return json.dumps(res)

    @server.tool(
        name="workflow_resume",
        description="Resume paused workflow execution.",
    )
    async def workflow_resume(workflow_run_id: str) -> str:
        res = await client.call("workflow_resume", {"workflow_run_id": workflow_run_id})
        return json.dumps(res)

    return server
