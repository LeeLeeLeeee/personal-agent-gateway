# Knowledge Request Draft Failure Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a delegated Team Run finishes without a usable Library draft, persist why on the Knowledge Request and show it on the Archive request card.

**Architecture:** Four nullable columns on `knowledge_requests` hold the last draft failure. `ArchiveService` gains `record_draft_failure` / `clear_draft_failure`; `HookRunner` calls them instead of silently reopening the request. The requests API payload carries the four fields and `ArchiveView` renders a banner. No new endpoint, no new status value, no automatic retry.

**Tech Stack:** Python 3.12, FastAPI, SQLite (raw `sqlite3`), pytest / pytest-asyncio, ruff; React 18 + Vite + Vitest + Testing Library.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-knowledge-request-draft-failure-visibility-design.md`.
- Do not add a new Knowledge Request status. `_REQUEST_STATUSES`, `_USER_REQUEST_STATUSES`, and `_ACTIVE_REQUEST_STATUSES` stay as they are.
- Do not add a retry endpoint or a retry button. Re-delegation reuses the existing `Send to team` control.
- `last_draft_error_message` is model-derived text: pass it through `redact_text` and truncate to 500 characters before storing.
- Recording a failure must never break cycle settlement. Swallow `KeyError` / `ValueError` from the recording call and continue.
- Backend commands run from the repo root with `PYTHONPATH=src`; frontend commands run from `frontend/`.
  - Backend test: `.venv/Scripts/python.exe -m pytest tests/<file> -v`
  - Lint: `.venv/Scripts/python.exe -m ruff check .`
  - Frontend test: `npm test -- <path>` inside `frontend/`
- Korean Conventional Commit subjects, matching the existing history style.

---

### Task 1: Migration 21 — draft failure columns

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` (add `_migration_21_knowledge_request_draft_failure`, register in `MIGRATIONS`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `_columns(connection, table)` helper already in `migrations.py`.
- Produces: `_migration_21_knowledge_request_draft_failure(connection: sqlite3.Connection) -> None`; `knowledge_requests` gains nullable text columns `last_draft_error_code`, `last_draft_error_message`, `last_draft_failed_at`, `last_draft_cycle_id`; `LATEST_SCHEMA_VERSION` becomes 21.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrations.py`. Extend the existing import block from `personal_agent_gateway.migrations` with `_migration_21_knowledge_request_draft_failure`.

```python
def test_migration_21_adds_knowledge_request_draft_failure_columns_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table knowledge_requests (
            id text primary key,
            status text not null,
            created_at text not null,
            updated_at text not null
        );
        """
    )

    _migration_21_knowledge_request_draft_failure(connection)
    _migration_21_knowledge_request_draft_failure(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(knowledge_requests)")
    }
    assert {
        "last_draft_error_code",
        "last_draft_error_message",
        "last_draft_failed_at",
        "last_draft_cycle_id",
    } <= columns
    assert LATEST_SCHEMA_VERSION == 21
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migrations.py::test_migration_21_adds_knowledge_request_draft_failure_columns_idempotently -v`
Expected: FAIL with `ImportError: cannot import name '_migration_21_knowledge_request_draft_failure'`

- [ ] **Step 3: Write minimal implementation**

In `src/personal_agent_gateway/migrations.py`, add after `_migration_20_team_model_operations`:

```python
def _migration_21_knowledge_request_draft_failure(
    connection: sqlite3.Connection,
) -> None:
    columns = _columns(connection, "knowledge_requests")
    for column in (
        "last_draft_error_code",
        "last_draft_error_message",
        "last_draft_failed_at",
        "last_draft_cycle_id",
    ):
        if column not in columns:
            connection.execute(
                f"alter table knowledge_requests add column {column} text"
            )
```

Register it in the `MIGRATIONS` tuple, directly after the entry for 20:

```python
    (21, "knowledge-request-draft-failure", _migration_21_knowledge_request_draft_failure),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migrations.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/migrations.py tests/test_migrations.py
git commit -m "feat: Knowledge Request 초안 실패 컬럼 추가"
```

---

### Task 2: ArchiveService records and clears the failure

**Files:**
- Modify: `src/personal_agent_gateway/archive.py` (`KnowledgeRequest` dataclass ~line 79, `assign_request_team` ~line 637, `_request_from_row` ~line 1172, new module constant)
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: migration 21 columns from Task 1; existing `_now()`, `self._db.execute`, `self.get_request`.
- Produces:
  - `KnowledgeRequest.last_draft_error_code / last_draft_error_message / last_draft_failed_at / last_draft_cycle_id`, all `str | None`
  - `ArchiveService.record_draft_failure(request_id: str, *, error_code: str, message: str, cycle_id: str | None) -> KnowledgeRequest`
  - `ArchiveService.clear_draft_failure(request_id: str) -> KnowledgeRequest`
  - `assign_request_team` clears the four columns.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_archive.py`:

```python
def _documentation_team_run(tmp_path: Path, db: Database, personas: PersonaService):
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Library Lead", "Editor", "", [], [])
    return teams.create_team_run(
        "Prepare reviewed Library drafts",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )


def test_record_draft_failure_reopens_request_and_stores_the_reason(tmp_path: Path) -> None:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    archive = ArchiveService(db)
    personas = PersonaService(db)
    team_run = _documentation_team_run(tmp_path, db, personas)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=["Signals", "Steps"],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.assign_request_team(request.id, team_run.id)

    failed = archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="Team response must contain exactly one Library Draft marker",
        cycle_id="cycle-1",
    )

    assert failed.status == "open"
    assert failed.last_draft_error_code == "draft_contract_violation"
    assert failed.last_draft_error_message == (
        "Team response must contain exactly one Library Draft marker"
    )
    assert failed.last_draft_cycle_id == "cycle-1"
    assert failed.last_draft_failed_at
    assert archive.get_request(request.id).last_draft_error_code == (
        "draft_contract_violation"
    )


def test_record_draft_failure_truncates_the_message(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )

    failed = archive.record_draft_failure(
        request.id,
        error_code="draft_invalid_payload",
        message="x" * 900,
        cycle_id=None,
    )

    assert failed.last_draft_error_message == "x" * 500
    assert failed.last_draft_cycle_id is None


def test_fulfilled_request_cannot_record_a_draft_failure(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title="Rollback checklist",
        summary="Verified rollback steps.",
        content_markdown="Stop traffic, then roll back.",
        tags=[],
        source_urls=[],
        persona_ids=[],
        request_id=request.id,
    )

    with pytest.raises(ValueError):
        archive.record_draft_failure(
            request.id,
            error_code="draft_save_failed",
            message="too late",
            cycle_id=None,
        )


def test_clear_and_redelegation_remove_the_recorded_failure(tmp_path: Path) -> None:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    archive = ArchiveService(db)
    personas = PersonaService(db)
    team_run = _documentation_team_run(tmp_path, db, personas)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )

    cleared = archive.clear_draft_failure(request.id)

    assert cleared.last_draft_error_code is None
    assert cleared.last_draft_error_message is None
    assert cleared.last_draft_failed_at is None
    assert cleared.last_draft_cycle_id is None

    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )
    reassigned = archive.assign_request_team(request.id, team_run.id)

    assert reassigned.status == "in_progress"
    assert reassigned.last_draft_error_code is None
    assert reassigned.last_draft_failed_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_archive.py -k draft_failure -v`
Expected: FAIL with `AttributeError: 'ArchiveService' object has no attribute 'record_draft_failure'`

- [ ] **Step 3: Write the implementation**

3a. In `src/personal_agent_gateway/archive.py`, add the constant next to the other module constants (near `_LIBRARY_DRAFT_CLOSE`, ~line 35):

```python
_DRAFT_ERROR_MESSAGE_LIMIT = 500
```

3b. Extend the `KnowledgeRequest` dataclass — insert the four fields directly after `fulfilled_by_entry_id` (~line 91):

```python
    fulfilled_by_entry_id: str | None
    last_draft_error_code: str | None
    last_draft_error_message: str | None
    last_draft_failed_at: str | None
    last_draft_cycle_id: str | None
    created_at: str
    updated_at: str
```

Before editing, confirm `_request_from_row` is the only constructor so the field order change is safe:

```bash
grep -rn "KnowledgeRequest(" src tests
```

If any other call site constructs it positionally, convert that call site to keyword arguments.

3c. Extend `_request_from_row` (~line 1185):

```python
        fulfilled_by_entry_id=row["fulfilled_by_entry_id"],
        last_draft_error_code=row["last_draft_error_code"],
        last_draft_error_message=row["last_draft_error_message"],
        last_draft_failed_at=row["last_draft_failed_at"],
        last_draft_cycle_id=row["last_draft_cycle_id"],
```

`get_request` and `list_requests` already select `request.*`, so no query change is needed.

3d. Add the two methods after `update_request_status` (~line 635):

```python
    def record_draft_failure(
        self,
        request_id: str,
        *,
        error_code: str,
        message: str,
        cycle_id: str | None,
    ) -> KnowledgeRequest:
        request = self.get_request(request_id)
        if request.status == "fulfilled":
            raise ValueError("A fulfilled request cannot record a draft failure")
        now = _now()
        self._db.execute(
            """
            update knowledge_requests
            set status = 'open',
                last_draft_error_code = ?,
                last_draft_error_message = ?,
                last_draft_failed_at = ?,
                last_draft_cycle_id = ?,
                updated_at = ?
            where id = ?
            """,
            (
                error_code,
                message.strip()[:_DRAFT_ERROR_MESSAGE_LIMIT],
                now,
                cycle_id,
                now,
                request_id,
            ),
        )
        return self.get_request(request_id)

    def clear_draft_failure(self, request_id: str) -> KnowledgeRequest:
        self.get_request(request_id)
        self._db.execute(
            """
            update knowledge_requests
            set last_draft_error_code = null,
                last_draft_error_message = null,
                last_draft_failed_at = null,
                last_draft_cycle_id = null,
                updated_at = ?
            where id = ?
            """,
            (_now(), request_id),
        )
        return self.get_request(request_id)
```

3e. Clear the columns on re-delegation — replace the update statement inside `assign_request_team` (~line 654):

```python
        self._db.execute(
            """
            update knowledge_requests
            set assigned_team_run_id = ?,
                status = 'in_progress',
                last_draft_error_code = null,
                last_draft_error_message = null,
                last_draft_failed_at = null,
                last_draft_cycle_id = null,
                updated_at = ?
            where id = ?
            """,
            (team_run_id, _now(), request_id),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_archive.py -v`
Expected: PASS, all tests in the file (the pre-existing ones still pass because the new fields are keyword-constructed)

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/archive.py tests/test_archive.py
git commit -m "feat: Knowledge Request 초안 실패 기록·해제 추가"
```

---

### Task 3: Settlement and reconciliation record the failure

**Files:**
- Modify: `src/personal_agent_gateway/hook_runner.py` (`_settle_knowledge_request` lines 381-417, `_save_knowledge_request_draft` lines 419-433, `_reopen_knowledge_request` lines 435-441, `_reconcile_knowledge_requests` lines 460-484)
- Test: `tests/test_hook_runner.py`

**Interfaces:**
- Consumes: `ArchiveService.record_draft_failure` / `clear_draft_failure` from Task 2; existing `parse_library_draft_response`, `redact_text`, `self._save_library_draft`.
- Produces:
  - `HookRunner._apply_knowledge_request_draft(request_id: str, cycle: TeamRunCycle) -> tuple[ArchiveEntry | None, str, str]` returning `(draft, error_code, message)`; `error_code` is `""` on success or when the cycle is not in a terminal state. Both the settlement path and startup reconciliation go through it, so the parse/save/record sequence exists once.
  - `HookRunner._fail_draft(request_id: str, cycle: TeamRunCycle, error_code: str, exc: Exception | None) -> tuple[None, str, str]` — records the failure and builds the message.
  - `_save_knowledge_request_draft` and `_reopen_knowledge_request` are deleted.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hook_runner.py`, after `test_knowledge_request_cycle_prepares_contract_and_saves_draft`:

```python
async def _delegated_knowledge_cycle(tmp_path: Path):
    (
        runner,
        _runs,
        teams,
        team_run,
        _run,
        cycles,
        _dispatcher,
        archive,
    ) = _setup_team_hook(tmp_path)
    knowledge_request = archive.create_knowledge_request(
        title="Search verification method",
        reason="The personas need a reusable source-checking method.",
        suggested_outline=["Primary sources"],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.assign_request_team(knowledge_request.id, team_run.id)
    cycles.enqueue_request(
        team_run.id,
        "knowledge_request",
        f"{knowledge_request.id}#attempt-1",
        "placeholder",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(team_run.id)
    assert claimed is not None
    cycle = teams.create_cycle(
        team_run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )
    return runner, teams, team_run, archive, knowledge_request, cycle


@pytest.mark.asyncio
async def test_completed_cycle_without_marker_records_contract_violation(
    tmp_path: Path,
) -> None:
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    teams.set_cycle_status(
        cycle.id,
        "completed",
        summary="## 완료 요약\n\n초안을 파일로 정리했습니다.",
    )

    await runner.on_team_run_settled(teams.get_team_run(team_run.id), cycle.id)

    stored = archive.get_request(knowledge_request.id)
    assert archive.list_entries(status="draft") == []
    assert stored.status == "open"
    assert stored.last_draft_error_code == "draft_contract_violation"
    assert "Library Draft marker" in (stored.last_draft_error_message or "")
    assert stored.last_draft_cycle_id == cycle.id


@pytest.mark.asyncio
async def test_failed_cycle_records_the_cycle_status(tmp_path: Path) -> None:
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    teams.set_cycle_status(cycle.id, "failed", error_message="Worker crashed")

    await runner.on_team_run_settled(teams.get_team_run(team_run.id), cycle.id)

    stored = archive.get_request(knowledge_request.id)
    assert stored.status == "open"
    assert stored.last_draft_error_code == "cycle_failed"
    assert stored.last_draft_cycle_id == cycle.id


@pytest.mark.asyncio
async def test_successful_draft_clears_an_earlier_failure(tmp_path: Path) -> None:
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    archive.record_draft_failure(
        knowledge_request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="older-cycle",
    )
    summary = (
        "Draft ready.\n\n"
        '<library_draft>{"kind":"search_method","title":"Search verification method",'
        '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck primary sources.",'
        '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
    )
    teams.set_cycle_status(cycle.id, "completed", summary=summary)

    await runner.on_team_run_settled(teams.get_team_run(team_run.id), cycle.id)

    stored = archive.get_request(knowledge_request.id)
    assert len(archive.list_entries(status="draft")) == 1
    assert stored.last_draft_error_code is None
    assert stored.last_draft_failed_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hook_runner.py -k knowledge -v`
Expected: FAIL — `last_draft_error_code` is `None` because the current code only reopens the request

- [ ] **Step 3: Write the implementation**

3a. Replace `_settle_knowledge_request` (lines 381-417) with:

```python
    async def _settle_knowledge_request(
        self,
        request: TeamCycleRequest,
        cycle: TeamRunCycle,
    ) -> None:
        if self._archive is None:
            return
        request_id = knowledge_request_id_from_source(request.source_id)
        draft, error_code, message = self._apply_knowledge_request_draft(
            request_id, cycle
        )
        if draft is not None:
            await self._event_bus.publish(
                {
                    "type": "archive.draft.created",
                    "draft_id": draft.id,
                    "source_type": "knowledge_request",
                    "source_id": request_id,
                    "team_run_id": cycle.team_run_id,
                    "cycle_id": cycle.id,
                }
            )
            return
        if error_code:
            await self._event_bus.publish(
                {
                    "type": "archive.draft.failed",
                    "source_type": "knowledge_request",
                    "source_id": request_id,
                    "team_run_id": cycle.team_run_id,
                    "cycle_id": cycle.id,
                    "error_code": error_code,
                    "error": message,
                }
            )
```

3b. Replace `_save_knowledge_request_draft` and `_reopen_knowledge_request` (lines 419-441) with these two helpers. They hold the whole parse/save/record sequence so the settlement path and startup reconciliation share one copy of it:

```python
    def _apply_knowledge_request_draft(
        self,
        request_id: str,
        cycle: TeamRunCycle,
    ) -> tuple[ArchiveEntry | None, str, str]:
        """Save the cycle's Library draft, or record why it could not be saved.

        Returns (draft, error_code, message); error_code is empty on success
        and for a cycle that has not reached a terminal state. Only the
        settlement path publishes events for the outcome.
        """
        if self._archive is None:
            return None, "", ""
        if cycle.status in {"completed", "completed_with_failures"}:
            try:
                _result_text, payload = parse_library_draft_response(cycle.summary or "")
            except ValueError as exc:
                return self._fail_draft(
                    request_id, cycle, "draft_contract_violation", exc
                )
            try:
                draft = self._save_library_draft(
                    payload,
                    origin_source_type="knowledge_request",
                    origin_source_id=request_id,
                    origin_team_run_id=cycle.team_run_id,
                    origin_cycle_id=cycle.id,
                    origin_request_id=request_id,
                )
            except ValueError as exc:
                return self._fail_draft(
                    request_id, cycle, "draft_invalid_payload", exc
                )
            except (KeyError, RuntimeError) as exc:
                return self._fail_draft(request_id, cycle, "draft_save_failed", exc)
            try:
                self._archive.clear_draft_failure(request_id)
            except KeyError:
                pass
            return draft, "", ""
        if cycle.status in {"blocked", "failed", "canceled", "interrupted"}:
            return self._fail_draft(request_id, cycle, f"cycle_{cycle.status}", None)
        return None, "", ""

    def _fail_draft(
        self,
        request_id: str,
        cycle: TeamRunCycle,
        error_code: str,
        exc: Exception | None,
    ) -> tuple[None, str, str]:
        if exc is not None:
            message = redact_text(exc) or type(exc).__name__
        elif cycle.error_message:
            message = redact_text(cycle.error_message)
        else:
            message = f"Team Run Cycle {cycle.status}"
        if self._archive is not None:
            try:
                self._archive.record_draft_failure(
                    request_id,
                    error_code=error_code,
                    message=message,
                    cycle_id=cycle.id,
                )
            except (KeyError, ValueError):
                pass
        return None, error_code, message
```

3c. Replace the tail of `_reconcile_knowledge_requests` (lines 477-484) — the whole `if cycle.status in {"completed", ...}: ... continue` block and the `if cycle.status in {"blocked", ...}` block that follows it — with the shared call. This path is synchronous, so it records without publishing:

```python
            self._apply_knowledge_request_draft(request_id, cycle)
```

This path already re-saves against an existing draft when one exists; that behaviour is unchanged (`save_draft` is keyed on `(source_type, source_id)`).

3d. Confirm nothing still references the deleted helpers:

```bash
grep -rn "_save_knowledge_request_draft\|_reopen_knowledge_request" src tests
```

Expected: no matches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hook_runner.py -v`
Expected: PASS, including the pre-existing `test_startup_reconciliation_saves_latest_knowledge_request_draft` and `test_knowledge_request_cycle_prepares_contract_and_saves_draft`

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_runner.py tests/test_hook_runner.py
git commit -m "feat: 초안 생성 실패를 Knowledge Request에 기록"
```

---

### Task 4: Expose the failure through the requests API

**Files:**
- Modify: `src/personal_agent_gateway/api/archive.py` (`_request_payload`, lines 291-307)
- Test: `tests/test_api_archive.py`

**Interfaces:**
- Consumes: the four `KnowledgeRequest` fields from Task 2 and `ArchiveService.record_draft_failure` from Task 2.
- Produces: `GET /api/archive/requests` items carry `last_draft_error_code`, `last_draft_error_message`, `last_draft_failed_at`, `last_draft_cycle_id`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_archive.py`:

```python
def test_requests_api_exposes_the_last_draft_failure(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    archive = client.app.state.archive_service
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=["Signals"],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="Team response must contain exactly one Library Draft marker",
        cycle_id="cycle-1",
    )

    listed = client.get("/api/archive/requests").json()["requests"]

    item = next(entry for entry in listed if entry["id"] == request.id)
    assert item["last_draft_error_code"] == "draft_contract_violation"
    assert item["last_draft_error_message"] == (
        "Team response must contain exactly one Library Draft marker"
    )
    assert item["last_draft_cycle_id"] == "cycle-1"
    assert item["last_draft_failed_at"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_archive.py::test_requests_api_exposes_the_last_draft_failure -v`
Expected: FAIL with `KeyError: 'last_draft_error_code'`

- [ ] **Step 3: Write the implementation**

In `src/personal_agent_gateway/api/archive.py`, extend `_request_payload`:

```python
        "status": item.status,
        "fulfilled_by_entry_id": item.fulfilled_by_entry_id,
        "last_draft_error_code": item.last_draft_error_code,
        "last_draft_error_message": item.last_draft_error_message,
        "last_draft_failed_at": item.last_draft_failed_at,
        "last_draft_cycle_id": item.last_draft_cycle_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_archive.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/archive.py tests/test_api_archive.py
git commit -m "feat: requests API에 초안 실패 필드 노출"
```

---

### Task 5: Render the failure banner in ArchiveView

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx` (request card body, after `<p>{item.reason}</p>` at line 1369)
- Modify: `src/personal_agent_gateway/static/styles.css` (after `.archive-request-delegated`, line 5492)
- Test: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`

**Interfaces:**
- Consumes: the four fields on each item returned by `client.knowledgeRequests()` from Task 4.
- Produces: a `.archive-request-failure` block on the request card. No new props and no new client method.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx` inside the `describe("ArchiveView", ...)` block:

```jsx
  it("shows why a delegated Team Run produced no draft", async () => {
    const client = makeClient();
    client.knowledgeRequests = vi.fn().mockResolvedValue([
      {
        ...request,
        status: "open",
        assigned_team_run_id: documentationTeam.id,
        last_draft_error_code: "draft_contract_violation",
        last_draft_error_message:
          "Team response must contain exactly one Library Draft marker",
        last_draft_failed_at: "2026-08-03T00:42:36Z",
        last_draft_cycle_id: "cycle-1"
      }
    ]);

    render(<ArchiveView client={client} artifacts={[]} />);

    await screen.findByRole("heading", { name: "Archive" });

    expect(screen.getByText(/DRAFT FAILED/)).toHaveTextContent(
      "draft_contract_violation"
    );
    expect(
      screen.getByText(/exactly one Library Draft marker/)
    ).toBeInTheDocument();
    expect(screen.getByText(/CYCLE cycle-1/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: `Send ${request.title} to documentation team` })
    ).toBeInTheDocument();
  });

  it("shows no failure banner when the request has never failed", async () => {
    render(<ArchiveView client={makeClient()} artifacts={[]} />);

    await screen.findByRole("heading", { name: "Archive" });

    expect(screen.queryByText(/DRAFT FAILED/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx`
Expected: FAIL — `Unable to find an element with the text: /DRAFT FAILED/`

- [ ] **Step 3: Write the implementation**

3a. In `frontend/src/components/organisms/ArchiveView/index.jsx`, insert directly after `<p>{item.reason}</p>`:

```jsx
                  {active && item.last_draft_error_code ? (
                    <div className="archive-request-failure">
                      <span className="mono">
                        {`DRAFT FAILED · ${item.last_draft_error_code}`}
                        {item.last_draft_failed_at
                          ? ` · ${item.last_draft_failed_at}`
                          : ""}
                      </span>
                      {item.last_draft_error_message ? (
                        <p>{item.last_draft_error_message}</p>
                      ) : null}
                      {item.last_draft_cycle_id ? (
                        <span className="mono">
                          {`CYCLE ${item.last_draft_cycle_id}`}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
```

`active` is already computed at the top of the map callback (line 1344).

3b. In `src/personal_agent_gateway/static/styles.css`, add after the `.archive-request-delegated` rule:

```css
.archive-request-failure {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0 14px;
    border: 2px solid var(--c-warn);
    padding: 10px 12px;
    color: var(--c-warn);
    font-size: 8px;
    letter-spacing: 0.7px;
}
.archive-request-failure > p {
    color: var(--c-dark);
    font-size: 12px;
    letter-spacing: normal;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/organisms/ArchiveView/index.jsx \
  frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx \
  src/personal_agent_gateway/static/styles.css
git commit -m "feat: Archive 요청 카드에 초안 실패 배너 표시"
```

---

### Task 6: Full verification

**Files:** none modified unless a regression appears.

- [ ] **Step 1: Run the backend suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures compared to the pre-change baseline. Capture the baseline first with `git stash` if the suite has known pre-existing failures, and report which failures pre-date this work.

- [ ] **Step 2: Run lint**

Run: `.venv/Scripts/python.exe -m ruff check .`
Expected: PASS

- [ ] **Step 3: Run the frontend suite and build**

Run (from `frontend/`): `npm test` then `npm run build`
Expected: PASS

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "test: 초안 실패 가시화 회귀 정리"
```

Skip this step if nothing needed fixing.
