import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from personal_agent_gateway.model_client import (
    ClaudeModelClient,
    CodexModelClient,
    ModelResponse,
    OpenAIModelClient,
    _codex_prompt,
    _parse_claude_session_id,
    _parse_codex_session_id,
    ToolCall,
)


def write_fake_codex(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        codex_bin = tmp_path / "codex.cmd"
        codex_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return codex_bin

    codex_bin = tmp_path / "codex"
    codex_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    codex_bin.chmod(0o755)
    return codex_bin


def write_fake_claude(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        claude_bin = tmp_path / "claude.cmd"
        claude_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return claude_bin

    claude_bin = tmp_path / "claude"
    claude_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    claude_bin.chmod(0o755)
    return claude_bin


def test_gateway_prompt_sends_single_user_message_as_raw_cli_prompt() -> None:
    prompt = _codex_prompt(
        [
            {"role": "user", "content": "넌 누구니?"},
        ]
    )

    assert prompt == "넌 누구니?\n"


@pytest.mark.asyncio
async def test_openai_client_posts_valid_wire_tool_names() -> None:
    captured_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "done", "tool_calls": []}}]},
        )

    client = OpenAIModelClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1/chat/completions",
        transport=httpx.MockTransport(handler),
    )

    response = await client.complete(
        [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "previous"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "fs.list", "arguments": '{"path": "."}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "fs.list",
                "content": "[]",
            },
            {
                "role": "tool",
                "tool_call_id": "call-2",
                "content": "unnamed",
            },
        ]
    )

    assert response == ModelResponse(content="done", tool_calls=[])
    assert captured_payloads[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fs_list",
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
                "name": "fs_read",
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
                "name": "shell_run",
                "description": "Request approval to run a shell command in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]
    assert "tool_calls" not in captured_payloads[0]["messages"][1]
    assert captured_payloads[0]["messages"][2]["tool_calls"][0]["function"]["name"] == "fs_list"
    assert "name" not in captured_payloads[0]["messages"][3]
    assert "name" not in captured_payloads[0]["messages"][4]


@pytest.mark.asyncio
async def test_openai_client_maps_wire_tool_call_names_to_internal_names() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_run",
                                        "arguments": '{"command": "pwd"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = OpenAIModelClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1/chat/completions",
        transport=httpx.MockTransport(handler),
    )

    response = await client.complete([{"role": "user", "content": "run pwd"}])

    assert response == ModelResponse(
        content="",
        tool_calls=[ToolCall(id="call-1", name="shell.run", arguments={"command": "pwd"})],
    )


@pytest.mark.asyncio
async def test_codex_client_runs_local_codex_exec_and_parses_final_message(tmp_path: Path) -> None:
    payload = '{"type":"item.completed","item":{"type":"agent_message","text":"local answer"}}'
    if os.name == "nt":
        body = f"echo {payload}"
    else:
        body = f"printf '%s\\n' '{payload}'"
    codex_bin = write_fake_codex(
        tmp_path,
        body,
    )

    client = CodexModelClient(binary=str(codex_bin), model="default", workspace_root=tmp_path)

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(content="local answer", tool_calls=[])


@pytest.mark.asyncio
async def test_codex_client_publishes_json_events_while_collecting_final_message(tmp_path: Path) -> None:
    first = '{"type":"thread.started","thread_id":"thread-1"}'
    second = '{"type":"item.completed","item":{"id":"item-1","type":"agent_message","text":"streamed answer"}}'
    if os.name == "nt":
        body = f"echo {first}\r\necho {second}"
    else:
        body = f"printf '%s\\n%s\\n' '{first}' '{second}'"
    codex_bin = write_fake_codex(tmp_path, body)
    events: list[dict[str, object]] = []

    async def publish(event: dict[str, object]) -> None:
        events.append(event)

    client = CodexModelClient(
        binary=str(codex_bin),
        model="default",
        workspace_root=tmp_path,
        on_event=publish,
    )

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(
        content="streamed answer",
        tool_calls=[],
        upstream_session_id="thread-1",
    )
    assert events == [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "agent_message",
                "text": "streamed answer",
            },
        },
    ]


