# Local Agent Registry Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the backend runtime wiring first, then add session-level local agent selection for Codex CLI and Claude Code CLI.

**Architecture:** Move provider construction out of `app.py` into registry/factory boundaries. Store the selected agent/model/options on the transcript as session metadata, lock it when the first user message is accepted, and resolve runtime clients from the active session config. Add frontend controls only after backend APIs and runtime selection are tested.

**Tech Stack:** FastAPI, Pydantic, pytest, Vite React, Vitest, local CLI subprocess adapters.

---

## Source Spec

Implement from `docs/superpowers/specs/2026-07-08-local-agent-registry-refactor-design.md`.

Important decisions from the spec:

- Claude means Claude Code CLI, not direct Anthropic API.
- Codex and Claude are both local CLI agents.
- Session config is editable only while the session has no conversation/runtime events other than `session_config_set` and `session_rename`.
- The first user message locks the session config.
- CLI availability is probed automatically; model/option choices come from built-in descriptors.
- Backend refactor must precede frontend work.

## File Structure

Create:

- `src/personal_agent_gateway/agents.py`: agent descriptors, option schemas, built-in registry, validation, and CLI availability probe.
- `src/personal_agent_gateway/runtime_factory.py`: `AgentRuntimeFactory` that resolves app/default config and later session config into `AgentRuntime`.
- `src/personal_agent_gateway/session_config.py`: session config models, transcript-backed read/write, locking policy.
- `src/personal_agent_gateway/api/agents.py`: `/api/agents` and active session config endpoints.
- `tests/test_agents.py`: registry, probe, and validation tests.
- `tests/test_session_config.py`: transcript-backed config and lock policy tests.
- `tests/test_api_agents.py`: agent catalog and session config API tests.

Modify:

- `src/personal_agent_gateway/app.py`: use `AgentRuntimeFactory`, register `agents_router`, return active session agent/model in status.
- `src/personal_agent_gateway/api/__init__.py`: export `agents_router`.
- `src/personal_agent_gateway/config.py`: add Claude binary/default fields only; keep existing Codex/OpenAI env behavior.
- `src/personal_agent_gateway/model_client.py`: add Claude CLI adapter and isolate local CLI command-building enough for tests.
- `src/personal_agent_gateway/transcript.py`: add `session_config_set` to event kinds and expose helpers needed by `session_config.py`.
- `frontend/src/api/client.js`: add `agents()`, `activeSessionConfig()`, `updateActiveSessionConfig()`.
- `frontend/src/api/client.test.js`: cover new endpoints.
- `frontend/src/components/containers/GatewayApp/index.jsx`: load/save config around session state.
- `frontend/src/components/organisms/Statusbar/index.jsx`: show active session agent/model.
- `frontend/src/components/organisms/ChatView/index.jsx`: accept agent picker props.
- `frontend/src/components/organisms/AgentPicker/index.jsx`: new organism.
- `frontend/src/components/molecules/AgentOptionField/index.jsx`: new molecule.
- `frontend/src/components/molecules/AgentAvailabilityBadge/index.jsx`: new molecule.
- `frontend/src/components/references/organisms.md` and `frontend/src/components/references/molecules.md`: register new atomic design components.
- `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`: cover editable/locked/unavailable UI states.

Do not modify:

- `src/personal_agent_gateway/static/**` unless explicitly required for a regression. Vite React owns the current frontend.
- `.env` from the app or tests.

---

## Task 1: Extract Runtime Factory Without Behavior Change

**Files:**
- Create: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Add a failing regression test proving app runtime construction is delegated**

Append this test to `tests/test_app.py`:

```python
def test_create_app_uses_runtime_factory_when_runtime_not_injected(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    created: list[dict[str, object]] = []

    class StubFactory:
        def __init__(self, app_config, transcript, job_service, event_bus) -> None:
            created.append(
                {
                    "config": app_config,
                    "transcript": transcript,
                    "job_service": job_service,
                    "event_bus": event_bus,
                }
            )

        def create_default_runtime(self) -> FakeRuntime:
            return FakeRuntime()

    monkeypatch.setattr(app_module, "AgentRuntimeFactory", StubFactory)
    client = auth_client(config, runtime=None)

    response = client.post("/api/chat", json={"message": "factory"})

    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == "reply: factory"
    assert len(created) == 1
    assert created[0]["config"] is config
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
pytest tests/test_app.py::test_create_app_uses_runtime_factory_when_runtime_not_injected -q
```

Expected: FAIL with `AttributeError` because `app_module.AgentRuntimeFactory` does not exist yet.

- [ ] **Step 3: Create `runtime_factory.py` with behavior copied from `_create_runtime()`**

Create `src/personal_agent_gateway/runtime_factory.py`:

```python
from collections.abc import Awaitable, Callable

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import CodexModelClient, OpenAIModelClient
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class AgentRuntimeFactory:
    def __init__(
        self,
        config: AppConfig,
        transcript: TranscriptStore,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._transcript = transcript
        self._job_service = job_service
        self._event_bus = event_bus

    def create_default_runtime(self) -> AgentRuntime:
        return self._create_runtime_for_app_config()

    def _create_runtime_for_app_config(self) -> AgentRuntime:
        config = self._config
        if config.model_provider == "codex":
            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish({"type": "codex.event", **event})

            return self._runtime(
                CodexModelClient(
                    binary=config.codex_binary,
                    model=config.model,
                    workspace_root=config.workspace_root,
                    sandbox=config.codex_sandbox,
                    approval_policy=config.codex_approval_policy,
                    timeout_seconds=config.codex_timeout_seconds,
                    on_event=publish_codex_event,
                )
            )

        if config.model_provider != "openai":
            raise ConfigError(f"Unsupported model provider: {config.model_provider}")
        if not config.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

        return self._runtime(OpenAIModelClient(api_key=config.openai_api_key or "", model=config.model))

    def _runtime(self, model) -> AgentRuntime:
        return AgentRuntime(
            transcript=self._transcript,
            tools=WorkspaceTools(self._config.workspace_root, ApprovalStore()),
            model=model,
            job_service=self._job_service,
            event_bus=self._event_bus,
        )
```

Remove unused `Callable`/`Awaitable` imports if ruff flags them.

- [ ] **Step 4: Modify `app.py` to use the factory**

In `src/personal_agent_gateway/app.py`:

1. Add import:

```python
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
```

2. Replace the `shared_runtime = ...` block with:

```python
    runtime_factory = AgentRuntimeFactory(
        app_config,
        transcript,
        app.state.job_service,
        event_bus,
    )
    shared_runtime = runtime or runtime_factory.create_default_runtime()
```

