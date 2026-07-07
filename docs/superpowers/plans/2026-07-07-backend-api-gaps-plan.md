# Backend API Gaps Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans (or subagent-driven-development) to implement task-by-task. Steps use checkbox (`- [x]`) syntax.

**Goal:** Expose the backend capabilities the frontend redesign needs. Most underlying services already exist ??this plan is mostly thin API wiring, not new subsystems.

**Ownership boundary:** This plan is backend-only (Codex). **Do not edit `src/personal_agent_gateway/static/**`** ??the frontend is owned by the design track and consumes these endpoints as a contract.

**Contract rules:**
- New `/api/*` routers require the `agent_session` cookie via the existing `require_session` dependency (`api/jobs.py`).
- Do not make breaking changes to existing `/api/*` response shapes; additive fields are fine.
- Timestamps are ISO 8601 UTC strings. Responses are snake_case, wrapped in a top-level key (`{"schedules": [...]}`, `{"job": {...}}`), matching existing routers.
- TDD per task: failing test ??implement ??`python -m pytest` + `python -m ruff check .` green.
- After each task, record the new/changed endpoint's request/response example in the commit message (the frontend uses it as the contract).

**Branch:** `feat/backend-api-gaps` off `main`.

**Current status (2026-07-07):** Completed and merged into `main` via `b2eb37a`.
Verified from `feat/live-activity-stream` with
`python -m pytest --basetemp=.tmp-pytest-baseline -o cache_dir=.tmp-pytest-cache`
(`120 passed`) and `python -m ruff check .` (`All checks passed`). The task list
below is retained as the API contract/audit trail.

---

## Task 1: Schedules API Router

`ScheduleService` (`schedules.py`) is fully implemented; only the HTTP router is missing.

**Files:** Create `src/personal_agent_gateway/api/schedules.py`; modify `src/personal_agent_gateway/api/__init__.py` and `app.py` (register router + expose `schedule_service` on `app.state`); create `tests/test_api_schedules.py`.

- [x] Failing tests: list requires session (401 without cookie); create ??200 with schedule; pause/resume toggles `enabled`; run-now returns a job with `source="schedule"`.
- [x] Endpoints:
  - `GET /api/schedules` ??`{"schedules": [schedule]}`
  - `POST /api/schedules` `{name, capability_id, cron_expression, timezone, input_template}` ??`{"schedule": ...}`
  - `POST /api/schedules/{id}/pause` · `/resume` · `/run-now`
  - `DELETE /api/schedules/{id}`
- [x] `schedule` payload (map from the `Schedule` dataclass): `id, name, capability_id, cron_expression, timezone, input_template, enabled, last_run_job_id, last_run_at, next_run_at`. If a human-readable frequency string is cheap to derive, add `human_readable`; otherwise omit (frontend can format from cron).
- [x] Wire `ScheduleService` onto `app.state` in `_attach_local_services`.
- [x] Verify: `python -m pytest tests/test_api_schedules.py -v` + ruff.

## Task 2: Artifact Content + Thumbnail Serving

`ArtifactStore.content_path()` exists; the `artifacts` table has `file_path`, `thumbnail_path`, `created_at`.

**Files:** Modify `src/personal_agent_gateway/api/artifacts.py`; test `tests/test_api_artifacts.py`.

- [x] Add `created_at` (and `thumbnail_path` presence, if on the model) to `_artifact_payload`.
- [x] `GET /api/artifacts/{id}/content` ??`FileResponse` with correct `mime_type` and a `Content-Disposition` filename. Reuse `content_path()`; keep path-escape protection.
- [x] `GET /api/artifacts/{id}/thumbnail` ??thumbnail file if `thumbnail_path` set, else fall back to content (or 404 for non-visual types).
- [x] Failing tests first (content bytes match; 401 without session; unknown id ??404).
- [x] Verify: pytest + ruff.

## Task 3: Job Logs + Timestamps

The `jobs` table has `created_at/started_at/finished_at`; `job_events` records lifecycle events. Neither is exposed.

**Files:** Modify `src/personal_agent_gateway/api/jobs.py` (and add a `list_events` read on `JobService` if needed); test `tests/test_api_jobs.py`.

- [x] Add `created_at`, `started_at`, `finished_at` to `_job_payload`.
- [x] `GET /api/jobs/{id}/events` ??`{"events": [{id, kind, payload, created_at}]}` ordered by `created_at asc`.
- [x] Failing tests first (approved job exposes timestamps; events endpoint returns created/approved/running/... in order).
- [x] Verify: pytest + ruff.

## Task 4: Settings Read Endpoint

