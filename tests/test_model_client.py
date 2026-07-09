import json
import os
from pathlib import Path

import httpx
import pytest

from personal_agent_gateway.model_client import (
    ClaudeModelClient,
    CodexModelClient,
    ModelResponse,
    OpenAIModelClient,
    _parse_claude_session_id,
    _parse_codex_session_id,
    ToolCall,
)


def write_fake_codex(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        codex_bin = tmp_path / "codex.cmd"
        codex_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return codex_bin

    codex_bin = tmp_path / "codex"
    codex_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    return codex_bin


def write_fake_claude(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        claude_bin = tmp_path / "claude.cmd"
        claude_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return claude_bin

    claude_bin = tmp_path / "claude"
    claude_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    claude_bin.chmod(0o755)
    return claude_bin


@pytest.mark.asyncio
async def test_openai_client_posts_valid_wire_tool_names() -> None:
    captured_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "done", "tool_calls": []}}]},
        )

    client = OpenAIModelClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1/chat/completions",
        transport=httpx.MockTransport(handler),
    )

    response = await client.complete(
        [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "previous"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "fs.list", "arguments": '{"path": "."}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "fs.list",
                "content": "[]",
            },
            {
                "role": "tool",
                "tool_call_id": "call-2",
                "content": "unnamed",
            },
        ]
    )

    assert response == ModelResponse(content="done", tool_calls=[])
    assert captured_payloads[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fs_list",
                "description": "List files under a workspace-relative path.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fs_read",
                "description": "Read a UTF-8 text file from a workspace-relative path.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shell_run",
                "description": "Request approval to run a shell command in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]
    assert "tool_calls" not in captured_payloads[0]["messages"][1]
    assert captured_payloads[0]["messages"][2]["tool_calls"][0]["function"]["name"] == "fs_list"
    assert "name" not in captured_payloads[0]["messages"][3]
    assert "name" not in captured_payloads[0]["messages"][4]


@pytest.mark.asyncio
async def test_openai_client_maps_wire_tool_call_names_to_internal_names() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_run",
                                        "arguments": '{"command": "pwd"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = OpenAIModelClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1/chat/completions",
        transport=httpx.MockTransport(handler),
    )

    response = await client.complete([{"role": "user", "content": "run pwd"}])

    assert response == ModelResponse(
        content="",
        tool_calls=[ToolCall(id="call-1", name="shell.run", arguments={"command": "pwd"})],
    )


@pytest.mark.asyncio
async def test_codex_client_runs_local_codex_exec_and_parses_final_message(tmp_path: Path) -> None:
    payload = '{"type":"item.completed","item":{"type":"agent_message","text":"local answer"}}'
    if os.name == "nt":
        body = f"echo {payload}"
    else:
        body = f"printf '%s\\n' '{payload}'"
    codex_bin = write_fake_codex(
        tmp_path,
        body,
    )

    client = CodexModelClient(binary=str(codex_bin), model="default", workspace_root=tmp_path)

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(content="local answer", tool_calls=[])


@pytest.mark.asyncio
async def test_codex_client_publishes_json_events_while_collecting_final_message(tmp_path: Path) -> None:
    first = '{"type":"thread.started","thread_id":"thread-1"}'
    second = '{"type":"item.completed","item":{"id":"item-1","type":"agent_message","text":"streamed answer"}}'
    if os.name == "nt":
        body = f"echo {first}\r\necho {second}"
    else:
        body = f"printf '%s\\n%s\\n' '{first}' '{second}'"
    codex_bin = write_fake_codex(tmp_path, body)
    events: list[dict[str, object]] = []

    async def publish(event: dict[str, object]) -> None:
        events.append(event)

    client = CodexModelClient(
        binary=str(codex_bin),
        model="default",
        workspace_root=tmp_path,
        on_event=publish,
    )

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(
        content="streamed answer",
        tool_calls=[],
        upstream_session_id="thread-1",
    )
    assert events == [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "agent_message",
                "text": "streamed answer",
            },
        },
    ]


@pytest.mark.asyncio
async def test_codex_client_reports_local_codex_failure(tmp_path: Path) -> None:
    if os.name == "nt":
        body = "echo not logged in 1>&2\r\nexit /b 7"
    else:
        body = "printf '%s\\n' 'not logged in' >&2\nexit 7"
    codex_bin = write_fake_codex(tmp_path, body)

    client = CodexModelClient(binary=str(codex_bin), model="default", workspace_root=tmp_path)

    with pytest.raises(RuntimeError, match="Codex exited with status 7: not logged in"):
        await client.complete([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_codex_client_reports_missing_binary() -> None:
    client = CodexModelClient(
        binary="/definitely/missing/codex",
        model="default",
        workspace_root=Path("."),
    )

    with pytest.raises(RuntimeError, match="Codex binary not found"):
        await client.complete([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_claude_client_runs_print_json_and_parses_result(tmp_path: Path) -> None:
    payload = '{"result":"claude answer"}'
    if os.name == "nt":
        body = f"echo {payload}"
    else:
        body = f"printf '%s\\n' '{payload}'"
    claude_bin = write_fake_claude(tmp_path, body)

    client = ClaudeModelClient(
        binary=str(claude_bin),
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
    )

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(content="claude answer", tool_calls=[])


def test_claude_client_builds_expected_command(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
    ]


def test_codex_client_includes_profile_flag_when_configured(tmp_path: Path) -> None:
    client = CodexModelClient(
        binary="codex",
        model="default",
        workspace_root=tmp_path,
        profile="local-dev",
    )

    assert client._command() == [
        "codex",
        "exec",
        "--json",
        "-c",
        'approval_policy="never"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(tmp_path),
        "--skip-git-repo-check",
        "--profile",
        "local-dev",
        "-",
    ]


def test_codex_client_builds_resume_command_when_upstream_session_exists(tmp_path: Path) -> None:
    client = CodexModelClient(
        binary="codex",
        model="gpt-5.5",
        workspace_root=tmp_path,
        effort="xhigh",
        approval_policy="never",
        upstream_session_id="0199a213-81c0-7800-8aa1-bbab2a035a53",
    )

    assert client._command() == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="xhigh"',
        "--skip-git-repo-check",
        "-m",
        "gpt-5.5",
        "0199a213-81c0-7800-8aa1-bbab2a035a53",
        "-",
    ]


def test_claude_client_includes_agent_flag_when_configured(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
        agent="reviewer",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
        "--agent",
        "reviewer",
    ]


def test_claude_client_builds_resume_command_when_upstream_session_exists(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
        upstream_session_id="f7c44fcb-e059-4799-94e3-f64d39305050",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--resume",
        "f7c44fcb-e059-4799-94e3-f64d39305050",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
    ]


def test_parse_codex_output_includes_upstream_thread_id() -> None:
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
        ]
    )

    assert _parse_codex_session_id(output) == "0199a213-81c0-7800-8aa1-bbab2a035a53"


def test_parse_claude_output_includes_upstream_session_id() -> None:
    output = json.dumps(
        {
            "type": "result",
            "result": "done",
            "session_id": "f7c44fcb-e059-4799-94e3-f64d39305050",
        }
    )

    assert _parse_claude_session_id(output) == "f7c44fcb-e059-4799-94e3-f64d39305050"
