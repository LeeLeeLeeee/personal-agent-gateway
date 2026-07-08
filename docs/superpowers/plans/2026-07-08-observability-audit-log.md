# Observability and Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable local audit logs and structured local application logging for Full Access and Agent Teams operation.

**Architecture:** Store security/recovery-critical audit events in SQLite through `AuditLogService`; keep live UI on `EventBus`; keep debug/error logs in structured Python logging. Audit and local log metadata use one shared redaction helper so prompts, raw command output, secrets, and local absolute paths are not written into durable diagnostic logs.

**Tech Stack:** Python 3.11, FastAPI, stdlib `sqlite3`, stdlib `logging`, pytest, existing static UI.

---

## Scope

This plan implements:

- `audit_events` SQLite table.
- `AuditLogService`.
- Shared redaction helper.
- Audit API.
- Auth/chat/job/team audit hooks.
- Structured local logging setup.
- Minimal UI for recent audit events and observability status.

Out of scope:

- SIEM/export pipeline.
- encrypted SQLite.
- OS-level process monitoring.
- long-term retention automation beyond a configurable cleanup method.

## File Structure

- `src/personal_agent_gateway/db.py`
  - Add `audit_events`.
- `src/personal_agent_gateway/redaction.py`
  - Shared sanitization for audit/log metadata.
- `src/personal_agent_gateway/audit.py`
  - Audit event dataclass and service.
- `src/personal_agent_gateway/observability.py`
  - Structured local logging setup.
- `src/personal_agent_gateway/api/audit.py`
  - Audit read API and observability status/test endpoint.
- `src/personal_agent_gateway/api/__init__.py`
  - Export audit router.
- `src/personal_agent_gateway/config.py`
  - Add observability env vars.
- `src/personal_agent_gateway/app.py`
  - Initialize observability, attach audit service, include router, record request-level failures.
- Existing hook points:
  - `src/personal_agent_gateway/api/auth.py`
  - `src/personal_agent_gateway/runtime.py`
  - `src/personal_agent_gateway/jobs.py`
  - `src/personal_agent_gateway/team_runtime.py` after Agent Teams lands.
- Frontend (Vite React):
  - `frontend/src/api/client.js` (add `auditEvents`, `observabilityStatus`)
  - `frontend/src/api/client.test.js`
  - `frontend/src/components/organisms/AuditLog/index.jsx` (new)
  - `frontend/src/components/organisms/AuditLog/AuditLog.test.jsx` (new)
  - `frontend/src/components/organisms/Sidebar/index.jsx` (add `Audit` to `NAV`)
  - `frontend/src/components/containers/GatewayApp/index.jsx` (own audit state + screen route; link from Settings/Team/Job detail)
  - `frontend/src/components/references/organisms.md`
  - `src/personal_agent_gateway/static/**` is legacy and MUST NOT be edited; the live app is the Vite React build served from `frontend_dist/`.
- Tests:
  - `tests/test_audit.py`
  - `tests/test_api_audit.py`
  - `tests/test_observability.py`
  - focused updates to existing auth/runtime/job/team tests.

---

## Task 1: Audit Events Schema