3. In `/api/reset`, replace:

```python
            shared_runtime = _create_runtime(app_config, transcript, app.state.job_service)
```

with:

```python
            shared_runtime = runtime_factory.create_default_runtime()
```

4. Leave `_create_runtime()` in place for this task. It will be deleted after regression tests are green.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_app.py::test_create_app_uses_runtime_factory_when_runtime_not_injected tests/test_app.py::test_app_reuses_one_runtime_instance tests/test_app.py::test_reset_invalidates_real_runtime_pending_approval -q
```

Expected: PASS.

- [ ] **Step 6: Delete obsolete `_create_runtime()` and imports from `app.py`**

Remove from `app.py`:

- direct imports of `ApprovalStore`, `CodexModelClient`, `OpenAIModelClient`, and `WorkspaceTools` if unused after extraction;
- the `_create_runtime()` function.

- [ ] **Step 7: Run backend regression**

Run:

```powershell
pytest tests/test_app.py tests/test_model_client.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/runtime_factory.py tests/test_app.py
git commit -m "refactor: extract agent runtime factory"
```

---

## Task 2: Add Agent Registry and CLI Probe

**Files:**
- Create: `src/personal_agent_gateway/agents.py`
- Modify: `src/personal_agent_gateway/config.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_agents.py`:

```python
from pathlib import Path

import pytest

from personal_agent_gateway.agents import AgentRegistry, CliProbeResult
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        codex_binary="codex-test",
        claude_binary="claude-test",
    )


