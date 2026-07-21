import json
import asyncio
import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import httpx

ToolName = Literal["fs.list", "fs.read", "shell.run"]
WIRE_TOOL_NAMES: dict[ToolName, str] = {
    "fs.list": "fs_list",
    "fs.read": "fs_read",
    "shell.run": "shell_run",
}
INTERNAL_TOOL_NAMES = {wire_name: tool_name for tool_name, wire_name in WIRE_TOOL_NAMES.items()}
_PROCESS_EXIT_GRACE_SECONDS = 2.0
_PROCESS_KILL_GRACE_SECONDS = 5.0
_STREAM_READ_CHUNK_BYTES = 64 * 1024


class _CodexIdleTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: ToolName
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    upstream_session_id: str | None = None


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


class CodexModelClient:
    def __init__(
        self,
        binary: str,
        model: str,
        workspace_root: Path,
        sandbox: str = "workspace-write",
        approval_policy: str = "never",
        profile: str | None = None,
        timeout_seconds: int = 3600,
        idle_timeout_seconds: int = 600,
        on_event: Callable[[dict[str, object]], Awaitable[None]] | None = None,
        additional_write_dirs: list[Path] | None = None,
        *,
        effort: str | None = None,
        upstream_session_id: str | None = None,
    ) -> None:
        self._binary = binary
        self._model = model
        self._workspace_root = workspace_root
        self._sandbox = sandbox
        self._approval_policy = approval_policy
        self._effort = effort
        self._profile = profile
        self._upstream_session_id = upstream_session_id
        self._timeout_seconds = timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._on_event = on_event
        self._additional_write_dirs = [path.resolve() for path in (additional_write_dirs or [])]

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        stdout_parts: list[str] = []
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root),
                **_process_group_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Codex binary not found: {self._binary}") from exc

        try:
            stdout_text, stderr_text = await asyncio.wait_for(
                self._communicate_stream(
                    process,
                    _codex_prompt(messages).encode(),
                    stdout_parts,
                ),
                timeout=self._timeout_seconds,
            )
        except _CodexIdleTimeout as exc:
            stdout_text = "".join(stdout_parts)
            final_message = _parse_codex_final_message(stdout_text)
            await _terminate_process_tree(process)
            if final_message is None:
                raise RuntimeError("Codex execution idle timed out") from exc
            stderr_text = ""
        except TimeoutError as exc:
            stdout_text = "".join(stdout_parts)
            final_message = _parse_codex_final_message(stdout_text)
            await _terminate_process_tree(process)
            if final_message is None:
                raise RuntimeError("Codex execution timed out") from exc
            stderr_text = ""
        except asyncio.CancelledError:
            await _terminate_process_tree(process)
            raise

        if process.returncode != 0 and _parse_codex_final_message(stdout_text) is None:
            detail = _summarize_process_output(stderr_text, stdout_text)
            raise RuntimeError(f"Codex exited with status {process.returncode}: {detail}")

        content = _parse_codex_output(stdout_text).strip()
        return ModelResponse(
            content=content,
            tool_calls=[],
            upstream_session_id=_parse_codex_session_id(stdout_text),
        )

    async def _communicate_stream(
        self,
        process: asyncio.subprocess.Process,
        prompt: bytes,
        stdout_parts: list[str],
    ) -> tuple[str, str]:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("Codex process pipes were not available")

        try:
            process.stdin.write(prompt)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # A CLI may exit after emitting a result before consuming stdin.
            pass
        finally:
            process.stdin.close()

        stderr_task = asyncio.create_task(process.stderr.read())
        pending = bytearray()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(_STREAM_READ_CHUNK_BYTES),
                        timeout=self._idle_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise _CodexIdleTimeout from exc
                if not chunk:
                    if pending:
                        pending.extend(b"\n")
                    else:
                        break
                else:
                    pending.extend(chunk)

                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(pending[: newline + 1])
                    del pending[: newline + 1]
                    text = line.decode(errors="replace")
                    stdout_parts.append(text)
                    event = _parse_json_line(text)
                    if event is not None and self._on_event is not None:
                        await self._on_event(event)
                    if (
                        event is not None
                        and _is_codex_terminal_event(event)
                        and _parse_codex_final_message("".join(stdout_parts)) is not None
                    ):
                        try:
                            stderr = await asyncio.wait_for(
                                stderr_task,
                                timeout=_PROCESS_EXIT_GRACE_SECONDS,
                            )
                            await asyncio.wait_for(
                                process.wait(),
                                timeout=_PROCESS_EXIT_GRACE_SECONDS,
                            )
                        except TimeoutError:
                            if not stderr_task.done():
                                stderr_task.cancel()
                            await _terminate_process_tree(process)
                            stderr = b""
                        return "".join(stdout_parts), stderr.decode(errors="replace")

                if not chunk:
                    break
        except BaseException:
            if not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task
            await _terminate_process_tree(process)
            raise

        stderr = await stderr_task
        await process.wait()
        return "".join(stdout_parts), stderr.decode(errors="replace")

    def _command(self) -> list[str]:
        if self._upstream_session_id:
            return self._resume_command()
        return self._start_command()

    def _base_config_args(self) -> list[str]:
        command = [
            "-c",
            f'approval_policy={json.dumps(self._approval_policy)}',
        ]
        if self._effort:
            command.extend(["-c", f'model_reasoning_effort={json.dumps(self._effort)}'])
        return command

    def _start_command(self) -> list[str]:
        command = [
            self._binary,
            "exec",
            "--json",
            *self._base_config_args(),
            "--sandbox",
            self._sandbox,
            "-C",
            str(self._workspace_root),
            "--skip-git-repo-check",
        ]
        for path in self._additional_write_dirs:
            command.extend(["--add-dir", str(path)])
        if self._model and self._model != "default":
            command.extend(["-m", self._model])
        if self._profile:
            command.extend(["--profile", self._profile])
        command.append("-")
        return command

    def _resume_command(self) -> list[str]:
        command = [
            self._binary,
            "exec",
            "resume",
            "--json",
            *self._base_config_args(),
            "--skip-git-repo-check",
        ]
        if self._model and self._model != "default":
            command.extend(["-m", self._model])
        command.extend([str(self._upstream_session_id), "-"])
        return command


