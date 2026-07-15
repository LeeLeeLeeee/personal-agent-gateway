# Event Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이메일을 주기적으로 폴링해 새 메시지가 오면 지정한 Agent가 headless로 자동 처리하고, 그 실행을 `hook_run` 기록으로 남기며 SSE로 알림을 발행하는 hook 프레임워크(첫 어댑터=IMAP 이메일)를 백엔드+API로 구현한다.

**Architecture:** 기존 `SchedulerLoop`+`JobWorker` 쌍과 `AgentRunner` headless 실행 패턴을 그대로 복제한다. `HookLoop`(감지+enqueue)와 `HookRunner`(순차 실행)를 앱 lifespan에 배선하고, `SourceAdapter` 인터페이스 뒤에 이메일 지식을 격리한다. 신규 테이블 2개(`hooks`, `hook_runs`)를 추가하고 IMAP 앱 비밀번호는 DB가 아닌 파일 기반 `HookSecretStore`에 둔다.

**Tech Stack:** Python 3.11+, FastAPI, SQLite(sqlite3), 표준 라이브러리 `imaplib`/`email`, pytest, `fastapi.testclient.TestClient`.

## Global Constraints

- 언어/스타일: 기존 모듈 관례를 따른다. datetime은 UTC ISO 문자열(`datetime.now(timezone.utc).isoformat()`)로 저장한다.
- DB 접근: `Database.execute/fetchone/fetchall`만 사용한다. 매 호출이 새 connection이며 `pragma foreign_keys = on`이 켜져 있다.
- 신규 테이블은 `db.py`의 `SCHEMA_SQL`에 `create table if not exists`로 추가한다. 기존 테이블/컬럼은 변경하지 않는다.
- API 라우터는 모든 엔드포인트에서 `_session: SessionPrincipal = session_dependency`(alias `_session: None = session_dependency` 형태)로 보호한다.
- 비밀(IMAP 앱 비번)은 응답/로그/오류/SSE에 절대 노출하지 않는다. 오류 문자열은 `job_worker._redact_text` 패턴을 재사용한다.
- 시간 주입: 서비스의 시간 의존 메서드는 `now: datetime | None = None` 파라미터를 받아 테스트에서 주입 가능하게 한다(`ScheduleService`와 동일).
- 테스트 실행: `python -m pytest tests/<file> -v` (저장소 루트에서).

## 이 계획의 범위

**포함(v1):** DB 스키마, `HookSecretStore`, `HookRunService`, `SourceAdapter`+`ImapEmailAdapter`, `HookService`, `AgentRuntimeFactory.create_headless_runtime`, `HookRunner`, `HookLoop`, config, `api/hooks.py`(CRUD·enable/disable·run-now·runs 목록)와 앱 배선(lifespan start/stop·startup 복구·SSE 발행).

**스펙 §8 대비 의도적 연기(후속 계획):** `test-connection`, `rerun` 엔드포인트는 편의 기능이라 v1에서 제외한다. 프론트엔드 UI(hook 목록/생성 폼/hook_run 뷰)는 컴포넌트 설계가 별도로 필요하므로 후속 계획으로 분리한다. 단, UI가 소비할 SSE 이벤트(`hook.run.updated`)와 조회 API는 이 계획에 포함한다.

---

### Task 1: DB 스키마 (hooks, hook_runs)

**Files:**
- Modify: `src/personal_agent_gateway/db.py` (SCHEMA_SQL 문자열 끝에 테이블 2개 추가)
- Test: `tests/test_db_hooks_schema.py`

**Interfaces:**
- Produces: SQLite 테이블 `hooks`, `hook_runs` (스펙 §3의 컬럼). `hook_runs`에 `unique(hook_id, dedup_key)`, `foreign key (hook_id) references hooks(id) on delete cascade`.

- [ ] **Step 1: Write the failing test**

`tests/test_db_hooks_schema.py`:
```python
from pathlib import Path

from personal_agent_gateway.db import Database


def _table_columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.fetchall(f"pragma table_info({table})")}


def test_initialize_creates_hook_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    assert _table_columns(db, "hooks") == {
        "id", "name", "source_type", "connection_ref", "filter_json",
        "target_backend", "target_model", "target_options_json",
        "prompt_template", "poll_interval_seconds", "enabled",
        "cursor_json", "last_polled_at", "last_error",
        "created_at", "updated_at",
    }
    assert _table_columns(db, "hook_runs") == {
        "id", "hook_id", "dedup_key", "trigger_summary", "trigger_payload_json",
        "status", "result_text", "error_message",
        "created_at", "started_at", "finished_at",
    }


def test_hook_runs_dedup_key_is_unique_per_hook(tmp_path: Path) -> None:
    import sqlite3

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    db.execute(
        "insert into hooks (id, name, source_type, connection_ref, filter_json, "
        "target_backend, target_model, target_options_json, prompt_template, "
        "poll_interval_seconds, enabled, created_at, updated_at) "
        "values ('h1','n','email','c','{}','codex','default','{}','t',300,1,'t','t')"
    )
    db.execute(
        "insert into hook_runs (id, hook_id, dedup_key, trigger_summary, "
        "trigger_payload_json, status, created_at) "
        "values ('r1','h1','k','s','{}','queued','t')"
    )
    try:
        db.execute(
            "insert into hook_runs (id, hook_id, dedup_key, trigger_summary, "
            "trigger_payload_json, status, created_at) "
            "values ('r2','h1','k','s','{}','queued','t')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_hooks_schema.py -v`
Expected: FAIL (`no such table: hooks`).

- [ ] **Step 3: Add tables to SCHEMA_SQL**

`db.py`의 `SCHEMA_SQL` 문자열 안, 마지막 `rule_sets` 테이블 뒤(닫는 `"""` 앞)에 추가:
```sql
create table if not exists hooks (
    id text primary key,
    name text not null,
    source_type text not null,
    connection_ref text not null,
    filter_json text not null default '{}',
    target_backend text not null,
    target_model text not null,
    target_options_json text not null default '{}',
    prompt_template text not null,
    poll_interval_seconds integer not null default 300,
    enabled integer not null,
    cursor_json text,
    last_polled_at text,
    last_error text,
    created_at text not null,
    updated_at text not null
);

create table if not exists hook_runs (
    id text primary key,
    hook_id text not null,
    dedup_key text not null,
    trigger_summary text not null,
    trigger_payload_json text not null,
    status text not null,
    result_text text,
    error_message text,
    created_at text not null,
    started_at text,
    finished_at text,
    foreign key (hook_id) references hooks(id) on delete cascade,
    unique(hook_id, dedup_key)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_hooks_schema.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/db.py tests/test_db_hooks_schema.py
git commit -m "feat: hooks/hook_runs 테이블 스키마 추가"
```

---

### Task 2: HookSecretStore (파일 기반 비밀 저장)

**Files:**
- Create: `src/personal_agent_gateway/hook_secrets.py`
- Test: `tests/test_hook_secrets.py`

**Interfaces:**
- Produces:
  - `class HookSecretStore(root: Path)`
  - `save(self, connection_ref: str, secret: str) -> None`
  - `load(self, connection_ref: str) -> str | None`
  - `delete(self, connection_ref: str) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_hook_secrets.py`:
```python
from pathlib import Path

from personal_agent_gateway.hook_secrets import HookSecretStore


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "app-password")
    assert store.load("conn-1") == "app-password"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    assert store.load("nope") is None


def test_delete_removes_secret(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "app-password")
    store.delete("conn-1")
    assert store.load("conn-1") is None


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.delete("nope")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hook_secrets.py -v`
Expected: FAIL (ModuleNotFoundError: hook_secrets).

- [ ] **Step 3: Implement HookSecretStore**