def test_registry_lists_codex_and_claude_with_safe_defaults(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    registry = AgentRegistry(
        config,
        probe=lambda binary: CliProbeResult(available=binary == "codex-test", error=None if binary == "codex-test" else "not found"),
    )

    catalog = registry.catalog()

    assert [agent.id for agent in catalog] == ["codex", "claude"]
    codex = catalog[0]
    claude = catalog[1]
    assert codex.available is True
    assert codex.binary == "codex-test"
    assert codex.default_model == "default"
    assert codex.defaults["sandbox"] == "workspace-write"
    assert claude.available is False
    assert claude.availability_error == "not found"
    assert claude.defaults["effort"] == "medium"


def test_registry_rejects_unknown_agent_model_and_option(tmp_path: Path) -> None:
    registry = AgentRegistry(make_config(tmp_path), probe=lambda _binary: CliProbeResult(True, None))

    with pytest.raises(ValueError, match="Unknown agent"):
        registry.validate_config("missing", "default", {})

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("codex", "not-listed", {})

    with pytest.raises(ValueError, match="Unsupported option"):
        registry.validate_config("codex", "default", {"not_allowed": True})


def test_registry_accepts_supported_provider_options(tmp_path: Path) -> None:
    registry = AgentRegistry(make_config(tmp_path), probe=lambda _binary: CliProbeResult(True, None))

    assert registry.validate_config(
        "claude",
        "sonnet",
        {"effort": "high", "permission_mode": "manual"},
    ) == {
        "agent_id": "claude",
        "model": "sonnet",
        "options": {"effort": "high", "permission_mode": "manual"},
    }
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_agents.py -q
```

Expected: FAIL because `personal_agent_gateway.agents` and `claude_binary` do not exist.

- [ ] **Step 3: Add Claude config field**

Modify `src/personal_agent_gateway/config.py`:

1. Add default helper:

```python
def _default_claude_binary(os_name: str | None = None) -> str:
    if (os_name or os.name) == "nt":
        return "claude.cmd"
    return "claude"
```

2. Add field to `AppConfig`:

```python
    claude_binary: str = _default_claude_binary()
```

3. In `from_env()`, pass:

```python
                claude_binary=env.get("AGENT_CLAUDE_BIN") or _default_claude_binary(),
```

4. In `load_config()`, include:

```python
            "AGENT_CLAUDE_BIN": os.getenv("AGENT_CLAUDE_BIN"),
```

- [ ] **Step 4: Implement `agents.py`**

Create `src/personal_agent_gateway/agents.py`:

```python
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from personal_agent_gateway.config import AppConfig

AgentId = Literal["codex", "claude"]


class AgentOption(BaseModel):
    name: str
    kind: str
    choices: list[str] = []
    required: bool = False


class AgentDescriptor(BaseModel):
    id: AgentId
    label: str
    kind: Literal["local_cli"] = "local_cli"
    binary: str
    available: bool
    availability_error: str | None = None
    models: list[str]
    default_model: str
    options_schema: list[AgentOption]
    defaults: dict[str, Any]


@dataclass(frozen=True)
class CliProbeResult:
    available: bool
    error: str | None


Probe = Callable[[str], CliProbeResult]


def probe_cli(binary: str) -> CliProbeResult:
    try:
        completed = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return CliProbeResult(False, "not found on PATH")
    except TimeoutError:
        return CliProbeResult(False, "probe timed out")
    except OSError as exc:
        return CliProbeResult(False, str(exc))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return CliProbeResult(False, detail[:200] or f"exit {completed.returncode}")
    return CliProbeResult(True, None)


class AgentRegistry:
    def __init__(self, config: AppConfig, probe: Probe | None = None) -> None:
        self._config = config
        self._probe = probe or probe_cli

    def catalog(self) -> list[AgentDescriptor]:
        return [self._codex(), self._claude()]

    def get(self, agent_id: str) -> AgentDescriptor:
        for descriptor in self.catalog():
            if descriptor.id == agent_id:
                return descriptor
        raise ValueError(f"Unknown agent: {agent_id}")

    def validate_config(self, agent_id: str, model: str, options: dict[str, Any]) -> dict[str, Any]:
        descriptor = self.get(agent_id)
        if model not in descriptor.models:
            raise ValueError(f"Unsupported model for {agent_id}: {model}")
        allowed = {option.name for option in descriptor.options_schema}
        for key in options:
            if key not in allowed:
                raise ValueError(f"Unsupported option for {agent_id}: {key}")
        return {"agent_id": descriptor.id, "model": model, "options": dict(options)}

    def _codex(self) -> AgentDescriptor:
        probe = self._probe(self._config.codex_binary)
        return AgentDescriptor(
            id="codex",
            label="Codex CLI",
            binary=self._config.codex_binary,
            available=probe.available,
            availability_error=probe.error,
            models=["default"],
            default_model="default",
            options_schema=[
                AgentOption(name="sandbox", kind="select", choices=["read-only", "workspace-write", "danger-full-access"]),
                AgentOption(name="approval_policy", kind="select", choices=["untrusted", "on-request", "never"]),
                AgentOption(name="profile", kind="text"),
            ],
            defaults={
                "sandbox": self._config.codex_sandbox,
                "approval_policy": self._config.codex_approval_policy,
            },
        )

    def _claude(self) -> AgentDescriptor:
        probe = self._probe(self._config.claude_binary)
        return AgentDescriptor(
            id="claude",
            label="Claude Code",
            binary=self._config.claude_binary,
            available=probe.available,
            availability_error=probe.error,
            models=["sonnet", "opus"],
            default_model="sonnet",
            options_schema=[
                AgentOption(name="effort", kind="select", choices=["low", "medium", "high", "xhigh", "max"]),
                AgentOption(name="permission_mode", kind="select", choices=["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"]),
                AgentOption(name="agent", kind="text"),
            ],
            defaults={"effort": "medium", "permission_mode": "manual"},
        )
```

- [ ] **Step 5: Run registry tests**

Run:

```powershell
pytest tests/test_agents.py -q
```

Expected: PASS.

- [ ] **Step 6: Run config tests**

Run:

```powershell
pytest tests/test_config_auth.py tests/test_agents.py -q
```

Expected: PASS. If config tests assert an exact set of env keys, update them to include `AGENT_CLAUDE_BIN` with no secret exposure.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/config.py src/personal_agent_gateway/agents.py tests/test_agents.py tests/test_config_auth.py
git commit -m "feat: add local agent registry"
```

---

## Task 3: Expose `/api/agents`

**Files:**
- Create: `src/personal_agent_gateway/api/agents.py`
- Modify: `src/personal_agent_gateway/api/__init__.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_api_agents.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_api_agents.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        codex_binary="codex-test",
        claude_binary="claude-test",
    )


def test_agents_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/agents")

    assert response.status_code == 401


def test_agents_returns_safe_catalog(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(
        agents_module,
        "probe_cli",
        lambda binary: agents_module.CliProbeResult(binary == "codex-test", None if binary == "codex-test" else "not found"),
    )
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    response = client.get("/api/agents")

    assert response.status_code == 200
    payload = response.json()
    assert [agent["id"] for agent in payload["agents"]] == ["codex", "claude"]
    assert payload["agents"][0]["available"] is True
    assert payload["agents"][1]["available"] is False
    assert payload["agents"][1]["availability_error"] == "not found"
    assert "openai_api_key" not in response.text
    assert "web_token" not in response.text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_api_agents.py -q
```

Expected: FAIL because `/api/agents` is not registered.

- [ ] **Step 3: Add router**

Create `src/personal_agent_gateway/api/agents.py`:

```python
from fastapi import APIRouter, Request

from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.api.jobs import session_dependency

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    registry = AgentRegistry(request.app.state.app_config)
    return {"agents": [agent.model_dump(mode="json") for agent in registry.catalog()]}
```

- [ ] **Step 4: Register router**

Modify `src/personal_agent_gateway/api/__init__.py`:

```python
from personal_agent_gateway.api.agents import router as agents_router
```

Add `agents_router` to `__all__` if the file uses one.

Modify `src/personal_agent_gateway/app.py`:

```python
from personal_agent_gateway.api import (
    agents_router,
    artifacts_router,
    auth_router,
    capabilities_router,
    jobs_router,
    schedules_router,
    settings_router,
)
```

and include it before settings:

```python
    app.include_router(agents_router)
```

- [ ] **Step 5: Run API tests**

Run:

```powershell
pytest tests/test_api_agents.py tests/test_api_settings.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/api/agents.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_agents.py
git commit -m "feat: expose local agent catalog API"
```

---

## Task 4: Add Transcript-Backed Session Agent Config

**Files:**
- Create: `src/personal_agent_gateway/session_config.py`
- Modify: `src/personal_agent_gateway/transcript.py`
- Test: `tests/test_session_config.py`

- [ ] **Step 1: Write failing session config tests**

Create `tests/test_session_config.py`:

```python
import pytest

from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.transcript import TranscriptStore


def test_effective_config_defaults_to_codex_for_empty_session(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    store.start_new()
    service = SessionAgentConfigService(store)

    config = service.effective_config(session_id)

    assert config.session_id == session_id
    assert config.agent_id == "codex"
    assert config.model == "default"
    assert config.options == {}
    assert config.editable is True


def test_set_config_appends_session_config_event(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    service = SessionAgentConfigService(store)

    config = service.set_config(session_id, "claude", "sonnet", {"effort": "high"})

    assert config.agent_id == "claude"
    assert config.model == "sonnet"
    assert config.options == {"effort": "high"}
    events = store.load(session_id)
    assert events[-1].kind == "session_config_set"
    assert events[-1].payload["agent_id"] == "claude"


def test_config_is_locked_after_first_user_message(tmp_path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    store.append("user", {"content": "hello"})
    service = SessionAgentConfigService(store)

    config = service.effective_config(session_id)

    assert config.editable is False
    with pytest.raises(ValueError, match="Session config is locked"):
        service.set_config(session_id, "claude", "sonnet", {})
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_session_config.py -q
```

Expected: FAIL because `session_config.py` and `session_config_set` do not exist.

- [ ] **Step 3: Extend transcript event kinds**

Modify `src/personal_agent_gateway/transcript.py`:

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
]
```

- [ ] **Step 4: Implement session config service**

Create `src/personal_agent_gateway/session_config.py`:

```python
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from personal_agent_gateway.transcript import TranscriptEvent, TranscriptStore

AgentId = Literal["codex", "claude"]


class SessionAgentConfig(BaseModel):
    session_id: str
    agent_id: AgentId
    model: str
    options: dict[str, Any]
    editable: bool
    updated_at: datetime | None = None


