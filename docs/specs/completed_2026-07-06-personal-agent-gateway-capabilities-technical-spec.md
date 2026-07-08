# Personal Agent Gateway Capabilities Technical Spec

## Status

Draft for product/design alignment.

## Goal

Extend Personal Agent Gateway from a simple local Codex chat gateway into a personal local agent control console.

The extension must keep the product single-user and local-first. It should improve auth, chat usability, job visibility, scheduled execution, ffmpeg workflows, capture workflows, and artifact storage without introducing team accounts or public SaaS assumptions.

## Non-Goals

- Multi-user account system.
- Team permissions, roles, invitations, or organizations.
- Public hosted service.
- Cloudflare Access or custom domain as a required dependency.
- Remote shell product separate from the agent gateway.
- Distributed worker fleet.
- Arbitrary background execution without visible job state and approval rules.

## Recommended Stack

### Frontend

- React + TypeScript.
- Vite for local development and production build.
- React Router for app sections.
- TanStack Query for API state, polling, and mutation lifecycle.
- Zustand or simple React context for local UI state.
- CSS modules or vanilla CSS initially; avoid a heavy design system until UI patterns stabilize.

The FastAPI backend should serve the built frontend assets in production. During development, Vite may proxy API requests to FastAPI.

### Backend

- Python 3.11+.
- FastAPI for HTTP API and static asset serving.
- Uvicorn for local server.
- SQLite for jobs, schedules, artifacts, auth/session metadata, and indexes.
- JSONL transcripts may remain for conversation history, but job/artifact metadata should move to SQLite.
- Local subprocess execution for Codex CLI, ffmpeg, ffprobe, screencapture, and approved shell commands.
- APScheduler or a small scheduler loop backed by SQLite for local cron-style schedules.

### Local Tools

- Codex CLI remains the default agent provider.
- ffmpeg and ffprobe are external binaries configured by env or settings.
- macOS capture can start with `screencapture` for screen/window capture.
- Browser/page capture can be added through Playwright when browser automation is required.

## Product Model

The app should be organized around five durable concepts.

### Capability

A capability is a declared local action the system knows how to prepare and run.

Examples:

- `agent.chat`
- `shell.run`
- `file.read`
- `file.write`
- `ffmpeg.inspect`
- `ffmpeg.transcode`
- `ffmpeg.extract-audio`
- `ffmpeg.thumbnail`
- `capture.screen`
- `capture.window`
- `capture.browser-page`
- `schedule.create`
- `schedule.pause`

Suggested fields:

```text
id
label
description
category
risk_level: low | medium | high
input_schema
output_schema
requires_approval
runner_type
enabled
```

Capabilities should be visible in the UI so the user understands what the gateway can do.

### Job

A job is one concrete execution of a capability.

Suggested fields:

```text
id
capability_id
source: chat | manual | schedule | api
source_session_id
source_schedule_id
title
status: draft | waiting_approval | queued | running | succeeded | failed | canceled
input_json
command_preview
approval_id
started_at
finished_at
created_at
updated_at
error_message
```

Jobs are the unit of visibility. Chat should create or propose jobs; it should not hide local execution inside assistant text only.

### Schedule

A schedule is a repeatable job template.

Suggested fields:

```text
id
name
capability_id
cron_expression
timezone
input_template_json
enabled
last_run_job_id
last_run_at
next_run_at
created_at
updated_at
```

The UI should always show both the raw cron expression and a human-readable explanation.

### Artifact

An artifact is a local result produced by a job or attached by the user.

Suggested fields:

```text
id
type: image | video | audio | text | log | report | archive | other
title
file_path
relative_path
mime_type
size_bytes
thumbnail_path
source_job_id
source_session_id
created_at
tags_json
metadata_json
```

Artifacts should be first-class UI objects, not hidden files. Captures, ffmpeg outputs, logs, generated reports, and command output files all belong here.

### Approval

An approval is a one-time user decision before a risky local action runs.

Suggested fields:

```text
id
job_id
risk_level
command_preview
status: pending | approved | denied | expired
created_at
decided_at
```

Approval must show the exact command or action summary before execution.

## Auth Scope

Keep auth single-user.

Required improvements:

- Dedicated login screen for Google Authenticator-compatible OTP entry.
- Browser login defaults to OTP-only after the initial OTP setup is complete.
- Access token is not required for normal browser login.
- Keep token support only for initial setup protection, recovery/admin use, and optional direct API bearer access.
- On query-token setup or recovery success, remove `?token=...` from the URL using browser history replacement.
- Set `HttpOnly`, `SameSite=Strict` or `Lax`, optionally `Secure` cookie.
- Add logout endpoint that clears the cookie.
- Add auth status endpoint for UI bootstrapping.
- Add OTP setup endpoints that generate an `otpauth://` URI and QR code payload for Google Authenticator.
- Add recovery codes so losing the OTP device does not permanently lock out the owner.
- Add rate limiting/backoff for failed OTP attempts.
- Show clear states for unauthenticated, OTP not configured, invalid OTP, expired cookie, and local server unavailable.

Suggested endpoints:

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/status
POST /api/auth/totp/setup/start
POST /api/auth/totp/setup/verify
POST /api/auth/totp/disable
POST /api/auth/recovery-codes/regenerate
```

No user database is needed for Version B.

Recommended dependencies:

- `pyotp` for TOTP generation and verification.
- `qrcode` for setup QR generation.

Recommended storage:

```text
data/
  auth/
    totp.json
    recovery_codes.json
