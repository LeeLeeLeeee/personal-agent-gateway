import json
from collections.abc import Awaitable, Callable

import httpx

from personal_agent_gateway.model_client import INTERNAL_TOOL_NAMES, ModelResponse, ToolCall, WIRE_TOOL_NAMES


class HttpModelClient:
    def __init__(
        self,
        base_url: str,
        provider: str,
        model: str,
        execution: dict[str, object],
        *,
        on_event: Callable[[dict[str, object]], Awaitable[None]] | None = None,
        upstream_session_id: str | None = None,
        timeout_seconds: int = 3600,
        idle_timeout_seconds: int = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._model = model
        self._execution = execution
        self._on_event = on_event
        self._upstream_session_id = upstream_session_id
        self._timeout_seconds = timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._transport = transport

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        outgoing = messages
        body_extra = {}
        if self._provider == "openai":
            outgoing = _wire_messages(messages)
            body_extra["tools"] = _tool_definitions()
        body = {
            "provider": self._provider,
            "model": self._model,
            "messages": outgoing,
            "session": {"upstream_id": self._upstream_session_id or ""},
            "execution": self._execution,
            "timeout_ms": self._timeout_seconds * 1000,
            "idle_timeout_ms": self._idle_timeout_seconds * 1000,
            **body_extra,
        }
        content = ""
        tool_calls: list[ToolCall] = []
        upstream_session_id = self._upstream_session_id
        async with httpx.AsyncClient(timeout=None, transport=self._transport) as client:
            async with client.stream("POST", f"{self._base_url}/v1/runs", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    sid = event.get("upstream_session_id")
                    if isinstance(sid, str) and sid:
                        upstream_session_id = sid
                    kind = event.get("kind")
                    if kind == "run.failed":
                        raise RuntimeError(str(event.get("error") or "remote run failed"))
                    if kind == "run.completed":
                        raw_content = event.get("content")
                        content = raw_content if isinstance(raw_content, str) else ""
                        tool_calls = _parse_tool_calls(event.get("tool_calls"))
                        return ModelResponse(content=content, tool_calls=tool_calls, upstream_session_id=upstream_session_id)
                    if self._on_event is not None:
                        await self._on_event(event)
        return ModelResponse(content=content, tool_calls=tool_calls, upstream_session_id=upstream_session_id)


def _tool_definitions() -> list[dict]:
    return [
        {"type": "function", "function": {"name": WIRE_TOOL_NAMES["fs.list"],
            "description": "List files under a workspace-relative path.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
        {"type": "function", "function": {"name": WIRE_TOOL_NAMES["fs.read"],
            "description": "Read a UTF-8 text file from a workspace-relative path.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
        {"type": "function", "function": {"name": WIRE_TOOL_NAMES["shell.run"],
            "description": "Request approval to run a shell command in the workspace.",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    ]


def _wire_messages(messages: list[dict]) -> list[dict]:
    wired = []
    for message in messages:
        m = dict(message)
        if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
            m["tool_calls"] = [_wire_tool_call(tc) for tc in m["tool_calls"]]
        if m.get("role") == "tool":
            m.pop("name", None)
        wired.append(m)
    return wired


def _wire_tool_call(tc: object) -> object:
    if not isinstance(tc, dict):
        return tc
    out = dict(tc)
    fn = out.get("function")
    if isinstance(fn, dict):
        wfn = dict(fn)
        wfn["name"] = WIRE_TOOL_NAMES.get(wfn.get("name"), wfn.get("name"))
        out["function"] = wfn
    return out


def _parse_tool_calls(raw: object) -> list[ToolCall]:
    if not isinstance(raw, list):
        return []
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        call_id = item.get("id")
        name = item.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            continue
        args = item.get("arguments")
        arguments = args if isinstance(args, dict) else {}
        internal = INTERNAL_TOOL_NAMES.get(name, name)
        calls.append(ToolCall(id=call_id, name=internal, arguments=arguments))
    return calls