**Files:**
- Modify: `src/personal_agent_gateway/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing schema test**

Add to `tests/test_db.py`:

```python
def test_database_initializes_audit_events_table(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()

    row = db.fetchone(
        "select name from sqlite_master where type = 'table' and name = ?",
        ("audit_events",),
    )

    assert row is not None
    assert row["name"] == "audit_events"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_db.py::test_database_initializes_audit_events_table -q
```

Expected: FAIL because `audit_events` does not exist.

- [ ] **Step 3: Add schema**

Append to `SCHEMA_SQL` in `src/personal_agent_gateway/db.py`:

```sql
create table if not exists audit_events (
    id text primary key,
    occurred_at text not null,
    event_type text not null,
    severity text not null,
    actor_type text not null,
    actor_id text,
    session_id text,
    team_run_id text,
    team_agent_id text,
    team_task_id text,
    job_id text,
    artifact_id text,
    request_id text,
    action text not null,
    target_type text,
    target_id text,
    status text not null,
    command_preview text,
    cwd text,
    exit_code integer,
    ip_hash text,
    user_agent_hash text,
    metadata_json text not null,
    redaction_version text not null
);

create index if not exists idx_audit_events_occurred_at on audit_events(occurred_at);
create index if not exists idx_audit_events_type on audit_events(event_type);
create index if not exists idx_audit_events_severity on audit_events(severity);
create index if not exists idx_audit_events_team_run on audit_events(team_run_id);
create index if not exists idx_audit_events_job on audit_events(job_id);
create index if not exists idx_audit_events_session on audit_events(session_id);
```

- [ ] **Step 4: Run schema test**

Run:

```bash
python -m pytest tests/test_db.py::test_database_initializes_audit_events_table -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/db.py tests/test_db.py
git commit -m "feat(audit): add audit events schema"
```

---

## Task 2: Redaction Helper

**Files:**
- Create: `src/personal_agent_gateway/redaction.py`
- Test: `tests/test_observability.py`

- [ ] **Step 1: Write redaction tests**

Create `tests/test_observability.py`:

```python
from personal_agent_gateway.redaction import redact_value, sanitize_metadata


def test_redacts_secret_like_keys_and_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")

    sanitized = sanitize_metadata(
        {
            "OPENAI_API_KEY": "sk-secret",
            "message": "token sk-secret should disappear",
            "nested": {"password": "abc123"},
        }
    )

    assert sanitized["OPENAI_API_KEY"] == "[redacted]"
    assert sanitized["message"] == "token [redacted] should disappear"
    assert sanitized["nested"]["password"] == "[redacted]"


def test_redacts_private_key_blocks():
    value = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----"

    assert redact_value("content", value) == "[redacted-private-key]"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_observability.py -q
```

Expected: FAIL because `redaction.py` does not exist.

- [ ] **Step 3: Implement redaction helper**

Create `src/personal_agent_gateway/redaction.py`:

```python
import os
import re
from typing import Any


REDACTION_VERSION = "2026-07-08"
SECRET_KEY_PARTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "OTP", "RECOVERY")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def sanitize_metadata(value: dict[str, object]) -> dict[str, object]:
    return {str(key): redact_value(str(key), child) for key, child in value.items()}


def redact_value(key: str, value: object) -> object:
    if _secret_key(key):
        return "[redacted]"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(child_key): redact_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_value("", item) for item in value]
    return value


def _secret_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in SECRET_KEY_PARTS)


def _redact_text(value: str) -> str:
    if PRIVATE_KEY_RE.search(value):
        return "[redacted-private-key]"
    redacted = value
    for name, secret in os.environ.items():
        if _secret_key(name) and secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
```

- [ ] **Step 4: Run redaction tests**

Run:

```bash
python -m pytest tests/test_observability.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/redaction.py tests/test_observability.py
git commit -m "feat(audit): add shared redaction helper"
```

---

## Task 3: AuditLogService

**Files:**
- Create: `src/personal_agent_gateway/audit.py`
- Test: `tests/test_audit.py`

- [ ] **Step 1: Write service tests**

Create `tests/test_audit.py`:

```python
from personal_agent_gateway.audit import AuditLogService
from personal_agent_gateway.db import Database


