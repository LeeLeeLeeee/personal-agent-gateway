# Backend API Gaps Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans (or subagent-driven-development) to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose the backend capabilities the frontend redesign needs. Most underlying services already exist — this plan is mostly thin API wiring, not new subsystems.

**Ownership boundary:** This plan is backend-only (Codex). **Do not edit `src/personal_agent_gateway/static/**`** — the frontend is owned by the design track and consumes these endpoints as a contract.

**Contract rules:**
- New `/api/*` routers require the `agent_session` cookie via the existing `require_session` dependency (`api/jobs.py`).
- Do not make breaking changes to existing `/api/*` response shapes; additive fields are fine.
- Timestamps are ISO 8601 UTC strings. Responses are snake_case, wrapped in a top-level key (`{"schedules": [...]}`, `{"job": {...}}`), matching existing routers.
- TDD per task: failing test → implement → `python -m pytest` + `python -m ruff check .` green.
- After each task, record the new/changed endpoint's request/response example in the commit message (the frontend uses it as the contract).

**Branch:** `feat/backend-api-gaps` off `main`.

---

## Task 1: Schedules API Router

`ScheduleService` (`schedules.py`) is fully implemented; only the HTTP router is missing.

**Files:** Create `src/personal_agent_gateway/api/schedules.py`; modify `src/personal_agent_gateway/api/__init__.py` and `app.py` (register router + expose `schedule_service` on `app.state`); create `tests/test_api_schedules.py`.

- [ ] Failing tests: list requires session (401 without cookie); create → 200 with schedule; pause/resume toggles `enabled`; run-now returns a job with `source="schedule"`.
- [ ] Endpoints:
  - `GET /api/schedules` → `{"schedules": [schedule]}`
  - `POST /api/schedules` `{name, capability_id, cron_expression, timezone, input_template}` → `{"schedule": ...}`
  - `POST /api/schedules/{id}/pause` · `/resume` · `/run-now`
  - `DELETE /api/schedules/{id}`
- [ ] `schedule` payload (map from the `Schedule` dataclass): `id, name, capability_id, cron_expression, timezone, input_template, enabled, last_run_job_id, last_run_at, next_run_at`. If a human-readable frequency string is cheap to derive, add `human_readable`; otherwise omit (frontend can format from cron).
- [ ] Wire `ScheduleService` onto `app.state` in `_attach_local_services`.
- [ ] Verify: `python -m pytest tests/test_api_schedules.py -v` + ruff.

## Task 2: Artifact Content + Thumbnail Serving

`ArtifactStore.content_path()` exists; the `artifacts` table has `file_path`, `thumbnail_path`, `created_at`.

**Files:** Modify `src/personal_agent_gateway/api/artifacts.py`; test `tests/test_api_artifacts.py`.

- [ ] Add `created_at` (and `thumbnail_path` presence, if on the model) to `_artifact_payload`.
- [ ] `GET /api/artifacts/{id}/content` → `FileResponse` with correct `mime_type` and a `Content-Disposition` filename. Reuse `content_path()`; keep path-escape protection.
- [ ] `GET /api/artifacts/{id}/thumbnail` → thumbnail file if `thumbnail_path` set, else fall back to content (or 404 for non-visual types).
- [ ] Failing tests first (content bytes match; 401 without session; unknown id → 404).
- [ ] Verify: pytest + ruff.

## Task 3: Job Logs + Timestamps

The `jobs` table has `created_at/started_at/finished_at`; `job_events` records lifecycle events. Neither is exposed.

**Files:** Modify `src/personal_agent_gateway/api/jobs.py` (and add a `list_events` read on `JobService` if needed); test `tests/test_api_jobs.py`.

- [ ] Add `created_at`, `started_at`, `finished_at` to `_job_payload`.
- [ ] `GET /api/jobs/{id}/events` → `{"events": [{id, kind, payload, created_at}]}` ordered by `created_at asc`.
- [ ] Failing tests first (approved job exposes timestamps; events endpoint returns created/approved/running/... in order).
- [ ] Verify: pytest + ruff.

## Task 4: Settings Read Endpoint

**Files:** Create `src/personal_agent_gateway/api/settings.py`; register in `__init__.py`/`app.py`; test `tests/test_api_settings.py`.

- [ ] `GET /api/settings` → non-secret config snapshot:
  `workspace_root, session_dir, artifact_root, temp_dir, provider, model, codex_binary, codex_sandbox, codex_approval_policy, codex_timeout_seconds, ffmpeg_binary, ffprobe_binary, capture_binary, job_worker_concurrency, cookie_secure, totp_configured`.
- [ ] **Exclude/mask secrets**: never return `web_token`, `openai_api_key`, or the TOTP secret.
- [ ] Requires `agent_session`. Failing test: 401 without session; secrets absent from response.
- [ ] Verify: pytest + ruff.

## Task 5: Recovery-Code Login

`AuthStore.use_recovery_code()` exists but no endpoint calls it.

**Files:** Modify `src/personal_agent_gateway/api/auth.py`; test `tests/test_api_auth.py`.

- [ ] Either extend `POST /api/auth/login` to accept a recovery code when the input isn't a 6-digit OTP, or add `POST /api/auth/recovery {code}`. On success, issue `agent_session`. Codes are single-use (already enforced by the store).
- [ ] Failing tests: valid recovery code logs in and sets cookie; same code reused → 401.
- [ ] Verify: pytest + ruff.

## Task 6: Job List Server-Side Filters

**Files:** Modify `src/personal_agent_gateway/api/jobs.py` and `JobService.list_jobs`; test `tests/test_api_jobs.py`.

- [ ] `GET /api/jobs` accepts optional query params `status`, `source`, `capability_id` (repeatable/multi allowed). No params → unchanged (all jobs, newest first).
- [ ] Push filtering into the SQL `where` clause (don't fetch-all-then-filter).
- [ ] Failing tests first (filter by status; by source; combined; empty → all).
- [ ] Verify: pytest + ruff.

## Task 7: Final Verification

- [ ] `python -m pytest` — all green.
- [ ] `python -m ruff check .` — clean.
- [ ] Confirm no changes under `src/personal_agent_gateway/static/**`.

## Deliberately Deferred

- Batch ffmpeg folder workflows, extra capabilities (`ffmpeg.convert/compress`, `capture.window/browser`, `reports.summary`).
- Rich media thumbnail generation pipeline (T2 only serves an existing `thumbnail_path`).
- WebSocket/live log streaming for running jobs.
