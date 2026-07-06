# Local Backend Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local-first backend foundation for capabilities, jobs, schedules, artifacts, ffmpeg/capture runners, and improved single-user auth without prioritizing the React UI work yet.

**Architecture:** Keep FastAPI as the local HTTP boundary, keep Codex chat working, and add a small local execution core behind it. Use SQLite in WAL mode for durable metadata, JSONL transcripts for conversation continuity, local disk for artifacts, and a single-process async job worker so every local action is visible, recoverable, and approval-gated.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Pydantic, stdlib `sqlite3`, stdlib `asyncio`, local subprocess runners, existing pytest/ruff. Add `pyotp`/`qrcode` for OTP auth and `croniter` only when schedule next-run calculation is implemented.

---

## Backend Architecture Decisions

1. **SQLite is the local control database.**
   - Store jobs, job events, approvals, artifacts, schedules, and capability metadata snapshots.
   - Use `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.
   - Do not move full chat transcript into SQLite in this phase; keep existing JSONL transcript store.

2. **Files stay on disk; SQLite stores metadata.**
   - Artifact files live under `AGENT_ARTIFACT_ROOT`, defaulting to `<session_dir>/../artifacts`.
   - Log files live under `artifacts/logs`.
   - Temp files live under `AGENT_TEMP_DIR`, defaulting to `<session_dir>/../temp`.

3. **Jobs are the execution boundary.**
   - Chat, manual API calls, and schedules all create jobs.
   - A job can be `draft`, `waiting_approval`, `queued`, `running`, `succeeded`, `failed`, or `canceled`.
   - Risky work is never hidden inside assistant text.

4. **Use one in-process worker first.**
   - Local personal app does not need a distributed queue.
   - Default concurrency is `1` to avoid competing local tool runs.
   - Persist jobs before execution; on startup mark stale `running` jobs as `failed` with a recovery event.

5. **Capabilities are declared in code first.**
   - Start with a static registry for `shell.run`, `ffmpeg.inspect`, `ffmpeg.extract-audio`, `ffmpeg.thumbnail`, `capture.screen`.
   - Capability metadata drives API visibility and job validation.

6. **Schedules create jobs, not custom execution paths.**
   - Scheduler only decides when to create a job from a schedule template.
   - JobService still owns approval, runner dispatch, logs, and artifacts.

7. **Browser auth defaults to OTP-only.**
   - Normal browser login uses a Google Authenticator-compatible TOTP code.
   - Token is reserved for initial setup protection, recovery/admin access, and optional direct API bearer access.
   - Stronger token + OTP login can be added later as an optional security mode.

## Target File Structure

```text
src/personal_agent_gateway/
  app.py                         # FastAPI composition and app startup/shutdown hooks
  auth.py                        # session cookie dependency plus optional bearer token support
  auth_store.py                  # TOTP secret, recovery codes, auth lockout state
  config.py                      # local paths and tool binary config
  db.py                          # SQLite connection, schema, transaction helpers
  capabilities.py                # static capability registry and input validation
  jobs.py                        # job models, status transitions, job service
  job_worker.py                  # in-process async queue and runner dispatch
  artifacts.py                   # artifact path rules, metadata, content lookup
  schedules.py                   # schedule CRUD and due-job creation
  scheduler_loop.py              # local polling scheduler
  runners/
    __init__.py
    base.py                      # Runner protocol and RunResult
    shell.py                     # approved shell runner
    ffmpeg.py                    # ffprobe/ffmpeg runner
    capture.py                   # screen capture runner
  api/
    __init__.py
    auth.py                      # /api/auth/*
    capabilities.py              # /api/capabilities/*
    jobs.py                      # /api/jobs/*
    artifacts.py                 # /api/artifacts/*
    schedules.py                 # /api/schedules/*
tests/
  test_db.py
  test_capabilities.py
  test_jobs.py
  test_artifacts.py
  test_job_worker.py
  test_auth_store.py
  test_api_auth.py
  test_api_jobs.py
  test_schedules.py
```

## Task 1: Extend Local Configuration

**Files:**
- Modify: `src/personal_agent_gateway/config.py`
- Modify: `.env.example`
- Test: `tests/test_config_auth.py`

- [ ] **Step 1: Add failing config tests**

Add tests for default local paths and configured tool binaries.

```python
def test_load_config_derives_local_data_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "data" / "sessions"))

    config = load_config()

    assert config.app_db_path == tmp_path / "data" / "app.sqlite"
    assert config.artifact_root == tmp_path / "data" / "artifacts"
    assert config.temp_dir == tmp_path / "data" / "temp"
    assert config.ffmpeg_binary == "ffmpeg"
    assert config.ffprobe_binary == "ffprobe"


