import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

import httpx

from personal_agent_gateway.model_client import (
    INTERNAL_TOOL_NAMES,
    WIRE_TOOL_NAMES,
    ModelResponse,
    ToolCall,
)

_EVENT_KINDS = {
    "run.started",
    "session.updated",
    "message.delta",
    # 전체 텍스트 스냅샷. 누적이 아니라 교체 의미 (LMG Task 8).
    "message.snapshot",
    "reasoning.delta",
    "tool.activity",
    "message.completed",
    # 사용자에게 보일 출력은 끝났고 종료 정리만 남은 경계 (LMG Task 8).
    "output.completed",
    # 상류 재시도 대기 중 (LMG Task 8).
    "run.retrying",
    # 열린 provider item이 살아 있음을 알리는 LMG 생존 신호.
    "run.heartbeat",
    "run.completed",
    "run.failed",
    "run.aborted",
}
_TERMINAL_KINDS = {"run.completed", "run.failed", "run.aborted"}
_FAILURE_CODES = {
    "provider_unavailable",
    "provider_not_ready",
    "provider_auth_required",
    "provider_rate_limited",
    "provider_protocol_error",
    "provider_process_failed",
}
_ABORT_CODES = {"run_cancelled", "run_timeout"}
_STRING_FIELDS = {
    "kind",
    "run_id",
    "model",
    "provider",
    "text",
    "upstream_session_id",
    "content",
    "error",
    "error_code",
    "partial_content",
}
_DEFAULT_TERMINAL_DELIVERY_GRACE_SECONDS = 20


class RemoteRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        diagnostic: str,
        *,
        pre_stream: bool = False,
        consumer_run_id: str | None = None,
        partial_content: str = "",
        upstream_session_id: str | None = None,
    ) -> None:
        super().__init__(diagnostic)
        self.code = code
        self.diagnostic = diagnostic
        self.pre_stream = pre_stream
        self.consumer_run_id = consumer_run_id
        self.partial_content = partial_content
        self.upstream_session_id = upstream_session_id


class RemoteRunFailedError(RemoteRunError):
    pass


class RemoteRunAbortedError(RemoteRunError):
    pass


