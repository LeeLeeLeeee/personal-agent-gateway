# Personal Agent Web Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Version A: a private browser-accessible local Mac agent gateway exposed through Cloudflare Quick Tunnel, without Discord, without Hermes gateway, without buying a domain.

**Architecture:** FastAPI serves a token-protected single-user web UI and JSON API on `127.0.0.1`. A local custom agent runtime manages one active conversation, persists transcript events to local JSONL files, calls an OpenAI-compatible model adapter, executes safe workspace tools directly, and pauses shell execution until the web user approves each command.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Pydantic, httpx, pytest, pytest-asyncio, vanilla HTML/CSS/JS, Cloudflare Quick Tunnel via `cloudflared tunnel --url`.

---

## Success Criteria

- [ ] Local server binds only to `127.0.0.1`.
- [ ] Every HTML/API/approval route requires `AGENT_WEB_TOKEN`.
- [ ] External access works through a generated `trycloudflare.com` URL.
- [ ] One active chat session survives backend restart.
- [ ] `fs.read` and `fs.list` are restricted to `AGENT_WORKSPACE_ROOT`.
- [ ] `shell.run` never executes until approved through the web UI.
- [ ] Transcript stores user, assistant, tool request, approval, tool result, denial, and runtime error events.
- [ ] Tests cover auth, transcript restore, path restriction, shell approval, runtime loop, and API behavior.

## Target File Tree

```text
hermes-web-gateway/
  .env.example
  .gitignore
  README.md
  pyproject.toml
  scripts/
    run_local.sh
    run_tunnel.sh
  src/
    personal_agent_gateway/
      __init__.py
      app.py
      approval.py
      auth.py
      config.py
      model_client.py
      runtime.py
      tools.py
      transcript.py
      static/
        app.js
        index.html
        styles.css
  tests/
    conftest.py
    test_app.py
    test_config_auth.py
    test_runtime.py
    test_tools.py
    test_transcript.py
```

## Task 1: Scaffold Project, Config, And Auth

- [ ] Initialize package metadata and test tooling.

Create `pyproject.toml`:

```toml
[project]
name = "personal-agent-gateway"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.116.0",
  "uvicorn[standard]>=0.35.0",
  "pydantic>=2.11.0",
  "httpx>=0.28.0",
  "python-dotenv>=1.1.0"
]

[project.optional-dependencies]
dev = [
  "pytest>=8.4.0",
  "pytest-asyncio>=1.0.0",
  "ruff>=0.12.0"
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"
```

Create `.gitignore`:

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
data/
```

Create `.env.example`:

```bash
AGENT_WEB_HOST=127.0.0.1
AGENT_WEB_PORT=8787
AGENT_WEB_TOKEN=replace-with-strong-random-token
AGENT_WORKSPACE_ROOT=/Users/iyeonghyeon/works
AGENT_MODEL_PROVIDER=openai
AGENT_MODEL=gpt-4.1
AGENT_SESSION_DIR=./data/sessions
OPENAI_API_KEY=replace-with-api-key
```

- [ ] Write failing tests in `tests/test_config_auth.py`.

```python
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_agent_gateway.auth import require_token
from personal_agent_gateway.config import ConfigError, load_config


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    with pytest.raises(ConfigError, match="AGENT_WEB_TOKEN"):
        load_config()


def test_load_config_rejects_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_WEB_TOKEN", "secret-token")
    monkeypatch.setenv("AGENT_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    with pytest.raises(ConfigError, match="127.0.0.1"):
        load_config()


def test_require_token_accepts_query_token_and_sets_cookie() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/?token=secret-token")

    assert response.status_code == 200
    assert response.cookies.get("agent_web_token") == "secret-token"


def test_require_token_rejects_missing_or_invalid_token() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)

    assert client.get("/").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
```

- [ ] Implement `src/personal_agent_gateway/config.py`.

Use `pydantic.BaseModel`. `load_config()` reads env vars, defaults host to `127.0.0.1`, defaults port to `8787`, resolves `workspace_root` and `session_dir`, creates neither directory at config load, and rejects any host other than `127.0.0.1` or `localhost`.

- [ ] Implement `src/personal_agent_gateway/auth.py`.

`require_token(expected_token: str)` returns a FastAPI dependency. It accepts token from `?token=...`, `Authorization: Bearer ...`, or `agent_web_token` cookie. On query-token success, set an HTTP-only same-site-lax cookie on the response. On failure, raise `HTTPException(status_code=401)`.

- [ ] Verify Task 1.

```bash
cd /Users/iyeonghyeon/works/hermes-web-gateway
python -m pytest tests/test_config_auth.py
```

Expected result:

```text
4 passed
```

## Task 2: Implement Restart-Safe Transcript Store

- [ ] Write failing tests in `tests/test_transcript.py`.

Required behaviors:

- `TranscriptStore.start_new()` creates a new active transcript id.
- `TranscriptStore.append()` appends JSONL events in order.
- `TranscriptStore.load_active()` restores events after constructing a new store with the same directory.
- `TranscriptStore.reset()` starts a new active transcript without deleting old transcript files.

Test skeleton:

```python
from pathlib import Path

