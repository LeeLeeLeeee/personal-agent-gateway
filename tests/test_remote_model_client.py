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
    assert raw in seen  # original codex event relayed verbatim


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