class RemoteRunProtocolError(RemoteRunError):
    pass


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
        local_token: str | None = None,
        consumer: str = "personal-agent-gateway",
        consumer_session_id: str | None = None,
        consumer_context_fingerprint: str | None = None,
        timeout_seconds: float = 3600,
        idle_timeout_seconds: float = 600,
        timeout_grace_seconds: float = _DEFAULT_TERMINAL_DELIVERY_GRACE_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._model = model
        self._execution = execution
        self._on_event = on_event
        self._upstream_session_id = upstream_session_id
        self._local_token = local_token
        self._consumer = consumer
        self._consumer_session_id = consumer_session_id
        self._consumer_context_fingerprint = consumer_context_fingerprint
        self._timeout_seconds = timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._timeout_grace_seconds = timeout_grace_seconds
        self._transport = transport

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        return await self.complete_operation(
            messages,
            consumer_run_id=str(uuid.uuid4()),
        )

    async def complete_operation(
        self,
        messages: list[dict[str, object]],
        *,
        consumer_run_id: str,
    ) -> ModelResponse:
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
            "timeout_ms": int(self._timeout_seconds * 1000),
            "idle_timeout_ms": int(self._idle_timeout_seconds * 1000),
            "consumer": self._consumer,
            "consumer_run_id": consumer_run_id,
            **body_extra,
        }
        if self._consumer_session_id is not None:
            body["consumer_session_id"] = self._consumer_session_id
        if (
            self._consumer_session_id is not None
            and self._consumer_context_fingerprint is not None
        ):
            body["consumer_context_fingerprint"] = self._consumer_context_fingerprint
        upstream_session_id = self._upstream_session_id
        headers = {}
        if self._local_token is not None:
            headers["Authorization"] = f"Bearer {self._local_token}"
        terminal: dict[str, object] | None = None
        run_id: str | None = None
        frame_event: str | None = None
        frame_data: list[str] = []
        partial_content = ""
        event_count = 0
        started_seen = False

        async def process_frame() -> None:
            nonlocal event_count, frame_data, frame_event, partial_content
            nonlocal run_id, started_seen, terminal, upstream_session_id
            if frame_event is None and not frame_data:
                return
            if frame_event is None or not frame_data:
                raise contextual_protocol_error("invalid_sse_frame")
            try:
                event = _decode_event(frame_event, "\n".join(frame_data))
            except RemoteRunProtocolError as exc:
                raise _with_protocol_context(
                    exc,
                    partial_content,
                    upstream_session_id,
                    consumer_run_id,
                ) from exc
            frame_event = None
            frame_data = []
            kind = event["kind"]
            if event_count == 0 and kind != "run.started":
                raise contextual_protocol_error("missing_run_started")
            if kind == "run.started":
                if started_seen:
                    raise contextual_protocol_error("duplicate_run_started")
                started_seen = True
            event_count += 1

            event_run_id = event["run_id"]
            if run_id is None:
                run_id = event_run_id
            elif run_id != event_run_id:
                raise contextual_protocol_error("run_id_changed")

            if terminal is not None:
                if kind in _TERMINAL_KINDS:
                    raise contextual_protocol_error("duplicate_terminal")
                raise contextual_protocol_error("event_after_terminal")

            sid = event.get("upstream_session_id")
            if isinstance(sid, str) and sid:
                if upstream_session_id is not None and sid != upstream_session_id:
                    raise contextual_protocol_error("upstream_session_id_changed")
                upstream_session_id = sid
                if kind == "session.updated":
                    self._upstream_session_id = sid
            if kind == "message.delta":
                partial_content += str(event.get("text", ""))
            elif kind in ("message.snapshot", "message.completed"):
                partial_content = str(event.get("text", ""))
            if kind in _TERMINAL_KINDS:
                terminal_partial = event.get("partial_content")
                if isinstance(terminal_partial, str):
                    partial_content = terminal_partial
                terminal = event
                return
            if self._on_event is not None:
                await self._on_event(event)

        def contextual_protocol_error(
            diagnostic: str,
            *,
            code: str = "provider_protocol_error",
        ) -> RemoteRunProtocolError:
            return RemoteRunProtocolError(
                code,
                diagnostic,
                consumer_run_id=consumer_run_id,
                partial_content=partial_content,
                upstream_session_id=upstream_session_id,
            )

        request_timeout = httpx.Timeout(
            connect=min(max(self._timeout_seconds, 0.001), 10.0),
            read=max(
                self._idle_timeout_seconds + self._timeout_grace_seconds,
                0.001,
            ),
            write=min(max(self._timeout_seconds, 0.001), 30.0),
            pool=min(max(self._timeout_seconds, 0.001), 10.0),
        )
        try:
            async with asyncio.timeout(
                max(self._timeout_seconds + self._timeout_grace_seconds, 0.001)
            ):
                async with (
                    httpx.AsyncClient(
                        timeout=request_timeout,
                        transport=self._transport,
                    ) as client,
                    client.stream(
                        "POST",
                        f"{self._base_url}/v1/runs",
                        json=body,
                        headers=headers,
                    ) as response,
                ):
                    if not response.is_success:
                        await response.aread()
                        raise _http_run_error(response, consumer_run_id)

                    async for line in response.aiter_lines():
                        if line == "":
                            await process_frame()
                            if terminal is not None:
                                break
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            if frame_event is not None:
                                raise contextual_protocol_error(
                                    "duplicate_event_field"
                                )
                            frame_event = line[len("event:"):].strip()
                            continue
                        if line.startswith("data:"):
                            frame_data.append(line[len("data:"):].lstrip())
                            continue
                        raise contextual_protocol_error("invalid_sse_field")
                    await process_frame()
        except RemoteRunProtocolError as exc:
            raise _with_protocol_context(
                exc,
                partial_content,
                upstream_session_id,
                consumer_run_id,
            ) from exc
        except RemoteRunError:
            raise
        except TimeoutError as exc:
            raise RemoteRunAbortedError(
                "run_timeout",
                "remote_run_timeout",
                consumer_run_id=consumer_run_id,
                partial_content=partial_content,
                upstream_session_id=upstream_session_id,
            ) from exc
        except httpx.TimeoutException as exc:
            if _is_safe_admission_error(exc):
                raise RemoteRunFailedError(
                    "provider_unavailable",
                    "remote_gateway_unavailable",
                    pre_stream=True,
                    consumer_run_id=consumer_run_id,
                ) from exc
            raise RemoteRunAbortedError(
                "run_timeout",
                "remote_run_timeout",
                consumer_run_id=consumer_run_id,
                partial_content=partial_content,
                upstream_session_id=upstream_session_id,
            ) from exc
        except httpx.RequestError as exc:
            if _is_safe_admission_error(exc):
                raise RemoteRunFailedError(
                    "provider_unavailable",
                    "remote_gateway_unavailable",
                    pre_stream=True,
                    consumer_run_id=consumer_run_id,
                ) from exc
            raise contextual_protocol_error("stream_read_error") from exc

        if not started_seen:
            raise contextual_protocol_error("missing_run_started")
        if terminal is None:
            raise contextual_protocol_error(
                "missing_terminal",
                code="upstream_stream_incomplete",
            )
        try:
            return _terminal_result(terminal, upstream_session_id, consumer_run_id)
        except RemoteRunProtocolError as exc:
            raise _with_protocol_context(
                exc,
                partial_content,
                upstream_session_id,
                consumer_run_id,
            ) from exc