def test_load_config_accepts_local_tool_binary_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "data" / "sessions"))
    monkeypatch.setenv("AGENT_APP_DB_PATH", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("AGENT_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("AGENT_TEMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("AGENT_FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setenv("AGENT_FFPROBE_BIN", "/opt/homebrew/bin/ffprobe")

    config = load_config()

    assert config.app_db_path == tmp_path / "custom.sqlite"
    assert config.artifact_root == tmp_path / "store"
    assert config.temp_dir == tmp_path / "scratch"
    assert config.ffmpeg_binary == "/opt/homebrew/bin/ffmpeg"
    assert config.ffprobe_binary == "/opt/homebrew/bin/ffprobe"
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
python -m pytest tests/test_config_auth.py -v
```

Expected: the new tests fail because the config fields do not exist.

- [ ] **Step 3: Implement config fields**

Add fields to `AppConfig`:

```python
web_token: str | None = None
app_db_path: Path
artifact_root: Path
temp_dir: Path
ffmpeg_binary: str = "ffmpeg"
ffprobe_binary: str = "ffprobe"
capture_binary: str = "screencapture"
job_worker_concurrency: int = 1
auth_dir: Path
auth_setup_token: str | None = None
auth_require_token_and_otp: bool = False
```

In `from_env()`, derive defaults from `AGENT_SESSION_DIR`:

```python
session_path = Path(session_dir)
data_root = session_path.parent
app_db_path = env.get("AGENT_APP_DB_PATH") or str(data_root / "app.sqlite")
artifact_root = env.get("AGENT_ARTIFACT_ROOT") or str(data_root / "artifacts")
temp_dir = env.get("AGENT_TEMP_DIR") or str(data_root / "temp")
auth_dir = env.get("AGENT_AUTH_DIR") or str(data_root / "auth")
```

Resolve `app_db_path`, `artifact_root`, and `temp_dir` with the existing path validator.

- [ ] **Step 4: Update `.env.example`**

Add:

```bash
AGENT_APP_DB_PATH=./data/app.sqlite
AGENT_ARTIFACT_ROOT=./data/artifacts
AGENT_TEMP_DIR=./data/temp
AGENT_FFMPEG_BIN=ffmpeg
AGENT_FFPROBE_BIN=ffprobe
AGENT_CAPTURE_BIN=screencapture
AGENT_JOB_WORKER_CONCURRENCY=1
AGENT_AUTH_DIR=./data/auth
AGENT_AUTH_SETUP_TOKEN=
AGENT_AUTH_REQUIRE_TOKEN_AND_OTP=false
```

- [ ] **Step 5: Verify**

Run:

```bash
python -m pytest tests/test_config_auth.py -v
python -m ruff check src/personal_agent_gateway/config.py tests/test_config_auth.py
```

Expected: all config/auth tests pass and ruff passes.

## Task 1A: Add Single-User TOTP Auth Store

**Files:**
- Modify: `pyproject.toml`
- Create: `src/personal_agent_gateway/auth_store.py`
- Create: `tests/test_auth_store.py`

- [ ] **Step 1: Add OTP dependencies**

Modify `pyproject.toml` dependencies:

```toml
"pyotp>=2.9.0",
"qrcode>=8.0",
```

- [ ] **Step 2: Write TOTP store tests**

```python
from pathlib import Path

from personal_agent_gateway.auth_store import AuthStore


def test_totp_setup_generates_secret_and_otpauth_uri(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth")

    setup = store.start_totp_setup(account_name="local-owner")

    assert setup.secret
    assert setup.otpauth_uri.startswith("otpauth://totp/")


def test_totp_verify_enables_login(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth")
    setup = store.start_totp_setup(account_name="local-owner")
    code = store.current_code_for_test(setup.secret)

    result = store.verify_totp_setup(code)

    assert result.enabled is True
    assert len(result.recovery_codes) == 10
    assert store.verify_login_code(code) is True


def test_recovery_code_is_single_use(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth")
    setup = store.start_totp_setup(account_name="local-owner")
    result = store.verify_totp_setup(store.current_code_for_test(setup.secret))
    recovery_code = result.recovery_codes[0]

    assert store.use_recovery_code(recovery_code) is True
    assert store.use_recovery_code(recovery_code) is False
```

- [ ] **Step 3: Run and verify failure**

Run:

```bash
python -m pytest tests/test_auth_store.py -v
```

Expected: import fails.

- [ ] **Step 4: Implement `AuthStore`**

Persist local auth files under `AGENT_AUTH_DIR`:

```text
totp.json
recovery_codes.json
lockout.json
```

Implement:

```python
class AuthStore:
    def start_totp_setup(self, account_name: str) -> TotpSetup: ...
    def verify_totp_setup(self, code: str) -> TotpSetupResult: ...
    def verify_login_code(self, code: str) -> bool: ...
    def generate_recovery_codes(self) -> list[str]: ...
    def use_recovery_code(self, code: str) -> bool: ...
    def is_totp_enabled(self) -> bool: ...
```

Use `pyotp.TOTP(secret).verify(code, valid_window=1)`.

Hash recovery codes before storing them with `hashlib.sha256`.

Never return the TOTP secret after setup verification.

- [ ] **Step 5: Verify**

Run:

```bash
python -m pytest tests/test_auth_store.py -v
python -m ruff check src/personal_agent_gateway/auth_store.py tests/test_auth_store.py
```

Expected: tests and ruff pass.

## Task 2: Add SQLite Control Database

**Files:**
- Create: `src/personal_agent_gateway/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write database tests**

```python
from pathlib import Path

from personal_agent_gateway.db import Database


def test_database_initializes_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    tables = {
        row["name"]
        for row in db.fetchall(
            "select name from sqlite_master where type = 'table'",
        )
    }

    assert "jobs" in tables
    assert "job_events" in tables
    assert "approvals" in tables
    assert "artifacts" in tables
    assert "schedules" in tables


def test_database_uses_row_factory_and_foreign_keys(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    row = db.fetchone("pragma foreign_keys")

    assert row["foreign_keys"] == 1
```

- [ ] **Step 2: Run database tests and verify failure**

Run:

```bash
python -m pytest tests/test_db.py -v
```

Expected: import fails because `db.py` does not exist.

- [ ] **Step 3: Implement `Database`**

Implement a small SQLite wrapper:

```python
class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma journal_mode = wal")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
```

Add helper methods:

```python
execute(sql: str, parameters: Sequence[object] = ()) -> None
fetchone(sql: str, parameters: Sequence[object] = ()) -> sqlite3.Row | None
fetchall(sql: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]
```

Create schema tables:

```sql
create table if not exists jobs (...);
create table if not exists job_events (...);
create table if not exists approvals (...);
create table if not exists artifacts (...);
create table if not exists schedules (...);
```

Use ISO 8601 UTC text timestamps for all datetime fields.

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_db.py -v
python -m ruff check src/personal_agent_gateway/db.py tests/test_db.py
```

Expected: tests and ruff pass.

## Task 3: Add Capability Registry

**Files:**
- Create: `src/personal_agent_gateway/capabilities.py`
- Create: `tests/test_capabilities.py`

- [ ] **Step 1: Write capability tests**

```python
import pytest

from personal_agent_gateway.capabilities import CapabilityRegistry, CapabilityValidationError


def test_registry_lists_core_local_capabilities() -> None:
    registry = CapabilityRegistry.default()

    ids = {capability.id for capability in registry.list()}

    assert "shell.run" in ids
    assert "ffmpeg.inspect" in ids
    assert "ffmpeg.extract-audio" in ids
    assert "ffmpeg.thumbnail" in ids
    assert "capture.screen" in ids


def test_registry_rejects_unknown_capability() -> None:
    registry = CapabilityRegistry.default()

    with pytest.raises(CapabilityValidationError, match="Unknown capability"):
        registry.get("missing.capability")


def test_ffmpeg_extract_audio_requires_source_file() -> None:
    registry = CapabilityRegistry.default()

    with pytest.raises(CapabilityValidationError, match="source_file"):
        registry.validate_input("ffmpeg.extract-audio", {"format": "m4a"})
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python -m pytest tests/test_capabilities.py -v
```

Expected: import fails.

- [ ] **Step 3: Implement registry**

Use dataclasses:

```python
@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    description: str
    category: str
    risk_level: Literal["low", "medium", "high"]
    required_inputs: tuple[str, ...]
    output_types: tuple[str, ...]
    requires_approval: bool
    runner_type: str
    enabled: bool = True
```

Implement:

```python
class CapabilityRegistry:
    @classmethod
    def default(cls) -> Self: ...
    def list(self) -> list[Capability]: ...
    def get(self, capability_id: str) -> Capability: ...
    def validate_input(self, capability_id: str, payload: dict[str, object]) -> None: ...
```

Keep validation minimal: required keys must exist and must not be empty strings.

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_capabilities.py -v
python -m ruff check src/personal_agent_gateway/capabilities.py tests/test_capabilities.py
```

Expected: tests and ruff pass.

## Task 4: Add Job Service and Status Transitions

**Files:**
- Create: `src/personal_agent_gateway/jobs.py`
- Create: `tests/test_jobs.py`

- [ ] **Step 1: Write job service tests**

```python
from pathlib import Path

import pytest

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService, JobStatusError


def make_service(tmp_path: Path) -> JobService:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    return JobService(db, CapabilityRegistry.default())


def test_create_low_risk_job_queues_immediately(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect demo.mov",
        input_json={"source_file": "demo.mov"},
    )

    assert job.status == "queued"
    assert job.capability_id == "ffmpeg.inspect"


def test_create_medium_risk_job_waits_for_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    job = service.create_job(
        capability_id="ffmpeg.extract-audio",
        source="manual",
        title="Extract audio",
        input_json={"source_file": "demo.mov", "format": "m4a"},
    )

    assert job.status == "waiting_approval"
    assert job.approval_id is not None


def test_invalid_job_transition_is_rejected(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="Inspect demo.mov",
        input_json={"source_file": "demo.mov"},
    )

    with pytest.raises(JobStatusError, match="Cannot transition"):
        service.mark_succeeded(job.id)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python -m pytest tests/test_jobs.py -v
```

Expected: import fails.

- [ ] **Step 3: Implement job models and service**

Create:

```python
JobStatus = Literal[
    "draft",
    "waiting_approval",
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
]

@dataclass(frozen=True)
class Job:
    id: str
    capability_id: str
    source: Literal["chat", "manual", "schedule", "api"]
    title: str
    status: JobStatus
    input_json: dict[str, object]
    command_preview: str | None
    approval_id: str | None
```

Implement `JobService.create_job()`, `get_job()`, `list_jobs()`, `approve_job()`, `deny_job()`, `mark_running()`, `mark_succeeded()`, `mark_failed()`, and `append_event()`.

Status rules:

```text
waiting_approval -> queued after approve
queued -> running
running -> succeeded | failed | canceled
waiting_approval -> canceled after deny
```

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_jobs.py -v
python -m ruff check src/personal_agent_gateway/jobs.py tests/test_jobs.py
```

Expected: tests and ruff pass.

## Task 5: Add Artifact Store

**Files:**
- Create: `src/personal_agent_gateway/artifacts.py`
- Create: `tests/test_artifacts.py`

- [ ] **Step 1: Write artifact tests**

```python
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore, ArtifactPathError
from personal_agent_gateway.db import Database


def test_artifact_store_registers_file_under_root(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    artifact = store.register_bytes(
        artifact_type="text",
        title="run.log",
        relative_path="logs/run.log",
        content=b"hello",
        mime_type="text/plain",
    )

    assert artifact.relative_path == "logs/run.log"
    assert (tmp_path / "artifacts" / "logs" / "run.log").read_text() == "hello"


def test_artifact_store_rejects_path_escape(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    store = ArtifactStore(db, tmp_path / "artifacts")

    with pytest.raises(ArtifactPathError, match="outside artifact root"):
        store.register_bytes(
            artifact_type="text",
            title="bad",
            relative_path="../bad.txt",
            content=b"bad",
            mime_type="text/plain",
        )
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python -m pytest tests/test_artifacts.py -v
```

Expected: import fails.

- [ ] **Step 3: Implement artifact store**

Implement:

```python
class ArtifactStore:
    def __init__(self, db: Database, root: Path) -> None: ...
    def register_bytes(...) -> Artifact: ...
    def register_existing_file(...) -> Artifact: ...
    def get(artifact_id: str) -> Artifact: ...
    def list(...) -> list[Artifact]: ...
    def content_path(artifact_id: str) -> Path: ...
```

Use `Path.resolve()` and `relative_to()` to prevent escapes.

- [ ] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_artifacts.py -v
python -m ruff check src/personal_agent_gateway/artifacts.py tests/test_artifacts.py
```

Expected: tests and ruff pass.

## Task 6: Add Runner Interfaces and Local Runners

**Files:**
- Create: `src/personal_agent_gateway/runners/__init__.py`
- Create: `src/personal_agent_gateway/runners/base.py`
- Create: `src/personal_agent_gateway/runners/ffmpeg.py`
- Create: `src/personal_agent_gateway/runners/capture.py`
- Create: `src/personal_agent_gateway/runners/shell.py`
- Create: `tests/test_runners.py`

- [ ] **Step 1: Write runner tests with fake binaries**

Test command construction without requiring real ffmpeg:

```python
from pathlib import Path

from personal_agent_gateway.runners.ffmpeg import FfmpegRunner


def test_ffmpeg_extract_audio_builds_safe_command(tmp_path: Path) -> None:
    runner = FfmpegRunner(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        workspace_root=tmp_path,
        temp_dir=tmp_path / "temp",
    )

    command = runner.preview_command(
        "ffmpeg.extract-audio",
        {"source_file": "input.mov", "format": "m4a"},
    )

    assert command[:4] == ["ffmpeg", "-y", "-i", str(tmp_path / "input.mov")]
    assert command[-1].endswith(".m4a")
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python -m pytest tests/test_runners.py -v
```

Expected: import fails.

- [ ] **Step 3: Implement runner protocol**

In `base.py`:

```python
@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    artifact_paths: list[Path]


class Runner(Protocol):
    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]: ...
    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult: ...
```

- [ ] **Step 4: Implement ffmpeg runner**

Use `asyncio.create_subprocess_exec` with argument lists. Do not build ffmpeg commands as one shell string.

Initial capabilities:

```text
ffmpeg.inspect -> ffprobe metadata read
ffmpeg.extract-audio -> audio output in temp dir
ffmpeg.thumbnail -> image output in temp dir
```

- [ ] **Step 5: Implement capture runner**

Start with macOS `screencapture` command preview:

```text
screencapture -x <temp-output.png>
```

If the platform is not macOS, return a clear failed result: `capture.screen is only implemented for macOS screencapture`.

- [ ] **Step 6: Implement shell runner**

Keep shell runner approval-only. It may use `shell=True` only after JobService approval has moved the job to `queued`.

- [ ] **Step 7: Verify**

Run:

```bash
python -m pytest tests/test_runners.py -v
python -m ruff check src/personal_agent_gateway/runners tests/test_runners.py
```

Expected: tests and ruff pass.

## Task 7: Add In-Process Job Worker

**Files:**
- Create: `src/personal_agent_gateway/job_worker.py`
- Create: `tests/test_job_worker.py`

- [ ] **Step 1: Write worker tests with fake runner**

```python
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.runners.base import RunResult


class FakeRunner:
    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        return ["fake", capability_id]

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        output = Path(input_json["output_file"])
        output.write_text("done", encoding="utf-8")
        return RunResult(exit_code=0, stdout="ok", stderr="", artifact_paths=[output])


@pytest.mark.asyncio
async def test_worker_marks_job_succeeded_and_registers_artifact(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = JobService(db, CapabilityRegistry.default())
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    output_file = tmp_path / "temp" / "done.txt"
    job = service.create_job(
        capability_id="ffmpeg.inspect",
        source="manual",
        title="fake",
        input_json={"source_file": "demo.mov", "output_file": str(output_file)},
    )
    worker = JobWorker(service, artifacts, {"ffmpeg": FakeRunner()})

    await worker.run_one(job.id)

    updated = service.get_job(job.id)
    assert updated.status == "succeeded"
    assert len(artifacts.list()) == 1
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python -m pytest tests/test_job_worker.py -v
```

Expected: import fails.

- [ ] **Step 3: Implement job worker**

Implement:

```python
class JobWorker:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def enqueue(self, job_id: str) -> None: ...
    async def run_one(self, job_id: str) -> None: ...
```

Dispatch by `capability.runner_type`.

Register runner output files as artifacts. Store stdout/stderr as log artifacts when non-empty.

- [ ] **Step 4: Add startup recovery**

Add `JobService.recover_interrupted_jobs()`:

```text
running -> failed with "Gateway restarted while job was running"
queued -> remain queued
waiting_approval -> remain waiting_approval
```

- [ ] **Step 5: Verify**

Run:

```bash
python -m pytest tests/test_job_worker.py tests/test_jobs.py -v
python -m ruff check src/personal_agent_gateway/job_worker.py tests/test_job_worker.py
```

Expected: tests and ruff pass.

## Task 8: Add Backend API Routers

**Files:**
- Create: `src/personal_agent_gateway/api/__init__.py`
- Create: `src/personal_agent_gateway/api/auth.py`
- Create: `src/personal_agent_gateway/api/capabilities.py`
- Create: `src/personal_agent_gateway/api/jobs.py`
- Create: `src/personal_agent_gateway/api/artifacts.py`
- Modify: `src/personal_agent_gateway/app.py`
- Create: `tests/test_api_auth.py`
- Create: `tests/test_api_jobs.py`

- [ ] **Step 1: Write API auth tests**

```python
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app


def test_auth_status_reports_totp_required_when_not_configured(test_app_config) -> None:
    client = TestClient(create_app(test_app_config))

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["totp_configured"] is False


def test_auth_login_with_otp_sets_session_cookie(test_app_config, configured_auth_store) -> None:
    client = TestClient(create_app(test_app_config))
    code = configured_auth_store.current_login_code_for_test()

    response = client.post("/api/auth/login", json={"otp": code})

    assert response.status_code == 200
    assert response.cookies.get("agent_session") is not None


def test_auth_logout_clears_cookie(test_app_config) -> None:
    client = TestClient(create_app(test_app_config))

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.cookies.get("agent_session") == ""
```

- [ ] **Step 2: Write jobs API tests**

```python
def test_create_job_requires_auth(test_app_config) -> None:
    client = TestClient(create_app(test_app_config))

    response = client.post(
        "/api/jobs",
        json={
            "capability_id": "ffmpeg.inspect",
            "title": "Inspect",
            "input": {"source_file": "demo.mov"},
        },
    )

    assert response.status_code == 401
```

- [ ] **Step 3: Run and verify failure**

Run:

```bash
python -m pytest tests/test_api_auth.py tests/test_api_jobs.py -v
```

Expected: new endpoints and auth store fixtures do not exist.

- [ ] **Step 4: Split API routers**

Create routers with dependency injection from `app.state`:

```python
request.app.state.auth_store
request.app.state.job_service
request.app.state.artifact_store
request.app.state.capability_registry
request.app.state.job_worker
```

Keep existing routes working while adding new routers.

- [ ] **Step 5: Update app composition**

In `create_app()`:

```python
db = Database(app_config.app_db_path)
db.initialize()
registry = CapabilityRegistry.default()
job_service = JobService(db, registry)
artifact_store = ArtifactStore(db, app_config.artifact_root)
job_worker = JobWorker(...)
auth_store = AuthStore(app_config.auth_dir)
app.state.auth_store = auth_store
app.state.job_service = job_service
...
app.include_router(auth_router)
app.include_router(capabilities_router)
app.include_router(jobs_router)
app.include_router(artifacts_router)
```

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest tests/test_api_auth.py tests/test_api_jobs.py tests/test_app.py -v
python -m ruff check src/personal_agent_gateway/api src/personal_agent_gateway/app.py
```

Expected: new API tests pass and existing app tests still pass.

## Task 9: Add Schedules and Local Scheduler Loop

**Files:**
- Modify: `pyproject.toml`
- Create: `src/personal_agent_gateway/schedules.py`
- Create: `src/personal_agent_gateway/scheduler_loop.py`
- Create: `tests/test_schedules.py`

- [x] **Step 1: Add `croniter` dependency**

Modify `pyproject.toml`:

```toml
"croniter>=6.0.0"
```

- [x] **Step 2: Write schedule tests**

```python
from datetime import datetime, timezone
from pathlib import Path

from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.schedules import ScheduleService


def test_schedule_computes_next_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = ScheduleService(db, CapabilityRegistry.default())

    schedule = service.create_schedule(
        name="Daily inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="0 9 * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    )

    assert schedule.next_run_at.isoformat().startswith("2026-07-06T09:00:00")


def test_due_schedule_creates_job(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    registry = CapabilityRegistry.default()
    schedule_service = ScheduleService(db, registry)
    job_service = JobService(db, registry)
    schedule_service.create_schedule(
        name="Inspect",
        capability_id="ffmpeg.inspect",
        cron_expression="* * * * *",
        timezone_name="UTC",
        input_template_json={"source_file": "demo.mov"},
        now=datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    )

    jobs = schedule_service.create_due_jobs(
        job_service,
        now=datetime(2026, 7, 6, 0, 1, tzinfo=timezone.utc),
    )

    assert len(jobs) == 1
    assert jobs[0].source == "schedule"
```

- [x] **Step 3: Run and verify failure**

Run:

```bash
python -m pytest tests/test_schedules.py -v
```

Expected: imports fail.

- [x] **Step 4: Implement schedule service**

Implement CRUD and:

```python
create_due_jobs(job_service: JobService, now: datetime) -> list[Job]
pause(schedule_id: str) -> Schedule
resume(schedule_id: str) -> Schedule
run_now(schedule_id: str, job_service: JobService) -> Job
```

- [x] **Step 5: Implement scheduler loop**

Use an in-process loop:

```python
class SchedulerLoop:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Every 30 seconds:

```text
find due schedules
create jobs
enqueue queued jobs
update next_run_at
```

- [x] **Step 6: Verify**

Run:

```bash
python -m pytest tests/test_schedules.py -v
python -m ruff check src/personal_agent_gateway/schedules.py src/personal_agent_gateway/scheduler_loop.py tests/test_schedules.py
```

Expected: tests and ruff pass.

## Task 10: Connect Existing Chat to Jobs Gradually

**Files:**
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_runtime.py`
- Test: `tests/test_app.py`

- [x] **Step 1: Preserve existing chat behavior**

Run current tests first:

```bash
python -m pytest tests/test_runtime.py tests/test_app.py -v
```

Expected: pass before changes.

- [x] **Step 2: Add job event creation for shell approvals**

When runtime receives a `shell.run` tool call:

```text
create shell.run job
set status waiting_approval
append transcript tool_request
return pending approval response using job approval id
```

Do not change the external `/api/chat` response shape in this task.

- [x] **Step 3: Add test that shell approval creates a job**

Add a runtime test that sends a fake `shell.run` tool call and asserts:

```python
jobs = job_service.list_jobs()
assert len(jobs) == 1
assert jobs[0].capability_id == "shell.run"
assert jobs[0].status == "waiting_approval"
```

- [x] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_runtime.py tests/test_app.py tests/test_jobs.py -v
```

Expected: existing chat behavior still works, and shell approval is now visible as a job.

## Task 11: Final Backend Verification

**Files:**
- Modify only files touched by previous tasks if verification exposes issues.

- [x] **Step 1: Run full backend test suite**

Run:

```bash
python -m pytest
```

Expected: all tests pass.

- [x] **Step 2: Run ruff**

Run:

```bash
python -m ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Manual local smoke**

Use a local `.env` with:

```bash
AGENT_WEB_HOST=127.0.0.1
AGENT_WEB_PORT=8787
AGENT_WORKSPACE_ROOT=<absolute-workspace>
AGENT_SESSION_DIR=./data/sessions
AGENT_APP_DB_PATH=./data/app.sqlite
AGENT_ARTIFACT_ROOT=./data/artifacts
AGENT_TEMP_DIR=./data/temp
AGENT_AUTH_SETUP_TOKEN=<optional-setup-or-recovery-token>
```

Run:

```bash
scripts/run_local.sh
```

Verify:

```text
GET  /api/auth/status
GET  /api/capabilities
POST /api/jobs with ffmpeg.inspect
GET  /api/jobs
GET  /api/artifacts
```

Expected: auth works, capabilities are visible, a job can be created, and metadata persists after restart.

## Deliberately Deferred

- React/Vite frontend migration.
- Browser-page capture through Playwright.
- Batch ffmpeg folder workflows.
- Rich artifact thumbnails for every media type.
- Full transcript migration to SQLite.
- Multi-user auth, roles, teams, organizations.

## Self-Review

- Spec coverage: covers local config, SQLite, capabilities, jobs, artifact store, runners, schedules, auth API, and gradual chat integration.
- Backend focus: UI/UX implementation is intentionally deferred except API support needed by future UI.
- Local efficiency: avoids external queues and distributed services; uses WAL SQLite, local disk, and one in-process worker.
- Risk boundary: shell, capture, media writes, and schedules route through jobs and approvals.
