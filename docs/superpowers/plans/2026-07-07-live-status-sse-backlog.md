# Backlog: Live Local Status via SSE + State-Split Activity

**Status:** Future (not scheduled). Captured 2026-07-07 during frontend Slice 1.

> The SSE activity-stream portion is now detailed with a real codex event schema in
> `2026-07-07-live-activity-stream-plan.md`. The model + reasoning-effort display item below remains separate.

## Motivation

Slice 1's chat shows a single generic "AGENT WORKING" loader while a request is in
flight, because the runtime is request→response (one round trip). The UI cannot
distinguish phases (thinking vs running a tool vs writing output) and cannot reflect
live local machine state. We want richer, phase-aware feedback and live status.

## What this needs

### Backend (Codex domain — `app.py` / `runtime.py` / jobs)
- **SSE endpoint**, e.g. `GET /api/events` (text/event-stream), gated by the OTP session.
- Stream runtime/job lifecycle events as they happen:
  - agent phase: `thinking`, `tool_request`, `tool_running`, `assistant`, `error`, `done`
  - job phase: `queued`, `running`, `succeeded`, `failed` (+ progress if a runner emits it)
  - local status deltas: running-jobs count, pending-approvals count, tunnel/scheduler status
- Emit from the existing transcript/job append points (fan out to subscribers).
- Reconnect-friendly (event ids / last-event-id), single-user so one broadcast channel is fine.

### Frontend (design domain — `static/`)
- Subscribe via `EventSource("/api/events")`.
- Replace the single generic loader with **phase-aware** indicators inline in chat
  (e.g. "THINKING" → "RUNNING ffmpeg…" → done), reusing the neo-brutalist cube loader.
- Drive the **status bar** live: promote RUNNING and TUNNEL out of PLANNED using event deltas.
- Promote the chat drawer's **ACTIVITY** section (currently PLANNED) into a live timeline.

## Dependency / sequencing
- Backend SSE must land first (add a task to the backend plan when scheduled).
- Then a frontend slice consumes it. Until then, the generic cube loader stands in.

## Deferred sub-ideas
- Per-job live log tailing over the same stream.
- Progress bars for long ffmpeg/capture runs (needs runner progress parsing).

## Show codex model + reasoning effort in status (future)
- **Backend (Codex/Python):** add config `codex_reasoning_effort` (`low|medium|high`); in `CodexModelClient._command()` append `-c model_reasoning_effort=<val>`; expose `model` (already in `/api/status`) + a new `reasoning_effort` field.
- Optionally report the *actually resolved* codex model from `codex exec --json` output instead of only `config.model` (which is "default" unless `AGENT_MODEL` is set).
- **Frontend:** status bar MODEL → `codex/<model> · <EFFORT>` (e.g. `codex/gpt-5-codex · HIGH`).
