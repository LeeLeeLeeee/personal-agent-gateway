import asyncio
import json
import uuid

import httpx
import pytest

from personal_agent_gateway.remote_model_client import (
    HttpModelClient,
    RemoteRunAbortedError,
    RemoteRunFailedError,
    RemoteRunProtocolError,
)


def _sse(*events: dict) -> bytes:
    normalized = list(events)
    if not normalized or normalized[0].get("kind") != "run.started":
        normalized.insert(0, {"kind": "run.started"})
    out = []
    for e in normalized:
        event = {"run_id": "r1", **e}
        out.append(f"event: {event['kind']}\ndata: {json.dumps(event)}\n\n")
    return "".join(out).encode()


def _transport(body: bytes, capture: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["body"] = json.loads(request.content)
            capture["headers"] = dict(request.headers)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_complete_returns_content_and_session_id():
    body = _sse(
        {"kind": "run.started", "run_id": "r1", "provider": "codex"},
        {"kind": "session.updated", "upstream_session_id": "th_1"},
        {"kind": "message.completed", "text": "hello"},
        {"kind": "run.completed", "content": "hello", "upstream_session_id": "th_1"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {"workspace_root": "/ws"}, transport=_transport(body))
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert resp.upstream_session_id == "th_1"
    assert resp.tool_calls == []


@pytest.mark.asyncio
async def test_complete_relays_raw_to_on_event():
    raw = {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}
    body = _sse(
        {"kind": "message.completed", "text": "hi", "raw": raw},
        {"kind": "run.completed", "content": "hi"},
    )
    seen = []
    async def on_event(ev): seen.append(ev)
    client = HttpModelClient("http://lmg", "codex", "default", {}, on_event=on_event, transport=_transport(body))
    await client.complete([{"role": "user", "content": "hi"}])
    assert any(e.get("raw") == raw for e in seen)  # raw stays nested in the relayed normalized event


@pytest.mark.asyncio
async def test_on_event_receives_normalized_events_excluding_terminal():
    events = [
        {"kind": "run.started", "run_id": "r1"},
        {"kind": "session.updated", "upstream_session_id": "s1"},
        {"kind": "message.delta", "text": "Hel", "run_id": "r1", "raw": {"x": 1}},
        {"kind": "message.completed", "text": "Hello", "run_id": "r1"},
        {"kind": "run.completed", "content": "Hello", "upstream_session_id": "s1"},
    ]

    def handler(request):
        return httpx.Response(200, text=_sse(*events).decode())

    seen = []

    async def on_event(ev):
        seen.append(ev)

    client = HttpModelClient(
        base_url="http://lmg", provider="codex", model="codex",
        execution={}, on_event=on_event,
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete([{"role": "user", "content": "hi"}])
    kinds = [e["kind"] for e in seen]
    assert kinds == ["run.started", "session.updated", "message.delta", "message.completed"]
    assert result.content == "Hello"
    assert result.upstream_session_id == "s1"


@pytest.mark.asyncio
async def test_complete_sends_provider_model_execution_and_resume():
    cap = {}
    body = _sse({"kind": "run.completed", "content": "x"})
    client = HttpModelClient(
        "http://lmg", "claude", "claude-sonnet-5", {"sandbox": "workspace-write"},
        upstream_session_id="prev", transport=_transport(body, cap),
    )
    await client.complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["provider"] == "claude"
    assert cap["body"]["model"] == "claude-sonnet-5"
    assert cap["body"]["session"]["upstream_id"] == "prev"
    assert cap["body"]["execution"]["sandbox"] == "workspace-write"


@pytest.mark.asyncio
async def test_complete_sends_authenticated_consumer_tracking():
    cap = {}
    body = _sse({"kind": "run.completed", "content": "x"})
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        local_token="local-secret",
        consumer_session_id="pag-session-1",
        transport=_transport(body, cap),
    )

    await client.complete([{"role": "user", "content": "hi"}])

    assert cap["headers"]["authorization"] == "Bearer local-secret"
    assert cap["body"]["consumer"] == "personal-agent-gateway"
    assert cap["body"]["consumer_session_id"] == "pag-session-1"
    uuid.UUID(cap["body"]["consumer_run_id"])


@pytest.mark.asyncio
async def test_complete_creates_new_consumer_run_id_per_call():
    requests = []
    body = _sse({"kind": "run.completed", "content": "x"})

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    await client.complete([{"role": "user", "content": "first"}])
    await client.complete([{"role": "user", "content": "second"}])

    assert requests[0]["consumer_run_id"] != requests[1]["consumer_run_id"]
    assert all("consumer_session_id" not in request for request in requests)
    assert all(request["consumer"] == "personal-agent-gateway" for request in requests)


@pytest.mark.asyncio
async def test_complete_omits_authorization_without_token():
    cap = {}
    body = _sse({"kind": "run.completed", "content": "x"})
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=_transport(body, cap),
    )

    await client.complete([{"role": "user", "content": "hi"}])

    assert "authorization" not in cap["headers"]


@pytest.mark.asyncio
async def test_run_failed_raises():
    body = _sse(
        {
            "kind": "run.failed",
            "error": "boom",
            "error_code": "provider_process_failed",
            "partial_content": "partial",
            "upstream_session_id": "session-1",
        }
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunFailedError, match="boom") as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "provider_process_failed"
    assert raised.value.partial_content == "partial"
    assert raised.value.upstream_session_id == "session-1"


@pytest.mark.asyncio
async def test_complete_rejects_events_after_terminal():
    body = _sse(
        {"kind": "run.completed", "content": "final"},
        {"kind": "message.completed", "text": "LATE"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunProtocolError, match="event_after_terminal"):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_complete_rejects_duplicate_terminal():
    body = _sse(
        {"kind": "run.completed", "content": "final"},
        {"kind": "run.completed", "content": "OVERWRITE"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunProtocolError, match="duplicate_terminal"):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        (b"event: run.completed\ndata: not json\n\n", "malformed_event_json"),
        (b"event: run.completed\ndata: []\n\n", "event_is_not_object"),
        (
            b'event: invalid\ndata: {"kind":"invalid","run_id":"r1"}\n\n',
            "invalid_event_kind",
        ),
        (
            b'event: run.completed\ndata: {"kind":"run.completed","content":"ok"}\n\n',
            "invalid_run_id",
        ),
    ],
)
async def test_complete_rejects_malformed_events(body: bytes, diagnostic: str):
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunProtocolError, match=diagnostic) as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "provider_protocol_error"


