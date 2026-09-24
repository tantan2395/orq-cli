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
            "Orchestrator MCP provides multi-agent coordination and task board management. "
            "Use task_* tools to create, track, start, complete, and link dependencies on the Kanban board. "
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

    @server.tool(
        name="ask_question",
        description=(
            "Ask the human operator structured clarifying question(s) with selectable choices, "
            "multi-select options, or write-in responses (like agy grill-me). Pauses the workflow "
            "and displays an interactive question modal in the TUI until answered."
        ),
    )
    async def ask_question(
        workflow_run_id: str,
        question: Optional[str] = None,
        options: Optional[List[str]] = None,
        is_multi_select: bool = False,
        allow_write_in: bool = True,
        questions: Optional[List[Dict[str, Any]]] = None,
        sender_role: str = "decision_maker",
    ) -> str:
        payload = {
            "workflow_run_id": workflow_run_id,
            "question": question or (questions[0].get("question", "") if questions else ""),
            "options": options or (questions[0].get("options", []) if questions else []),
            "is_multi_select": is_multi_select or (questions[0].get("is_multi_select", False) if questions else False),
            "allow_write_in": allow_write_in,
            "questions": questions or [],
            "sender_role": sender_role,
        }
        res = await client.call("ask_question", payload)
        return json.dumps(res)

    # ---------------------------------------------------------
    # Task Management & Kanban MCP Tools
    # ---------------------------------------------------------

    @server.tool(
        name="task_create",
        description="Create a new task on the Kanban board with optional dependencies and initial column.",
    )
    async def task_create(
        workflow_run_id: str,
        title: str,
        description: str = "",
        target_role: Optional[str] = "developer",
        kanban_column: str = "ready",
        dependencies: Optional[List[str]] = None,
        stage_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        requested_by: str = "agent",
    ) -> str:
        p = {
            "workflow_run_id": workflow_run_id,
            "title": title,
            "description": description,
            "target_role": target_role,
            "kanban_column": kanban_column,
            "dependencies": dependencies or [],
            "stage_id": stage_id,
            "payload": payload or {},
            "idempotency_key": idempotency_key,
            "requested_by": requested_by,
        }
        res = await client.call("task_create", p)
        return json.dumps(res)

    @server.tool(
        name="task_get",
        description="Get task details, status, kanban column, dependencies, and dependents by task ID.",
    )
    async def task_get(task_id: str) -> str:
        res = await client.call("task_get", {"task_id": task_id})
        return json.dumps(res)

    @server.tool(
        name="task_list",
        description="List tasks in a workflow run, optionally filtered by kanban_column or target_role.",
    )
    async def task_list(
        workflow_run_id: str,
        kanban_column: Optional[str] = None,
        target_role: Optional[str] = None,
    ) -> str:
        p = {
            "workflow_run_id": workflow_run_id,
            "kanban_column": kanban_column,
            "target_role": target_role,
        }
        res = await client.call("task_list", p)
        return json.dumps(res)

    @server.tool(
        name="task_update",
        description="Update task title, description, target_role, kanban_column, status, or payload.",
    )
    async def task_update(
        task_id: str,
        workflow_run_id: str = "",
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_role: Optional[str] = None,
        kanban_column: Optional[str] = None,
        status: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        p = {
            "task_id": task_id,
            "workflow_run_id": workflow_run_id,
            "title": title,
            "description": description,
            "target_role": target_role,
            "kanban_column": kanban_column,
            "status": status,
            "payload": payload or {},
        }
        res = await client.call("task_update", p)
        return json.dumps(res)

    @server.tool(
        name="task_delete",
        description="Delete a task and its dependency associations.",
    )
    async def task_delete(task_id: str) -> str:
        res = await client.call("task_delete", {"task_id": task_id})
        return json.dumps(res)

    @server.tool(
        name="task_start",
        description="Start task execution. Checks dependencies; moves to in_progress or returns blocked reason.",
    )
    async def task_start(task_id: str, workflow_run_id: str = "") -> str:
        res = await client.call("task_start", {"task_id": task_id, "workflow_run_id": workflow_run_id})
        return json.dumps(res)

    @server.tool(
        name="task_complete",
        description="Mark a task completed. Automatically promotes eligible dependent tasks from blocked to ready.",
    )
    async def task_complete(task_id: str, workflow_run_id: str = "") -> str:
        res = await client.call("task_complete", {"task_id": task_id, "workflow_run_id": workflow_run_id})
        return json.dumps(res)

    @server.tool(
        name="task_cancel",
        description="Cancel a task execution.",
    )
    async def task_cancel(task_id: str, workflow_run_id: str = "", reason: Optional[str] = None) -> str:
        res = await client.call("task_cancel", {"task_id": task_id, "workflow_run_id": workflow_run_id, "reason": reason})
        return json.dumps(res)

    @server.tool(
        name="task_block",
        description="Mark a task as blocked with an optional reason.",
    )
    async def task_block(task_id: str, workflow_run_id: str = "", reason: Optional[str] = None) -> str:
        res = await client.call("task_block", {"task_id": task_id, "workflow_run_id": workflow_run_id, "reason": reason})
        return json.dumps(res)

    @server.tool(
        name="task_unblock",
        description="Unblock a task and transition to ready if dependencies are satisfied.",
    )
    async def task_unblock(task_id: str, workflow_run_id: str = "") -> str:
        res = await client.call("task_unblock", {"task_id": task_id, "workflow_run_id": workflow_run_id})
        return json.dumps(res)

    @server.tool(
        name="task_add_dependency",
        description="Add a prerequisite dependency edge between two tasks with cycle detection.",
    )
    async def task_add_dependency(
        task_id: str,
        depends_on_task_id: str,
        workflow_run_id: str = "",
    ) -> str:
        p = {
            "task_id": task_id,
            "depends_on_task_id": depends_on_task_id,
            "workflow_run_id": workflow_run_id,
        }
        res = await client.call("task_add_dependency", p)
        return json.dumps(res)

    @server.tool(
        name="task_remove_dependency",
        description="Remove a dependency edge between two tasks.",
    )
    async def task_remove_dependency(
        task_id: str,
        depends_on_task_id: str,
        workflow_run_id: str = "",
    ) -> str:
        p = {
            "task_id": task_id,
            "depends_on_task_id": depends_on_task_id,
            "workflow_run_id": workflow_run_id,
        }
        res = await client.call("task_remove_dependency", p)
        return json.dumps(res)

    @server.tool(
        name="task_dependencies",
        description="Get list of prerequisite task IDs that task_id depends on.",
    )
    async def task_dependencies(task_id: str) -> str:
        res = await client.call("task_dependencies", {"task_id": task_id})
        return json.dumps(res)

    @server.tool(
        name="task_dependents",
        description="Get list of downstream task IDs that depend on task_id.",
    )
    async def task_dependents(task_id: str) -> str:
        res = await client.call("task_dependents", {"task_id": task_id})
        return json.dumps(res)

    @server.tool(
        name="task_ready",
        description="Check whether task_id has all its dependencies satisfied and is ready to start.",
    )
    async def task_ready(task_id: str) -> str:
        res = await client.call("task_ready", {"task_id": task_id})
        return json.dumps(res)

    return server