`src/personal_agent_gateway/hook_secrets.py`:
```python
import json
from pathlib import Path


class HookSecretStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, connection_ref: str, secret: str) -> None:
        path = self._path(connection_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"secret": secret}), encoding="utf-8")

    def load(self, connection_ref: str) -> str | None:
        path = self._path(connection_ref)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        secret = payload.get("secret")
        return secret if isinstance(secret, str) else None

    def delete(self, connection_ref: str) -> None:
        self._path(connection_ref).unlink(missing_ok=True)

    def _path(self, connection_ref: str) -> Path:
        return self.root / f"{connection_ref}.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hook_secrets.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_secrets.py tests/test_hook_secrets.py
git commit -m "feat: hook 연결 비밀을 파일로 저장하는 HookSecretStore"
```

---

### Task 3: HookRunService (실행 기록 + 상태 전이)

**Files:**
- Create: `src/personal_agent_gateway/hook_runs.py`
- Test: `tests/test_hook_runs.py`

**Interfaces:**
- Consumes: `Database` (Task 1 스키마)
- Produces:
  - `@dataclass(frozen=True) class HookRun` with fields: `id, hook_id, dedup_key, trigger_summary, trigger_payload, status, result_text, error_message, created_at, started_at, finished_at`
  - `class HookRunService(db: Database)`
  - `create_run(hook_id, dedup_key, trigger_summary, trigger_payload, now=None) -> HookRun | None` (dedup 중복이면 `None`)
  - `get_run(run_id) -> HookRun`
  - `list_runs(hook_id) -> list[HookRun]`
  - `mark_running(run_id) -> HookRun`
  - `mark_succeeded(run_id, result_text) -> HookRun`
  - `mark_failed(run_id, message) -> HookRun`
  - `recover_interrupted_runs() -> None` (running → failed)

- [ ] **Step 1: Write the failing test**

`tests/test_hook_runs.py`:
```python
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRunService


def _service(tmp_path: Path) -> HookRunService:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    db.execute(
        "insert into hooks (id, name, source_type, connection_ref, filter_json, "
        "target_backend, target_model, target_options_json, prompt_template, "
        "poll_interval_seconds, enabled, created_at, updated_at) "
        "values ('h1','n','email','c','{}','codex','default','{}','t',300,1,'t','t')"
    )
    return HookRunService(db)


def test_create_run_returns_queued_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "email:1:2", "메일: hi — a@b", {"subject": "hi"})
    assert run is not None
    assert run.status == "queued"
    assert run.hook_id == "h1"
    assert run.dedup_key == "email:1:2"
    assert run.trigger_payload == {"subject": "hi"}


def test_create_run_dedup_returns_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.create_run("h1", "email:1:2", "s", {})
    second = service.create_run("h1", "email:1:2", "s", {})
    assert first is not None
    assert second is None
    assert len(service.list_runs("h1")) == 1


def test_status_transitions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    succeeded = service.mark_succeeded(run.id, "done")
    assert succeeded.status == "succeeded"
    assert succeeded.result_text == "done"
    assert succeeded.started_at is not None
    assert succeeded.finished_at is not None


def test_mark_failed_records_message(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    failed = service.mark_failed(run.id, "boom")
    assert failed.status == "failed"
    assert failed.error_message == "boom"


def test_recover_interrupted_marks_running_as_failed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    service.recover_interrupted_runs()
    assert service.get_run(run.id).status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hook_runs.py -v`
Expected: FAIL (ModuleNotFoundError: hook_runs).

- [ ] **Step 3: Implement HookRunService**

`src/personal_agent_gateway/hook_runs.py`:
```python
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class HookRun:
    id: str
    hook_id: str
    dedup_key: str
    trigger_summary: str
    trigger_payload: dict[str, object]
    status: str
    result_text: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class HookRunService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_run(
        self,
        hook_id: str,
        dedup_key: str,
        trigger_summary: str,
        trigger_payload: dict[str, object],
        now: datetime | None = None,
    ) -> HookRun | None:
        run_id = uuid4().hex
        created_at = _now(now)
        try:
            self._db.execute(
                """
                insert into hook_runs (
                    id, hook_id, dedup_key, trigger_summary, trigger_payload_json,
                    status, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    hook_id,
                    dedup_key,
                    trigger_summary,
                    json.dumps(trigger_payload, sort_keys=True),
                    "queued",
                    created_at,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> HookRun:
        row = self._db.fetchone("select * from hook_runs where id = ?", (run_id,))
        if row is None:
            raise KeyError(f"Hook run not found: {run_id}")
        return _run_from_row(row)

    def list_runs(self, hook_id: str) -> list[HookRun]:
        return [
            _run_from_row(row)
            for row in self._db.fetchall(
                "select * from hook_runs where hook_id = ? order by created_at desc",
                (hook_id,),
            )
        ]

    def mark_running(self, run_id: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'running', started_at = ? where id = ?",
            (_now(None), run_id),
        )
        return self.get_run(run_id)

    def mark_succeeded(self, run_id: str, result_text: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'succeeded', result_text = ?, finished_at = ? "
            "where id = ?",
            (result_text, _now(None), run_id),
        )
        return self.get_run(run_id)

    def mark_failed(self, run_id: str, message: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'failed', error_message = ?, finished_at = ? "
            "where id = ?",
            (message, _now(None), run_id),
        )
        return self.get_run(run_id)

    def recover_interrupted_runs(self) -> None:
        for row in self._db.fetchall(
            "select id from hook_runs where status = 'running'"
        ):
            self.mark_failed(row["id"], "Gateway restarted while hook run was running")


def _run_from_row(row: object) -> HookRun:
    return HookRun(
        id=row["id"],
        hook_id=row["hook_id"],
        dedup_key=row["dedup_key"],
        trigger_summary=row["trigger_summary"],
        trigger_payload=json.loads(row["trigger_payload_json"]),
        status=row["status"],
        result_text=row["result_text"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _now(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hook_runs.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_runs.py tests/test_hook_runs.py
git commit -m "feat: hook_run 기록/상태전이 HookRunService"
```

---

### Task 4: SourceAdapter 인터페이스 + ImapEmailAdapter

**Files:**
- Create: `src/personal_agent_gateway/sources/__init__.py` (빈 파일)
- Create: `src/personal_agent_gateway/sources/base.py`
- Create: `src/personal_agent_gateway/sources/email.py`
- Test: `tests/test_source_email.py`

**Interfaces:**
- Produces (`sources/base.py`):
  - `@dataclass(frozen=True) class HookEvent(dedup_key: str, summary: str, payload: dict[str, object])`
  - `@dataclass(frozen=True) class PollResult(events: list[HookEvent], cursor: dict[str, object])`
  - `class SourceAdapter(Protocol)` with `poll(connection: dict[str, object], secret: str, cursor: dict[str, object] | None, filter_config: dict[str, object]) -> PollResult`
- Produces (`sources/email.py`):
  - `class ImapEmailAdapter(client_factory: Callable[[str, int], ImapClientProtocol] | None = None)` implementing `poll(...)`
  - `class ImapClientProtocol(Protocol)` with `login(username, password)`, `select(folder) -> int` (returns uidvalidity), `max_uid() -> int`, `search_uids_after(uid: int) -> list[int]`, `fetch_rfc822(uid: int) -> bytes`, `logout()`
  - module functions (pure, testable): `normalize_email(message: email.message.Message) -> dict[str, object]`, `passes_filter(normalized: dict[str, object], filter_config: dict[str, object]) -> bool`

Note: 실 IMAP 접속을 감싸는 `ImapClient`(imaplib 기반) 구현은 서버 없이 단위 테스트하지 않는다. 어댑터 로직·정규화·필터는 fake client와 순수 함수로 검증한다.

- [ ] **Step 1: Write the failing test**

