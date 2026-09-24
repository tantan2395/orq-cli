# Agent Orchestrator

A decoupled local multi-agent orchestration runtime coordinating heterogeneous coding CLIs (Codex, Agy, and future backends) with first-class roles, model profiles, an event-driven engine, durable tasks, asynchronous MCP ingress, worktree containment, and an interactive Textual TUI human control plane.

## Orchestrator MCP Tools

The Orchestrator provides a built-in Model Context Protocol (MCP) server (`src/orchestrator/mcp/server.py`) enabling agents to coordinate workflows, hand off tasks, and communicate. The seven registered tools are:

- **`agents_handoff`**: Formally hand off task execution to another role in the workflow (passing task descriptions, context, constraints, and acceptance criteria).
- **`review_request`**: Submit implemented work, test evidence, and diffs/patches for independent review.
- **`review_decision`**: Submit a formal evaluation verdict (`approved`, `changes_requested`, `blocked`) and findings for submitted work.
- **`agents_message`**: Send a conversational message or question to another role or the human operator.
- **`workflow_status`**: Get current workflow run stage, active tasks, and status.
- **`workflow_pause`**: Request workflow pause for human clarification or external dependency.
- **`workflow_resume`**: Resume paused workflow execution.