def _decode_event(event_name: str, raw_data: str) -> dict[str, object]:
    try:
        raw_event = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise _protocol_error("malformed_event_json") from exc
    if not isinstance(raw_event, dict):
        raise _protocol_error("event_is_not_object")
    event = {str(key): value for key, value in raw_event.items()}
    kind = event.get("kind")
    if not isinstance(kind, str) or kind not in _EVENT_KINDS:
        raise _protocol_error("invalid_event_kind")
    if event_name != kind:
        raise _protocol_error("sse_event_kind_mismatch")
    run_id = event.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise _protocol_error("invalid_run_id")
    for field in _STRING_FIELDS:
        if field in event and not isinstance(event[field], str):
            raise _protocol_error("invalid_event_shape")
    if "tool" in event and not isinstance(event["tool"], dict):
        raise _protocol_error("invalid_event_shape")
    if "tool_calls" in event and not isinstance(event["tool_calls"], list):
        raise _protocol_error("invalid_event_shape")
    return event


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _parse_usage(value: object) -> dict[str, int] | None:
    """게이트웨이가 실어 보낸 토큰 수를 읽는다.

    없으면 None 이다. 0 으로 채우지 않는 이유: 보고하지 않는 프로바이더와
    정말 아무것도 안 쓴 호출이 구분되지 않게 되고, 합계는 후자로 읽힌다.

    모르는 칸은 버린다. 프로바이더마다 이름이 달라 새 칸이 붙을 수 있는데,
    그것을 그대로 저장하면 합계를 내는 쪽이 무엇을 더해야 할지 모른다.
    """
    if not isinstance(value, dict):
        return None
    counts = {
        key: int(value[key])
        for key in _USAGE_KEYS
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
    }
    return counts or None