class SessionAgentConfigService:
    def __init__(self, transcript: TranscriptStore) -> None:
        self._transcript = transcript

    def effective_config(self, session_id: str | None = None) -> SessionAgentConfig:
        resolved_id = session_id or self._transcript.active_id() or self._transcript.start_new()
        events = self._transcript.load(resolved_id)
        latest = _latest_config_event(events)
        editable = _is_editable(events)
        if latest is None:
            return SessionAgentConfig(
                session_id=resolved_id,
                agent_id="codex",
                model="default",
                options={},
                editable=editable,
                updated_at=None,
            )
        return SessionAgentConfig(
            session_id=resolved_id,
            agent_id=str(latest.payload["agent_id"]),  # type: ignore[arg-type]
            model=str(latest.payload["model"]),
            options=dict(latest.payload.get("options") or {}),
            editable=editable,
            updated_at=latest.created_at,
        )

    def set_config(
        self,
        session_id: str | None,
        agent_id: AgentId,
        model: str,
        options: dict[str, Any],
    ) -> SessionAgentConfig:
        resolved_id = session_id or self._transcript.active_id() or self._transcript.start_new()
        events = self._transcript.load(resolved_id)
        if not _is_editable(events):
            raise ValueError("Session config is locked")
        self._transcript.activate(resolved_id)
        event = self._transcript.append(
            "session_config_set",
            {"agent_id": agent_id, "model": model, "options": dict(options)},
        )
        return SessionAgentConfig(
            session_id=resolved_id,
            agent_id=agent_id,
            model=model,
            options=dict(options),
            editable=True,
            updated_at=event.created_at,
        )


def _latest_config_event(events: list[TranscriptEvent]) -> TranscriptEvent | None:
    for event in reversed(events):
        if event.kind == "session_config_set":
            return event
    return None


def _is_editable(events: list[TranscriptEvent]) -> bool:
    return all(event.kind in {"session_config_set", "session_rename"} for event in events)
```

- [ ] **Step 5: Run session config tests**

Run:

```powershell
pytest tests/test_session_config.py tests/test_transcript.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/transcript.py src/personal_agent_gateway/session_config.py tests/test_session_config.py
git commit -m "feat: store session agent config"
```

---

## Task 5: Add Active Session Config API

**Files:**
- Modify: `src/personal_agent_gateway/api/agents.py`
- Test: `tests/test_api_agents.py`

- [ ] **Step 1: Add failing API tests**

Append to `tests/test_api_agents.py`:

```python
def test_active_session_config_defaults_and_can_be_updated_while_empty(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module

    monkeypatch.setattr(agents_module, "probe_cli", lambda _binary: agents_module.CliProbeResult(True, None))
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    default_response = client.get("/api/sessions/active/config")

    assert default_response.status_code == 200
    assert default_response.json()["config"]["agent_id"] == "codex"
    assert default_response.json()["config"]["editable"] is True

    update_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )

    assert update_response.status_code == 200
    assert update_response.json()["config"]["agent_id"] == "claude"
    assert update_response.json()["config"]["options"] == {"effort": "high"}


def test_active_session_config_rejects_invalid_and_locked_updates(tmp_path: Path, monkeypatch) -> None:
    from personal_agent_gateway import agents as agents_module
    from personal_agent_gateway.transcript import TranscriptStore

    monkeypatch.setattr(agents_module, "probe_cli", lambda _binary: agents_module.CliProbeResult(True, None))
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    store.start_new()
    store.append("user", {"content": "already started"})
    client = TestClient(create_app(config))
    client.cookies.set("agent_session", "test-session")

    invalid_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "missing", "options": {}},
    )
    locked_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {}},
    )

    assert invalid_response.status_code == 400
    assert locked_response.status_code == 409
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_api_agents.py -q
```

Expected: FAIL because active config endpoints do not exist.

- [ ] **Step 3: Implement request model and endpoints**

Modify `src/personal_agent_gateway/api/agents.py` so the file has two routers:

```python
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.api.jobs import session_dependency
from personal_agent_gateway.session_config import SessionAgentConfigService


class SessionConfigRequest(BaseModel):
    agent_id: str
    model: str
    options: dict[str, Any] = Field(default_factory=dict)


session_config_router = APIRouter(prefix="/api/sessions/active/config", tags=["agents"])


@session_config_router.get("")
def get_active_session_config(request: Request, _session: None = session_dependency) -> dict[str, dict[str, object]]:
    service = SessionAgentConfigService(request.app.state.transcript_store)
    return {"config": service.effective_config().model_dump(mode="json")}


