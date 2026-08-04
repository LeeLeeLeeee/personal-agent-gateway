# Artifact Retention Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preview and explicitly remove expired temporary artifacts while preserving existing, pinned, and Team-input artifacts.

**Architecture:** Add retention columns to `artifacts` and make `ArtifactStore` own eligibility, Team-reference protection, pinning, and deletion. The API parses requests, calls the store, translates errors, and audits mutations. Team producers explicitly opt into temporary retention; no scheduler or type inference is added.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite, pytest.

## Global Constraints

- `expires_at` only makes an artifact a candidate; no automatic deletion scheduler exists.
- Existing rows migrate to `durable` with no expiry and cannot become candidates.
- Never infer retention from free-form artifact type, title, or model output.
- Batch cleanup excludes `pinned`, `durable`, and Team cycle-request, cycle, and task inputs.
- Direct delete checks Team references before unlinking files.
- Cleanup requires an explicit non-empty ID list and rechecks each artifact before deletion.

---

### Task 1: Persist retention metadata and enforce store protection

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `src/personal_agent_gateway/artifacts.py`
- Modify: `tests/test_artifacts.py`
- Modify: `tests/test_db_agent_teams_schema.py`

**Interfaces:**
- Produces `Artifact.retention_class: str` and `Artifact.expires_at: datetime | None`.
- Produces `ArtifactInUseError`, `ArtifactCleanupPreview`, and `ArtifactCleanupResult`.
- Produces `ArtifactStore.set_retention(artifact_id, retention_class, expires_at)`, `cleanup_preview(evaluated_at)`, and `cleanup(artifact_ids, evaluated_at)`.

- [ ] **Step 1: Write failing migration and store tests**

Add a schema assertion that `artifacts` contains `retention_class` and `expires_at`; assert a default registration returns `durable` and `None`. Add cleanup fixtures for one expired temporary artifact, a pinned artifact, a durable artifact, and artifacts referenced by each of `team_cycle_request_input_artifacts`, `team_cycle_input_artifacts`, and `team_task_input_artifacts`. Assert preview returns only the expired temporary artifact and sums its bytes. Assert direct `delete` on every referenced fixture raises `ArtifactInUseError` while the row and file remain.

- [ ] **Step 2: Verify the red state**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_db_agent_teams_schema.py -k "retention or cleanup or referenced" -q`

Expected: FAIL because columns, methods, and the error do not exist.

- [ ] **Step 3: Add migration 25 and the store API**

Append migration `artifact-retention-cleanup` containing `alter table artifacts add column retention_class text not null default 'durable'`, `alter table artifacts add column expires_at text`, and index `idx_artifacts_retention_expiry(retention_class, expires_at)`. Extend `Artifact`, `_artifact_from_row`, `register_bytes`, `register_existing_file`, and `_register` with `retention_class="durable"` and `expires_at=None`, serializing aware UTC timestamps via `isoformat()`.

Implement `_RETENTION_CLASSES = {"pinned", "durable", "temporary"}`. `set_retention` rejects unknown classes, requires an expiry for temporary, and clears expiry for pinned/durable. `cleanup_preview` selects expired temporary rows and filters the three Team-input reference tables. Factor the reference lookup into a private helper; call it from `delete` before its unlink loop and raise `ArtifactInUseError` if referenced. `cleanup` rejects empty input, rechecks eligibility one ID at a time, skips unknown/protected/unexpired/non-temporary rows, and calls `delete` only for eligible rows.

- [ ] **Step 4: Verify and commit the storage layer**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_db_agent_teams_schema.py -q`

Expected: PASS including registration, path-safety, pagination, migration, retention update, preview, batch revalidation, and every Team-reference case.

Commit: `git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/artifacts.py tests/test_artifacts.py tests/test_db_agent_teams_schema.py; git commit -m "feat: add artifact retention cleanup"`

### Task 2: Mark Team-generated archive outputs temporary

**Files:**
- Modify: `src/personal_agent_gateway/team_artifact_publisher.py`
- Modify: `src/personal_agent_gateway/team_results.py`
- Modify: `tests/test_team_artifact_publisher.py`
- Modify: `tests/test_team_results.py`

**Interfaces:**
- Consumes Task 1 registration parameters.
- Produces Team deliverables and result-package artifacts with `retention_class == "temporary"` and 30-day expiry.

- [ ] **Step 1: Write failing producer tests**

Extend `test_publishes_only_declared_files_with_integrity_metadata` to assert its result is temporary and its `expires_at - created_at` is 30 days. Extend the completed-run package test to collect artifacts having `package_kind` metadata and assert every one is temporary with a non-null expiry.