def make_service(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    return AuditLogService(db)


def test_records_and_lists_audit_events(tmp_path):
    audit = make_service(tmp_path)

    event = audit.record(
        event_type="runtime.chat.started",
        severity="info",
        actor_type="owner",
        action="chat",
        status="started",
        session_id="session-1",
        metadata={"prompt": "hello"},
    )

    events = audit.list_events()
    assert event.id
    assert events[0].event_type == "runtime.chat.started"
    assert events[0].session_id == "session-1"
    assert events[0].metadata == {"prompt": "hello"}


def test_filters_audit_events(tmp_path):
    audit = make_service(tmp_path)
    audit.record("team.run.started", "info", "agent", "team.run", "started", team_run_id="run-1")
    audit.record("job.failed", "error", "system", "job", "failed", job_id="job-1")

    assert [event.event_type for event in audit.list_events(severity="error")] == ["job.failed"]
    assert [event.event_type for event in audit.list_events(team_run_id="run-1")] == ["team.run.started"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_audit.py -q
```

Expected: FAIL because `audit.py` does not exist.

- [ ] **Step 3: Implement service**

Create `src/personal_agent_gateway/audit.py` with:

```python
@dataclass(frozen=True)
class AuditEvent:
    id: str
    occurred_at: str
    event_type: str
    severity: str
    actor_type: str
    actor_id: str | None
    session_id: str | None
    team_run_id: str | None
    team_agent_id: str | None
    team_task_id: str | None
    job_id: str | None
    artifact_id: str | None
    request_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    status: str
    command_preview: str | None
    cwd: str | None
    exit_code: int | None
    ip_hash: str | None
    user_agent_hash: str | None
    metadata: dict[str, object]
    redaction_version: str
```

Implement:

```python
class AuditLogService:
    def __init__(self, db: Database) -> None: ...
    def record(...all optional fields...) -> AuditEvent: ...
    def get_event(self, event_id: str) -> AuditEvent: ...
    def list_events(self, event_type: str | None = None, severity: str | None = None, session_id: str | None = None, team_run_id: str | None = None, team_agent_id: str | None = None, job_id: str | None = None, limit: int = 100) -> list[AuditEvent]: ...
```

Use `sanitize_metadata` before storing `metadata_json`.

- [ ] **Step 4: Run service tests**

Run:

```bash
python -m pytest tests/test_audit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/audit.py tests/test_audit.py
git commit -m "feat(audit): add audit log service"
```

---

## Task 4: Config and Structured Local Logging

**Files:**
- Modify: `src/personal_agent_gateway/config.py`
- Create: `src/personal_agent_gateway/observability.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_observability.py`

- [ ] **Step 1: Add config tests**

Append to `tests/test_observability.py`:

```python
from pathlib import Path

from personal_agent_gateway.config import AppConfig


def test_observability_config_defaults(tmp_path):
    config = AppConfig(
        workspace_root=tmp_path / "workspace",
        session_dir=tmp_path / "data" / "sessions",
    )

    assert config.audit_enabled is True
    assert config.log_level == "INFO"
    assert config.log_dir == tmp_path / "data" / "logs"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_observability.py::test_observability_config_defaults -q
```

Expected: FAIL because config fields do not exist.

- [ ] **Step 3: Add config fields**

In `AppConfig`:

```python
audit_enabled: bool = True
log_level: str = "INFO"
log_dir: Path | None = None
```

Derive `log_dir` in `derive_local_data_paths`:

```python
if self.log_dir is None:
    self.log_dir = (data_root / "logs").resolve()
```

Read env vars in `from_env` and `load_config`.

- [ ] **Step 4: Implement observability setup**

Create `src/personal_agent_gateway/observability.py`:

```python
import logging
from logging.handlers import RotatingFileHandler

from personal_agent_gateway.config import AppConfig


def setup_logging(config: AppConfig) -> None:
    assert config.log_dir is not None
    config.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("personal_agent_gateway")
    logger.setLevel(config.log_level.upper())
    handler = RotatingFileHandler(config.log_dir / "gateway.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
```

In `create_app`, call:

```python
setup_logging(app_config)
```

- [ ] **Step 5: Run config tests**

Run:

```bash
python -m pytest tests/test_observability.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/config.py src/personal_agent_gateway/observability.py src/personal_agent_gateway/app.py tests/test_observability.py
git commit -m "feat(observability): add structured local logging"
```

---

## Task 5: Audit API

**Files:**
- Create: `src/personal_agent_gateway/api/audit.py`
- Modify: `src/personal_agent_gateway/api/__init__.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_api_audit.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_audit.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-key",
    )


def authenticated_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")
    return client


def test_audit_events_require_auth(tmp_path):
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/audit/events")

    assert response.status_code == 401


def test_lists_audit_events(tmp_path):
    client = authenticated_client(tmp_path)
    client.app.state.audit_log.record(
        event_type="runtime.chat.started",
        severity="info",
        actor_type="owner",
        action="chat",
        status="started",
        session_id="session-1",
    )

    response = client.get("/api/audit/events")

    assert response.status_code == 200
    assert response.json()["events"][0]["event_type"] == "runtime.chat.started"


def test_observability_status_reports_local_state(tmp_path):
    client = authenticated_client(tmp_path)

    response = client.get("/api/observability/status")

    assert response.status_code == 200
    assert response.json()["audit_enabled"] is True
    assert response.json()["log_level"] == "INFO"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_api_audit.py -q
```

Expected: FAIL because routes are not registered.

- [ ] **Step 3: Implement router**

Create `src/personal_agent_gateway/api/audit.py` with:

```python
router = APIRouter(tags=["audit"])

@router.get("/api/audit/events")
def list_audit_events(...filters..., request: Request, _session: None = session_dependency): ...

@router.get("/api/audit/events/{event_id}")
def get_audit_event(...): ...

@router.get("/api/observability/status")
def observability_status(request: Request, _session: None = session_dependency):
    config = request.app.state.app_config
    return {
        "audit_enabled": config.audit_enabled,
        "log_dir": str(config.log_dir),
        "log_level": config.log_level,
    }
```

- [ ] **Step 4: Attach service and router**

In `_attach_local_services`:

```python
audit_log = AuditLogService(db)
app.state.audit_log = audit_log
```

Export and include `audit_router`.

- [ ] **Step 5: Run API tests**

Run:

```bash
python -m pytest tests/test_api_audit.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/api/audit.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_audit.py
git commit -m "feat(audit): add audit and observability API"
```

---

## Task 6: Audit Hooks for Auth, Chat, Jobs, and Codex Events

**Files:**
- Modify: `src/personal_agent_gateway/api/auth.py`
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `src/personal_agent_gateway/jobs.py`
- Modify: `src/personal_agent_gateway/model_client.py`
- Tests: existing focused tests plus new assertions.

- [ ] **Step 1: Add auth audit test**

In `tests/test_api_auth.py`, add:

```python
def test_successful_login_records_audit_event(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    setup_totp_for_test(client)

    response = client.post("/api/auth/login", json={"otp": current_otp_for_test(client)})

    assert response.status_code == 200
    events = client.app.state.audit_log.list_events(event_type="auth.login.succeeded")
    assert len(events) == 1
```

Use existing auth test helpers if present. If helper names differ, adapt to existing setup flow.

- [ ] **Step 2: Add runtime audit test**

In `tests/test_runtime.py`, add assertion around successful message:

```python
events = audit_log.list_events(event_type="runtime.chat.completed")
assert events[0].session_id == transcript.active_id()
```

Wire `audit_log` into `AgentRuntime` constructor as optional.

- [ ] **Step 3: Add job audit test**

In `tests/test_jobs.py`, after `create_job`, assert:

```python
events = audit_log.list_events(event_type="job.created")
assert events[0].job_id == job.id
```

Wire `audit_log` into `JobService` constructor as optional.

- [ ] **Step 4: Implement hooks**

Guidelines:

- Auth API records login success/failure/logout/recovery events.
- `AgentRuntime.handle_user_message` records started/completed/failed.
- `JobService.create_job`, `mark_running`, `mark_succeeded`, `mark_failed`, `deny_job` record job events.
- `CodexModelClient` command execution JSON events can be observed by runtime/model callback; record command started/completed/failed from parsed `item.type == "command_execution"` when context has an audit service.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_api_auth.py tests/test_runtime.py tests/test_jobs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/api/auth.py src/personal_agent_gateway/runtime.py src/personal_agent_gateway/jobs.py src/personal_agent_gateway/model_client.py tests/test_api_auth.py tests/test_runtime.py tests/test_jobs.py
git commit -m "feat(audit): record auth runtime job and codex events"
```

---

## Task 7: Agent Teams Audit Attribution

**Files:**
- Modify after Agent Teams exists:
  - `src/personal_agent_gateway/team_runtime.py`
  - `src/personal_agent_gateway/teams.py`
  - `tests/test_team_runtime.py`

- [ ] **Step 1: Add team attribution test**

Append to `tests/test_team_runtime.py`:

```python
def assert_team_audit_attribution(audit_log, team_run_id):
    events = audit_log.list_events(team_run_id=team_run_id)
    assert any(event.event_type == "team.run.started" for event in events)
    assert any(event.team_agent_id for event in events if event.actor_type == "agent")
```

Add the assertion to a plan-and-execute runtime test.

- [ ] **Step 2: Implement team audit records**

When TeamRuntime changes state:

```python
audit.record(
    event_type="team.task.completed",
    severity="info",
    actor_type="agent",
    actor_id=agent.id,
    action="team.task",
    status="succeeded",
    team_run_id=run.id,
    team_agent_id=agent.id,
    team_task_id=task.id,
    metadata={"persona_name": agent.name, "persona_role": agent.role},
)
```

- [ ] **Step 3: Run team tests**

Run:

```bash
python -m pytest tests/test_team_runtime.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py tests/test_team_runtime.py
git commit -m "feat(audit): attribute team actions to personas"
```

---

## Task 8: Audit Log UI (Vite React)

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`
- Create: `frontend/src/components/organisms/AuditLog/index.jsx`
- Create: `frontend/src/components/organisms/AuditLog/AuditLog.test.jsx`
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/references/organisms.md`

> React conventions (match the existing app): `api` is a single object literal using the `jsonOrNull`/`jsonList` helpers; organisms are pure render + callbacks; the `GatewayApp` container owns data loading, filter state, and screen routing; nav keys live in the exported `NAV`. Reuse the `StatusBadge` atom for severity and existing CSS tokens; never edit `static/**`.

- [ ] **Step 1: Add failing API client test**

Append to `frontend/src/api/client.test.js`, adapting to the file's existing `fetch` mock / `jsonResponse` helpers:

```javascript
  it("supports audit and observability endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ events: [{ id: "e1", event_type: "auth.login.succeeded", severity: "info" }] }))
      .mockResolvedValueOnce(jsonResponse({ audit_enabled: true, log_level: "INFO", log_dir: "./data/logs" }));

    await expect(api.auditEvents({ severity: "info" })).resolves.toEqual([
      { id: "e1", event_type: "auth.login.succeeded", severity: "info" }
    ]);
    await expect(api.observabilityStatus()).resolves.toEqual({ audit_enabled: true, log_level: "INFO", log_dir: "./data/logs" });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/audit/events?severity=info");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/observability/status");
  });
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
cd frontend && npm test -- src/api/client.test.js
```

Expected: FAIL because the methods do not exist.

- [ ] **Step 3: Add API methods**

Add to the `api` object in `frontend/src/api/client.js`, reusing the existing helpers:

```javascript
  async auditEvents(params = {}) {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, value]) => value !== "" && value != null)
    ).toString();
    return jsonList(await fetch(`/api/audit/events${query ? `?${query}` : ""}`), "events");
  },
  async observabilityStatus() {
    return jsonOrNull(await fetch("/api/observability/status"));
  },
```

- [ ] **Step 4: Add navigation entry**

In `frontend/src/components/organisms/Sidebar/index.jsx`, add to the exported `NAV` (after `settings`, or grouped near it):

```javascript
  { key: "audit", label: "Audit" },
```

- [ ] **Step 5: Write failing `AuditLog` component test**

Create `frontend/src/components/organisms/AuditLog/AuditLog.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuditLog } from "./index.jsx";

describe("AuditLog", () => {
  it("shows observability status, events, and emits filter changes", async () => {
    const onFilterChange = vi.fn();
    render(
      <AuditLog
        status={{ audit_enabled: true, log_level: "INFO", log_dir: "./data/logs" }}
        events={[{ id: "e1", event_type: "job.failed", severity: "error", status: "failed", occurred_at: "2026-07-08T00:00:00Z" }]}
        filters={{ severity: "", event_type: "" }}
        onFilterChange={onFilterChange}
      />
    );

    expect(screen.getByText(/INFO/)).toBeInTheDocument();
    expect(screen.getByText("job.failed")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/severity/i), "error");
    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ severity: "error" }));
  });
});
```

- [ ] **Step 6: Implement the `AuditLog` organism**

Create `frontend/src/components/organisms/AuditLog/index.jsx`. Pure render + callbacks:

- status row: audit enabled, log level, log path (from `status`);
- filter controls (`severity`, `event_type` selects) that call `onFilterChange(nextFilters)`;
- recent-events table using the `StatusBadge` atom for severity, columns time / type / status / target;
- an expandable row or detail drawer for a selected event (local UI state only; no `fetch` inside the organism).

- [ ] **Step 7: Wire into the `GatewayApp` container**

In `frontend/src/components/containers/GatewayApp/index.jsx`:

- Add state: `auditEvents`, `observability`, `auditFilters` (`{ severity: "", event_type: "" }`).
- On entering the `audit` screen, load `api.observabilityStatus()` and `api.auditEvents(auditFilters)`; reload events when filters change.
- Route `screen === "audit"` to `<AuditLog status={observability} events={auditEvents} filters={auditFilters} onFilterChange={...} />`, replacing the PLANNED placeholder.
- Cross-links: from the Settings screen show observability status; from Team Run / Job detail (once those screens exist) pass `team_run_id` / `job_id` as an audit filter to open the Audit screen scoped to that entity.

- [ ] **Step 8: Register component and run frontend suite**

Add `AuditLog` to `frontend/src/components/references/organisms.md`, then run:

```bash
cd frontend && npm test && npm run build
```

Expected: Vitest passes and the Vite build succeeds (refreshing `frontend_dist/`).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/components/organisms/AuditLog frontend/src/components/organisms/Sidebar/index.jsx frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/references/organisms.md
git commit -m "feat(ui): add audit log viewer"
```

---

## Task 9: Verification

- [ ] **Step 1: Run focused tests**

```bash
python -m pytest tests/test_audit.py tests/test_api_audit.py tests/test_observability.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run ruff**

```bash
python -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 4: Run frontend tests and build**

```bash
cd frontend && npm test && npm run build
```

Expected: Vitest passes and the Vite build succeeds (refreshing `frontend_dist/`, which is what the app serves at `/`).

- [ ] **Step 5: Manual local verification**

Run:

```bash
python -m uvicorn personal_agent_gateway.app:create_app --factory --host 127.0.0.1 --port 8787
```

Verify:

- OTP login writes audit event.
- Chat writes started/completed audit events.
- A Codex command execution writes command audit event.
- Job lifecycle writes audit events.
- Team Run writes team attribution audit events after Agent Teams is implemented.
- `/api/observability/status` reports audit/log settings.
- Audit UI shows recent events.

## Self-Review

- Spec coverage:
  - Local durable audit log: Tasks 1, 3, 5, 6, 7.
  - Shared redaction: Task 2.
  - Structured app logs: Task 4.
  - Audit API/UI: Tasks 5, 8.
  - Team/persona attribution: Task 7.
  - Verification: Task 9.
- Placeholder scan:
  - No TBD/TODO placeholders. Team hooks are explicitly dependent on Agent Teams landing first.
- Type consistency:
  - `AuditLogService`, `AuditEvent`, `audit_events`, `/api/audit/events`, `/api/observability/status`, `team_run_id`, `team_agent_id`, `team_task_id` are used consistently.