@session_config_router.put("")
def set_active_session_config(
    payload: SessionConfigRequest,
    request: Request,
    _session: None = session_dependency,
) -> dict[str, dict[str, object]]:
    registry = AgentRegistry(request.app.state.app_config)
    try:
        validated = registry.validate_config(payload.agent_id, payload.model, payload.options)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    service = SessionAgentConfigService(request.app.state.transcript_store)
    try:
        config = service.set_config(
            request.app.state.transcript_store.active_id(),
            validated["agent_id"],
            validated["model"],
            validated["options"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"config": config.model_dump(mode="json")}
```

- [ ] **Step 4: Expose transcript store on app state**

Modify `create_app()` in `src/personal_agent_gateway/app.py` after `transcript = TranscriptStore(...)`:

```python
    app.state.transcript_store = transcript
```

If this line is before `app = FastAPI()`, move it after `app = FastAPI()`.

- [ ] **Step 5: Register both routers**

Modify `src/personal_agent_gateway/api/__init__.py` to export:

```python
from personal_agent_gateway.api.agents import router as agents_router
from personal_agent_gateway.api.agents import session_config_router
```

Modify `app.py` imports and include both:

```python
    app.include_router(agents_router)
    app.include_router(session_config_router)
```

- [ ] **Step 6: Run API tests**

Run:

```powershell
pytest tests/test_api_agents.py tests/test_app.py::test_reset_returns_empty_events_and_resets_history -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/api/agents.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_agents.py
git commit -m "feat: add active session agent config API"
```

---

## Task 6: Make Status and Session Summaries Reflect Session Agent Config

**Files:**
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `src/personal_agent_gateway/transcript.py`
- Test: `tests/test_app.py`, `tests/test_transcript.py`

- [ ] **Step 1: Add failing status and session summary tests**

Append to `tests/test_app.py`:

```python
def test_status_reports_active_session_agent_config(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = auth_client(config, FakeRuntime())

    response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )
    assert response.status_code == 200

    status = client.get("/api/status").json()

    assert status["provider"] == "claude"
    assert status["model"] == "sonnet"
    assert status["session_config"]["agent_id"] == "claude"
    assert status["session_config"]["editable"] is True
```

Append to `tests/test_transcript.py`:

```python
def test_list_sessions_includes_agent_config_metadata(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    session_id = store.start_new()
    store.append(
        "session_config_set",
        {"agent_id": "claude", "model": "sonnet", "options": {"effort": "high"}},
    )

    session = store.list_sessions()[0]

    assert session.id == session_id
    assert session.agent_id == "claude"
    assert session.model == "sonnet"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_app.py::test_status_reports_active_session_agent_config tests/test_transcript.py::test_list_sessions_includes_agent_config_metadata -q
```

Expected: FAIL because status and summary do not expose session config.

- [ ] **Step 3: Extend `SessionSummary`**

Modify `src/personal_agent_gateway/transcript.py`:

```python
class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    status: SessionStatus
    is_active: bool
    agent_id: str = "codex"
    model: str = "default"
```

In `_session_summary()`, compute config from events:

```python
        agent_id, model = _session_agent_model(events)
```

and pass:

```python
            agent_id=agent_id,
            model=model,
```

Add helper:

```python
def _session_agent_model(events: list[TranscriptEvent]) -> tuple[str, str]:
    for event in reversed(events):
        if event.kind == "session_config_set":
            agent_id = event.payload.get("agent_id")
            model = event.payload.get("model")
            return (
                agent_id if isinstance(agent_id, str) else "codex",
                model if isinstance(model, str) else "default",
            )
    return "codex", "default"
```

- [ ] **Step 4: Update status endpoint**

Modify `/api/status` in `src/personal_agent_gateway/app.py`:

```python
        session_config = SessionAgentConfigService(transcript).effective_config(session_id)
```

Add import:

```python
from personal_agent_gateway.session_config import SessionAgentConfigService
```

Change provider/model fields:

```python
            "provider": session_config.agent_id,
            "model": session_config.model,
            "session_config": session_config.model_dump(mode="json"),
```

- [ ] **Step 5: Update exact status test**

Modify `test_status_returns_safe_runtime_metadata` expected JSON in `tests/test_app.py` to include:

```python
        "session_config": {
            "session_id": response.json()["session_config"]["session_id"],
            "agent_id": "codex",
            "model": "default",
            "options": {},
            "editable": True,
            "updated_at": None,
        },
```

If using an exact dict becomes awkward because `session_id` is generated, split assertions:

```python
    payload = response.json()
    assert payload["provider"] == "codex"
    assert payload["model"] == "default"
    assert payload["session_config"]["agent_id"] == "codex"
    assert payload["session_config"]["editable"] is True
    assert "secret-token" not in response.text
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
pytest tests/test_app.py::test_status_returns_safe_runtime_metadata tests/test_app.py::test_status_reports_active_session_agent_config tests/test_transcript.py::test_list_sessions_includes_agent_config_metadata -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/transcript.py tests/test_app.py tests/test_transcript.py
git commit -m "feat: expose session agent metadata"
```

---

## Task 7: Add Claude CLI Adapter

**Files:**
- Modify: `src/personal_agent_gateway/model_client.py`
- Test: `tests/test_model_client.py`

- [ ] **Step 1: Add failing Claude adapter tests**

Append to `tests/test_model_client.py`:

```python
def write_fake_claude(tmp_path: Path, body: str) -> Path:
    if os.name == "nt":
        claude_bin = tmp_path / "claude.cmd"
        claude_bin.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
        return claude_bin

    claude_bin = tmp_path / "claude"
    claude_bin.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    claude_bin.chmod(0o755)
    return claude_bin


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
```

Add `ClaudeModelClient` to the import list at the top of the file.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_model_client.py::test_claude_client_runs_print_json_and_parses_result tests/test_model_client.py::test_claude_client_builds_expected_command -q
```

Expected: FAIL because `ClaudeModelClient` does not exist.

- [ ] **Step 3: Implement Claude client**

Modify `src/personal_agent_gateway/model_client.py` after `CodexModelClient`:

```python
class ClaudeModelClient:
    def __init__(
        self,
        binary: str,
        model: str,
        workspace_root: Path,
        effort: str = "medium",
        permission_mode: str = "manual",
        timeout_seconds: int = 600,
    ) -> None:
        self._binary = binary
        self._model = model
        self._workspace_root = workspace_root
        self._effort = effort
        self._permission_mode = permission_mode
        self._timeout_seconds = timeout_seconds

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(),
                _claude_prompt(messages),
                cwd=str(self._workspace_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Claude binary not found: {self._binary}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError("Claude execution timed out") from exc

        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        if process.returncode != 0:
            detail = _summarize_process_output(stderr_text, stdout_text)
            raise RuntimeError(f"Claude exited with status {process.returncode}: {detail}")
        return ModelResponse(content=_parse_claude_output(stdout_text), tool_calls=[])

    def _command(self) -> list[str]:
        command = [self._binary, "-p", "--output-format", "json"]
        if self._model and self._model != "default":
            command.extend(["--model", self._model])
        if self._effort:
            command.extend(["--effort", self._effort])
        if self._permission_mode:
            command.extend(["--permission-mode", self._permission_mode])
        return command
```

Add helpers:

```python
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
```

- [ ] **Step 4: Run model client tests**

Run:

```powershell
pytest tests/test_model_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/personal_agent_gateway/model_client.py tests/test_model_client.py
git commit -m "feat: add claude cli model client"
```

---

## Task 8: Select Runtime by Active Session Config

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_app.py`, `tests/test_api_agents.py`

- [ ] **Step 1: Add failing runtime selection test**

Append to `tests/test_app.py`:

```python
def test_chat_uses_active_session_config_runtime_factory(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    created_for: list[tuple[str, str]] = []

    class StubFactory:
        def __init__(self, app_config, transcript, job_service, event_bus) -> None:
            self.transcript = transcript

        def create_default_runtime(self) -> FakeRuntime:
            return FakeRuntime()

        def create_runtime_for_active_session(self) -> FakeRuntime:
            from personal_agent_gateway.session_config import SessionAgentConfigService

            session_config = SessionAgentConfigService(self.transcript).effective_config()
            created_for.append((session_config.agent_id, session_config.model))
            return FakeRuntime()

    monkeypatch.setattr(app_module, "AgentRuntimeFactory", StubFactory)
    client = auth_client(config, runtime=None)
    assert client.put(
        "/api/sessions/active/config",
        json={"agent_id": "claude", "model": "sonnet", "options": {}},
    ).status_code == 200

    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert created_for[-1] == ("claude", "sonnet")
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
pytest tests/test_app.py::test_chat_uses_active_session_config_runtime_factory -q
```

Expected: FAIL because app does not call `create_runtime_for_active_session()`.

- [ ] **Step 3: Extend runtime factory**

Modify `src/personal_agent_gateway/runtime_factory.py`:

```python
from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.model_client import ClaudeModelClient
from personal_agent_gateway.session_config import SessionAgentConfigService
```

Add:

```python
    def create_runtime_for_active_session(self) -> AgentRuntime:
        session_config = SessionAgentConfigService(self._transcript).effective_config()
        if session_config.agent_id == "codex":
            options = session_config.options

            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish({"type": "codex.event", **event})

            return self._runtime(
                CodexModelClient(
                    binary=self._config.codex_binary,
                    model=session_config.model,
                    workspace_root=self._config.workspace_root,
                    sandbox=str(options.get("sandbox") or self._config.codex_sandbox),
                    approval_policy=str(options.get("approval_policy") or self._config.codex_approval_policy),
                    timeout_seconds=self._config.codex_timeout_seconds,
                    on_event=publish_codex_event,
                )
            )
        if session_config.agent_id == "claude":
            options = session_config.options
            return self._runtime(
                ClaudeModelClient(
                    binary=self._config.claude_binary,
                    model=session_config.model,
                    workspace_root=self._config.workspace_root,
                    effort=str(options.get("effort") or "medium"),
                    permission_mode=str(options.get("permission_mode") or "manual"),
                    timeout_seconds=self._config.codex_timeout_seconds,
                )
            )
        raise ConfigError(f"Unsupported session agent: {session_config.agent_id}")
```

Do not use `AgentRegistry` here unless validation is needed at runtime. Validation already happens in the config API.

- [ ] **Step 4: Update chat/approval/deny runtime resolution**

Modify `src/personal_agent_gateway/app.py`:

1. Replace the mutable `shared_runtime` model for non-injected runtime with a helper:

```python
    injected_runtime = runtime

    def active_runtime() -> AgentRuntime:
        if injected_runtime is not None:
            return injected_runtime
        return runtime_factory.create_runtime_for_active_session()
```

2. In `/api/chat`, replace:

```python
            return _runtime_response(await shared_runtime.handle_user_message(request.message))
```

with:

```python
            return _runtime_response(await active_runtime().handle_user_message(request.message))
```

3. In approve/deny, use `active_runtime()` similarly.

4. In reset, remove recreation of `shared_runtime` when runtime is not injected. Keep reset returning `events` and `session_id`.

5. Preserve injected runtime tests: if `runtime` is passed to `create_app`, it must still be reused.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_app.py::test_chat_uses_active_session_config_runtime_factory tests/test_app.py::test_app_reuses_one_runtime_instance tests/test_app.py::test_reset_invalidates_real_runtime_pending_approval -q
```

Expected: PASS.

- [ ] **Step 6: Run backend API regression**

Run:

```powershell
pytest tests/test_app.py tests/test_api_agents.py tests/test_model_client.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/runtime_factory.py tests/test_app.py
git commit -m "feat: select runtime from session agent config"
```

---

## Task 9: Add Frontend API Client Methods

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`

- [ ] **Step 1: Add failing frontend API tests**

Append to `frontend/src/api/client.test.js`:

```javascript
  it("supports agent registry and active session config endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ agents: [{ id: "codex" }] }))
      .mockResolvedValueOnce(jsonResponse({ config: { agent_id: "codex", model: "default" } }))
      .mockResolvedValueOnce(jsonResponse({ config: { agent_id: "claude", model: "sonnet" } }));

    await expect(api.agents()).resolves.toEqual([{ id: "codex" }]);
    await expect(api.activeSessionConfig()).resolves.toEqual({ agent_id: "codex", model: "default" });
    await api.updateActiveSessionConfig({ agent_id: "claude", model: "sonnet", options: {} });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/agents");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/sessions/active/config");
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/sessions/active/config", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ agent_id: "claude", model: "sonnet", options: {} })
    }));
  });
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
cd frontend
npm test -- src/api/client.test.js
```

Expected: FAIL because methods do not exist.

- [ ] **Step 3: Implement API methods**

Modify `frontend/src/api/client.js`:

```javascript
  async agents() {
    return jsonList(await fetch("/api/agents"), "agents");
  },
  async activeSessionConfig() {
    const body = await jsonOrNull(await fetch("/api/sessions/active/config"));
    return body?.config || null;
  },
  async updateActiveSessionConfig(config) {
    const body = await jsonOrNull(await fetch("/api/sessions/active/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    }));
    return body?.config || null;
  },
```

Place these near the existing session methods.

- [ ] **Step 4: Run frontend API tests**

Run:

```powershell
cd frontend
npm test -- src/api/client.test.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/api/client.js frontend/src/api/client.test.js
git commit -m "feat: add frontend agent config API client"
```

---

## Task 10: Add Atomic Agent Picker UI

**Files:**
- Create: `frontend/src/components/molecules/AgentAvailabilityBadge/index.jsx`
- Create: `frontend/src/components/molecules/AgentOptionField/index.jsx`
- Create: `frontend/src/components/organisms/AgentPicker/index.jsx`
- Modify: `frontend/src/components/references/molecules.md`
- Modify: `frontend/src/components/references/organisms.md`
- Test: `frontend/src/components/organisms/AgentPicker/AgentPicker.test.jsx`

- [ ] **Step 1: Write failing component tests**

Create `frontend/src/components/organisms/AgentPicker/AgentPicker.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentPicker } from "./index.jsx";

const agents = [
  {
    id: "codex",
    label: "Codex CLI",
    available: true,
    models: ["default"],
    default_model: "default",
    defaults: { sandbox: "workspace-write", approval_policy: "never" },
    options_schema: [
      { name: "sandbox", kind: "select", choices: ["read-only", "workspace-write"] },
      { name: "approval_policy", kind: "select", choices: ["never", "on-request"] }
    ]
  },
  {
    id: "claude",
    label: "Claude Code",
    available: false,
    availability_error: "not found on PATH",
    models: ["sonnet"],
    default_model: "sonnet",
    defaults: { effort: "medium" },
    options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }]
  }
];

