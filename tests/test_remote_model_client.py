import asyncio
import json
import uuid

import httpx
import pytest

from personal_agent_gateway.remote_model_client import (
    HttpModelClient,
    RemoteRunAbortedError,
    RemoteRunError,
    RemoteRunFailedError,
    RemoteRunProtocolError,
    _terminal_result,
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
        "http://lmg",
        "claude",
        "claude-sonnet-5",
        {"sandbox": "workspace-write", "network": "required"},
        upstream_session_id="prev", transport=_transport(body, cap),
    )
    await client.complete([{"role": "user", "content": "hi"}])
    assert cap["body"]["provider"] == "claude"
    assert cap["body"]["model"] == "claude-sonnet-5"
    assert cap["body"]["session"]["upstream_id"] == "prev"
    assert cap["body"]["execution"]["sandbox"] == "workspace-write"
    assert cap["body"]["execution"]["network"] == "required"


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
        consumer_context_fingerprint="context-fp-1",
        transport=_transport(body, cap),
    )

    await client.complete([{"role": "user", "content": "hi"}])

    assert cap["headers"]["authorization"] == "Bearer local-secret"
    assert cap["body"]["consumer"] == "personal-agent-gateway"
    assert cap["body"]["consumer_session_id"] == "pag-session-1"
    assert cap["body"]["consumer_context_fingerprint"] == "context-fp-1"
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
    assert all("consumer_context_fingerprint" not in request for request in requests)
    assert all(request["consumer"] == "personal-agent-gateway" for request in requests)


@pytest.mark.asyncio
async def test_session_update_immediately_changes_resume_id_even_when_run_fails():
    requests = []
    responses = [
        _sse(
            {"kind": "session.updated", "upstream_session_id": "native-1"},
            {
                "kind": "run.failed",
                "error": "failed",
                "error_code": "provider_process_failed",
                "upstream_session_id": "native-1",
            },
        ),
        _sse({"kind": "run.completed", "content": "recovered"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=responses[len(requests) - 1],
            headers={"content-type": "text/event-stream"},
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunFailedError):
        await client.complete([{"role": "user", "content": "first"}])
    await client.complete([{"role": "user", "content": "retry"}])

    assert client._upstream_session_id == "native-1"
    assert requests[0]["session"]["upstream_id"] == ""
    assert requests[1]["session"]["upstream_id"] == "native-1"


@pytest.mark.asyncio
async def test_complete_rejects_upstream_session_id_change() -> None:
    body = _sse(
        {"kind": "session.updated", "upstream_session_id": "session-1"},
        {
            "kind": "run.completed",
            "content": "done",
            "upstream_session_id": "session-2",
        },
    )
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=_transport(body),
    )

    with pytest.raises(
        RemoteRunProtocolError,
        match="upstream_session_id_changed",
    ):
        await client.complete([{"role": "user", "content": "hi"}])

    assert client._upstream_session_id == "session-1"


@pytest.mark.asyncio
async def test_complete_rejects_changed_session_id_when_resuming() -> None:
    body = _sse(
        {"kind": "session.updated", "upstream_session_id": "different"},
        {"kind": "run.completed", "content": "done"},
    )
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        upstream_session_id="existing",
        transport=_transport(body),
    )

    with pytest.raises(
        RemoteRunProtocolError,
        match="upstream_session_id_changed",
    ):
        await client.complete([{"role": "user", "content": "hi"}])

    assert client._upstream_session_id == "existing"


