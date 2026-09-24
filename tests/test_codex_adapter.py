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