@pytest.mark.asyncio
async def test_complete_rejects_eof_without_terminal():
    body = _sse({"kind": "message.delta", "text": "partial"})
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunProtocolError, match="missing_terminal") as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "upstream_stream_incomplete"


@pytest.mark.asyncio
async def test_run_aborted_preserves_typed_details():
    body = _sse(
        {
            "kind": "run.aborted",
            "error": "deadline exceeded",
            "error_code": "run_timeout",
            "partial_content": "partial",
            "upstream_session_id": "session-1",
        }
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RemoteRunAbortedError, match="deadline exceeded") as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "run_timeout"
    assert raised.value.partial_content == "partial"
    assert raised.value.upstream_session_id == "session-1"


@pytest.mark.asyncio
async def test_provider_unavailable_503_does_not_expose_response_body():
    secret_body = "provider stderr containing super-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=secret_body, request=request)

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "provider_unavailable"
    assert str(raised.value) == "remote_provider_unavailable"
    assert secret_body not in str(raised.value)


@pytest.mark.asyncio
async def test_request_error_uses_stable_code_and_diagnostic():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("socket detail", request=request)

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "provider_unavailable"
    assert str(raised.value) == "remote_gateway_unavailable"


class _FailingStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield _sse({"kind": "message.delta", "text": "partial"})
        raise httpx.ReadError("socket detail")