@pytest.mark.asyncio
async def test_terminal_session_id_is_returned_but_not_saved_for_resume() -> None:
    body = _sse(
        {
            "kind": "run.completed",
            "content": "done",
            "upstream_session_id": "terminal-only",
        },
    )
    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=_transport(body),
    )

    result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.upstream_session_id == "terminal-only"
    assert client._upstream_session_id is None


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
async def test_complete_does_not_read_events_after_terminal():
    body = _sse(
        {"kind": "run.completed", "content": "final"},
        {"kind": "message.completed", "text": "LATE"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "final"


@pytest.mark.asyncio
async def test_complete_uses_first_terminal_as_authoritative():
    body = _sse(
        {"kind": "run.completed", "content": "final"},
        {"kind": "run.completed", "content": "OVERWRITE"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "final"


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
@pytest.mark.parametrize(
    ("status_code", "gateway_code", "expected_code", "diagnostic"),
    [
        (409, "session_busy", "session_busy", "remote_session_busy"),
        (
            409,
            "session_identity_conflict",
            "session_identity_conflict",
            "remote_session_identity_conflict",
        ),
        (
            409,
            "storage_metadata_stale",
            "storage_metadata_stale",
            "remote_storage_metadata_stale",
        ),
        (
            429,
            "capacity_exceeded",
            "capacity_exceeded",
            "remote_capacity_exceeded",
        ),
        (
            422,
            "invalid_execution_path",
            "invalid_execution_path",
            "remote_invalid_execution_path",
        ),
        (
            422,
            "unsupported_execution_capability",
            "unsupported_execution_capability",
            "remote_unsupported_execution_capability",
        ),
        (
            503,
            "provider_not_ready",
            "provider_not_ready",
            "remote_provider_not_ready",
        ),
    ],
)
async def test_pre_stream_gateway_errors_are_typed(
    status_code,
    gateway_code,
    expected_code,
    diagnostic,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"code": gateway_code, "error": "sensitive detail"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == expected_code
    assert str(raised.value) == diagnostic
    assert "sensitive detail" not in str(raised.value)


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
    def __init__(self, terminal: dict | None = None) -> None:
        self._terminal = terminal or {"kind": "run.completed", "content": "done"}

    async def __aiter__(self):
        yield _sse(self._terminal)
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_completed_terminal_returns_without_waiting_for_stream_eof():
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

    result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "error_type", "expected_code"),
    [
        (
            {
                "kind": "run.failed",
                "error": "provider failed",
                "error_code": "provider_process_failed",
            },
            RemoteRunFailedError,
            "provider_process_failed",
        ),
        (
            {
                "kind": "run.aborted",
                "error": "deadline exceeded",
                "error_code": "run_timeout",
            },
            RemoteRunAbortedError,
            "run_timeout",
        ),
    ],
)
async def test_error_terminal_raises_without_waiting_for_stream_eof(
    terminal,
    error_type,
    expected_code,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_HangingAfterTerminalStream(terminal),
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

    with pytest.raises(error_type) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == expected_code
    assert str(raised.value) == terminal["error"]


@pytest.mark.asyncio
async def test_default_http_read_timeout_outlives_lmg_idle_kill_and_terminal():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions["timeout"])
        return httpx.Response(
            200,
            content=_sse({"kind": "run.completed", "content": "done"}),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        idle_timeout_seconds=600,
        transport=httpx.MockTransport(handler),
    )

    await client.complete([{"role": "user", "content": "hi"}])

    assert captured["read"] == 620


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


@pytest.mark.asyncio
async def test_complete_operation_uses_supplied_consumer_run_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse({"kind": "message.completed", "text": "done"}, {"kind": "run.completed", "content": "done"}),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "claude",
        "sonnet",
        execution={},
        transport=httpx.MockTransport(handler),
    )

    await client.complete_operation(
        [{"role": "user", "content": "work"}],
        consumer_run_id="operation-attempt-1",
    )

    assert captured["consumer_run_id"] == "operation-attempt-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connect"),
        httpx.WriteError("write"),
        httpx.PoolTimeout("pool"),
    ],
)
async def test_pre_response_admission_transport_failures_are_safe(error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete_operation(
            [{"role": "user", "content": "work"}],
            consumer_run_id="operation-attempt-2",
        )

    assert raised.value.pre_stream is True
    assert raised.value.consumer_run_id == "operation-attempt-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,gateway_code",
    [(429, "capacity_exceeded"), (503, "provider_not_ready")],
)
async def test_http_admission_failures_are_safe(status_code, gateway_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"code": gateway_code, "error": "sensitive detail"},
            request=request,
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunFailedError) as raised:
        await client.complete_operation(
            [{"role": "user", "content": "work"}],
            consumer_run_id="operation-attempt-3",
        )

    assert raised.value.pre_stream is True
    assert raised.value.consumer_run_id == "operation-attempt-3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("read"), httpx.ReadError("read"), TimeoutError()],
)
async def test_read_and_response_open_timeouts_are_ambiguous(error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RemoteRunError) as raised:
        await client.complete_operation(
            [{"role": "user", "content": "work"}],
            consumer_run_id="operation-attempt-read",
        )

    assert raised.value.pre_stream is False
    assert raised.value.consumer_run_id == "operation-attempt-read"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        _sse(
            {
                "kind": "run.failed",
                "error": "failed",
                "error_code": "provider_process_failed",
            }
        ),
        _sse({"kind": "message.completed", "text": "partial"}),
    ],
)
async def test_terminal_and_incomplete_stream_failures_are_ambiguous(body):
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))

    with pytest.raises(RemoteRunError) as raised:
        await client.complete_operation(
            [{"role": "user", "content": "work"}],
            consumer_run_id="operation-attempt-4",
        )

    assert raised.value.pre_stream is False
    assert raised.value.consumer_run_id == "operation-attempt-4"


def test_a_completed_run_carries_the_token_counts_the_gateway_reported():
    """게이트웨이가 종료 이벤트에 사용량을 싣기 시작했다. 여기서 읽지 않으면
    한 층 위에서 저장할 것이 없다."""
    response = _terminal_result(
        {
            "kind": "run.completed",
            "content": "done",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 45,
                "cache_creation_input_tokens": 900,
                "cache_read_input_tokens": 7000,
            },
        },
        None,
    )

    assert response.usage == {
        "input_tokens": 120,
        "output_tokens": 45,
        "cache_creation_input_tokens": 900,
        "cache_read_input_tokens": 7000,
    }


def test_a_run_without_reported_usage_leaves_it_unset():
    """보고하지 않은 것과 0 을 쓴 것은 다르다. 0 으로 채우면 합계가 조용히
    낮아지고, 어느 프로바이더가 보고를 안 하는지도 알 수 없게 된다."""
    response = _terminal_result({"kind": "run.completed", "content": "done"}, None)

    assert response.usage is None