@pytest.mark.asyncio
async def test_codex_client_reports_local_codex_failure(tmp_path: Path) -> None:
    if os.name == "nt":
        body = "echo not logged in 1>&2\r\nexit /b 7"
    else:
        body = "printf '%s\\n' 'not logged in' >&2\nexit 7"
    codex_bin = write_fake_codex(tmp_path, body)

    client = CodexModelClient(binary=str(codex_bin), model="default", workspace_root=tmp_path)

    with pytest.raises(RuntimeError, match="Codex exited with status 7: not logged in"):
        await client.complete([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_codex_client_reports_missing_binary() -> None:
    client = CodexModelClient(
        binary="/definitely/missing/codex",
        model="default",
        workspace_root=Path("."),
    )

    with pytest.raises(RuntimeError, match="Codex binary not found"):
        await client.complete([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_claude_client_runs_print_json_and_parses_result(tmp_path: Path) -> None:
    payload = '{"result":"claude answer"}'
    if os.name == "nt":
        body = f"echo {payload}"
    else:
        body = f"printf '%s\\n' '{payload}'"
    claude_bin = write_fake_claude(tmp_path, body)

    client = ClaudeModelClient(
        binary=str(claude_bin),
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
    )

    response = await client.complete([{"role": "user", "content": "hello"}])

    assert response == ModelResponse(content="claude answer", tool_calls=[])


def test_claude_client_builds_expected_command(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
    ]


def test_codex_client_includes_effort_and_profile_flags_when_configured(tmp_path: Path) -> None:
    client = CodexModelClient(
        binary="codex",
        model="default",
        workspace_root=tmp_path,
        effort="xhigh",
        profile="local-dev",
    )

    assert client._command() == [
        "codex",
        "exec",
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="xhigh"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(tmp_path),
        "--skip-git-repo-check",
        "--profile",
        "local-dev",
        "-",
    ]


def test_codex_client_preserves_profile_positional_argument(tmp_path: Path) -> None:
    client = CodexModelClient(
        "codex",
        "default",
        tmp_path,
        "workspace-write",
        "never",
        "local-dev",
    )

    assert client._command() == [
        "codex",
        "exec",
        "--json",
        "-c",
        'approval_policy="never"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(tmp_path),
        "--skip-git-repo-check",
        "--profile",
        "local-dev",
        "-",
    ]
    assert "model_reasoning_effort" not in " ".join(client._command())


def test_codex_client_builds_resume_command_when_upstream_session_exists(tmp_path: Path) -> None:
    client = CodexModelClient(
        binary="codex",
        model="gpt-5.5",
        workspace_root=tmp_path,
        effort="xhigh",
        approval_policy="never",
        upstream_session_id="0199a213-81c0-7800-8aa1-bbab2a035a53",
    )

    assert client._command() == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="xhigh"',
        "--skip-git-repo-check",
        "-m",
        "gpt-5.5",
        "0199a213-81c0-7800-8aa1-bbab2a035a53",
        "-",
    ]


def test_claude_client_includes_agent_flag_when_configured(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
        agent="reviewer",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
        "--agent",
        "reviewer",
    ]


def test_claude_client_preserves_timeout_positional_argument(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        "claude",
        "sonnet",
        tmp_path,
        "high",
        "manual",
        "reviewer",
        30,
    )

    assert client._command() == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
        "--agent",
        "reviewer",
    ]
    assert "--resume" not in client._command()
    assert "30" not in client._command()


def test_claude_client_builds_resume_command_when_upstream_session_exists(tmp_path: Path) -> None:
    client = ClaudeModelClient(
        binary="claude",
        model="sonnet",
        workspace_root=tmp_path,
        effort="high",
        permission_mode="manual",
        upstream_session_id="f7c44fcb-e059-4799-94e3-f64d39305050",
    )

    assert client._command() == [
        "claude",
        "-p",
        "--resume",
        "f7c44fcb-e059-4799-94e3-f64d39305050",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--permission-mode",
        "manual",
    ]


def test_parse_codex_output_includes_upstream_thread_id() -> None:
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
        ]
    )

    assert _parse_codex_session_id(output) == "0199a213-81c0-7800-8aa1-bbab2a035a53"


def test_parse_claude_output_includes_upstream_session_id() -> None:
    output = json.dumps(
        {
            "type": "result",
            "result": "done",
            "session_id": "f7c44fcb-e059-4799-94e3-f64d39305050",
        }
    )

    assert _parse_claude_session_id(output) == "f7c44fcb-e059-4799-94e3-f64d39305050"


class _HangingStdout:
    async def read(self) -> bytes:
        await asyncio.sleep(60)
        return b""

    async def readline(self) -> bytes:
        await asyncio.sleep(60)
        return b""


class _FakeStdin:
    def write(self, _data: bytes) -> None: ...
    async def drain(self) -> None: ...
    def close(self) -> None: ...


class _FakeProcess:
    def __init__(self) -> None:
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _HangingStdout()
        self.stderr = _HangingStdout()
        self.returncode = None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(60)
        return b"", b""