`tests/test_source_email.py`:
```python
import email

from personal_agent_gateway.sources.email import (
    ImapEmailAdapter,
    normalize_email,
    passes_filter,
)


class FakeImapClient:
    def __init__(self, uidvalidity: int, messages: dict[int, bytes]) -> None:
        self._uidvalidity = uidvalidity
        self._messages = messages
        self.logged_out = False

    def login(self, username: str, password: str) -> None:
        self._user = username

    def select(self, folder: str) -> int:
        return self._uidvalidity

    def max_uid(self) -> int:
        return max(self._messages) if self._messages else 0

    def search_uids_after(self, uid: int) -> list[int]:
        return sorted(u for u in self._messages if u > uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        return self._messages[uid]

    def logout(self) -> None:
        self.logged_out = True


def _raw(from_addr: str, subject: str, body: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 15 Jul 2026 09:00:00 +0000\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def _adapter(client: FakeImapClient) -> ImapEmailAdapter:
    return ImapEmailAdapter(client_factory=lambda host, port: client)


CONNECTION = {"host": "imap.test", "port": 993, "username": "me@test"}


def test_first_poll_sets_baseline_and_emits_nothing() -> None:
    client = FakeImapClient(uidvalidity=100, messages={5: _raw("a@b", "hi", "x")})
    result = _adapter(client).poll(CONNECTION, "pw", cursor=None, filter_config={})
    assert result.events == []
    assert result.cursor == {"uidvalidity": 100, "last_uid": 5}
    assert client.logged_out is True


def test_second_poll_emits_new_messages() -> None:
    client = FakeImapClient(
        uidvalidity=100,
        messages={5: _raw("a@b", "old", "x"), 6: _raw("boss@corp", "urgent", "help")},
    )
    result = _adapter(client).poll(
        CONNECTION, "pw", cursor={"uidvalidity": 100, "last_uid": 5}, filter_config={}
    )
    assert [e.dedup_key for e in result.events] == ["email:100:6"]
    assert result.events[0].payload["subject"] == "urgent"
    assert result.cursor == {"uidvalidity": 100, "last_uid": 6}


def test_uidvalidity_change_resets_baseline() -> None:
    client = FakeImapClient(uidvalidity=200, messages={5: _raw("a@b", "hi", "x")})
    result = _adapter(client).poll(
        CONNECTION, "pw", cursor={"uidvalidity": 100, "last_uid": 3}, filter_config={}
    )
    assert result.events == []
    assert result.cursor == {"uidvalidity": 200, "last_uid": 5}


def test_filter_excludes_nonmatching_but_advances_cursor() -> None:
    client = FakeImapClient(
        uidvalidity=100,
        messages={
            6: _raw("noise@spam", "promo", "x"),
            7: _raw("boss@corp", "urgent", "help"),
        },
    )
    result = _adapter(client).poll(
        CONNECTION,
        "pw",
        cursor={"uidvalidity": 100, "last_uid": 5},
        filter_config={"from_contains": "boss@corp"},
    )
    assert [e.dedup_key for e in result.events] == ["email:100:7"]
    assert result.cursor == {"uidvalidity": 100, "last_uid": 7}


def test_normalize_and_filter_pure_functions() -> None:
    message = email.message_from_bytes(_raw("Boss <boss@corp>", "Q3 Report", "please review"))
    normalized = normalize_email(message)
    assert normalized["from"] == "Boss <boss@corp>"
    assert normalized["subject"] == "Q3 Report"
    assert "please review" in normalized["body_text"]
    assert passes_filter(normalized, {"subject_contains": "Q3"}) is True
    assert passes_filter(normalized, {"subject_contains": "Q4"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_source_email.py -v`
Expected: FAIL (ModuleNotFoundError: sources.email).

- [ ] **Step 3: Implement base + email adapter**

`src/personal_agent_gateway/sources/__init__.py`: (빈 파일)

`src/personal_agent_gateway/sources/base.py`:
```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HookEvent:
    dedup_key: str
    summary: str
    payload: dict[str, object]


@dataclass(frozen=True)
class PollResult:
    events: list[HookEvent]
    cursor: dict[str, object]


class SourceAdapter(Protocol):
    def poll(
        self,
        connection: dict[str, object],
        secret: str,
        cursor: dict[str, object] | None,
        filter_config: dict[str, object],
    ) -> PollResult: ...
```

`src/personal_agent_gateway/sources/email.py`:
```python
import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message
from typing import Callable, Protocol

from personal_agent_gateway.sources.base import HookEvent, PollResult

_BODY_LIMIT = 8000


class ImapClientProtocol(Protocol):
    def login(self, username: str, password: str) -> None: ...
    def select(self, folder: str) -> int: ...
    def max_uid(self) -> int: ...
    def search_uids_after(self, uid: int) -> list[int]: ...
    def fetch_rfc822(self, uid: int) -> bytes: ...
    def logout(self) -> None: ...


class ImapEmailAdapter:
    def __init__(
        self,
        client_factory: Callable[[str, int], ImapClientProtocol] | None = None,
    ) -> None:
        self._client_factory = client_factory or _default_client_factory

    def poll(
        self,
        connection: dict[str, object],
        secret: str,
        cursor: dict[str, object] | None,
        filter_config: dict[str, object],
    ) -> PollResult:
        folder = str(filter_config.get("folder") or "INBOX")
        client = self._client_factory(str(connection["host"]), int(connection["port"]))
        try:
            client.login(str(connection["username"]), secret)
            uidvalidity = client.select(folder)
            last_uid = _cursor_last_uid(cursor, uidvalidity)
            if last_uid is None:
                return PollResult(
                    events=[],
                    cursor={"uidvalidity": uidvalidity, "last_uid": client.max_uid()},
                )
            events: list[HookEvent] = []
            highest = last_uid
            for uid in client.search_uids_after(last_uid):
                highest = max(highest, uid)
                message = email.message_from_bytes(client.fetch_rfc822(uid))
                normalized = normalize_email(message)
                if not passes_filter(normalized, filter_config):
                    continue
                events.append(
                    HookEvent(
                        dedup_key=f"email:{uidvalidity}:{uid}",
                        summary=f"메일: {normalized['subject']} — {normalized['from']}",
                        payload=normalized,
                    )
                )
            return PollResult(
                events=events,
                cursor={"uidvalidity": uidvalidity, "last_uid": highest},
            )
        finally:
            client.logout()


def normalize_email(message: Message) -> dict[str, object]:
    return {
        "from": _header(message, "From"),
        "subject": _header(message, "Subject"),
        "date": _header(message, "Date"),
        "body_text": _body_text(message)[:_BODY_LIMIT],
    }


def passes_filter(normalized: dict[str, object], filter_config: dict[str, object]) -> bool:
    from_contains = filter_config.get("from_contains")
    subject_contains = filter_config.get("subject_contains")
    if from_contains and str(from_contains).lower() not in str(normalized["from"]).lower():
        return False
    if subject_contains and str(subject_contains).lower() not in str(normalized["subject"]).lower():
        return False
    return True


def _cursor_last_uid(cursor: dict[str, object] | None, uidvalidity: int) -> int | None:
    if cursor is None:
        return None
    if int(cursor.get("uidvalidity", -1)) != uidvalidity:
        return None
    return int(cursor["last_uid"])


def _header(message: Message, name: str) -> str:
    raw = message.get(name, "")
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _body_text(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return _decode_part(part)
        return ""
    return _decode_part(message)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _default_client_factory(host: str, port: int) -> ImapClientProtocol:
    return _ImapClient(host, port)


class _ImapClient:
    """imaplib 기반 실 IMAP 클라이언트. (서버 없이 단위 테스트하지 않음)"""

    def __init__(self, host: str, port: int) -> None:
        self._conn = imaplib.IMAP4_SSL(host, port)
        self._folder = "INBOX"

    def login(self, username: str, password: str) -> None:
        self._conn.login(username, password)

    def select(self, folder: str) -> int:
        self._folder = folder
        self._conn.select(folder, readonly=True)
        status, data = self._conn.status(folder, "(UIDVALIDITY)")
        text = data[0].decode() if data and data[0] else ""
        digits = "".join(ch for ch in text.split("UIDVALIDITY")[-1] if ch.isdigit())
        return int(digits or 0)

    def max_uid(self) -> int:
        uids = self._all_uids()
        return max(uids) if uids else 0

    def search_uids_after(self, uid: int) -> list[int]:
        status, data = self._conn.uid("search", None, f"UID {uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []
        found = [int(part) for part in data[0].split()]
        # IMAP 'uid+1:*' 은 마지막 메시지를 항상 포함하므로 실제 초과분만 남긴다.
        return sorted(u for u in found if u > uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        status, data = self._conn.uid("fetch", str(uid), "(RFC822)")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return b""
        return data[0][1]

    def logout(self) -> None:
        try:
            self._conn.logout()
        except Exception:
            pass

    def _all_uids(self) -> list[int]:
        status, data = self._conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        return [int(part) for part in data[0].split()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_source_email.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/sources tests/test_source_email.py
git commit -m "feat: SourceAdapter 인터페이스와 IMAP 이메일 어댑터"
```

