# CLI Session Resume Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Personal Agent Gateway web session map to a native Codex or Claude CLI session, so later messages resume the local agent context instead of replaying the whole gateway transcript into a fresh CLI run.

**Architecture:** Keep `TranscriptStore` as the gateway source of truth for UI, audit, search, and recovery. Add a small session-link layer that records the upstream Codex `thread_id` or Claude `session_id` in the transcript, and pass that id into provider-specific CLI clients. First turns send the full gateway transcript to bootstrap native context; resumed turns send only the latest user message and rely on the native CLI session for continuity.

**Tech Stack:** Python/FastAPI/Pydantic backend, pytest, Codex CLI `codex exec` / `codex exec resume`, Claude Code CLI `claude -p --output-format json --resume`.

## Global Constraints

- Do not introduce Codex SDK or Claude Agent SDK in this change.
- Do not keep an interactive TUI process open; use non-interactive CLI commands plus native CLI resume.
- Gateway transcript remains the durable UI/audit history.
- Native Codex/Claude session ids are implementation metadata and must not replace gateway session ids.
- Do not replay full gateway history after a matching native session link exists.
- Do not pass unsupported Codex `exec resume` flags such as `--sandbox`, `-C`, or `--profile`; Codex resume accepts its own option set.
- Do not change frontend behavior unless backend status or error handling requires it.
- Keep existing model option work intact: curated model lists, no `allow_custom_model`, no Claude `fable`, Codex effort via `model_reasoning_effort`.

---

## File Structure

- Modify `src/personal_agent_gateway/model_client.py`
  - Add `upstream_session_id` to `ModelResponse`.
  - Add optional `upstream_session_id` constructor parameter to `CodexModelClient` and `ClaudeModelClient`.
  - Build Codex resume commands with `codex exec resume ... <session_id> -`.
  - Build Claude resume commands with `claude -p --resume <session_id> --output-format json ...`.
  - Parse Codex `thread.started.thread_id` and Claude JSON `session_id`.

- Modify `src/personal_agent_gateway/transcript.py`
  - Add a new transcript kind: `agent_session_link`.
  - Keep session summaries unchanged except for ignoring this event in title/message counts.

- Create `src/personal_agent_gateway/agent_session_link.py`
  - Encapsulate reading and writing upstream native session links from transcript events.
  - Match links by gateway session id, agent id, model, and option fingerprint.

- Modify `src/personal_agent_gateway/runtime.py`
  - Add a prompt history mode so native resumed runs receive only the latest user turn.
  - Record upstream session ids returned by model clients.

- Modify `src/personal_agent_gateway/runtime_factory.py`
  - Resolve the active gateway session config.
  - Load a matching upstream native session link.
  - Pass `upstream_session_id` to Codex/Claude clients.
  - Select full-history bootstrap mode when no link exists and latest-user resume mode when a link exists.

- Modify tests:
  - `tests/test_model_client.py`
  - `tests/test_runtime.py` or `tests/test_app.py`
  - Create `tests/test_agent_session_link.py`

---

### Task 1: Model Response Session Metadata

**Files:**
- Modify: `src/personal_agent_gateway/model_client.py`
- Test: `tests/test_model_client.py`

**Interfaces:**
- Produces: `ModelResponse(content: str, tool_calls: list[ToolCall], upstream_session_id: str | None = None)`
- Produces: `_parse_codex_session_id(output: str) -> str | None`
- Produces: `_parse_claude_session_id(output: str) -> str | None`

- [ ] **Step 1: Write failing tests for Codex and Claude session id parsing**

Append these tests to `tests/test_model_client.py`:

```python
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
```

