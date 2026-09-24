"""Unit tests for the CodexAdapter command construction and output parsing."""

from orchestrator.adapters.codex import CodexAdapter
from orchestrator.models import ReasoningEffort


def test_codex_adapter_build_command():
    adapter = CodexAdapter(binary_path="/usr/bin/codex")
    cmd = adapter.build_command(
        prompt="Review the code",
        model="gpt-6-astra",
        reasoning=ReasoningEffort.HIGH,
        workspace_root="/tmp/repo",
        session_id=None,
    )

    assert cmd[0] == "/usr/bin/codex"
    assert cmd[1] == "-C"
    assert cmd[3] == "exec"
    assert "--json" in cmd
    assert "-m" in cmd
    assert "gpt-6-astra" in cmd
    assert '-c model_reasoning_effort="high"' in " ".join(cmd)
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[-1] == "Review the code"


def test_codex_adapter_build_command_with_resume():
    adapter = CodexAdapter(binary_path="/usr/bin/codex")
    cmd = adapter.build_command(
        prompt="Continue task",
        model="gpt-6-astra",
        reasoning=ReasoningEffort.MEDIUM,
        workspace_root="/tmp/repo",
        session_id="01a0a43e-4c6d",
    )

    assert cmd[1] == "-C"
    assert cmd[3] == "exec"
    assert cmd[4] == "resume"
    assert cmd[5] == "01a0a43e-4c6d"


def test_codex_adapter_parse_lines():
    adapter = CodexAdapter()

    # Tool call event
    tool_line = '{"type": "tool_call", "name": "agents_handoff", "args": {"task": "build"}}'
    parsed_tool = adapter._parse_line(tool_line)
    assert parsed_tool["type"] == "tool_call"
    assert parsed_tool["name"] == "agents_handoff"

    # Tool result event
    res_line = '{"type": "tool_result", "output": "accepted"}'
    parsed_res = adapter._parse_line(res_line)
    assert parsed_res["type"] == "tool_result"

    # Thread/session creation
    thread_line = '{"type": "thread.created", "thread_id": "thread_abc_123"}'
    adapter._parse_line(thread_line)
    assert adapter.active_session_id == "thread_abc_123"

    # Text delta
    delta_line = '{"delta": {"text": "I will proceed with the review"}}'
    parsed_delta = adapter._parse_line(delta_line)
    assert parsed_delta["type"] == "agent_message"
    assert "proceed" in parsed_delta["content"]

    # Codex CLI nested event_msg: AgentMessage
    agent_msg_line = '{"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "content": [{"type": "text", "text": "I am reviewing the architecture."}]}}}'
    parsed_nested = adapter._parse_line(agent_msg_line)
    assert parsed_nested["type"] == "agent_message"
    assert "reviewing the architecture" in parsed_nested["content"]

    # Codex CLI nested event_msg: McpToolCall
    mcp_call_line = '{"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "McpToolCall", "server": "orchestrator", "tool": "workflow_pause", "arguments": {"reason": "Need repo path"}}}}'
    parsed_mcp = adapter._parse_line(mcp_call_line)
    assert parsed_mcp["type"] == "tool_call"
    assert parsed_mcp["name"] == "orchestrator:workflow_pause"
    assert parsed_mcp["args"]["reason"] == "Need repo path"

    # Codex CLI response_item message
    resp_line = '{"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "All checks passed."}]}}'
    parsed_resp = adapter._parse_line(resp_line)
    assert parsed_resp["type"] == "agent_message"
    assert "All checks passed." in parsed_resp["content"]

    # Native Codex exec stdout stream: thread.started
    thread_started_line = '{"type":"thread.started","thread_id":"01a0d1ae-e512-7f80-a60b-b126834ca874"}'
    adapter._parse_line(thread_started_line)
    assert adapter.active_session_id == "01a0d1ae-e512-7f80-a60b-b126834ca874"

    # Native Codex exec stdout stream: item.completed (agent_message)
    native_msg_line = '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Yes. Orq exposes seven MCP tools."}}'
    parsed_native_msg = adapter._parse_line(native_msg_line)
    assert parsed_native_msg["type"] == "agent_message"
    assert "Orq exposes seven MCP tools" in parsed_native_msg["content"]

    # Native Codex exec stdout stream: item.started (mcp_tool_call)
    native_tool_start = '{"type":"item.started","item":{"id":"item_1","type":"mcp_tool_call","server":"orchestrator","tool":"workflow_status","arguments":{"workflow_run_id":"run_123"}}}'
    parsed_tool_start = adapter._parse_line(native_tool_start)
    assert parsed_tool_start["type"] == "tool_call"
    assert parsed_tool_start["name"] == "orchestrator:workflow_status"
    assert parsed_tool_start["args"]["workflow_run_id"] == "run_123"

    # Native Codex exec stdout stream: item.completed (command_execution)
    native_cmd_line = '{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"echo hello","aggregated_output":"hello\\n","exit_code":0}}'
    parsed_cmd = adapter._parse_line(native_cmd_line)
    assert parsed_cmd["type"] == "status"
    assert "echo hello" in parsed_cmd["status"]


