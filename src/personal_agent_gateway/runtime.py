import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.events import EventBus, EventScope
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import ModelClient, ToolCall
from personal_agent_gateway.redaction import is_sensitive_key, redact_text
from personal_agent_gateway.remote_model_client import (
    RemoteRunAbortedError,
    RemoteRunError,
)
from personal_agent_gateway.tools import ShellResult, WorkspaceTools
from personal_agent_gateway.transcript import TranscriptEvent, TranscriptStore

RuntimeTermination = Literal["completed", "failed", "cancelled", "timed_out"]


@dataclass(frozen=True)
class RuntimeResult:
    messages: list[dict[str, object]]
    pending_approval: dict[str, object] | None
    termination: RuntimeTermination = "completed"
    error_code: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class PendingShellRequest:
    approval_id: str
    tool_call_id: str
    command: str


class AgentRuntime:
    def __init__(
        self,
        transcript: TranscriptStore,
        tools: WorkspaceTools,
        model: ModelClient,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
        history_mode: Literal["full", "latest_user"] = "full",
        session_id: str | None = None,
        system_prompt: str | None = None,
        archive_service: ArchiveService | None = None,
        persona_id: str | None = None,
    ) -> None:
        self._transcript = transcript
        self._tools = tools
        self._model = model
        self._job_service = job_service
        self._event_bus = event_bus
        self._history_mode = history_mode
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._archive_service = archive_service
        self._persona_id = persona_id
        self._scopes: dict[str, EventScope] = {}

    def attach_event_bus(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    def for_session(self, session_id: str) -> "AgentRuntime":
        return AgentRuntime(
            self._transcript,
            self._tools,
            self._model,
            job_service=self._job_service,
            event_bus=self._event_bus,
            history_mode=self._history_mode,
            session_id=session_id,
            system_prompt=self._system_prompt,
            archive_service=self._archive_service,
            persona_id=self._persona_id,
        )

    async def handle_user_message(self, content: str) -> RuntimeResult:
        session_id: str | None = None
        try:
            session_id = self._resolve_session_id(create=True)
            pending = _unresolved_shell_request(self._transcript.load(session_id))
            if pending is not None:
                self._restore_pending_shell(pending)
                return RuntimeResult(messages=[], pending_approval=_pending_response(pending))

            await self._publish(
                "runtime.user_message.started",
                {"message": content, "session_id": session_id},
            )
            self._append("user", {"content": content}, session_id)
            result = await self._run_model_loop(session_id)
            await self._publish(
                "runtime.completed",
                {
                    "session_id": session_id,
                    "pending_approval": result.pending_approval,
                    "termination": result.termination,
                },
            )
            return result
        except asyncio.CancelledError:
            await self._record_cancellation(session_id)
            raise
        except Exception as exc:  # noqa: BLE001
            return await self._handle_runtime_error(exc, session_id)

    async def approve(self, approval_id: str) -> RuntimeResult:
        session_id: str | None = None
        try:
            session_id = self._resolve_session_id(create=False)
            pending = _unresolved_shell_request_for_approval(
                self._transcript.load(session_id) if session_id is not None else [],
                approval_id,
            )
            if pending is None:
                raise RuntimeError(f"No pending approval: {approval_id}")

            self._restore_pending_shell(pending)
            result = self._tools.approve_shell(approval_id)
            self._append(
                "approval",
                {
                    "id": result.approval_id,
                    "command": result.command,
                    "status": "approved",
                },
                session_id,
            )
            self._append("tool_result", _shell_result_payload(result, pending.tool_call_id), session_id)
            runtime_result = await self._run_model_loop(session_id)
            await self._publish_completion(runtime_result, session_id)
            return runtime_result
        except asyncio.CancelledError:
            await self._record_cancellation(session_id)
            raise
        except Exception as exc:  # noqa: BLE001
            return await self._handle_runtime_error(exc, session_id)

    async def deny(self, approval_id: str) -> RuntimeResult:
        session_id: str | None = None
        try:
            session_id = self._resolve_session_id(create=False)
            pending = _unresolved_shell_request_for_approval(
                self._transcript.load(session_id) if session_id is not None else [],
                approval_id,
            )
            if pending is None:
                raise RuntimeError(f"No pending approval: {approval_id}")

            self._restore_pending_shell(pending)
            denied = self._tools.deny_shell(approval_id)
            self._append(
                "tool_denial",
                {
                    "id": pending.tool_call_id,
                    "command": denied.command,
                    "status": denied.status,
                },
                session_id,
            )
            runtime_result = RuntimeResult(
                messages=[{"role": "assistant", "content": "Command denied."}],
                pending_approval=None,
            )
            await self._publish_completion(runtime_result, session_id)
            return runtime_result
        except asyncio.CancelledError:
            await self._record_cancellation(session_id)
            raise
        except Exception as exc:  # noqa: BLE001
            return await self._handle_runtime_error(exc, session_id)

    async def _run_model_loop(self, session_id: str) -> RuntimeResult:
        for _iteration in range(8):
            events = self._transcript.load(session_id)
            messages = _events_to_messages(
                events,
                latest_user_only=self._history_mode == "latest_user",
            )
            system_messages: list[dict[str, object]] = []
            if self._system_prompt:
                system_messages.append({"role": "system", "content": self._system_prompt})
            if self._archive_service is not None:
                system_messages.append(
                    {
                        "role": "system",
                        "content": self._archive_service.prompt_context(
                            _latest_user_content(events),
                            persona_id=self._persona_id,
                        ),
                    }
                )
            messages[0:0] = system_messages
            response = await self._model.complete(messages)

            if not response.tool_calls:
                content = response.content
                if self._archive_service is not None:
                    content, _requests = self._archive_service.capture_response_requests(
                        content,
                        persona_id=self._persona_id,
                        session_id=session_id,
                    )
                if content:
                    self._append("assistant", {"content": content}, session_id)
                return RuntimeResult(
                    messages=[{"role": "assistant", "content": content}],
                    pending_approval=None,
                )

            for tool_call in response.tool_calls:
                pending = self._handle_tool_call(tool_call, session_id)
                if pending is not None:
                    return RuntimeResult(messages=[], pending_approval=pending)

        raise RuntimeError("Tool loop exceeded 8 iterations")

    def _handle_tool_call(self, tool_call: ToolCall, session_id: str) -> dict[str, object] | None:
        if tool_call.name == "shell.run":
            command = _required_string(tool_call.arguments, "command", "shell.run")
            pending = self._tools.shell_request(command)
            self._create_shell_job(command, session_id)
            self._append(
                "tool_request",
                {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "approval_id": pending.id,
                },
                session_id,
            )
            return {"id": pending.id, "command": pending.command}

        self._append(
            "tool_request",
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            },
            session_id,
        )
        if tool_call.name == "fs.list":
            result: object = self._tools.fs_list(_optional_path(tool_call.arguments))
        elif tool_call.name == "fs.read":
            result = self._tools.fs_read(_required_string(tool_call.arguments, "path", "fs.read"))
        else:
            raise RuntimeError(f"Unsupported tool: {tool_call.name}")

        self._append(
            "tool_result",
            {"id": tool_call.id, "name": tool_call.name, "result": result},
            session_id,
        )
        return None

    def _create_shell_job(self, command: str, session_id: str) -> None:
        if self._job_service is None:
            return
        self._job_service.create_job(
            capability_id="shell.run",
            source="chat",
            title="Shell command",
            input_json={"command": command},
            source_session_id=session_id,
            command_preview=command,
        )

    def _append(self, kind: str, payload: dict[str, object], session_id: str | None) -> TranscriptEvent:
        if session_id is None:
            return self._transcript.append(kind, _redact_payload(payload))
        return self._transcript.append_to(session_id, kind, _redact_payload(payload))

    def _restore_pending_shell(self, pending: PendingShellRequest) -> None:
        self._tools.approvals.restore_pending(pending.approval_id, pending.command)

    async def _handle_runtime_error(
        self,
        exc: Exception,
        session_id: str | None = None,
    ) -> RuntimeResult:
        payload = _runtime_error_payload(exc)
        self._append("runtime_error", payload, session_id)
        await self._publish("runtime.error", {"session_id": session_id, **payload})
        return RuntimeResult(
            messages=[
                {
                    "role": "assistant",
                    "content": f"Error: {payload['message']}",
                }
            ],
            pending_approval=None,
            termination=payload["termination"],
            error_code=str(payload["error_code"]),
            diagnostic=str(payload["message"]),
        )

    async def _record_cancellation(self, session_id: str | None) -> None:
        await self._handle_runtime_error(
            RemoteRunAbortedError(
                "run_cancelled",
                "remote_run_cancelled",
            ),
            session_id,
        )

    async def _publish_completion(
        self,
        result: RuntimeResult,
        session_id: str | None,
    ) -> None:
        await self._publish(
            "runtime.completed",
            {
                "session_id": session_id,
                "pending_approval": result.pending_approval,
                "termination": result.termination,
            },
        )

    def _resolve_session_id(self, create: bool) -> str | None:
        if self._session_id is not None:
            return self._session_id
        session_id = self._transcript.active_id()
        if session_id is None and create:
            session_id = self._transcript.start_new()
        return session_id

    async def _publish(self, event_type: str, payload: dict[str, object]) -> None:
        if self._event_bus is None:
            return
        session_id = payload.get("session_id")
        redacted = _redact_payload(payload)
        if isinstance(session_id, str) and session_id:
            scope = self._scopes.setdefault(session_id, self._event_bus.scope(session_id))
            await scope.publish({"type": event_type, **redacted})
            return
        await self._event_bus.publish({"type": event_type, **redacted})