from personal_agent_gateway.transcript import TranscriptStore


def test_transcript_restores_active_session_after_restart(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    store.start_new()
    store.append("user", {"content": "hello"})
    store.append("assistant", {"content": "hi"})

    restarted = TranscriptStore(tmp_path)
    events = restarted.load_active()

    assert [event.kind for event in events] == ["user", "assistant"]
    assert events[0].payload == {"content": "hello"}


def test_reset_preserves_old_transcript_file(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    first_id = store.start_new()
    store.append("user", {"content": "first"})

    second_id = store.reset()

    assert second_id != first_id
    assert (tmp_path / f"{first_id}.jsonl").exists()
    assert store.load_active() == []
```

- [ ] Implement `src/personal_agent_gateway/transcript.py`.

Data model:

```python
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

TranscriptKind = Literal[
    "user",
    "assistant",
    "tool_request",
    "approval",
    "tool_result",
    "tool_denial",
    "runtime_error",
]


class TranscriptEvent(BaseModel):
    id: str
    transcript_id: str
    kind: TranscriptKind
    payload: dict[str, object]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Implementation details:

- Store active transcript pointer in `active.json`.
- Store events in `<transcript_id>.jsonl`.
- Create `AGENT_SESSION_DIR` on first write.
- Use append-only writes for events.
- Use `json.loads` and `model_validate` when reading.

- [ ] Verify Task 2.

```bash
python -m pytest tests/test_transcript.py
```

Expected result:

```text
2 passed
```

## Task 3: Implement Workspace Tools And Shell Approval Queue

- [ ] Write failing tests in `tests/test_tools.py`.

Required behaviors:

- `fs_list(".")` lists only inside `AGENT_WORKSPACE_ROOT`.
- `fs_read("file.txt")` reads inside the root.
- `../outside.txt` is rejected even when the string prefix looks similar.
- `shell_request("pwd")` creates a pending approval and does not execute.
- `approve_shell(id)` executes exactly one approved command.
- `deny_shell(id)` records denial and does not execute.

Test skeleton:

```python
from pathlib import Path

import pytest

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.tools import ToolError, WorkspaceTools


def test_workspace_path_traversal_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside.txt"
    root.mkdir()
    outside.write_text("secret", encoding="utf-8")

    tools = WorkspaceTools(root, ApprovalStore())

    with pytest.raises(ToolError, match="outside workspace"):
        tools.fs_read("../outside.txt")


def test_shell_command_requires_approval(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    approvals = ApprovalStore()
    tools = WorkspaceTools(root, approvals)

    pending = tools.shell_request("pwd")

    assert pending.status == "pending"
    assert approvals.get(pending.id).command == "pwd"


def test_approved_shell_command_executes_once(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    approvals = ApprovalStore()
    tools = WorkspaceTools(root, approvals)
    pending = tools.shell_request("pwd")

    result = tools.approve_shell(pending.id)

    assert result.exit_code == 0
    assert str(root) in result.stdout

    with pytest.raises(ToolError, match="not pending"):
        tools.approve_shell(pending.id)
```

- [ ] Implement `src/personal_agent_gateway/approval.py`.

Models:

```python
from dataclasses import dataclass
from typing import Literal

ApprovalStatus = Literal["pending", "approved", "denied"]


@dataclass(frozen=True)
class ShellApproval:
    id: str
    command: str
    status: ApprovalStatus
```

Implementation details:

- In-memory approval queue is sufficient for Version A.
- Runtime transcript persists approval/denial/result events for restart audit.
- `create(command)`, `get(id)`, `approve(id)`, `deny(id)`, and `pending()` are required.

- [ ] Implement `src/personal_agent_gateway/tools.py`.

Models:

```python
from dataclasses import dataclass
from typing import Literal


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ShellResult:
    approval_id: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PendingShellCommand:
    id: str
    command: str
    status: Literal["pending"]
```

Implementation details:

- Resolve paths with `Path.resolve()`.
- Validate containment with `resolved.relative_to(workspace_root)`.
- Do not use string-prefix checks for path safety.
- Shell execution uses `subprocess.run` with `cwd=workspace_root`, `capture_output=True`, `text=True`, `timeout=60`.
- Store command output in transcript through the runtime, not directly inside `WorkspaceTools`.

- [ ] Verify Task 3.

```bash
python -m pytest tests/test_tools.py
```

Expected result:

```text
3 passed
```

## Task 4: Implement Model Adapter Interface And Runtime Loop

- [ ] Write failing tests in `tests/test_runtime.py`.

Required behaviors:

- User input is appended as a `user` event.
- Plain assistant output is appended as an `assistant` event.
- Model-requested `fs.list` and `fs.read` execute automatically and append tool events.
- Model-requested `shell.run` appends `tool_request` and returns a pending approval response.
- Approving a shell request appends `approval`, `tool_result`, and the resumed assistant answer.
- Denying a shell request appends `tool_denial`.

Fake client shape:

```python
from collections.abc import Sequence

from personal_agent_gateway.model_client import ModelClient, ModelResponse, ToolCall


class FakeModelClient(ModelClient):
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self.responses = list(responses)
        self.messages: list[dict[str, object]] = []

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        self.messages.append({"count": len(messages)})
        return self.responses.pop(0)
```

- [ ] Implement `src/personal_agent_gateway/model_client.py`.

Data models:

```python
from dataclasses import dataclass
from typing import Literal, Protocol

ToolName = Literal["fs.list", "fs.read", "shell.run"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: ToolName
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]


class ModelClient(Protocol):
    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        raise NotImplementedError
```

OpenAI-compatible adapter:

- Use `httpx.AsyncClient`.
- Endpoint: `https://api.openai.com/v1/chat/completions`.
- Headers: `Authorization: Bearer <OPENAI_API_KEY>`.
- Request includes `model`, `messages`, and `tools`.
- Parse assistant `message.content` and `message.tool_calls`.
- Keep the adapter behind `ModelClient` so tests never call the network.

- [ ] Implement `src/personal_agent_gateway/runtime.py`.

Public API:

```python
class AgentRuntime:
    async def handle_user_message(self, content: str) -> RuntimeResult: ...
    async def approve(self, approval_id: str) -> RuntimeResult: ...
    async def deny(self, approval_id: str) -> RuntimeResult: ...
```

`RuntimeResult` fields:

- `messages: list[dict[str, object]]`
- `pending_approval: dict[str, object] | None`

Runtime loop rules:

- Convert transcript events to model messages before each model call.
- Execute `fs.list` and `fs.read` immediately.
- For `shell.run`, create approval and stop the loop.
- Limit automatic tool loop to 8 iterations per user message.
- On exceptions, append `runtime_error` and return an error message to the UI.
- Never include `AGENT_WEB_TOKEN` or `OPENAI_API_KEY` in transcript payloads.

- [ ] Verify Task 4.

```bash
python -m pytest tests/test_runtime.py
```

Expected result:

```text
all runtime tests passed
```

## Task 5: Implement FastAPI App And API Tests

- [ ] Write failing tests in `tests/test_app.py`.

Required behaviors:

- Unauthenticated `/`, `/api/history`, `/api/chat`, `/api/reset`, and approval routes return `401`.
- `/?token=<token>` authenticates and sets cookie.
- `POST /api/chat` returns runtime output.
- `GET /api/history` returns restored transcript after app recreation using the same session dir.
- `POST /api/approvals/{id}/approve` resumes execution.
- `POST /api/approvals/{id}/deny` records denial.

Route contract:

```text
GET  /                         -> HTML UI
GET  /static/app.js             -> JavaScript
GET  /static/styles.css         -> CSS
GET  /api/history               -> { events: [...] }
POST /api/chat                  -> { messages: [...], pending_approval: ... | null }
POST /api/approvals/{id}/approve -> { messages: [...], pending_approval: ... | null }
POST /api/approvals/{id}/deny    -> { messages: [...], pending_approval: ... | null }
POST /api/reset                 -> { events: [] }
```

- [ ] Implement `src/personal_agent_gateway/app.py`.

Implementation details:

- Provide `create_app(config: AppConfig | None = None, runtime: AgentRuntime | None = None) -> FastAPI`.
- Mount static files from `src/personal_agent_gateway/static`.
- Use one shared runtime per app instance.
- Apply token dependency to every UI and API route.
- Return JSON errors with no stack traces.
- `main()` loads config and runs Uvicorn using `AGENT_WEB_HOST` and `AGENT_WEB_PORT`.

- [ ] Verify Task 5.

```bash
python -m pytest tests/test_app.py
```

Expected result:

```text
all API tests passed
```

## Task 6: Implement Minimal Web UI

- [ ] Create `src/personal_agent_gateway/static/index.html`.

UI elements:

- Token entry state when no valid cookie exists.
- Transcript message list.
- Message composer.
- Pending shell approval panel showing the exact command.
- Approve and deny buttons.
- Reset conversation button.
- Error banner for API errors.

- [ ] Create `src/personal_agent_gateway/static/app.js`.

Client behavior:

- Read token from URL input and call `/?token=...`.
- Load `/api/history` on page load.
- Submit chat to `/api/chat`.
- Render returned messages and pending approval.
- Approve with `/api/approvals/{id}/approve`.
- Deny with `/api/approvals/{id}/deny`.
- Reset with `/api/reset`.

- [ ] Create `src/personal_agent_gateway/static/styles.css`.

Design constraints:

- Dense utility-style app UI, not a marketing page.
- No card-inside-card layout.
- Stable composer and approval panel dimensions.
- Text must wrap inside message bubbles and command blocks.
- Use system font and restrained colors.

- [ ] Add one API-level UI smoke test to `tests/test_app.py`.

Assert `/` returns HTML containing the app root element and `/static/app.js` returns JavaScript.

- [ ] Verify Task 6.

```bash
python -m pytest tests/test_app.py
```

Expected result:

```text
all API and UI smoke tests passed
```

## Task 7: Add Local And Tunnel Run Scripts

- [ ] Create `scripts/run_local.sh`.

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python -m uvicorn personal_agent_gateway.app:create_app --factory --host "${AGENT_WEB_HOST:-127.0.0.1}" --port "${AGENT_WEB_PORT:-8787}"
```

- [ ] Create `scripts/run_tunnel.sh`.

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_WEB_PORT:=8787}"

cloudflared tunnel --url "http://127.0.0.1:${AGENT_WEB_PORT}"
```

- [ ] Make scripts executable.

```bash
chmod +x scripts/run_local.sh scripts/run_tunnel.sh
```

- [ ] Verify Task 7.

```bash
test -x scripts/run_local.sh
test -x scripts/run_tunnel.sh
```

Expected result: both commands exit with status `0`.

## Task 8: Write Operator Documentation

- [ ] Create `README.md`.

Required sections:

- What this is.
- What this is not.
- Security model.
- Environment setup.
- Local run.
- Cloudflare Quick Tunnel run.
- How restart persistence works.
- Shell approval behavior.
- Troubleshooting.

Required commands:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
source .env
scripts/run_local.sh
scripts/run_tunnel.sh
```

Cloudflare note:

- Quick Tunnel gives a generated `https://<random>.trycloudflare.com` URL.
- No domain purchase is required.
- The URL changes when the tunnel restarts.
- The web token is still required because the URL can be accessed by anyone who knows it.

- [ ] Verify Task 8.

```bash
python -m pytest
```

Expected result:

```text
all tests passed
```

## Task 9: End-To-End Manual Verification

- [ ] Install dependencies.

```bash
cd /Users/iyeonghyeon/works/hermes-web-gateway
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

- [ ] Create local env file.

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edit `.env` with the generated token and valid model API key.

- [ ] Start local server.

```bash
source .env
scripts/run_local.sh
```

Expected output includes:

```text
Uvicorn running on http://127.0.0.1:8787
```

- [ ] Start Cloudflare Quick Tunnel in a second terminal.

```bash
source .env
scripts/run_tunnel.sh
```

Expected output includes a generated `https://*.trycloudflare.com` URL.

- [ ] Open the external URL with token.

```text
https://<generated>.trycloudflare.com/?token=<AGENT_WEB_TOKEN>
```

- [ ] Verify chat, filesystem read/list, shell approval, denial, reset, and backend restart restore through the browser.

## Final Verification Before Completion

Run:

```bash
cd /Users/iyeonghyeon/works/hermes-web-gateway
python -m pytest
python -m ruff check .
```

Expected result:

```text
all tests passed
All checks passed!
```

Then run the local server and tunnel manually as described in Task 9.

## References

- Version A spec: `docs/specs/2026-07-05-cloudflare-quick-tunnel-version-a-spec.md`
- OpenAI Chat Completions API: `https://platform.openai.com/docs/api-reference/chat/create`
- OpenAI function calling guide: `https://platform.openai.com/docs/guides/function-calling`