class ClaudeModelClient:
    def __init__(
        self,
        binary: str,
        model: str,
        workspace_root: Path,
        effort: str = "medium",
        permission_mode: str = "manual",
        agent: str | None = None,
        timeout_seconds: int = 600,
        additional_dirs: list[Path] | None = None,
        *,
        upstream_session_id: str | None = None,
    ) -> None:
        self._binary = binary
        self._model = model
        self._workspace_root = workspace_root
        self._effort = effort
        self._permission_mode = permission_mode
        self._agent = agent
        self._upstream_session_id = upstream_session_id
        self._timeout_seconds = timeout_seconds
        self._additional_dirs = [path.resolve() for path in (additional_dirs or [])]

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(),
                cwd=str(self._workspace_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_process_group_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Claude binary not found: {self._binary}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(_claude_prompt(messages).encode()),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            await _terminate_process_tree(process)
            raise RuntimeError("Claude execution timed out") from exc
        except asyncio.CancelledError:
            await _terminate_process_tree(process)
            raise

        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        if process.returncode != 0:
            detail = _summarize_process_output(stderr_text, stdout_text)
            raise RuntimeError(f"Claude exited with status {process.returncode}: {detail}")
        return ModelResponse(
            content=_parse_claude_output(stdout_text),
            tool_calls=[],
            upstream_session_id=_parse_claude_session_id(stdout_text),
        )

    def _command(self) -> list[str]:
        command = [self._binary, "-p"]
        if self._upstream_session_id:
            command.extend(["--resume", self._upstream_session_id])
        command.extend(["--output-format", "json"])
        if self._model and self._model != "default":
            command.extend(["--model", self._model])
        if self._effort:
            command.extend(["--effort", self._effort])
        if self._permission_mode:
            command.extend(["--permission-mode", self._permission_mode])
        if self._agent:
            command.extend(["--agent", self._agent])
        if self._additional_dirs:
            command.append("--add-dir")
            command.extend(str(path) for path in self._additional_dirs)
        return command


def _codex_prompt(messages: list[dict[str, object]]) -> str:
    if len(messages) == 1:
        message = messages[0]
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            return content.rstrip() + "\n"

    lines = [
        "You are the local agent behind a personal web gateway.",
        "Use the configured local workspace directly when the request requires code or file work.",
        "Keep the final answer concise and actionable.",
        "",
        "Conversation:",
    ]
    for message in messages:
        role = str(message.get("role", "message")).upper()
        content = message.get("content")
        if isinstance(content, str) and content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines).strip() + "\n"


def _claude_prompt(messages: list[dict[str, object]]) -> str:
    return _codex_prompt(messages)


def _parse_claude_output(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output.strip()
    if isinstance(payload, dict):
        result = payload.get("result") or payload.get("content")
        if isinstance(result, str):
            return result.strip()
    return output.strip()


def _parse_codex_session_id(output: str) -> str | None:
    for line in output.splitlines():
        event = _parse_json_line(line)
        if event is None:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def _parse_claude_session_id(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def _parse_codex_final_message(output: str) -> str | None:
    final_message = ""
    for line in output.splitlines():
        event = _parse_json_line(line)
        if event is None:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                final_message = text
    if final_message:
        return final_message
    return None


def _parse_codex_output(output: str) -> str:
    final_message = _parse_codex_final_message(output)
    if final_message is not None:
        return final_message
    return output


def _is_codex_terminal_event(event: dict[str, object]) -> bool:
    return event.get("type") in {"turn.completed", "task_complete"}


def _process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    pid = getattr(process, "pid", None)
    try:
        if os.name == "nt" and isinstance(pid, int):
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=_PROCESS_KILL_GRACE_SECONDS)
        elif isinstance(pid, int):
            os.killpg(pid, signal.SIGKILL)
        else:
            process.kill()
    except (FileNotFoundError, ProcessLookupError, TimeoutError):
        with suppress(ProcessLookupError):
            process.kill()

    with suppress(ProcessLookupError, TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=_PROCESS_KILL_GRACE_SECONDS)


def _parse_json_line(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    return {str(key): value for key, value in event.items()}


def _summarize_process_output(stderr_text: str, stdout_text: str) -> str:
    detail = stderr_text.strip() or stdout_text.strip()
    if not detail:
        return "no output"
    if len(detail) <= 800:
        return detail
    return f"{detail[:800]}... [truncated]"


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