**Files:** Create `src/personal_agent_gateway/api/settings.py`; register in `__init__.py`/`app.py`; test `tests/test_api_settings.py`.

- [x] `GET /api/settings` ??non-secret config snapshot:
  `workspace_root, session_dir, artifact_root, temp_dir, provider, model, codex_binary, codex_sandbox, codex_approval_policy, codex_timeout_seconds, ffmpeg_binary, ffprobe_binary, capture_binary, job_worker_concurrency, cookie_secure, totp_configured`.
- [x] **Exclude/mask secrets**: never return `web_token`, `openai_api_key`, or the TOTP secret.
- [x] Requires `agent_session`. Failing test: 401 without session; secrets absent from response.
- [x] Verify: pytest + ruff.

## Task 5: Recovery-Code Login

`AuthStore.use_recovery_code()` exists but no endpoint calls it.

**Files:** Modify `src/personal_agent_gateway/api/auth.py`; test `tests/test_api_auth.py`.

- [x] Either extend `POST /api/auth/login` to accept a recovery code when the input isn't a 6-digit OTP, or add `POST /api/auth/recovery {code}`. On success, issue `agent_session`. Codes are single-use (already enforced by the store).
- [x] Failing tests: valid recovery code logs in and sets cookie; same code reused ??401.
- [x] Verify: pytest + ruff.

## Task 6: Job List Server-Side Filters

**Files:** Modify `src/personal_agent_gateway/api/jobs.py` and `JobService.list_jobs`; test `tests/test_api_jobs.py`.

- [x] `GET /api/jobs` accepts optional query params `status`, `source`, `capability_id` (repeatable/multi allowed). No params ??unchanged (all jobs, newest first).
- [x] Push filtering into the SQL `where` clause (don't fetch-all-then-filter).
- [x] Failing tests first (filter by status; by source; combined; empty ??all).
- [x] Verify: pytest + ruff.

## Task 7: OTP-first browser auth ??remove the web_token page gate

**Problem:** The backend is half-migrated. `require_token` (web_token) still gates the browser-facing routes (`GET /`, `/static/app.js`, `/static/styles.css`, `/api/status`, `/api/history`, `/api/sessions*`, `/api/reset`, `/api/approvals/*`) while OTP only guards `/api/chat`. The result is a confusing double gate: the user needs a token in the URL just to load the page **and** an OTP. Per the UI/UX brief, browser login must be **OTP-only**; the token is for first-time setup, recovery, and optional API bearer ??never a page gate.

**Files:** Modify `src/personal_agent_gateway/app.py` (route dependencies), possibly `src/personal_agent_gateway/auth.py`; tests `tests/test_app.py`, `tests/test_api_auth.py`.

**Target behavior:**
- Fresh browser, **no** `agent_session`, TOTP **configured** ??`GET /` serves the app; the app shows the OTP login. **No `?token=` required.**
- No session, TOTP **not** configured ??the app shows first-time setup; setup endpoints stay gated by the setup token (`_require_setup_access` / `AGENT_AUTH_SETUP_TOKEN`), unchanged.
- Valid `agent_session` ??full access.
- Optional hardening: when `auth_require_token_and_otp` is true, additionally require the token (keeps the strict mode available, off by default).

- [x] Replace `require_token` on the browser-facing routes with an OTP-session gate (reuse/adjust `_require_otp_session_if_configured`), so `GET /`, `/static/*`, and the read/session/approval/reset APIs no longer demand `?token=`.
- [x] `GET /` must serve the shell HTML **without** a token so the JS can render the OTP login (the page itself is not a secret; the session/OTP protects actions and data).
- [x] Keep `/api/chat` and state-changing/data routes gated by the OTP session.
- [x] Keep setup routes (`/api/auth/setup/*`) gated by the setup token.
- [x] Update tests: `test_unauthenticated_routes_return_401` and `test_query_token_authenticates_and_sets_cookie` must be rewritten for the OTP model ??page/static reachable without a token; data/action routes require an `agent_session`; setup still token-gated. Coordinate with the frontend track, which owns `test_ui_assets_smoke`.
- [x] Verify: pytest + ruff.

## Task 8: Final Verification

- [x] `python -m pytest` ??all green.
- [x] `python -m ruff check .` ??clean.
- [x] Confirm no changes under `src/personal_agent_gateway/static/**`.

## Deliberately Deferred

- Batch ffmpeg folder workflows, extra capabilities (`ffmpeg.convert/compress`, `capture.window/browser`, `reports.summary`).
- Rich media thumbnail generation pipeline (T2 only serves an existing `thumbnail_path`).
- WebSocket/live log streaming for running jobs.
