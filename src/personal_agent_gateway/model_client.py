import json
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

ToolName = Literal["fs.list", "fs.read", "shell.run"]
WIRE_TOOL_NAMES: dict[ToolName, str] = {
    "fs.list": "fs_list",
    "fs.read": "fs_read",
    "shell.run": "shell_run",
}
INTERNAL_TOOL_NAMES = {wire_name: tool_name for tool_name, wire_name in WIRE_TOOL_NAMES.items()}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: ToolName
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]


class ModelClient(Protocol):
    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        pass


class OpenAIModelClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1/chat/completions",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._transport = transport

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as client:
            response = await client.post(
                self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": _wire_messages(messages),
                    "tools": _tool_definitions(),
                },
            )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            return ModelResponse(content="", tool_calls=[])

        message = choices[0].get("message", {})
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = ""

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            parsed = _parse_tool_call(raw_call)
            if parsed is not None:
                tool_calls.append(parsed)

        return ModelResponse(content=content, tool_calls=tool_calls)


def _parse_tool_call(raw_call: object) -> ToolCall | None:
    if not isinstance(raw_call, dict):
        return None

    call_id = raw_call.get("id")
    function = raw_call.get("function")
    if not isinstance(call_id, str) or not isinstance(function, dict):
        return None

    name = function.get("name")
    if not isinstance(name, str):
        return None
    internal_name = INTERNAL_TOOL_NAMES.get(name)
    if internal_name is None:
        return None

    arguments = _parse_arguments(function.get("arguments"))
    return ToolCall(id=call_id, name=internal_name, arguments=arguments)


def _wire_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_wire_message(message) for message in messages]


def _wire_message(message: dict[str, object]) -> dict[str, object]:
    wire_message = dict(message)
    if wire_message.get("role") == "assistant" and "tool_calls" in wire_message:
        wire_message["tool_calls"] = _wire_tool_calls(wire_message.get("tool_calls"))
    if wire_message.get("role") == "tool":
        wire_message.pop("name", None)
    return wire_message


def _wire_tool_calls(raw_tool_calls: object) -> object:
    if not isinstance(raw_tool_calls, list):
        return raw_tool_calls

    return [_wire_tool_call(raw_tool_call) for raw_tool_call in raw_tool_calls]


def _wire_tool_call(raw_tool_call: object) -> object:
    if not isinstance(raw_tool_call, dict):
        return raw_tool_call

    tool_call = dict(raw_tool_call)
    function = tool_call.get("function")
    if isinstance(function, dict):
        wire_function = dict(function)
        wire_function["name"] = _wire_tool_name(wire_function.get("name"))
        tool_call["function"] = wire_function
    return tool_call


def _wire_tool_name(raw_name: object) -> object:
    if raw_name == "fs.list":
        return WIRE_TOOL_NAMES["fs.list"]
    if raw_name == "fs.read":
        return WIRE_TOOL_NAMES["fs.read"]
    if raw_name == "shell.run":
        return WIRE_TOOL_NAMES["shell.run"]
    return raw_name


def _parse_arguments(raw_arguments: object) -> dict[str, object]:
    if not isinstance(raw_arguments, str):
        return {}

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}

    if not isinstance(arguments, dict):
        return {}
    return {str(key): value for key, value in arguments.items()}


def _tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": WIRE_TOOL_NAMES["fs.list"],
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
                "name": WIRE_TOOL_NAMES["fs.read"],
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
                "name": WIRE_TOOL_NAMES["shell.run"],
                "description": "Request approval to run a shell command in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]
