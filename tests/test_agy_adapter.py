"""Unit tests for the AgyAdapter command construction and output parsing."""

from orchestrator.adapters.agy import AgyAdapter
from orchestrator.models import ReasoningEffort


def test_agy_adapter_build_command():
    adapter = AgyAdapter(binary_path="/usr/bin/agy")
    cmd = adapter.build_command(
        prompt="Implement the feature",
        model="gemini-3.8-flash-high",
        reasoning=ReasoningEffort.MEDIUM,
        workspace_root="/tmp/repo",
        session_id=None,
    )

    assert cmd[0] == "/usr/bin/agy"
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--model" in cmd
    assert "gemini-3.8-flash-high" in cmd
    assert "--effort" in cmd
    assert "medium" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[-2] == "-p"
    assert cmd[-1] == "Implement the feature"


def test_agy_adapter_build_command_with_resume():
    adapter = AgyAdapter(binary_path="/usr/bin/agy")
    cmd = adapter.build_command(
        prompt="Continue implementation",
        model="gemini-3.8-flash-high",
        reasoning=ReasoningEffort.LOW,
        workspace_root="/tmp/repo",
        session_id="conv_xyz_789",
    )

    assert "--conversation" in cmd
    assert "conv_xyz_789" in cmd


def test_agy_adapter_parse_lines():
    adapter = AgyAdapter()

    # Tool call event
    tool_line = '{"type": "tool_call", "name": "review_request", "args": {"summary": "Done"}}'
    parsed_tool = adapter._parse_line(tool_line)
    assert parsed_tool["type"] == "tool_call"
    assert parsed_tool["name"] == "review_request"

    # Conversation ID discovery
    conv_line = '{"type": "status", "conversation_id": "conv_found_456"}'
    adapter._parse_line(conv_line)
    assert adapter.active_session_id == "conv_found_456"

    # Message content
    msg_line = '{"type": "chunk", "content": "Writing test files now"}'
    parsed_msg = adapter._parse_line(msg_line)
    assert parsed_msg["type"] == "agent_message"
    assert "Writing" in parsed_msg["content"]


def test_agy_adapter_native_stream_json_events():
    adapter = AgyAdapter()

    # 1. init event with conversation_id
    init_line = '{"event":"init","conversation_id":"conv-abc-123","init":{"cwd":"/repo"}}'
    assert adapter._parse_line(init_line) is None
    assert adapter.active_session_id == "conv-abc-123"

    # 2. step_update tool call (ACTIVE)
    tool_active = '{"event":"step_update","step_update":{"conversation_id":"conv-abc-123","step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"echo hello"}}}}'
    ev = adapter._parse_line(tool_active)
    assert ev is not None
    assert ev["type"] == "tool_call"
    assert ev["name"] == "run_command"
    assert ev["args"] == {"CommandLine": "echo hello"}

    # 3. step_update tool result (DONE)
    tool_done = '{"event":"step_update","step_update":{"conversation_id":"conv-abc-123","step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"echo hello"},"output":"hello\\n"}}}'
    ev = adapter._parse_line(tool_done)
    assert ev is not None
    assert ev["type"] == "tool_result"
    assert "hello" in ev["output"]

    # 4. step_update agent response streaming and completion
    resp_chunk1 = '{"event":"step_update","step_update":{"conversation_id":"conv-abc-123","step_index":3,"state":"ACTIVE","step_type":"agent_response","text_delta":"Task is "}}'
    assert adapter._parse_line(resp_chunk1) is None
    resp_chunk2 = '{"event":"step_update","step_update":{"conversation_id":"conv-abc-123","step_index":3,"state":"DONE","step_type":"agent_response","text_delta":"complete."}}'
    ev = adapter._parse_line(resp_chunk2)
    assert ev is not None
    assert ev["type"] == "agent_message"
    assert ev["content"] == "Task is complete."

    # 5. result event fallback
    res_line = '{"event":"result","result":{"conversation_id":"conv-abc-123","status":"SUCCESS","response":"Final output."}}'
    # Since message was already emitted for this turn, result does not duplicate
    assert adapter._parse_line(res_line) is None
