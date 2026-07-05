import json

import httpx
import pytest

from personal_agent_gateway.model_client import ModelResponse, OpenAIModelClient, ToolCall


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
