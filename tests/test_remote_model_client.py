import json
import httpx
import pytest

from personal_agent_gateway.remote_model_client import HttpModelClient


def _sse(*events: dict) -> bytes:
    out = []
    for e in events:
        out.append(f"event: {e['kind']}\ndata: {json.dumps(e)}\n\n")
    return "".join(out).encode()


def _transport(body: bytes, capture: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["body"] = json.loads(request.content)
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
    assert kinds == ["run.started", "message.delta", "message.completed"]
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
async def test_run_failed_raises():
    body = _sse({"kind": "run.failed", "error": "boom"})
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    with pytest.raises(RuntimeError, match="boom"):
        await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_complete_returns_on_first_run_completed_ignoring_trailing_events():
    body = _sse(
        {"kind": "run.completed", "content": "final"},
        {"kind": "message.completed", "text": "LATE"},
        {"kind": "run.completed", "content": "OVERWRITE"},
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "final"


@pytest.mark.asyncio
async def test_complete_skips_malformed_lines_without_raising():
    body = (
        b"data: not json\n\n"
        b"event: ping\n\n"
        b"data: {\"foo\": \"bar\"}\n\n"
        b'data: {"kind": "run.completed", "content": "ok"}\n\n'
    )
    client = HttpModelClient("http://lmg", "codex", "default", {}, transport=_transport(body))
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "ok"


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