def _events_to_messages(
    events: list[TranscriptEvent],
    latest_user_only: bool = False,
) -> list[dict[str, object]]:
    selected_events = events
    if latest_user_only:
        selected_events = _latest_user_slice(events)

    messages: list[dict[str, object]] = []
    for event in selected_events:
        if event.kind in {"user", "assistant"}:
            messages.append(
                {
                    "role": event.kind,
                    "content": _content(event.payload),
                }
            )
        elif event.kind == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(event.payload.get("id", "")),
                    "content": json.dumps(event.payload.get("result", event.payload), sort_keys=True),
                }
            )
        elif event.kind == "tool_request":
            messages.append(_tool_request_message(event.payload))
        elif event.kind == "tool_denial":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(event.payload.get("id", "")),
                    "content": "denied",
                }
            )
        elif event.kind in {
            "approval",
            "runtime_error",
            "agent_session_link",
            "session_config_set",
            "session_metadata_set",
        }:
            continue
        else:
            messages.append(
                {
                    "role": "system",
                    "content": json.dumps(
                        {"kind": event.kind, "payload": event.payload},
                        sort_keys=True,
                    ),
                }
            )
    return messages


def _latest_user_slice(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    for index in range(len(events) - 1, -1, -1):
        if events[index].kind == "user":
            return events[index:]
    return []


def _latest_user_content(events: list[TranscriptEvent]) -> str:
    for event in reversed(events):
        if event.kind == "user":
            return _content(event.payload)
    return ""


def _tool_request_message(payload: dict[str, object]) -> dict[str, object]:
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": str(payload.get("id", "")),
                "type": "function",
                "function": {
                    "name": str(payload.get("name", "")),
                    "arguments": json.dumps(arguments, sort_keys=True),
                },
            }
        ],
    }