---

### Task 5: HookService (CRUD + 폴링 조율)

**Files:**
- Create: `src/personal_agent_gateway/hooks.py`
- Test: `tests/test_hooks_service.py`

**Interfaces:**
- Consumes: `Database`, `HookSecretStore` (Task 2), `HookRunService` (Task 3), `SourceAdapter`/`PollResult`/`HookEvent` (Task 4)
- Produces:
  - `@dataclass(frozen=True) class Hook` with fields: `id, name, source_type, connection_ref, filter, target_backend, target_model, target_options, prompt_template, poll_interval_seconds, enabled, cursor, last_polled_at, last_error, created_at, updated_at`
  - `class HookService(db, secret_store, adapters: dict[str, SourceAdapter])`
  - `create_hook(name, source_type, connection, secret, filter, target_backend, target_model, target_options, prompt_template, poll_interval_seconds, now=None) -> Hook`
  - `get_hook(hook_id) -> Hook`
  - `list_hooks() -> list[Hook]`
  - `set_enabled(hook_id, enabled, now=None) -> Hook`
  - `delete(hook_id) -> None` (비밀 파일도 삭제)
  - `due_hooks(now) -> list[Hook]`
  - `poll_hook(hook_id, run_service, now=None) -> list[HookRun]`
  - `poll_due(run_service, now=None) -> list[HookRun]`
  - module function `render_prompt(template: str, payload: dict[str, object]) -> str`

Note: `connection`은 비밀을 제외한 dict(`{host, port, username}`). 비밀은 `secret`으로 분리 전달되어 `HookSecretStore`에만 저장된다. `poll_hook`은 폴링 성공 시에만 `cursor`/`last_polled_at`를 갱신하고, 실패 시 `last_error`를 기록하고 cursor를 유지한다.

- [ ] **Step 1: Write the failing test**

`tests/test_hooks_service.py`:
```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.sources.base import HookEvent, PollResult


class StubAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result = PollResult(events=[], cursor={"uidvalidity": 1, "last_uid": 0})
        self.error: Exception | None = None

    def poll(self, connection, secret, cursor, filter_config):
        self.calls.append({"secret": secret, "cursor": cursor})
        if self.error is not None:
            raise self.error
        return self.result


def _service(tmp_path: Path, adapter: StubAdapter) -> tuple[HookService, HookRunService]:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    secret_store = HookSecretStore(tmp_path / "hooks")
    hook_service = HookService(db, secret_store, {"email": adapter})
    return hook_service, HookRunService(db)


def _create(hook_service: HookService) -> str:
    hook = hook_service.create_hook(
        name="Inbox watcher",
        source_type="email",
        connection={"host": "imap.test", "port": 993, "username": "me@test"},
        secret="app-pw",
        filter={"from_contains": "boss"},
        target_backend="codex",
        target_model="default",
        target_options={},
        prompt_template="요약: {{subject}}",
        poll_interval_seconds=300,
    )
    return hook.id


def test_create_hook_stores_secret_out_of_db(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    hook = hook_service.get_hook(hook_id)

    assert hook.enabled is True
    assert hook.target_backend == "codex"
    # 비밀은 DB에 없고 secret store에서만 로드된다.
    assert hook_service._secret_store.load(hook.connection_ref) == "app-pw"


def test_render_prompt_substitutes_placeholders() -> None:
    prompt = render_prompt(
        "From {{from}} / {{subject}}: {{body}}",
        {"from": "a@b", "subject": "hi", "body_text": "hello"},
    )
    assert prompt == "From a@b / hi: hello"


def test_poll_hook_creates_runs_and_advances_cursor(tmp_path: Path) -> None:
    adapter = StubAdapter()
    adapter.result = PollResult(
        events=[HookEvent("email:1:2", "메일: hi — boss", {"subject": "hi"})],
        cursor={"uidvalidity": 1, "last_uid": 2},
    )
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)

    runs = hook_service.poll_hook(hook_id, run_service)

    assert len(runs) == 1
    assert runs[0].status == "queued"
    hook = hook_service.get_hook(hook_id)
    assert hook.cursor == {"uidvalidity": 1, "last_uid": 2}
    assert hook.last_polled_at is not None
    assert hook.last_error is None
    assert adapter.calls[0]["secret"] == "app-pw"


def test_poll_hook_failure_records_error_without_cursor_advance(tmp_path: Path) -> None:
    adapter = StubAdapter()
    adapter.error = RuntimeError("auth failed")
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)

    runs = hook_service.poll_hook(hook_id, run_service)

    assert runs == []
    hook = hook_service.get_hook(hook_id)
    assert hook.cursor is None
    assert "auth failed" in hook.last_error


def test_poll_due_skips_recently_polled(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    hook_service.poll_hook(hook_id, run_service, now=now)
    # 60초 뒤: poll_interval(300초) 미만 → due 아님
    assert hook_service.due_hooks(now + timedelta(seconds=60)) == []
    # 301초 뒤: due
    assert [h.id for h in hook_service.due_hooks(now + timedelta(seconds=301))] == [hook_id]


def test_disabled_hook_is_not_due(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    hook_service.set_enabled(hook_id, False)
    assert hook_service.due_hooks(datetime.now(timezone.utc)) == []


def test_delete_removes_hook_and_secret(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    ref = hook_service.get_hook(hook_id).connection_ref

    hook_service.delete(hook_id)

    assert hook_service.list_hooks() == []
    assert hook_service._secret_store.load(ref) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hooks_service.py -v`
Expected: FAIL (ModuleNotFoundError: hooks).

- [ ] **Step 3: Implement HookService**

