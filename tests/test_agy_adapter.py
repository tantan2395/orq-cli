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