Also add `_parse_codex_session_id` and `_parse_claude_session_id` to the import list at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_model_client.py::test_parse_codex_output_includes_upstream_thread_id tests/test_model_client.py::test_parse_claude_output_includes_upstream_session_id -q
```

Expected: both tests fail with import/name errors because the parser functions do not exist.

- [ ] **Step 3: Add session id fields and parsers**

In `src/personal_agent_gateway/model_client.py`, change `ModelResponse` to:

```python
@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    upstream_session_id: str | None = None
```

Add these parser functions near `_parse_codex_output` and `_parse_claude_output`:

```python
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
```

In `CodexModelClient.complete`, return:

```python
return ModelResponse(
    content=content,
    tool_calls=[],
    upstream_session_id=_parse_codex_session_id(stdout_text),
)
```

In `ClaudeModelClient.complete`, return:

```python
return ModelResponse(
    content=_parse_claude_output(stdout_text),
    tool_calls=[],
    upstream_session_id=_parse_claude_session_id(stdout_text),
)
```

- [ ] **Step 4: Run model client tests**

Run:

```bash
python -m pytest tests/test_model_client.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/model_client.py tests/test_model_client.py
git commit -m "feat(model): capture upstream cli session ids"
```

---

### Task 2: Transcript-Backed Agent Session Link Service

**Files:**
- Create: `src/personal_agent_gateway/agent_session_link.py`
- Modify: `src/personal_agent_gateway/transcript.py`
- Test: `tests/test_agent_session_link.py`

**Interfaces:**
- Consumes: `TranscriptStore.append_to(transcript_id, kind, payload)`
- Produces: `AgentSessionLinkService.latest(session_id: str, agent_id: str, model: str, options: dict[str, object]) -> AgentSessionLink | None`
- Produces: `AgentSessionLinkService.record(session_id: str, agent_id: str, model: str, options: dict[str, object], upstream_session_id: str) -> AgentSessionLink`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_agent_session_link.py`:

```python
from pathlib import Path

from personal_agent_gateway.agent_session_link import AgentSessionLinkService
from personal_agent_gateway.transcript import TranscriptStore


def test_session_link_records_and_reads_matching_upstream_session(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    recorded = service.record(
        session_id=session_id,
        agent_id="codex",
        model="gpt-5.5",
        options={"effort": "high", "sandbox": "workspace-write"},
        upstream_session_id="codex-thread-1",
    )

    latest = service.latest(
        session_id=session_id,
        agent_id="codex",
        model="gpt-5.5",
        options={"sandbox": "workspace-write", "effort": "high"},
    )

    assert latest == recorded
    assert latest is not None
    assert latest.upstream_session_id == "codex-thread-1"


def test_session_link_ignores_different_agent_model_or_options(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path)
    session_id = transcript.start_new()
    service = AgentSessionLinkService(transcript)

    service.record(
        session_id=session_id,
        agent_id="claude",
        model="sonnet",
        options={"effort": "medium"},
        upstream_session_id="claude-session-1",
    )

    assert service.latest(session_id, "codex", "sonnet", {"effort": "medium"}) is None
    assert service.latest(session_id, "claude", "opus", {"effort": "medium"}) is None
    assert service.latest(session_id, "claude", "sonnet", {"effort": "high"}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_agent_session_link.py -q
```

Expected: fail because `personal_agent_gateway.agent_session_link` does not exist.

- [ ] **Step 3: Allow transcript session link events**

In `src/personal_agent_gateway/transcript.py`, add `"agent_session_link"` to `TranscriptKind`:

```python
TranscriptKind = Literal[
    "user",
    "assistant",
    "tool_request",
    "approval",
    "tool_result",
    "tool_denial",
    "runtime_error",
    "session_rename",
    "session_config_set",
    "agent_session_link",
]
```

No session summary changes are required because `message_count` already counts only `user` and `assistant`, and `_session_title` ignores metadata events.

- [ ] **Step 4: Implement the service**

Create `src/personal_agent_gateway/agent_session_link.py`:

```python
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from personal_agent_gateway.transcript import TranscriptStore


@dataclass(frozen=True)
class AgentSessionLink:
    session_id: str
    agent_id: str
    model: str
    options_fingerprint: str
    upstream_session_id: str
    updated_at: datetime


class AgentSessionLinkService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._transcript = transcript

    def latest(
        self,
        session_id: str,
        agent_id: str,
        model: str,
        options: dict[str, object],
    ) -> AgentSessionLink | None:
        expected = _fingerprint_options(options)
        for event in reversed(self._transcript.load(session_id)):
            if event.kind != "agent_session_link":
                continue
            payload = event.payload
            if payload.get("agent_id") != agent_id:
                continue
            if payload.get("model") != model:
                continue
            if payload.get("options_fingerprint") != expected:
                continue
            upstream_session_id = payload.get("upstream_session_id")
            if not isinstance(upstream_session_id, str) or not upstream_session_id:
                continue
            return AgentSessionLink(
                session_id=session_id,
                agent_id=agent_id,
                model=model,
                options_fingerprint=expected,
                upstream_session_id=upstream_session_id,
                updated_at=event.created_at,
            )
        return None

    def record(
        self,
        session_id: str,
        agent_id: str,
        model: str,
        options: dict[str, object],
        upstream_session_id: str,
    ) -> AgentSessionLink:
        options_fingerprint = _fingerprint_options(options)
        event = self._transcript.append_to(
            session_id,
            "agent_session_link",
            {
                "agent_id": agent_id,
                "model": model,
                "options_fingerprint": options_fingerprint,
                "upstream_session_id": upstream_session_id,
            },
        )
        return AgentSessionLink(
            session_id=session_id,
            agent_id=agent_id,
            model=model,
            options_fingerprint=options_fingerprint,
            upstream_session_id=upstream_session_id,
            updated_at=event.created_at,
        )


def _fingerprint_options(options: dict[str, object]) -> str:
    encoded = json.dumps(options, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run service tests**

Run:

```bash
python -m pytest tests/test_agent_session_link.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/transcript.py src/personal_agent_gateway/agent_session_link.py tests/test_agent_session_link.py
git commit -m "feat(sessions): store native agent session links"
```

---

### Task 3: CLI Resume Command Builders

**Files:**
- Modify: `src/personal_agent_gateway/model_client.py`
- Test: `tests/test_model_client.py`

**Interfaces:**
- Consumes: `CodexModelClient(..., upstream_session_id: str | None = None)`
- Consumes: `ClaudeModelClient(..., upstream_session_id: str | None = None)`
- Produces: provider commands that start new sessions when no id exists and resume native sessions when an id exists.

- [ ] **Step 1: Write failing command tests**

Append these tests to `tests/test_model_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_model_client.py::test_codex_client_builds_resume_command_when_upstream_session_exists tests/test_model_client.py::test_claude_client_builds_resume_command_when_upstream_session_exists -q
```

Expected: fail because the constructors do not accept `upstream_session_id`.

- [ ] **Step 3: Add upstream session constructor parameters**

In `CodexModelClient.__init__`, add:

```python
        upstream_session_id: str | None = None,
```

Set:

```python
        self._upstream_session_id = upstream_session_id
```

In `ClaudeModelClient.__init__`, add the same parameter and assignment.

- [ ] **Step 4: Split Codex command building**

Replace `CodexModelClient._command` with:

```python
    def _command(self) -> list[str]:
        if self._upstream_session_id:
            return self._resume_command()
        return self._start_command()

    def _base_config_args(self) -> list[str]:
        command = [
            "-c",
            f"approval_policy={json.dumps(self._approval_policy)}",
        ]
        if self._effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(self._effort)}"])
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
```

In `CodexModelClient.complete`, pass `cwd=str(self._workspace_root)` to `asyncio.create_subprocess_exec`:

```python
            process = await asyncio.create_subprocess_exec(
                *self._command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root),
            )
```

- [ ] **Step 5: Update Claude command building**

Replace `ClaudeModelClient._command` with:

```python
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
        return command
```

- [ ] **Step 6: Run model client tests**

Run:

```bash
python -m pytest tests/test_model_client.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/model_client.py tests/test_model_client.py
git commit -m "feat(model): resume native cli sessions"
```

---

### Task 4: Runtime History Mode And Session Link Recording

**Files:**
- Modify: `src/personal_agent_gateway/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `ModelResponse.upstream_session_id`
- Produces: `AgentRuntime(..., history_mode: Literal["full", "latest_user"] = "full", on_upstream_session_id: Callable[[str], None] | None = None)`
- Produces: `_events_to_messages(events: list[TranscriptEvent], latest_user_only: bool = False) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing runtime tests**

Append these tests to `tests/test_runtime.py`. If `tests/test_runtime.py` does not exist, create it with the imports shown here:

```python
from pathlib import Path