`src/personal_agent_gateway/hooks.py`:
```python
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRun, HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.sources.base import SourceAdapter


@dataclass(frozen=True)
class Hook:
    id: str
    name: str
    source_type: str
    connection_ref: str
    connection: dict[str, object]
    filter: dict[str, object]
    target_backend: str
    target_model: str
    target_options: dict[str, object]
    prompt_template: str
    poll_interval_seconds: int
    enabled: bool
    cursor: dict[str, object] | None
    last_polled_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str


class HookService:
    def __init__(
        self,
        db: Database,
        secret_store: HookSecretStore,
        adapters: dict[str, SourceAdapter],
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._adapters = adapters

    def create_hook(
        self,
        name: str,
        source_type: str,
        connection: dict[str, object],
        secret: str,
        filter: dict[str, object],
        target_backend: str,
        target_model: str,
        target_options: dict[str, object],
        prompt_template: str,
        poll_interval_seconds: int,
        now: datetime | None = None,
    ) -> Hook:
        if source_type not in self._adapters:
            raise ValueError(f"Unsupported source type: {source_type}")
        hook_id = uuid4().hex
        connection_ref = uuid4().hex
        self._secret_store.save(connection_ref, secret)
        stamp = _now(now)
        filter_payload = dict(filter)
        filter_payload["_connection"] = connection
        self._db.execute(
            """
            insert into hooks (
                id, name, source_type, connection_ref, filter_json,
                target_backend, target_model, target_options_json, prompt_template,
                poll_interval_seconds, enabled, cursor_json, last_polled_at, last_error,
                created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, null, null, null, ?, ?)
            """,
            (
                hook_id,
                name,
                source_type,
                connection_ref,
                json.dumps(filter_payload, sort_keys=True),
                target_backend,
                target_model,
                json.dumps(target_options, sort_keys=True),
                prompt_template,
                poll_interval_seconds,
                stamp,
                stamp,
            ),
        )
        return self.get_hook(hook_id)

    def get_hook(self, hook_id: str) -> Hook:
        row = self._db.fetchone("select * from hooks where id = ?", (hook_id,))
        if row is None:
            raise KeyError(f"Hook not found: {hook_id}")
        return _hook_from_row(row)

    def list_hooks(self) -> list[Hook]:
        return [
            _hook_from_row(row)
            for row in self._db.fetchall("select * from hooks order by created_at desc")
        ]

    def set_enabled(self, hook_id: str, enabled: bool, now: datetime | None = None) -> Hook:
        self.get_hook(hook_id)
        self._db.execute(
            "update hooks set enabled = ?, updated_at = ? where id = ?",
            (1 if enabled else 0, _now(now), hook_id),
        )
        return self.get_hook(hook_id)

    def delete(self, hook_id: str) -> None:
        hook = self.get_hook(hook_id)
        self._db.execute("delete from hooks where id = ?", (hook_id,))
        self._secret_store.delete(hook.connection_ref)

    def due_hooks(self, now: datetime) -> list[Hook]:
        due: list[Hook] = []
        for hook in self.list_hooks():
            if not hook.enabled:
                continue
            if hook.last_polled_at is None:
                due.append(hook)
                continue
            last = datetime.fromisoformat(hook.last_polled_at)
            if last + timedelta(seconds=hook.poll_interval_seconds) <= now:
                due.append(hook)
        return due

    def poll_due(self, run_service: HookRunService, now: datetime | None = None) -> list[HookRun]:
        moment = now or datetime.now(timezone.utc)
        created: list[HookRun] = []
        for hook in self.due_hooks(moment):
            created.extend(self.poll_hook(hook.id, run_service, now=moment))
        return created

    def poll_hook(
        self,
        hook_id: str,
        run_service: HookRunService,
        now: datetime | None = None,
    ) -> list[HookRun]:
        hook = self.get_hook(hook_id)
        adapter = self._adapters[hook.source_type]
        secret = self._secret_store.load(hook.connection_ref) or ""
        stamp = _now(now)
        try:
            result = adapter.poll(hook.connection, secret, hook.cursor, hook.filter)
        except Exception as exc:
            self._db.execute(
                "update hooks set last_error = ?, updated_at = ? where id = ?",
                (str(exc)[:2000] or type(exc).__name__, stamp, hook_id),
            )
            return []
        created: list[HookRun] = []
        for event in result.events:
            run = run_service.create_run(
                hook_id, event.dedup_key, event.summary, event.payload, now=now
            )
            if run is not None:
                created.append(run)
        self._db.execute(
            "update hooks set cursor_json = ?, last_polled_at = ?, last_error = null, "
            "updated_at = ? where id = ?",
            (json.dumps(result.cursor, sort_keys=True), stamp, stamp, hook_id),
        )
        return created


def render_prompt(template: str, payload: dict[str, object]) -> str:
    return (
        template.replace("{{from}}", str(payload.get("from", "")))
        .replace("{{subject}}", str(payload.get("subject", "")))
        .replace("{{body}}", str(payload.get("body_text", "")))
        .replace("{{date}}", str(payload.get("date", "")))
    )


def _hook_from_row(row: object) -> Hook:
    filter_payload = json.loads(row["filter_json"])
    connection = filter_payload.pop("_connection", {})
    return Hook(
        id=row["id"],
        name=row["name"],
        source_type=row["source_type"],
        connection_ref=row["connection_ref"],
        connection=connection,
        filter=filter_payload,
        target_backend=row["target_backend"],
        target_model=row["target_model"],
        target_options=json.loads(row["target_options_json"]),
        prompt_template=row["prompt_template"],
        poll_interval_seconds=row["poll_interval_seconds"],
        enabled=bool(row["enabled"]),
        cursor=json.loads(row["cursor_json"]) if row["cursor_json"] else None,
        last_polled_at=row["last_polled_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()
```

Note: `connection`(비밀 제외 접속 정보)은 별도 컬럼을 늘리지 않기 위해 `filter_json` 안 `_connection` 키에 함께 보관한다. 비밀만 별도 파일이라는 핵심 계약은 유지된다.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hooks_service.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hooks.py tests/test_hooks_service.py
git commit -m "feat: hook CRUD와 폴링 조율 HookService"
```

---

### Task 6: AgentRuntimeFactory.create_headless_runtime

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py` (메서드 1개 추가)
- Test: `tests/test_runtime_factory_headless.py`

**Interfaces:**
- Consumes: `AppConfig`, `TranscriptStore`
- Produces: `AgentRuntimeFactory.create_headless_runtime(backend: str, model: str, options: dict[str, object]) -> AgentRuntime`. `backend`이 `codex`/`claude`가 아니면 `ConfigError`.

- [ ] **Step 1: Write the failing test**

`tests/test_runtime_factory_headless.py`:
```python
from pathlib import Path

import pytest

from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
from personal_agent_gateway.transcript import TranscriptStore


def _factory(tmp_path: Path) -> AgentRuntimeFactory:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
    )
    return AgentRuntimeFactory(config, TranscriptStore(config.session_dir))


def test_headless_codex_runtime_uses_codex_client(tmp_path: Path) -> None:
    runtime = _factory(tmp_path).create_headless_runtime("codex", "gpt-x", {})
    assert isinstance(runtime._model, CodexModelClient)


def test_headless_claude_runtime_uses_claude_client(tmp_path: Path) -> None:
    runtime = _factory(tmp_path).create_headless_runtime("claude", "sonnet", {})
    assert isinstance(runtime._model, ClaudeModelClient)


def test_headless_unsupported_backend_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _factory(tmp_path).create_headless_runtime("bogus", "x", {})
```

Note: `AgentRuntime`은 생성자 인자 `model`을 `self._model`로 보관한다(`runtime.py`에서 확인됨).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_factory_headless.py -v`
Expected: FAIL (AttributeError: create_headless_runtime 없음).

- [ ] **Step 3: Implement create_headless_runtime**

`runtime_factory.py`의 `create_default_runtime` 메서드 바로 아래에 추가:
```python
    def create_headless_runtime(
        self, backend: str, model: str, options: dict[str, object]
    ) -> AgentRuntime:
        workspace_root = self._config.workspace_root
        if backend == "claude":
            client = ClaudeModelClient(
                binary=self._config.claude_binary,
                model=model,
                workspace_root=workspace_root,
                effort=str(options.get("effort") or "high"),
                permission_mode=str(
                    options.get("permission_mode") or self._config.claude_permission_mode
                ),
                agent=str(options["agent"]) if options.get("agent") else None,
                timeout_seconds=self._config.codex_timeout_seconds,
            )
            return self._runtime(client, session_id=None)
        if backend == "codex":
            client = CodexModelClient(
                binary=self._config.codex_binary,
                model=model,
                workspace_root=workspace_root,
                sandbox=str(options.get("sandbox") or self._config.codex_sandbox),
                approval_policy=str(
                    options.get("approval_policy") or self._config.codex_approval_policy
                ),
                profile=str(options["profile"]) if options.get("profile") else None,
                effort=str(options.get("effort") or "high"),
                timeout_seconds=self._config.codex_timeout_seconds,
                idle_timeout_seconds=self._config.codex_idle_timeout_seconds,
            )
            return self._runtime(client, session_id=None)
        raise ConfigError(f"Unsupported hook backend: {backend}")
```

`ConfigError`는 이미 이 파일에서 import되어 있다(`from personal_agent_gateway.config import AppConfig, ConfigError`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime_factory_headless.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/runtime_factory.py tests/test_runtime_factory_headless.py
git commit -m "feat: hook용 headless 런타임 생성 팩토리 메서드"
```

---

### Task 7: HookRunner (queued run 순차 실행)

**Files:**
- Create: `src/personal_agent_gateway/hook_runner.py`
- Test: `tests/test_hook_runner.py`

**Interfaces:**
- Consumes: `HookService` (`get_hook`, `render_prompt`), `HookRunService` (`get_run`/`mark_*`), `AgentRuntimeFactory` (`create_headless_runtime`), `EventBus` (`publish`)
- Produces:
  - `class HookRunner(hooks: HookService, hook_runs: HookRunService, runtime_factory: AgentRuntimeFactory, event_bus: EventBus)`
  - `async start() -> None`, `async stop() -> None`, `async enqueue(run_id: str) -> None`, `async run_one(run_id: str) -> None`
  - `@property alive: bool`