def _content(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    return ""


def _optional_path(arguments: dict[str, object]) -> str:
    path = arguments.get("path", ".")
    if isinstance(path, str):
        return path
    raise ValueError("fs.list path must be a string")


def _required_string(arguments: dict[str, object], key: str, tool_name: str) -> str:
    value = arguments.get(key)
    if isinstance(value, str):
        return value
    raise ValueError(f"{tool_name} {key} must be a string")


def _unresolved_shell_request_for_approval(
    events: list[TranscriptEvent],
    approval_id: str,
) -> PendingShellRequest | None:
    pending = _unresolved_shell_request(events)
    if pending is None:
        return None
    if pending.approval_id != approval_id:
        return None
    return pending


def _unresolved_shell_request(events: list[TranscriptEvent]) -> PendingShellRequest | None:
    pending_by_tool_call_id: dict[str, PendingShellRequest] = {}
    for event in events:
        if event.kind == "tool_request" and event.payload.get("name") == "shell.run":
            pending = _pending_shell_request(event.payload)
            if pending is not None:
                pending_by_tool_call_id[pending.tool_call_id] = pending
        elif event.kind in {"tool_result", "tool_denial"}:
            pending_by_tool_call_id.pop(str(event.payload.get("id", "")), None)

    if not pending_by_tool_call_id:
        return None
    return list(pending_by_tool_call_id.values())[-1]


def _pending_shell_request(payload: dict[str, object]) -> PendingShellRequest | None:
    approval_id = payload.get("approval_id")
    tool_call_id = payload.get("id")
    arguments = payload.get("arguments")
    if not isinstance(approval_id, str) or not isinstance(tool_call_id, str):
        return None
    if not isinstance(arguments, dict):
        return None
    command = arguments.get("command")
    if not isinstance(command, str):
        return None
    return PendingShellRequest(
        approval_id=approval_id,
        tool_call_id=tool_call_id,
        command=command,
    )


def _pending_response(pending: PendingShellRequest) -> dict[str, object]:
    return {"id": pending.approval_id, "command": pending.command}


def _shell_result_payload(result: ShellResult, tool_call_id: str) -> dict[str, object]:
    return {
        "id": tool_call_id,
        "name": "shell.run",
        "command": result.command,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _redact_payload(payload: dict[str, object]) -> dict[str, object]:
    return {key: _redact_value(key, value) for key, value in payload.items()}


def _redact_value(key: str, value: object) -> object:
    if is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(child_key): _redact_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    return value


def _runtime_error_payload(exc: Exception) -> dict[str, object]:
    termination: RuntimeTermination = "failed"
    code = "runtime_error"
    payload: dict[str, object] = {
        "message": redact_text(exc),
        "error_code": code,
        "termination": termination,
    }
    if not isinstance(exc, RemoteRunError):
        return payload

    if isinstance(exc, RemoteRunAbortedError):
        termination = "timed_out" if exc.code == "run_timeout" else "cancelled"
    payload["error_code"] = exc.code
    payload["termination"] = termination
    payload["partial_content"] = redact_text(exc.partial_content)
    if exc.upstream_session_id:
        payload["upstream_session_id"] = redact_text(exc.upstream_session_id)
    return payload
