import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import ModelClient, ToolCall
from personal_agent_gateway.tools import ShellResult, WorkspaceTools
from personal_agent_gateway.transcript import TranscriptEvent, TranscriptStore


@dataclass(frozen=True)
class RuntimeResult:
    messages: list[dict[str, object]]
    pending_approval: dict[str, object] | None


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
        on_upstream_session_id: Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._transcript = transcript
        self._tools = tools
        self._model = model
        self._job_service = job_service
        self._event_bus = event_bus
        self._history_mode = history_mode
        self._on_upstream_session_id = on_upstream_session_id
        self._session_id = session_id

    def attach_event_bus(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

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
                },
            )
            return result
        except Exception as exc:
            await self._publish("runtime.error", {"message": _redact_text(str(exc)), "session_id": session_id})
            return self._handle_runtime_error(exc, session_id)

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
            return await self._run_model_loop(session_id)
        except Exception as exc:
            return self._handle_runtime_error(exc, session_id)

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
            return RuntimeResult(
                messages=[{"role": "assistant", "content": "Command denied."}],
                pending_approval=None,
            )
        except Exception as exc:
            return self._handle_runtime_error(exc, session_id)

    async def _run_model_loop(self, session_id: str) -> RuntimeResult:
        for _iteration in range(8):
            response = await self._model.complete(
                _events_to_messages(
                    self._transcript.load(session_id),
                    latest_user_only=self._history_mode == "latest_user",
                )
            )
            if response.upstream_session_id and self._on_upstream_session_id is not None:
                self._on_upstream_session_id(response.upstream_session_id)

            if not response.tool_calls:
                if response.content:
                    self._append("assistant", {"content": response.content}, session_id)
                return RuntimeResult(
                    messages=[{"role": "assistant", "content": response.content}],
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

    def _handle_runtime_error(self, exc: Exception, session_id: str | None = None) -> RuntimeResult:
        message = _redact_text(str(exc))
        self._append("runtime_error", {"message": message}, session_id)
        return RuntimeResult(
            messages=[{"role": "assistant", "content": f"Error: {message}"}],
            pending_approval=None,
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
        await self._event_bus.publish({"type": event_type, **_redact_payload(payload)})


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
        elif event.kind in {"approval", "runtime_error", "agent_session_link", "session_config_set"}:
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
    if key in {"AGENT_WEB_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"}:
        return "[redacted]"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(child_key): _redact_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    return value


def _redact_text(value: str) -> str:
    redacted = value
    for name in ("AGENT_WEB_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        redacted = redacted.replace(name, "[redacted]")
        secret = os.getenv(name)
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