@pytest.mark.asyncio
async def test_codex_client_kills_process_on_cancel(monkeypatch) -> None:
    fake = _FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(binary="codex", model="m", workspace_root=Path("."), sandbox="s", approval_policy="never")
    task = asyncio.ensure_future(client.complete([{"role": "user", "content": "hi"}]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.killed is True


@pytest.mark.asyncio
async def test_claude_client_kills_process_on_cancel(monkeypatch) -> None:
    fake = _FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = ClaudeModelClient(binary="claude", model="sonnet", workspace_root=Path("."), effort="high", permission_mode="manual")
    task = asyncio.ensure_future(client.complete([{"role": "user", "content": "hi"}]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.killed is True


class _ScriptedStdout:
    def __init__(self, lines: list[tuple[float, bytes]], hang_after: bool = False) -> None:
        self._lines = list(lines)
        self._hang_after = hang_after

    async def readline(self) -> bytes:
        if self._lines:
            delay, line = self._lines.pop(0)
            await asyncio.sleep(delay)
            return line
        if self._hang_after:
            await asyncio.sleep(60)
        return b""


class _EmptyStderr:
    async def read(self) -> bytes:
        return b""


class _StreamingProcess:
    def __init__(
        self,
        lines: list[tuple[float, bytes]],
        *,
        hang_after: bool = False,
        wait_hangs: bool = False,
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _ScriptedStdout(lines, hang_after=hang_after)
        self.stderr = _EmptyStderr()
        self.returncode = None
        self.killed = False
        self._wait_hangs = wait_hangs

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self._wait_hangs and not self.killed:
            await asyncio.sleep(60)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.mark.asyncio
async def test_codex_client_uses_idle_timeout_without_limiting_active_stream(monkeypatch) -> None:
    final = b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
    fake = _StreamingProcess([
        (0.04, b'{"type":"thread.started","thread_id":"thread-1"}\n'),
        (0.04, b'{"type":"item.started","item":{"type":"reasoning"}}\n'),
        (0.04, final),
    ])

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(
        binary="codex",
        model="m",
        workspace_root=Path("."),
        timeout_seconds=0.3,
        idle_timeout_seconds=0.08,
    )

    response = await client.complete([{"role": "user", "content": "hi"}])

    assert response.content == "done"
    assert fake.killed is False


@pytest.mark.asyncio
async def test_codex_client_preserves_final_message_at_hard_timeout(monkeypatch) -> None:
    final = b'{"type":"item.completed","item":{"type":"agent_message","text":"finished"}}\n'
    fake = _StreamingProcess([(0.001, final)], hang_after=True)

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(
        binary="codex",
        model="m",
        workspace_root=Path("."),
        timeout_seconds=0.03,
        idle_timeout_seconds=1,
    )

    response = await client.complete([{"role": "user", "content": "hi"}])

    assert response.content == "finished"
    assert fake.killed is True


@pytest.mark.asyncio
async def test_codex_client_idle_timeout_fails_and_cleans_up(monkeypatch) -> None:
    fake = _StreamingProcess([], hang_after=True)

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(
        binary="codex",
        model="m",
        workspace_root=Path("."),
        timeout_seconds=1,
        idle_timeout_seconds=0.02,
    )

    with pytest.raises(RuntimeError, match="Codex execution idle timed out"):
        await client.complete([{"role": "user", "content": "hi"}])

    assert fake.killed is True


@pytest.mark.asyncio
async def test_codex_client_accepts_terminal_event_when_wrapper_lingers(monkeypatch) -> None:
    final = b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
    terminal = b'{"type":"turn.completed"}\n'
    fake = _StreamingProcess([(0, final), (0, terminal)], wait_hangs=True)

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    monkeypatch.setattr("personal_agent_gateway.model_client._PROCESS_EXIT_GRACE_SECONDS", 0.01)
    client = CodexModelClient(binary="codex", model="m", workspace_root=Path("."))

    response = await client.complete([{"role": "user", "content": "hi"}])

    assert response.content == "done"
    assert fake.killed is True


@pytest.mark.asyncio
async def test_codex_client_waits_for_final_message_after_early_terminal_event(monkeypatch) -> None:
    terminal = b'{"type":"turn.completed"}\n'
    final = b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
    fake = _StreamingProcess([(0, terminal), (0, final)])

    async def fake_create(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("personal_agent_gateway.model_client.asyncio.create_subprocess_exec", fake_create)
    client = CodexModelClient(binary="codex", model="m", workspace_root=Path("."))

    response = await client.complete([{"role": "user", "content": "hi"}])

    assert response.content == "done"
    assert fake.killed is False
