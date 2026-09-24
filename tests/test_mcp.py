"""Unit tests for the Orchestrator MCP server and IPC transport layer."""

import asyncio
import json
from pathlib import Path
import pytest

from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.mcp.ipc import IPCClient, IPCServer


@pytest.mark.asyncio
async def test_ipc_server_and_client_roundtrip(tmp_path: Path):
    socket_path = tmp_path / "test_ipc.sock"

    async def mock_handler(method: str, params: dict):
        if method == "echo":
            return {"status": "ok", "echo": params.get("message")}
        return {"error": "unknown method"}

    server = IPCServer(handler=mock_handler, socket_path=socket_path)
    await server.start()

    client = IPCClient(socket_path=socket_path)
    res = await client.call("echo", {"message": "hello orchestrator"})
    assert res.get("status") == "ok"
    assert res.get("echo") == "hello orchestrator"

    await server.stop()


@pytest.mark.asyncio
async def test_ipc_missing_socket():
    client = IPCClient(socket_path=Path("/tmp/non_existent_socket_123.sock"))
    res = await client.call("echo", {})
    assert res.get("status") == "error"
    assert "not found" in res.get("error", "").lower()


@pytest.mark.asyncio
async def test_engine_mcp_ipc_integration(tmp_path: Path):
    socket_path = tmp_path / "engine_ipc.sock"
    cfg = get_default_config()
    cfg.sqlite_db_path = str(tmp_path / "mcp_engine.db")

    engine = OrchestrationEngine(config=cfg, db=Database(cfg.sqlite_db_path), events=EventBus())
    await engine.initialize()

    server = IPCServer(handler=engine.handle_ipc_command, socket_path=socket_path)
    await server.start()

    client = IPCClient(socket_path=socket_path)

    # 1. Start run
    run = await engine.create_workflow_run(workspace_root=str(tmp_path))
    assert run.current_stage == "strategy"

    # 2. Call agents_handoff over IPC
    handoff_res = await client.call("agents_handoff", {
        "workflow_run_id": run.run_id,
        "target_role": "developer",
        "task": "Build core service",
        "idempotency_key": "mcp_idemp_1",
    })
    assert handoff_res.get("accepted") is True
    task_id = handoff_res.get("task_id")
    assert task_id is not None

    # Verify stage transition happened in engine
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.current_stage == "implementation"

    # 3. Duplicate handoff with same idempotency key returns same task_id
    duplicate_res = await client.call("agents_handoff", {
        "workflow_run_id": run.run_id,
        "target_role": "developer",
        "task": "Build core service",
        "idempotency_key": "mcp_idemp_1",
    })
    assert duplicate_res.get("task_id") == task_id

    # 4. Call review_request over IPC
    rev_res = await client.call("review_request", {
        "workflow_run_id": run.run_id,
        "summary": "Implementation done",
        "target_role": "code_reviewer",
    })
    assert rev_res.get("accepted") is True
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.current_stage == "review"

    # 5. Call review_decision over IPC (approved)
    dec_res = await client.call("review_decision", {
        "workflow_run_id": run.run_id,
        "reviewer_role": "code_reviewer",
        "decision": "approved",
        "summary": "Looks great",
    })
    assert dec_res.get("accepted") is True
    run = await engine.db.get_workflow_run(run.run_id)
    assert run.current_stage == "final_review"

    # 6. Check workflow_status over IPC
    status_res = await client.call("workflow_status", {"workflow_run_id": run.run_id})
    assert status_res.get("run_id") == run.run_id
    assert status_res.get("current_stage") == "final_review"

    await server.stop()