동작: `run_one`은 run→hook 로드, `render_prompt`로 프롬프트 생성, `create_headless_runtime`로 런타임 생성 후 `handle_user_message` 실행. `result.pending_approval`가 있으면 `mark_failed`(승인 backstop), 아니면 응답 텍스트로 `mark_succeeded`. 완료 후 `event_bus.publish({"type": "hook.run.updated", "hook_id", "run_id", "status"})`. `AgentRunner`의 응답 텍스트 추출 로직을 그대로 따른다.

- [ ] **Step 1: Write the failing test**

`tests/test_hook_runner.py`:
```python
from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService


@dataclass
class FakeRuntimeResult:
    messages: list
    pending_approval: object


class FakeRuntime:
    def __init__(self, result: FakeRuntimeResult) -> None:
        self._result = result
        self.received_prompt: str | None = None

    async def handle_user_message(self, message: str) -> FakeRuntimeResult:
        self.received_prompt = message
        return self._result


class FakeFactory:
    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime
        self.calls: list[tuple] = []

    def create_headless_runtime(self, backend, model, options) -> FakeRuntime:
        self.calls.append((backend, model, options))
        return self._runtime


def _setup(tmp_path: Path, runtime: FakeRuntime):
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    hooks = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": object()})
    hook = hooks.create_hook(
        name="w", source_type="email",
        connection={"host": "h", "port": 993, "username": "u"}, secret="pw",
        filter={}, target_backend="codex", target_model="default",
        target_options={}, prompt_template="요약: {{subject}}", poll_interval_seconds=300,
    )
    runs = HookRunService(db)
    run = runs.create_run(hook.id, "k", "s", {"subject": "hi"})
    bus = EventBus()
    runner = HookRunner(hooks, runs, FakeFactory(runtime), bus)
    return runner, runs, run, hook, bus


@pytest.mark.asyncio
async def test_run_one_success_records_result_and_publishes(tmp_path: Path) -> None:
    runtime = FakeRuntime(FakeRuntimeResult([{"content": "done"}], None))
    runner, runs, run, hook, bus = _setup(tmp_path, runtime)

    await runner.run_one(run.id)

    updated = runs.get_run(run.id)
    assert updated.status == "succeeded"
    assert updated.result_text == "done"
    assert runtime.received_prompt == "요약: hi"
    events = bus.recent()
    assert events[-1]["type"] == "hook.run.updated"
    assert events[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_run_one_pending_approval_marks_failed(tmp_path: Path) -> None:
    runtime = FakeRuntime(FakeRuntimeResult([{"content": "partial"}], {"id": "a1"}))
    runner, runs, run, hook, bus = _setup(tmp_path, runtime)

    await runner.run_one(run.id)

    assert runs.get_run(run.id).status == "failed"
```

Note: `test_app.py` 등 기존 async 테스트가 통과하는 것으로 보아 `pytest-asyncio`가 이미 설정돼 있다. 아니라면 이 태스크에서 `pytest.ini`/`pyproject`에 `asyncio_mode = auto`를 추가한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hook_runner.py -v`
Expected: FAIL (ModuleNotFoundError: hook_runner).

- [ ] **Step 3: Implement HookRunner**

`src/personal_agent_gateway/hook_runner.py`:
```python
import asyncio

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory


