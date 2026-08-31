import asyncio
import json

import httpx
import pytest

from personal_agent_gateway.remote_model_client import (
    HttpModelClient,
    RemoteRunAbortedError,
    RemoteRunFailedError,
    RemoteRunProtocolError,
    _decode_event,
    _terminal_result,
)


def _sse(*events: dict) -> str:
    normalized = list(events)
    if not normalized or normalized[0].get("kind") != "run.started":
        normalized.insert(0, {"kind": "run.started"})
    frames = []
    for event in normalized:
        full = {"run_id": "run-1", **event}
        frames.append(f"event: {full['kind']}\ndata: {json.dumps(full)}\n\n")
    return "".join(frames)


@pytest.mark.parametrize(
    "kind",
    ["message.snapshot", "output.completed", "run.retrying", "run.heartbeat"],
)
def test_decode_event_accepts_extended_kinds(kind: str) -> None:
    event = _decode_event(kind, '{"kind": "%s", "run_id": "run-1"}' % kind)

    assert event["kind"] == kind


def test_decode_event_still_rejects_unknown_kind() -> None:
    with pytest.raises(RemoteRunProtocolError):
        _decode_event("message.bogus", '{"kind": "message.bogus", "run_id": "run-1"}')


def test_decode_event_passes_through_unknown_fields() -> None:
    event = _decode_event(
        "tool.activity",
        '{"kind": "tool.activity", "run_id": "run-1", "subagent": {"parent_tool_call_id": "t1"}}',
    )

    assert event["subagent"] == {"parent_tool_call_id": "t1"}


@pytest.mark.parametrize(
    "code",
    ["provider_auth_required", "provider_rate_limited"],
)
def test_terminal_result_accepts_extended_failure_codes(code: str) -> None:
    terminal = {
        "kind": "run.failed",
        "run_id": "run-1",
        "error": "boom",
        "error_code": code,
    }

    with pytest.raises(RemoteRunFailedError) as raised:
        _terminal_result(terminal, None)

    assert raised.value.code == code


def test_terminal_result_still_rejects_unknown_failure_code() -> None:
    terminal = {
        "kind": "run.failed",
        "run_id": "run-1",
        "error": "boom",
        "error_code": "provider_made_up",
    }

    with pytest.raises(RemoteRunProtocolError):
        _terminal_result(terminal, None)


class _StalledStream(httpx.AsyncByteStream):
    """Yields the given frames, then hangs — simulating a run that stops
    streaming mid-flight instead of sending a terminal frame."""

    def __init__(self, chunks: list[bytes], hang_seconds: float) -> None:
        self._chunks = chunks
        self._hang_seconds = hang_seconds

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        await asyncio.sleep(self._hang_seconds)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_snapshot_text_survives_a_run_aborted_by_timeout() -> None:
    chunks = [
        _sse({"kind": "message.snapshot", "text": "snapshot so far"}).encode()
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_StalledStream(chunks, hang_seconds=10),
            headers={"content-type": "text/event-stream"},
        )

    client = HttpModelClient(
        "http://lmg",
        "codex",
        "default",
        {},
        transport=httpx.MockTransport(handler),
        timeout_seconds=0.05,
        idle_timeout_seconds=0.05,
        timeout_grace_seconds=0.01,
    )

    with pytest.raises(RemoteRunAbortedError) as raised:
        await client.complete([{"role": "user", "content": "hi"}])

    assert raised.value.code == "run_timeout"
    assert raised.value.partial_content == "snapshot so far"