def _terminal_result(
    terminal: dict[str, object],
    upstream_session_id: str | None,
    consumer_run_id: str | None = None,
) -> ModelResponse:
    kind = terminal["kind"]
    terminal_session_id = terminal.get("upstream_session_id")
    if isinstance(terminal_session_id, str) and terminal_session_id:
        upstream_session_id = terminal_session_id
    if kind == "run.completed":
        content = terminal.get("content", "")
        if not isinstance(content, str):
            raise _protocol_error("invalid_event_shape")
        return ModelResponse(
            content=content,
            tool_calls=_parse_tool_calls(terminal.get("tool_calls")),
            upstream_session_id=upstream_session_id,
            usage=_parse_usage(terminal.get("usage")),
        )

    code = terminal.get("error_code")
    error = terminal.get("error", "")
    partial_content = terminal.get("partial_content", "")
    if not isinstance(error, str) or not isinstance(partial_content, str):
        raise _protocol_error("invalid_event_shape")
    if kind == "run.failed":
        if code is None:
            code = "provider_process_failed"
        elif not isinstance(code, str) or code not in _FAILURE_CODES:
            raise _protocol_error("invalid_terminal_error_code")
        raise RemoteRunFailedError(
            code,
            error or "remote_run_failed",
            consumer_run_id=consumer_run_id,
            partial_content=partial_content,
            upstream_session_id=upstream_session_id,
        )
    if not isinstance(code, str) or code not in _ABORT_CODES:
        raise _protocol_error("invalid_terminal_error_code")
    raise RemoteRunAbortedError(
        code,
        error or "remote_run_aborted",
        consumer_run_id=consumer_run_id,
        partial_content=partial_content,
        upstream_session_id=upstream_session_id,
    )


def _http_run_error(
    response: httpx.Response,
    consumer_run_id: str | None = None,
) -> RemoteRunFailedError:
    code = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
        code = payload["code"]
    typed_errors = {
        (409, "session_busy"): ("session_busy", "remote_session_busy"),
        (409, "session_identity_conflict"): (
            "session_identity_conflict",
            "remote_session_identity_conflict",
        ),
        (409, "storage_metadata_stale"): (
            "storage_metadata_stale",
            "remote_storage_metadata_stale",
        ),
        (429, "capacity_exceeded"): (
            "capacity_exceeded",
            "remote_capacity_exceeded",
        ),
        (422, "invalid_execution_path"): (
            "invalid_execution_path",
            "remote_invalid_execution_path",
        ),
        (422, "unsupported_execution_capability"): (
            "unsupported_execution_capability",
            "remote_unsupported_execution_capability",
        ),
        (503, "provider_not_ready"): (
            "provider_not_ready",
            "remote_provider_not_ready",
        ),
    }
    mapped = typed_errors.get((response.status_code, code))
    if mapped is not None:
        return RemoteRunFailedError(
            *mapped,
            pre_stream=True,
            consumer_run_id=consumer_run_id,
        )
    if response.status_code == 503:
        return RemoteRunFailedError(
            "provider_unavailable",
            "remote_provider_unavailable",
            pre_stream=True,
            consumer_run_id=consumer_run_id,
        )
    return RemoteRunFailedError(
        "provider_process_failed",
        f"remote_gateway_http_{response.status_code}",
        pre_stream=True,
        consumer_run_id=consumer_run_id,
    )


def _protocol_error(diagnostic: str) -> RemoteRunProtocolError:
    return RemoteRunProtocolError("provider_protocol_error", diagnostic)


def _with_protocol_context(
    exc: RemoteRunProtocolError,
    partial_content: str,
    upstream_session_id: str | None,
    consumer_run_id: str | None = None,
) -> RemoteRunProtocolError:
    return RemoteRunProtocolError(
        exc.code,
        exc.diagnostic,
        consumer_run_id=consumer_run_id or exc.consumer_run_id,
        partial_content=partial_content or exc.partial_content,
        upstream_session_id=upstream_session_id or exc.upstream_session_id,
    )


def _is_safe_admission_error(exc: httpx.RequestError) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.WriteError,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ),
    )


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
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _protocol_error("invalid_tool_calls")
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _protocol_error("invalid_tool_call")
        call_id = item.get("id")
        name = item.get("name")
        if not isinstance(call_id, str) or not call_id:
            raise _protocol_error("invalid_tool_call_id")
        if not isinstance(name, str) or not name:
            raise _protocol_error("invalid_tool_call_name")
        args = item.get("arguments")
        if not isinstance(args, dict):
            raise _protocol_error("invalid_tool_call_arguments")
        internal = INTERNAL_TOOL_NAMES.get(name, name)
        calls.append(ToolCall(id=call_id, name=internal, arguments=args))
    return calls