import pytest

from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore
from personal_agent_gateway.approval import ApprovalStore


class CapturingModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.calls: list[list[dict[str, object]]] = []

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        self.calls.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_runtime_records_upstream_session_id_after_model_response(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path / "sessions")
    recorded: list[str] = []
    model = CapturingModel(ModelResponse("hello", [], upstream_session_id="native-1"))
    runtime = AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(tmp_path, ApprovalStore()),
        model=model,
        on_upstream_session_id=recorded.append,
    )

    await runtime.handle_user_message("hello")

    assert recorded == ["native-1"]


@pytest.mark.asyncio
async def test_runtime_latest_user_history_mode_sends_only_latest_user_message(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path / "sessions")
    transcript.start_new()
    transcript.append("user", {"content": "first"})
    transcript.append("assistant", {"content": "first answer"})
    model = CapturingModel(ModelResponse("second answer", [], upstream_session_id="native-1"))
    runtime = AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(tmp_path, ApprovalStore()),
        model=model,
        history_mode="latest_user",
    )

    await runtime.handle_user_message("second")

    assert model.calls == [[{"role": "user", "content": "second"}]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_runtime.py::test_runtime_records_upstream_session_id_after_model_response tests/test_runtime.py::test_runtime_latest_user_history_mode_sends_only_latest_user_message -q
```

Expected: fail because `AgentRuntime` does not accept `history_mode` or `on_upstream_session_id`.

- [ ] **Step 3: Add runtime constructor parameters**

In `src/personal_agent_gateway/runtime.py`, import:

```python
from collections.abc import Callable
from typing import Literal
```

Update `AgentRuntime.__init__`:

```python
    def __init__(
        self,
        transcript: TranscriptStore,
        tools: WorkspaceTools,
        model: ModelClient,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
        history_mode: Literal["full", "latest_user"] = "full",
        on_upstream_session_id: Callable[[str], None] | None = None,
    ) -> None:
        self._transcript = transcript
        self._tools = tools
        self._model = model
        self._job_service = job_service
        self._event_bus = event_bus
        self._history_mode = history_mode
        self._on_upstream_session_id = on_upstream_session_id
```

- [ ] **Step 4: Use latest-user message mode and record upstream ids**

In `_run_model_loop`, replace the model call with:

```python
            response = await self._model.complete(
                _events_to_messages(
                    self._transcript.load_active(),
                    latest_user_only=self._history_mode == "latest_user",
                )
            )
            if response.upstream_session_id and self._on_upstream_session_id is not None:
                self._on_upstream_session_id(response.upstream_session_id)
```

Replace `_events_to_messages` with:

```python
def _events_to_messages(
    events: list[TranscriptEvent],
    latest_user_only: bool = False,
) -> list[dict[str, object]]:
    selected_events = events
    if latest_user_only:
        selected_events = _latest_user_event(events)

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
        elif event.kind in {"approval", "runtime_error", "agent_session_link"}:
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


def _latest_user_event(events: list[TranscriptEvent]) -> list[TranscriptEvent]:
    for event in reversed(events):
        if event.kind == "user":
            return [event]
    return []
```

- [ ] **Step 5: Run runtime tests**

Run:

```bash
python -m pytest tests/test_runtime.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): support native session resume history mode"
```

---

### Task 5: Runtime Factory Integration For Codex And Claude

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `AgentSessionLinkService.latest(...)`
- Consumes: `AgentSessionLinkService.record(...)`
- Consumes: `CodexModelClient(..., upstream_session_id=...)`
- Consumes: `ClaudeModelClient(..., upstream_session_id=...)`
- Produces: native session link reuse for active gateway sessions.

- [ ] **Step 1: Write failing app-level test for Codex resume link**

Add this test to `tests/test_app.py` near existing session config runtime tests:

```python
def test_chat_reuses_codex_upstream_session_after_first_response(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway.config import AppConfig
    from personal_agent_gateway.app import create_app
    from fastapi.testclient import TestClient

    captured_upstream_ids: list[str | None] = []
    captured_messages: list[list[dict[str, object]]] = []

    class FakeCodexModelClient:
        def __init__(self, *args, upstream_session_id=None, **kwargs) -> None:
            captured_upstream_ids.append(upstream_session_id)

        async def complete(self, messages):
            from personal_agent_gateway.model_client import ModelResponse

            captured_messages.append(messages)
            return ModelResponse(content="ok", tool_calls=[], upstream_session_id="codex-thread-1")

    monkeypatch.setattr("personal_agent_gateway.runtime_factory.CodexModelClient", FakeCodexModelClient)

    config = AppConfig(
        workspace_root=tmp_path,
        session_dir=tmp_path / "sessions",
        codex_binary="codex",
    )
    client = TestClient(create_app(config))

    first = client.post("/api/chat", json={"message": "first"})
    second = client.post("/api/chat", json={"message": "second"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert captured_upstream_ids == [None, "codex-thread-1"]
    assert captured_messages[0] == [{"role": "user", "content": "first"}]
    assert captured_messages[1] == [{"role": "user", "content": "second"}]
```

- [ ] **Step 2: Write failing app-level test for Claude resume link**

Add:

```python
def test_chat_reuses_claude_upstream_session_after_first_response(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway.config import AppConfig
    from personal_agent_gateway.app import create_app
    from fastapi.testclient import TestClient

    captured_upstream_ids: list[str | None] = []

    class FakeClaudeModelClient:
        def __init__(self, *args, upstream_session_id=None, **kwargs) -> None:
            captured_upstream_ids.append(upstream_session_id)

        async def complete(self, messages):
            from personal_agent_gateway.model_client import ModelResponse

            return ModelResponse(content="ok", tool_calls=[], upstream_session_id="claude-session-1")

    monkeypatch.setattr("personal_agent_gateway.runtime_factory.ClaudeModelClient", FakeClaudeModelClient)

    config = AppConfig(
        workspace_root=tmp_path,
        session_dir=tmp_path / "sessions",
        claude_binary="claude",
    )
    client = TestClient(create_app(config))
    agents = [{"id": "claude", "label": "Claude Code", "available": True, "models": ["sonnet"], "default_model": "sonnet", "defaults": {}, "options_schema": []}]
    monkeypatch.setattr("personal_agent_gateway.agents.AgentRegistry.catalog", lambda self: agents)

    response = client.put("/api/session-config", json={"agent_id": "claude", "model": "sonnet", "options": {}})
    assert response.status_code == 200

    first = client.post("/api/chat", json={"message": "first"})
    second = client.post("/api/chat", json={"message": "second"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert captured_upstream_ids == [None, "claude-session-1"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_app.py::test_chat_reuses_codex_upstream_session_after_first_response tests/test_app.py::test_chat_reuses_claude_upstream_session_after_first_response -q
```

Expected: fail because `runtime_factory.py` does not pass `upstream_session_id` or record links.

- [ ] **Step 4: Integrate session links in runtime factory**

In `src/personal_agent_gateway/runtime_factory.py`, import:

```python
from personal_agent_gateway.agent_session_link import AgentSessionLinkService
```

In `create_runtime_for_active_session`, after `session_config = ...`, add:

```python
        link_service = AgentSessionLinkService(self._transcript)
        link = link_service.latest(
            session_id=session_id,
            agent_id=session_config.agent_id,
            model=session_config.model,
            options=session_config.options,
        )
        history_mode = "latest_user" if link is not None else "full"

        def record_upstream_session(upstream_session_id: str) -> None:
            link_service.record(
                session_id=session_id,
                agent_id=session_config.agent_id,
                model=session_config.model,
                options=session_config.options,
                upstream_session_id=upstream_session_id,
            )
```

When creating `CodexModelClient`, pass:

```python
                    upstream_session_id=link.upstream_session_id if link is not None else None,
```

When returning the runtime for Codex, pass:

```python
                history_mode=history_mode,
                on_upstream_session_id=record_upstream_session,
```

When creating `ClaudeModelClient`, pass:

```python
                    upstream_session_id=link.upstream_session_id if link is not None else None,
```

When returning the runtime for Claude, pass the same `history_mode` and `on_upstream_session_id`.

In `_runtime`, add optional parameters:

```python
    def _runtime(
        self,
        model,
        history_mode: str = "full",
        on_upstream_session_id=None,
    ) -> AgentRuntime:
```

And pass them to `AgentRuntime`:

```python
            history_mode=history_mode,
            on_upstream_session_id=on_upstream_session_id,
```

Do not wire session links into `_create_runtime_for_app_config` unless a gateway session exists. The first `/api/chat` call creates a transcript session before `_run_model_loop`, and Task 5 tests cover link persistence for normal active sessions.

- [ ] **Step 5: Run app tests**

Run:

```bash
python -m pytest tests/test_app.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/runtime_factory.py tests/test_app.py
git commit -m "feat(runtime): connect gateway sessions to native cli sessions"
```

---

### Task 6: End-To-End Verification And Regression Guard

**Files:**
- Modify only if tests expose gaps.
- Test: backend pytest suite.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified behavior for Codex and Claude native session resume bridge.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
python -m pytest tests/test_agent_session_link.py tests/test_model_client.py tests/test_runtime.py tests/test_app.py -q
```

Expected: pass.

- [ ] **Step 2: Run full backend tests**

Run:

```bash
python -m pytest -q
```

Expected: pass.

- [ ] **Step 3: Manual Codex smoke test**

Start the gateway, send two messages in the same web session, and inspect emitted `codex.event` output or transcript JSONL.

Expected:

- First turn emits a Codex `thread.started` event.
- Transcript contains one `agent_session_link` event with `upstream_session_id` equal to the Codex thread id.
- Second turn uses `codex exec resume <thread_id> -`.
- Second turn prompt contains only the latest user message, not the whole transcript.

- [ ] **Step 4: Manual Claude smoke test**

Select Claude for a new gateway session, send two messages, and inspect the transcript JSONL.

Expected:

- First turn JSON result contains `session_id`.
- Transcript contains one `agent_session_link` event with that Claude `session_id`.
- Second turn uses `claude -p --resume <session_id> --output-format json`.
- Second turn prompt contains only the latest user message.

- [ ] **Step 5: Residual risk check**

Review these known constraints before merging:

- Codex `exec resume` does not expose all `codex exec` start flags. This plan relies on native session continuity for sandbox/workspace/profile settings after the first turn.
- If a native CLI session is deleted outside the gateway, resume can fail. The runtime should surface the CLI error as `runtime_error`; automatic fallback to full-history bootstrap is not included in this plan to avoid silently forking context.
- Existing gateway sessions without `agent_session_link` bootstrap the native CLI context by sending full transcript once, then resume with latest-user prompts afterward.

- [ ] **Step 6: Commit verification notes if docs are updated**

If a verification report is added, use:

```bash
git add docs/reports/<report-file>.md
git commit -m "docs: record cli session resume verification"
```

---

## Success Criteria

- A gateway session records a native Codex `thread_id` after the first Codex response.
- A gateway session records a native Claude `session_id` after the first Claude response.
- Later turns in the same gateway session pass the stored id to Codex/Claude resume commands.
- Later turns send only the latest user message to native CLI sessions.
- Existing sessions without native links still work by bootstrapping from full gateway history once.
- Gateway transcript remains complete for UI/history/search.
- Backend tests pass.

## Self-Review

- Spec coverage: Codex and Claude are both covered; CLI resume is used; SDKs are excluded; gateway history remains durable; resumed prompts avoid transcript duplication.
- Placeholder scan: no placeholder implementation steps are left.
- Type consistency: `upstream_session_id`, `agent_session_link`, `history_mode`, and `ModelResponse.upstream_session_id` are used consistently across tasks.