- [ ] **Step 2: Verify the red state**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_team_artifact_publisher.py tests\test_team_results.py -k "temporary" -q`

Expected: FAIL because both producers use the durable registration default.

- [ ] **Step 3: Pass the explicit 30-day Team policy**

In `TeamArtifactPublisher.publish` and the `TeamRunResultPackager` package registration group, calculate `datetime.now(timezone.utc) + timedelta(days=30)` once per operation. Pass `retention_class="temporary"` and that `expires_at` to each existing `register_existing_file` call. Do not change artifact type, title, metadata, paths, direct user registration, or publication rollback.

- [ ] **Step 4: Verify and commit Team defaults**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_team_artifact_publisher.py tests\test_team_results.py -q`

Expected: PASS including rollback and result-package replacement.

Commit: `git add src/personal_agent_gateway/team_artifact_publisher.py src/personal_agent_gateway/team_results.py tests/test_team_artifact_publisher.py tests/test_team_results.py; git commit -m "feat: expire temporary team artifacts"`

### Task 3: Expose preview, cleanup, and pinning through the API

**Files:**
- Modify: `src/personal_agent_gateway/api/artifacts.py`
- Modify: `tests/test_api_artifacts.py`

**Interfaces:**
- Consumes Task 1 store methods and `ArtifactInUseError`.
- Produces `GET /api/artifacts/cleanup-preview`.
- Produces `POST /api/artifacts/cleanup` accepting `{"artifact_ids": ["..."]}`.
- Produces `PATCH /api/artifacts/{artifact_id}/retention` accepting a retention class and optional ISO expiry.
- Extends every artifact payload with `retention_class` and `expires_at`.

- [ ] **Step 1: Write failing API and audit tests**

Create an expired temporary artifact through the store, request `GET /api/artifacts/cleanup-preview`, and assert its ID, byte total, evaluation timestamp, and unchanged stored row. Post a request containing eligible and durable IDs; assert `deleted_ids` has only the eligible ID, `skipped_ids` has the durable ID, and audit has action `artifacts.cleanup` for resource type `artifact_cleanup`. Patch a temporary artifact to `pinned`; assert expiry is null in GET payload. Create a Team-input reference and assert direct DELETE returns 409.

- [ ] **Step 2: Verify the red state**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_api_artifacts.py -k "cleanup or retention or team_input" -q`

Expected: FAIL because routes, response fields, audits, and conflict mapping do not exist.

- [ ] **Step 3: Add models, fixed routes, error mapping, payloads, and audit**

Add a Pydantic request with non-empty `artifact_ids: list[str]`. The retention request accepts `retention_class` and string `expires_at`; parse it with `datetime.fromisoformat` and map malformed/unknown input to HTTP 400. Define `GET /cleanup-preview` and `POST /cleanup` before `GET /{artifact_id}`. Preview returns artifact payloads, IDs, total bytes, and ISO evaluation time; cleanup returns lists from `ArtifactCleanupResult`.

Audit retention updates as event/action `artifact.retention_updated` / `artifacts.retention_update` with class/expiry metadata. Audit cleanup as `artifact.cleanup_executed` / `artifacts.cleanup` with requested/deleted/skipped IDs. Catch `ArtifactInUseError` in existing direct DELETE, return HTTP 409, and do not record a deletion audit. Extend `_artifact_payload` with both retention fields.

- [ ] **Step 4: Verify and commit the API**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_api_artifacts.py -q`

Expected: PASS including auth, registration/content paths, old payload checks, preview, cleanup, validation, audit, and direct-delete conflict.

Commit: `git add src/personal_agent_gateway/api/artifacts.py tests/test_api_artifacts.py; git commit -m "feat: expose artifact cleanup controls"`

### Task 4: Verify the complete retention boundary

**Files:**
- Test: `tests/test_artifacts.py`
- Test: `tests/test_api_artifacts.py`
- Test: `tests/test_team_artifact_publisher.py`
- Test: `tests/test_team_results.py`
- Test: `tests/test_db_agent_teams_schema.py`

- [ ] **Step 1: Run the complete focused suite**

Run: `\.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_api_artifacts.py tests\test_team_artifact_publisher.py tests\test_team_results.py tests\test_db_agent_teams_schema.py -q`

Expected: PASS. This covers migration, store protection, producer defaults, API, audit, and existing Team results together.

- [ ] **Step 2: Verify scope and diff hygiene**

Run: `git diff --check; rg -n "schedule.*cleanup|cleanup.*schedule|update artifacts set type" src tests`

Expected: no diff-check output and no new scheduler/type-rewrite matches.

- [ ] **Step 3: Commit a test-only correction only if Step 1 required one**

Run: `git status --short`

Commit only changed test files if a correction was required: `git add tests/test_artifacts.py tests/test_api_artifacts.py tests/test_team_artifact_publisher.py tests/test_team_results.py tests/test_db_agent_teams_schema.py; git commit -m "test: cover artifact retention cleanup"`. Otherwise leave the worktree clean after Task 3.
