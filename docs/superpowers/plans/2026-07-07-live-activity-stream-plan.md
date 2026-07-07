# Live Activity Stream (codex --json → SSE → web) Plan

**Status:** Planned (future slice). Not part of frontend Slice 1. Captured 2026-07-07 with a real codex 0.142.5 `exec --json` sample.

**Goal:** Show codex's live activity in the web UI as it works — reasoning text and shell-command execution (start/output/exit) — instead of only the final answer. "Show, not gate": `approval_policy=never` stays; we surface what codex does, we don't intercept it.

**Ownership:** Backend tasks = Codex (Python: `model_client.py`, `runtime.py`, `app.py`). Frontend tasks = design track (`static/`). Consumed as a contract via SSE.

## Captured event schema (codex 0.142.5 `exec --json`, stdout JSONL)

Envelope: `{"type": <event>, ...}` where `<event>` ∈ `thread.started`, `turn.started`, `item.started`, `item.completed` (also expect `turn.completed` and possibly `item.updated` / error events on longer runs).

Item events carry `item.type`:
- `agent_message` — `{ id, type:"agent_message", text }` — the agent's message / reasoning text.
- `command_execution` — `{ id, type:"command_execution", command, aggregated_output, exit_code, status }`, `status` ∈ `in_progress` → `completed` | `failed`, `exit_code` null until finished.

Real sample:
```json
{"type":"thread.started","thread_id":"019f3adf-..."}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"I'll run the directory listing..."}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"powershell ... 'ls -1'","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"...","aggregated_output":"...","exit_code":-1,"status":"failed"}}
```

**Windows caveat:** in the sample, command execution failed under the `read-only` sandbox with `windows sandbox: orchestrator_helper_exit_nonzero ... -1073741502`. codex's Windows sandbox helper is unreliable for read-only; validate command execution under `workspace-write` on Windows before relying on it.

## Backend (Codex / Python)

### Task B1: Stream codex stdout as events
- In `CodexModelClient` (or a streaming variant), replace `process.communicate(prompt)` with incremental reads: write the prompt to stdin, then `async for line in process.stdout:` parse each JSON line.
- Emit each parsed event to an injected async callback / bus (below). Keep accumulating the final `agent_message` as the returned `ModelResponse.content` so existing chat behavior is unchanged.
- Preserve timeout + non-zero exit handling.

### Task B2: In-process event bus
- Simple single-user async pub/sub (e.g. a set of `asyncio.Queue` subscribers). `publish(event)` fans out; subscribers created per SSE connection.
- Runtime publishes: on user message start, per codex event, on completion/error.

### Task B3: SSE endpoint
- `GET /api/events` → `text/event-stream`, gated by the `agent_session` cookie (same as other data routes).
- Yields `data: <json>\n\n` per event; sends a heartbeat comment every ~15s; cleans up the subscriber on disconnect.
- Include a monotonic `id:` per event for `Last-Event-ID` resume (single-user, best-effort).

## Frontend (design track / static)

### Task F1: EventSource client
- `const es = new EventSource("/api/events")`; parse `event.data` JSON; dispatch by `type`/`item.type`.
- Reconnect handled by EventSource automatically; tear down on logout.

### Task F2: Activity rendering (reuse neo-brutalist cube)
- `agent_message` → append/stream assistant text into the transcript.
- `command_execution` `in_progress` → an activity row: "RUNNING: `<command>`" + the cube loader.
- `command_execution` `completed`/`failed` → console block (`command`, `aggregated_output`, `exit_code`) with a green/red status badge; reuse `.console` + `status-chip`.
- Promote the chat drawer **ACTIVITY** section (currently PLANNED) into a live timeline of these events.
- Optionally drive status-bar RUNNING from in-progress command count (removes that PLANNED).

## Verification
- Backend: unit-test the JSONL parser against the captured sample fixtures; test `/api/events` yields published events and requires a session.
- Frontend + backend together: send a chat message that makes codex run a command; watch reasoning + RUNNING + console appear live in the browser (manual, since it needs a real codex run).

## Supersedes
- Detailed replacement for the SSE section of `2026-07-07-live-status-sse-backlog.md` (that doc's model+effort display item remains separate).