@pytest.mark.asyncio
async def test_stream_read_error_is_protocol_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_FailingStream(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RemoteRunProtocolError, match="stream_read_error") as raised:
        await client.complete([{"role": "user", "content": "hi"}])
    assert raised.value.code == "provider_protocol_error"
    assert raised.value.partial_content == "partial"


@pytest.mark.asyncio
async def test_protocol_error_preserves_observed_session_and_partial_content():
    body = _sse(
        {"kind": "session.updated", "upstream_session_id": "session-1"},
        {"kind": "message.delta", "text": "partial"},
        {"kind": "message.delta", "text": 42},
    )
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=_transport(body),
    )

    with pytest.raises(RemoteRunProtocolError, match="invalid_event_shape") as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.partial_content == "partial"
    assert raised.value.upstream_session_id == "session-1"


@pytest.mark.asyncio
async def test_complete_requires_exactly_one_run_started_as_first_event():
    missing = (
        b'event: message.delta\n'
        b'data: {"kind":"message.delta","run_id":"r1","text":"partial"}\n\n'
    )
    duplicate = _sse(
        {"kind": "run.started"},
        {"kind": "run.started"},
        {"kind": "run.completed", "content": "done"},
    )

    for body, diagnostic in (
        (missing, "missing_run_started"),
        (duplicate, "duplicate_run_started"),
    ):
        client = HttpModelClient(
            "http://lmg",
            "codex",
            "default",
            {},
            transport=_transport(body),
        )
        with pytest.raises(RemoteRunProtocolError, match=diagnostic):
            await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_call",
    [
        "not-an-object",
        {"id": "", "name": "fs_read", "arguments": {}},
        {"id": "c1", "name": "", "arguments": {}},
        {"id": "c1", "name": "fs_read"},
        {"id": "c1", "name": "fs_read", "arguments": "not-an-object"},
    ],
)
async def test_complete_rejects_malformed_completed_tool_calls(tool_call):
    body = _sse(
        {
            "kind": "run.completed",
            "content": "",
            "tool_calls": [tool_call],
        }
    )
    client = HttpModelClient(
        "http://lmg",
        "openai",
        "gpt-4o",
        {},
        transport=_transport(body),
    )

    with pytest.raises(RemoteRunProtocolError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == "provider_protocol_error"


@pytest.mark.asyncio
async def test_legacy_run_failed_without_code_maps_to_process_failure():
    body = _sse({"kind": "run.failed", "error": "legacy failure"})
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=_transport(body),
    )

    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == "provider_process_failed"


class _HangingAfterTerminalStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield _sse({"kind": "run.completed", "content": "done"})
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_overall_timeout_wins_when_stream_hangs_after_terminal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_HangingAfterTerminalStream(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        timeout_seconds=0.01,
        timeout_grace_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunAbortedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == "run_timeout"


@pytest.mark.asyncio
async def test_openai_provider_sends_tools_and_wire_maps_messages():
    cap = {}
    body = _sse({"kind": "run.completed", "content": "ok"})
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs.read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "fs.read", "content": "data"},
    ]
    client = HttpModelClient("http://lmg", "openai", "gpt-4o", {}, transport=_transport(body, cap))
    await client.complete(messages)
    b = cap["body"]
    assert any(t["function"]["name"] == "fs_read" for t in b["tools"])       # tool defs sent, wire names
    sent = b["messages"]
    assert sent[0]["tool_calls"][0]["function"]["name"] == "fs_read"          # assistant tool_call wire-mapped
    assert "name" not in sent[1]                                             # tool message dropped `name`


@pytest.mark.asyncio
async def test_codex_provider_omits_tools_and_does_not_wire_map():
    cap = {}
    body = _sse({"kind": "run.completed", "content": "ok"})
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body, cap))
    await client.complete([{"role": "user", "content": "hi"}])
    assert "tools" not in cap["body"]
    assert cap["body"]["messages"] == [{"role": "user", "content": "hi"}]