describe("AgentPicker", () => {
  it("shows editable available agents and disables unavailable agents", async () => {
    const onChange = vi.fn();
    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "codex", model: "default", options: {}, editable: true }}
        onChange={onChange}
      />
    );

    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("not found on PATH")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Model"), "default");

    expect(onChange).toHaveBeenCalled();
  });

  it("renders locked config as read-only summary", () => {
    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "codex", model: "default", options: { sandbox: "workspace-write" }, editable: false }}
        onChange={vi.fn()}
      />
    );

    expect(screen.getByText(/Locked/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cd frontend
npm test -- src/components/organisms/AgentPicker/AgentPicker.test.jsx
```

Expected: FAIL because components do not exist.

- [ ] **Step 3: Implement availability badge**

Create `frontend/src/components/molecules/AgentAvailabilityBadge/index.jsx`:

```jsx
export function AgentAvailabilityBadge({ available, reason }) {
  return (
    <span className={`status-badge ${available ? "ok" : "danger"}`} title={reason || ""}>
      {available ? "AVAILABLE" : "UNAVAILABLE"}
    </span>
  );
}
```

- [ ] **Step 4: Implement option field**

Create `frontend/src/components/molecules/AgentOptionField/index.jsx`:

```jsx
export function AgentOptionField({ option, value, disabled, onChange }) {
  const label = option.name.replaceAll("_", " ").toUpperCase();
  if (option.kind === "select") {
    return (
      <label className="field">
        <span>{label}</span>
        <select
          aria-label={label}
          value={value || ""}
          disabled={disabled}
          onChange={(event) => onChange(option.name, event.target.value)}
        >
          {(option.choices || []).map((choice) => (
            <option key={choice} value={choice}>{choice}</option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="field">
      <span>{label}</span>
      <input
        aria-label={label}
        value={value || ""}
        disabled={disabled}
        onChange={(event) => onChange(option.name, event.target.value)}
      />
    </label>
  );
}
```

- [ ] **Step 5: Implement AgentPicker organism**

Create `frontend/src/components/organisms/AgentPicker/index.jsx`:

```jsx
import { AgentAvailabilityBadge } from "../../molecules/AgentAvailabilityBadge/index.jsx";
import { AgentOptionField } from "../../molecules/AgentOptionField/index.jsx";

function selectedAgent(agents, config) {
  return agents.find((agent) => agent.id === config?.agent_id) || agents[0] || null;
}

export function AgentPicker({ agents = [], config, onChange, error = "" }) {
  const current = selectedAgent(agents, config);
  if (!current || !config) return null;

  if (!config.editable) {
    const optionText = Object.entries(config.options || {})
      .map(([key, value]) => `${key}: ${value}`)
      .join(" / ");
    return (
      <section className="agent-picker locked" aria-label="Agent configuration">
        <div className="section-title">Agent</div>
        <div className="meta">Locked - {current.label} / {config.model}{optionText ? ` / ${optionText}` : ""}</div>
      </section>
    );
  }

  function emit(next) {
    onChange({ ...config, ...next });
  }

  function changeAgent(agentId) {
    const agent = agents.find((candidate) => candidate.id === agentId);
    if (!agent || !agent.available) return;
    emit({
      agent_id: agent.id,
      model: agent.default_model,
      options: { ...(agent.defaults || {}) }
    });
  }

  function changeOption(name, value) {
    emit({ options: { ...(config.options || {}), [name]: value } });
  }

  return (
    <section className="agent-picker" aria-label="Agent configuration">
      <div className="section-title">Agent</div>
      <div className="agent-list">
        {agents.map((agent) => (
          <button
            key={agent.id}
            type="button"
            className={`agent-choice ${agent.id === current.id ? "active" : ""}`}
            disabled={!agent.available}
            onClick={() => changeAgent(agent.id)}
          >
            <span>{agent.label}</span>
            <AgentAvailabilityBadge available={agent.available} reason={agent.availability_error} />
            {!agent.available && <small>{agent.availability_error}</small>}
          </button>
        ))}
      </div>
      <label className="field">
        <span>MODEL</span>
        <select aria-label="Model" value={config.model} onChange={(event) => emit({ model: event.target.value })}>
          {(current.models || []).map((model) => (
            <option key={model} value={model}>{model}</option>
          ))}
        </select>
      </label>
      {(current.options_schema || []).map((option) => (
        <AgentOptionField
          key={option.name}
          option={option}
          value={(config.options || {})[option.name] || (current.defaults || {})[option.name]}
          onChange={changeOption}
        />
      ))}
      {error && <div className="error">{error}</div>}
    </section>
  );
}
```

- [ ] **Step 6: Add minimal CSS in existing stylesheet**

Inspect the current frontend CSS file before editing. If styles live in `frontend/src/main.css` or similar, add:

```css
.agent-picker {
  border: var(--bd);
  padding: 10px;
  display: grid;
  gap: 8px;
}
.agent-picker.locked {
  opacity: 0.78;
}
.agent-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}
.agent-choice {
  border: var(--bd);
  background: var(--c-white);
  padding: 8px;
  display: grid;
  gap: 4px;
  text-align: left;
}
.agent-choice.active {
  background: var(--c-accent);
}
.agent-choice:disabled {
  opacity: 0.55;
}
```

If the project only uses `src/personal_agent_gateway/static/styles.css`, do not edit it for Vite UI; first locate the active Vite stylesheet imported by `frontend/src/main.jsx`.

- [ ] **Step 7: Register component references**

Append concise entries to:

- `frontend/src/components/references/molecules.md`
- `frontend/src/components/references/organisms.md`

Use:

```markdown
- `AgentAvailabilityBadge`: compact availability status for a local agent descriptor.
- `AgentOptionField`: provider option input driven by registry option schema.
```

and:

```markdown
- `AgentPicker`: editable/read-only session agent configuration panel.
```

- [ ] **Step 8: Run component tests**

Run:

```powershell
cd frontend
npm test -- src/components/organisms/AgentPicker/AgentPicker.test.jsx
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add frontend/src/components/molecules/AgentAvailabilityBadge frontend/src/components/molecules/AgentOptionField frontend/src/components/organisms/AgentPicker frontend/src/components/references/molecules.md frontend/src/components/references/organisms.md
git commit -m "feat: add agent picker components"
```

---

## Task 11: Wire Agent Picker Into Gateway App

**Files:**
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/organisms/ChatView/index.jsx`
- Modify: `frontend/src/components/organisms/Statusbar/index.jsx`
- Test: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

- [ ] **Step 1: Add failing container tests**

Append to `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`:

```javascript
  it("loads editable agent config for an empty session and saves changes", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, session_config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [
        { id: "codex", label: "Codex CLI", available: true, models: ["default"], default_model: "default", defaults: {}, options_schema: [] },
        { id: "claude", label: "Claude Code", available: true, models: ["sonnet"], default_model: "sonnet", defaults: { effort: "medium" }, options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }] }
      ] },
      "GET /api/sessions/active/config": { config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "PUT /api/sessions/active/config": { config: { agent_id: "claude", model: "sonnet", options: { effort: "medium" }, editable: true } }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: /Claude Code/ }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/active/config",
      expect.objectContaining({ method: "PUT" })
    ));
  });

  it("shows locked session config read-only after history has messages", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, provider: "claude", model: "sonnet", session_config: { agent_id: "claude", model: "sonnet", options: { effort: "high" }, editable: false } },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "previous" } }] },
      "GET /api/agents": { agents: [{ id: "claude", label: "Claude Code", available: true, models: ["sonnet"], default_model: "sonnet", defaults: {}, options_schema: [] }] },
      "GET /api/sessions/active/config": { config: { agent_id: "claude", model: "sonnet", options: { effort: "high" }, editable: false } }
    });

    render(<GatewayApp />);

    expect(await screen.findByText(/Locked/)).toBeInTheDocument();
    expect(screen.getByText(/Claude Code/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cd frontend
npm test -- src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: FAIL because GatewayApp does not load or render agent config.

- [ ] **Step 3: Load agent catalog and config in `GatewayApp`**

Modify `GatewayApp` state:

```jsx
  const [agents, setAgents] = useState([]);
  const [sessionConfig, setSessionConfig] = useState(null);
  const [sessionConfigError, setSessionConfigError] = useState("");
```

In `loadApp`, fetch agents and config:

```jsx
    const [nextStatus, nextSessions, history, nextAgents, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.history(),
      api.agents(),
      api.activeSessionConfig()
    ]);
```

Then set:

```jsx
    setAgents(nextAgents);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
```

In `refreshStatusAndSessions`, refresh config too:

```jsx
    const [nextStatus, nextSessions, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.activeSessionConfig()
    ]);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
```

Add handler:

```jsx
  async function handleSessionConfigChange(nextConfig) {
    setSessionConfigError("");
    const saved = await api.updateActiveSessionConfig({
      agent_id: nextConfig.agent_id,
      model: nextConfig.model,
      options: nextConfig.options || {}
    });
    if (!saved) {
      setSessionConfigError("Config update failed");
      return;
    }
    setSessionConfig(saved);
    await refreshStatusAndSessions();
  }
```

- [ ] **Step 4: Pass picker props through ChatView**

Modify `GatewayApp` render:

```jsx
        <ChatView
          agents={agents}
          sessionConfig={sessionConfig}
          sessionConfigError={sessionConfigError}
          onSessionConfigChange={handleSessionConfigChange}
```

Modify `ChatView` signature to accept these props and render:

```jsx
import { AgentPicker } from "../AgentPicker/index.jsx";
```

Place near chat header or composer:

```jsx
      <AgentPicker
        agents={agents}
        config={sessionConfig}
        error={sessionConfigError}
        onChange={onSessionConfigChange}
      />
```

Use the existing layout structure; do not wrap the whole chat in a new decorative card.

- [ ] **Step 5: Update Statusbar model display**

Modify `frontend/src/components/organisms/Statusbar/index.jsx`:

```jsx
    ["MODEL", `${status?.provider || status?.session_config?.agent_id || "codex"}/${status?.model || status?.session_config?.model || "default"}`],
```

If the existing code already prefers `status.provider/status.model`, keep it and rely on backend changes from Task 6.

- [ ] **Step 6: Run container tests**

Run:

```powershell
cd frontend
npm test -- src/components/containers/GatewayApp/GatewayApp.test.jsx
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/organisms/Statusbar/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "feat: wire agent picker into chat shell"
```

---

## Task 12: Final Verification and Docs Touch-Up

**Files:**
- Modify: `README.md` only if the new behavior needs a short user-facing note.
- Modify: `.env.example` if `AGENT_CLAUDE_BIN` has not already been documented.

- [ ] **Step 1: Add `.env.example` entry if missing**

Add near `AGENT_CODEX_BIN`:

```bash
# Leave blank to use platform defaults:
# Windows: claude.cmd
# macOS: claude
AGENT_CLAUDE_BIN=
```

- [ ] **Step 2: Add README note if readable README encoding is intact**

If `README.md` is readable in the current checkout, add a short section:

```markdown
### Local agent selection

The gateway can show local CLI agents through `/api/agents`. The first supported agents are Codex CLI and Claude Code CLI. A session's agent/model/options can be changed only before the first message is sent; after that the session keeps a read-only config summary.
```

If `README.md` appears mojibake-corrupted in the terminal, skip README editing and report this as a separate docs cleanup item.

- [ ] **Step 3: Run backend tests**

Run:

```powershell
pytest -q
```

Expected: PASS.

- [ ] **Step 4: Run backend lint**

Run:

```powershell
ruff check .
```

Expected: PASS.

- [ ] **Step 5: Run frontend tests**

Run:

```powershell
cd frontend
npm test
```

Expected: PASS.

- [ ] **Step 6: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS and Vite writes `src/personal_agent_gateway/frontend_dist/`.

- [ ] **Step 7: Review git diff**

Run:

```powershell
git status --short
git diff --stat
```

Expected:

- source, tests, frontend, and optional docs/env changes only;
- no generated `frontend_dist` committed;
- no unrelated `.superpowers/` or unrelated `docs/specs/**` files staged.

- [ ] **Step 8: Commit final docs/config if changed**

If `.env.example` or README changed:

```powershell
git add .env.example README.md
git commit -m "docs: document local agent selection"
```

If no docs/config changed, skip this commit.

---

## Execution Notes

- Keep commits per task. Do not batch the entire plan into one commit.
- Do not push until the user asks or the execution mode explicitly includes push.
- Preserve existing tests that inject `runtime=FakeRuntime()` into `create_app`; injected runtimes must remain a supported test seam.
- Treat Claude `stream-json` as out of scope for this first implementation unless a spike proves the format is stable enough. Use `--output-format json` first.
- If frontend style file location is unclear, inspect `frontend/src/main.jsx` before editing styles.
- If `README.md` renders as mojibake in the terminal, do not make broad README edits in this plan.

## Plan Self-Review

- Spec coverage: Task 1 handles refactor-first runtime extraction. Tasks 2-3 handle agent registry and `/api/agents`. Tasks 4-6 handle session config storage, locking, and metadata. Tasks 7-8 handle Claude adapter and session-selected runtime invocation. Tasks 9-11 handle frontend API/client/UI integration. Task 12 handles verification and docs/config notes.
- Red-flag scan: the plan intentionally avoids empty markers, generic "add tests" steps, and unowned deferred work. Each task names files, tests, expected failures, implementation shape, verification command, and commit command.
- Type consistency: session config uses `agent_id`, `model`, `options`, and `editable` consistently across backend models, APIs, and frontend state. Agent descriptors use `id`, `label`, `available`, `models`, `default_model`, `options_schema`, and `defaults`.
- Scope check: direct Anthropic API, arbitrary CLI command editing, provider marketplace, and Ollama/LM Studio implementation are excluded from this first plan.