class HookRunner:
    def __init__(
        self,
        hooks: HookService,
        hook_runs: HookRunService,
        runtime_factory: AgentRuntimeFactory,
        event_bus: EventBus,
    ) -> None:
        self._hooks = hooks
        self._hook_runs = hook_runs
        self._runtime_factory = runtime_factory
        self._event_bus = event_bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def enqueue(self, run_id: str) -> None:
        await self._queue.put(run_id)

    async def run_one(self, run_id: str) -> None:
        run = self._hook_runs.get_run(run_id)
        hook = self._hooks.get_hook(run.hook_id)
        self._hook_runs.mark_running(run_id)
        prompt = render_prompt(hook.prompt_template, run.trigger_payload)
        runtime = self._runtime_factory.create_headless_runtime(
            hook.target_backend, hook.target_model, hook.target_options
        )
        result = await runtime.handle_user_message(prompt)
        if result.pending_approval is not None:
            self._hook_runs.mark_failed(
                run_id,
                "Agent turn paused awaiting tool approval; hook runs cannot approve tool calls.",
            )
            status = "failed"
        else:
            response_text = "\n".join(
                str(message["content"])
                for message in result.messages
                if message.get("content")
            )
            self._hook_runs.mark_succeeded(run_id, response_text)
            status = "succeeded"
        await self._publish(run.hook_id, run_id, status)

    async def _run_loop(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self.run_one(run_id)
            except asyncio.CancelledError:
                self._fail_if_active(run_id, "Gateway shutdown interrupted hook run")
                raise
            except Exception as exc:
                message = str(exc)[:2000] or type(exc).__name__
                self._last_error = message
                self._fail_if_active(run_id, message)
                await self._publish_safe(run_id)
            finally:
                self._queue.task_done()

    def _fail_if_active(self, run_id: str, message: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
            if run.status in {"queued", "running"}:
                self._hook_runs.mark_failed(run_id, message)
        except KeyError:
            return

    async def _publish(self, hook_id: str, run_id: str, status: str) -> None:
        await self._event_bus.publish(
            {"type": "hook.run.updated", "hook_id": hook_id, "run_id": run_id, "status": status}
        )

    async def _publish_safe(self, run_id: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
        except KeyError:
            return
        await self._publish(run.hook_id, run_id, run.status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hook_runner.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_runner.py tests/test_hook_runner.py
git commit -m "feat: queued hook_run을 순차 실행하는 HookRunner"
```

---

### Task 8: HookLoop (폴링 루프)

**Files:**
- Create: `src/personal_agent_gateway/hook_loop.py`
- Test: `tests/test_hook_loop.py`

**Interfaces:**
- Consumes: `HookService` (`poll_due`), `HookRunService`, `HookRunner` (`enqueue`)
- Produces:
  - `class HookLoop(hooks: HookService, hook_runs: HookRunService, runner: HookRunner, interval_seconds: float = 30.0)`
  - `async start()`, `async stop()`, `async tick()`, `@property alive`, `@property last_error`

동작: `tick()`은 `hooks.poll_due(hook_runs, now)`로 생성된 각 run을 `runner.enqueue`한다. `_run_loop`는 예외를 격리하고 `last_error`에 담은 뒤 계속 돈다(`SchedulerLoop`와 동일).

- [ ] **Step 1: Write the failing test**

`tests/test_hook_loop.py`:
```python
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_loop import HookLoop
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService
from personal_agent_gateway.sources.base import HookEvent, PollResult


class StubAdapter:
    def __init__(self, events: list[HookEvent]) -> None:
        self._events = events

    def poll(self, connection, secret, cursor, filter_config):
        return PollResult(events=self._events, cursor={"uidvalidity": 1, "last_uid": 9})


class RecordingRunner:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.enqueued.append(run_id)


@pytest.mark.asyncio
async def test_tick_creates_runs_and_enqueues(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    adapter = StubAdapter([HookEvent("email:1:2", "s", {"subject": "hi"})])
    hooks = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": adapter})
    hooks.create_hook(
        name="w", source_type="email",
        connection={"host": "h", "port": 993, "username": "u"}, secret="pw",
        filter={}, target_backend="codex", target_model="default",
        target_options={}, prompt_template="t", poll_interval_seconds=300,
    )
    runs = HookRunService(db)
    runner = RecordingRunner()
    loop = HookLoop(hooks, runs, runner)

    await loop.tick()

    assert len(runner.enqueued) == 1
    assert len(runs.list_runs(hooks.list_hooks()[0].id)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hook_loop.py -v`
Expected: FAIL (ModuleNotFoundError: hook_loop).

- [ ] **Step 3: Implement HookLoop**

`src/personal_agent_gateway/hook_loop.py`:
```python
import asyncio
from datetime import datetime, timezone

from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService


class HookLoop:
    def __init__(
        self,
        hooks: HookService,
        hook_runs: HookRunService,
        runner: HookRunner,
        interval_seconds: float = 30.0,
    ) -> None:
        self._hooks = hooks
        self._hook_runs = hook_runs
        self._runner = runner
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def tick(self) -> None:
        for run in self._hooks.poll_due(
            self._hook_runs, now=datetime.now(timezone.utc)
        ):
            await self._runner.enqueue(run.id)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)[:2000] or type(exc).__name__
            await asyncio.sleep(self._interval_seconds)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hook_loop.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_loop.py tests/test_hook_loop.py
git commit -m "feat: 주기적으로 hook을 폴링하는 HookLoop"
```

---

### Task 9: config 추가 (poll interval, hooks 디렉토리)

**Files:**
- Modify: `src/personal_agent_gateway/config.py`
- Test: `tests/test_config_hooks.py`

**Interfaces:**
- Produces: `AppConfig.hook_poll_interval_seconds: int = 30`, `AppConfig.hooks_dir: Path | None = None` (기본값 `session_dir.parent / "hooks"`). env: `AGENT_HOOK_POLL_INTERVAL_SECONDS`, `AGENT_HOOKS_DIR`.

- [ ] **Step 1: Write the failing test**

`tests/test_config_hooks.py`:
```python
from personal_agent_gateway.config import AppConfig


def test_hooks_dir_defaults_under_data_root() -> None:
    config = AppConfig(
        workspace_root="/tmp/ws",
        session_dir="/tmp/data/sessions",
    )
    assert config.hooks_dir.name == "hooks"
    assert config.hooks_dir.parent.name == "data"
    assert config.hook_poll_interval_seconds == 30


def test_hooks_config_from_env() -> None:
    config = AppConfig.from_env(
        {
            "AGENT_WORKSPACE_ROOT": "/tmp/ws",
            "AGENT_SESSION_DIR": "/tmp/data/sessions",
            "AGENT_HOOK_POLL_INTERVAL_SECONDS": "15",
        }
    )
    assert config.hook_poll_interval_seconds == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_hooks.py -v`
Expected: FAIL (AttributeError: hooks_dir / hook_poll_interval_seconds).

- [ ] **Step 3: Add config fields**

`config.py` 수정 (4곳):

(a) `AppConfig` 필드에 추가 (`auth_dir: Path | None = None` 아래):
```python
    hooks_dir: Path | None = None
```
그리고 `job_worker_concurrency: int = 1` 아래에:
```python
    hook_poll_interval_seconds: int = 30
```

(b) `resolve_path` validator의 필드 목록에 `"hooks_dir"` 추가:
```python
    @field_validator(
        "workspace_root",
        "session_dir",
        "app_db_path",
        "artifact_root",
        "temp_dir",
        "auth_dir",
        "hooks_dir",
        mode="after",
    )
```

(c) `derive_local_data_paths`에 추가 (`auth_dir` 파생 뒤, `return self` 앞):
```python
        if self.hooks_dir is None:
            self.hooks_dir = (data_root / "hooks").resolve()
```

(d) `from_env`에서 `auth_dir` 파생 줄 아래에:
```python
        hooks_dir = env.get("AGENT_HOOKS_DIR") or str(data_root / "hooks")
```
그리고 `cls(...)` 호출 인자에 추가 (`auth_dir=Path(auth_dir),` 아래):
```python
                hooks_dir=Path(hooks_dir),
```
`job_worker_concurrency=...` 아래에:
```python
                hook_poll_interval_seconds=int(
                    env.get("AGENT_HOOK_POLL_INTERVAL_SECONDS") or "30"
                ),
```

(e) `load_config`의 env dict에 추가:
```python
            "AGENT_HOOKS_DIR": os.getenv("AGENT_HOOKS_DIR"),
            "AGENT_HOOK_POLL_INTERVAL_SECONDS": os.getenv("AGENT_HOOK_POLL_INTERVAL_SECONDS"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_hooks.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/config.py tests/test_config_hooks.py
git commit -m "feat: hook 폴링 주기·hooks 디렉토리 설정 추가"
```

---

### Task 10: api/hooks.py + 앱 배선

**Files:**
- Create: `src/personal_agent_gateway/api/hooks.py`
- Modify: `src/personal_agent_gateway/api/__init__.py` (router export)
- Modify: `src/personal_agent_gateway/app.py` (`_attach_local_services` 서비스 생성/저장, router 등록, lifespan start/stop + startup 복구)
- Test: `tests/test_api_hooks.py`

**Interfaces:**
- Consumes: 모든 이전 태스크. `request.app.state.hook_service`, `request.app.state.hook_run_service`, `request.app.state.hook_runner`, `request.app.state.hook_loop`.
- Produces (엔드포인트):
  - `POST /api/hooks` → `{"hook": ...}` (요청에 `connection`+`secret` 포함, 응답에는 비밀 미포함)
  - `GET /api/hooks` → `{"hooks": [...]}`
  - `GET /api/hooks/{id}` → `{"hook": ...}`
  - `PATCH /api/hooks/{id}` (body `{"enabled": bool}`) → `{"hook": ...}`
  - `DELETE /api/hooks/{id}` → `{"deleted": true}`
  - `POST /api/hooks/{id}/run-now` → `{"created": <int>}` (즉시 poll, 생성된 run 수)
  - `GET /api/hooks/{id}/runs` → `{"runs": [...]}`
  - `_hook_payload(hook)`는 `connection_ref`·비밀을 제외한다.

- [ ] **Step 1: Write the failing test**

`tests/test_api_hooks.py`:
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
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
    return client


def _create_body() -> dict:
    return {
        "name": "Inbox watcher",
        "source_type": "email",
        "connection": {"host": "imap.test", "port": 993, "username": "me@test"},
        "secret": "app-password",
        "filter": {"from_contains": "boss"},
        "target_backend": "codex",
        "target_model": "default",
        "target_options": {},
        "prompt_template": "요약: {{subject}}",
        "poll_interval_seconds": 300,
    }


def test_list_hooks_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    assert client.get("/api/hooks").status_code == 401


def test_create_hook_hides_secret(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    response = client.post("/api/hooks", json=_create_body())
    assert response.status_code == 200
    hook = response.json()["hook"]
    assert hook["name"] == "Inbox watcher"
    assert hook["enabled"] is True
    assert hook["target_backend"] == "codex"
    serialized = response.text
    assert "app-password" not in serialized
    assert "connection_ref" not in hook


def test_get_and_list_and_delete(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]

    assert client.get(f"/api/hooks/{hook_id}").status_code == 200
    assert len(client.get("/api/hooks").json()["hooks"]) == 1

    assert client.delete(f"/api/hooks/{hook_id}").json() == {"deleted": True}
    assert client.get("/api/hooks").json()["hooks"] == []


def test_patch_toggles_enabled(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]

    response = client.patch(f"/api/hooks/{hook_id}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["hook"]["enabled"] is False


def test_get_missing_hook_returns_404(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.get("/api/hooks/nope").status_code == 404


def test_run_now_returns_created_count(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    hook_id = client.post("/api/hooks", json=_create_body()).json()["hook"]["id"]
    # 실제 IMAP 접속은 실패하므로(호스트 없음) poll_hook은 오류를 삼키고 0건 생성.
    response = client.post(f"/api/hooks/{hook_id}/run-now")
    assert response.status_code == 200
    assert response.json() == {"created": 0}
    runs = client.get(f"/api/hooks/{hook_id}/runs")
    assert runs.status_code == 200
    assert runs.json()["runs"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_hooks.py -v`
Expected: FAIL (create_app에 hooks 미배선 → 404/500, 또는 import 오류).

- [ ] **Step 3: Implement router**

`src/personal_agent_gateway/api/hooks.py`:
```python
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.hook_runs import HookRun
from personal_agent_gateway.hooks import Hook

router = APIRouter(prefix="/api/hooks", tags=["hooks"])


class CreateHookRequest(BaseModel):
    name: str
    source_type: str
    connection: dict[str, object] = Field(default_factory=dict)
    secret: str
    filter: dict[str, object] = Field(default_factory=dict)
    target_backend: str
    target_model: str
    target_options: dict[str, object] = Field(default_factory=dict)
    prompt_template: str
    poll_interval_seconds: int = 300


class UpdateHookRequest(BaseModel):
    enabled: bool


@router.get("")
def list_hooks(request: Request, _session: None = session_dependency) -> dict[str, object]:
    return {"hooks": [_hook_payload(h) for h in request.app.state.hook_service.list_hooks()]}


@router.post("")
def create_hook(
    request: Request, payload: CreateHookRequest, _session: None = session_dependency
) -> dict[str, object]:
    try:
        hook = request.app.state.hook_service.create_hook(
            name=payload.name,
            source_type=payload.source_type,
            connection=payload.connection,
            secret=payload.secret,
            filter=payload.filter,
            target_backend=payload.target_backend,
            target_model=payload.target_model,
            target_options=payload.target_options,
            prompt_template=payload.prompt_template,
            poll_interval_seconds=payload.poll_interval_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"hook": _hook_payload(hook)}


@router.get("/{hook_id}")
def get_hook(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, object]:
    return {"hook": _hook_payload(_require(request, hook_id))}


@router.patch("/{hook_id}")
def update_hook(
    request: Request, hook_id: str, payload: UpdateHookRequest, _session: None = session_dependency
) -> dict[str, object]:
    _require(request, hook_id)
    hook = request.app.state.hook_service.set_enabled(hook_id, payload.enabled)
    return {"hook": _hook_payload(hook)}


@router.delete("/{hook_id}")
def delete_hook(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, bool]:
    _require(request, hook_id)
    request.app.state.hook_service.delete(hook_id)
    return {"deleted": True}


@router.post("/{hook_id}/run-now")
async def run_hook_now(
    request: Request, hook_id: str, _session: None = session_dependency
) -> dict[str, int]:
    _require(request, hook_id)
    runs = request.app.state.hook_service.poll_hook(
        hook_id, request.app.state.hook_run_service
    )
    for run in runs:
        await request.app.state.hook_runner.enqueue(run.id)
    return {"created": len(runs)}


@router.get("/{hook_id}/runs")
def list_hook_runs(request: Request, hook_id: str, _session: None = session_dependency) -> dict[str, object]:
    _require(request, hook_id)
    runs = request.app.state.hook_run_service.list_runs(hook_id)
    return {"runs": [_run_payload(r) for r in runs]}


def _require(request: Request, hook_id: str) -> Hook:
    try:
        return request.app.state.hook_service.get_hook(hook_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Hook not found") from exc


def _hook_payload(hook: Hook) -> dict[str, object]:
    return {
        "id": hook.id,
        "name": hook.name,
        "source_type": hook.source_type,
        "connection": hook.connection,
        "filter": hook.filter,
        "target_backend": hook.target_backend,
        "target_model": hook.target_model,
        "target_options": hook.target_options,
        "prompt_template": hook.prompt_template,
        "poll_interval_seconds": hook.poll_interval_seconds,
        "enabled": hook.enabled,
        "last_polled_at": hook.last_polled_at,
        "last_error": hook.last_error,
        "created_at": hook.created_at,
        "updated_at": hook.updated_at,
    }


def _run_payload(run: HookRun) -> dict[str, object]:
    return {
        "id": run.id,
        "hook_id": run.hook_id,
        "trigger_summary": run.trigger_summary,
        "status": run.status,
        "result_text": run.result_text,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
```

- [ ] **Step 4: Register router in api/__init__.py**

`api/__init__.py`에 추가:
```python
from personal_agent_gateway.api.hooks import router as hooks_router
```
그리고 `__all__` 리스트에 `"hooks_router",` 추가.

- [ ] **Step 5: Wire services + lifespan in app.py**

(a) import 추가 (다른 import과 함께):
```python
from personal_agent_gateway.api import hooks_router
from personal_agent_gateway.hook_loop import HookLoop
from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService
from personal_agent_gateway.sources.email import ImapEmailAdapter
```

(b) `_attach_local_services` 안, `scheduler_loop = SchedulerLoop(...)` 아래에 추가:
```python
    assert config.hooks_dir is not None
    hook_secret_store = HookSecretStore(config.hooks_dir)
    hook_service = HookService(
        db, hook_secret_store, {"email": ImapEmailAdapter()}
    )
    hook_run_service = HookRunService(db)
    hook_runner = HookRunner(hook_service, hook_run_service, runtime_factory, event_bus)
    hook_loop = HookLoop(
        hook_service,
        hook_run_service,
        hook_runner,
        interval_seconds=config.hook_poll_interval_seconds,
    )
```
그리고 `app.state` 저장 블록에 추가:
```python
    app.state.hook_service = hook_service
    app.state.hook_run_service = hook_run_service
    app.state.hook_runner = hook_runner
    app.state.hook_loop = hook_loop
```

(c) router 등록: `app.include_router(schedules_router)` 아래에:
```python
    app.include_router(hooks_router)
```

(d) lifespan 배선: `await application.state.scheduler_loop.start()` 위에 복구 추가, 그 아래에 hook start 추가:
```python
        application.state.hook_run_service.recover_interrupted_runs()
        await application.state.hook_runner.start()
        await application.state.hook_loop.start()
```
그리고 `finally:` 블록에서 `await application.state.scheduler_loop.stop()` 아래에:
```python
            await application.state.hook_loop.stop()
            await application.state.hook_runner.stop()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_api_hooks.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Full suite regression**

Run: `python -m pytest -q`
Expected: 기존 테스트 전부 PASS + 신규 테스트 PASS. (실패 시 배선 회귀를 먼저 해결한다.)

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/api/hooks.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_hooks.py
git commit -m "feat: hooks API와 앱 lifespan 배선(HookLoop/HookRunner)"
```

---

## 후속 계획 (이 계획 밖)

1. **프론트엔드 UI**: hook 목록/생성 폼(연결·필터·타깃 Agent·프롬프트 템플릿)·hook_run 타임라인. `hook.run.updated` SSE 이벤트를 기존 `/api/events` 스트림에서 구독해 알림 표시. React 컴포넌트 설계가 필요하므로 별도 brainstorming → plan.
2. **편의 엔드포인트**: `POST /api/hooks/{id}/test-connection`(IMAP 로그인만 검증), `POST .../runs/{run_id}/rerun`(저장된 payload 재실행).
3. **어댑터 확장**: Slack/RSS/파일 감시 등 새 `SourceAdapter` 구현체.

## Self-Review 결과

- **스펙 커버리지**: §2 구성요소→Task 2·3·4·5·6·7·8·10, §3 데이터 모델→Task 1, §4 흐름→Task 7·8, §5 이메일 어댑터→Task 4, §6 보안(비밀 파일 분리·승인 backstop)→Task 2·7, §7 오류(poll 실패 격리·실행 실패)→Task 5·7·8, §8 API→Task 10(test-connection·rerun은 후속으로 명시 연기), §9 테스트→각 태스크 TDD. config→Task 9. lifespan/복구→Task 10.
- **Placeholder 스캔**: 코드 단계마다 실제 코드 포함. "적절히"류 문구 없음.
- **타입 일관성**: `poll(connection, secret, cursor, filter_config)` 시그니처가 base/email/StubAdapter/HookService에서 일치. `create_run(...) -> HookRun | None`, `create_headless_runtime(backend, model, options)`, `poll_due(run_service, now)`, `enqueue(run_id)` 등 태스크 간 이름·인자 일치 확인.
- **확인된 사실**: (a) `AgentRuntime`은 model을 `self._model`로 보관(runtime.py 확인). (b) `pytest-asyncio`가 `asyncio_mode="auto"`로 설정됨(pyproject.toml 확인). (c) `run-now` 테스트는 실 IMAP 접속 실패를 `poll_hook`이 삼켜 `created:0`이 되는 계약에 의존.
</content>