```

The OTP secret and recovery codes are local secrets and must never be written to transcripts, job logs, artifacts, or command output.

## API Shape

### Capabilities

```text
GET /api/capabilities
GET /api/capabilities/{id}
```

### Jobs

```text
GET    /api/jobs
POST   /api/jobs
GET    /api/jobs/{id}
POST   /api/jobs/{id}/approve
POST   /api/jobs/{id}/deny
POST   /api/jobs/{id}/cancel
GET    /api/jobs/{id}/logs
GET    /api/jobs/{id}/artifacts
```

### Schedules

```text
GET    /api/schedules
POST   /api/schedules
GET    /api/schedules/{id}
PATCH  /api/schedules/{id}
POST   /api/schedules/{id}/pause
POST   /api/schedules/{id}/resume
POST   /api/schedules/{id}/run-now
DELETE /api/schedules/{id}
```

### Artifacts

```text
GET    /api/artifacts
GET    /api/artifacts/{id}
GET    /api/artifacts/{id}/content
GET    /api/artifacts/{id}/thumbnail
POST   /api/artifacts/{id}/attach-to-session
DELETE /api/artifacts/{id}
```

### Capture

Capture may be exposed through generic job creation, but short routes can improve UI clarity:

```text
POST /api/capture/screen
POST /api/capture/window
POST /api/capture/browser-page
```

Each route creates a job and returns the job id.

## Data Storage

Recommended local layout:

```text
data/
  app.sqlite
  sessions/
    active.json
    <session-id>.jsonl
  artifacts/
    images/
    videos/
    audio/
    logs/
    reports/
    thumbnails/
  temp/
```

SQLite should store metadata. Files remain on disk under `data/artifacts`.

Path rules:

- All artifact paths must resolve under the configured artifact root.
- Workspace file access must still resolve under `AGENT_WORKSPACE_ROOT`.
- User-facing delete should delete metadata and local file only after confirmation.

## Runner Design

Use a small local job orchestrator.

```text
JobService
  create_job()
  approve_job()
  run_job()
  cancel_job()

CapabilityRegistry
  list()
  get()
  validate_input()

Runner
  run(job) -> artifacts/logs/status
```

Initial runners:

- `CodexRunner`
- `ShellRunner`
- `FfmpegRunner`
- `CaptureRunner`
- `ReportRunner` later if needed

Keep each runner narrow. Runners should not know about UI or HTTP.

## Safety Rules

- Shell and ffmpeg command previews must be visible before high-risk execution.
- `ffmpeg.inspect` can run without approval if it only reads metadata.
- Media conversion, file writes, capture, and shell execution should require approval by default.
- Schedule creation should require approval if the scheduled action writes files or runs shell commands.
- A scheduled run should follow the schedule's saved approval policy. For Version B, prefer requiring explicit approval when creating or modifying the schedule, then allow future runs without repeated prompts only if the schedule is marked trusted.
- Logs and artifacts may contain sensitive local data. The UI should label them as local/private.

## UI Sections

The React app should have these primary routes:

```text
/login
/chat
/jobs
/jobs/:id
/schedules
/schedules/:id
/capabilities
/artifacts
/artifacts/:id
/settings
```

The app shell should include:

- Left sidebar navigation.
- Top status bar showing workspace, provider, tunnel status if known, running jobs, pending approvals.
- Main content panel.
- Optional right drawer for current job, logs, or artifact preview.

## Implementation Phases

### Phase 1: Auth and React Shell

- Add React + TypeScript frontend.
- Add OTP-first login/logout/status endpoints.
- Add initial TOTP setup and recovery-code management.
- Serve built assets from FastAPI.
- Recreate current chat UI in React.
- Preserve existing chat API behavior.

### Phase 2: Job Model and Visibility

- Add SQLite metadata store.
- Add jobs table and job APIs.
- Represent shell approvals and Codex runs as jobs.
- Add Jobs page with statuses, logs, and detail view.

### Phase 3: Artifact Store

- Add artifact metadata and file storage.
- Add artifact APIs.
- Add Artifacts page and viewer.
- Connect job logs and generated files to artifacts.

### Phase 4: Capture and ffmpeg Capabilities

- Add capability registry.
- Add ffmpeg inspect/transcode/extract-audio/thumbnail jobs.
- Add capture screen/window/browser-page jobs.
- Generate thumbnails for image/video artifacts.

### Phase 5: Schedules

- Add schedule metadata.
- Add scheduler loop or APScheduler integration.
- Add schedule creation, pause/resume, run-now, execution history.
- Connect scheduled runs to jobs and artifacts.

## Testing Strategy

- Unit tests for capability validation, path containment, artifact storage, auth, and cron parsing.
- Integration tests for job creation, approval, runner execution, and artifact registration.
- API tests for auth-protected routes.
- Frontend component tests for login, job status, approval panel, artifact viewer, and schedule forms.
- Manual verification for real ffmpeg, real screen capture, and tunnel access.

## Open Decisions

- Whether to use APScheduler or a custom SQLite-backed scheduler loop.
- Whether browser capture requires Playwright in Version B or can wait.
- Whether schedules can run without per-run approval after the schedule itself is approved.
- Whether to store transcripts in JSONL long-term or migrate conversation metadata to SQLite.
