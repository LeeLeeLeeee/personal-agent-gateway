import json
import os
from pathlib import Path

import httpx
import pytest

from personal_agent_gateway.model_client import CodexModelClient, ModelResponse, OpenAIModelClient, ToolCall


def write_fake_codex(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        codex_bin = tmp_path / "codex.cmd"
        codex_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return codex_bin

    codex_bin = tmp_path / "codex"
    codex_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    return codex_bin


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

    assert response == ModelResponse(content="streamed answer", tool_calls=[])
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
