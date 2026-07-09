# Session-Scoped Chat and SSE Spec

- 작성일: 2026-07-09
- 대상: `personal-agent-gateway`
- 범위: session-scoped chat state, durable live activity, SSE event contract, running status isolation, session history loading
- 관련 문서:
  - `docs/specs/completed_2026-07-07-live-activity-viewer-chat-redesign-spec.md`
  - `docs/specs/2026-07-08-observability-audit-log-spec.md`
  - `docs/specs/2026-07-08-persona-agent-teams-spec.md`
  - `docs/superpowers/plans/completed_2026-07-09-cli-session-resume-bridge.md`

## 1. 배경 / 문제

현재 chat transcript 저장은 session 단위에 가까워졌지만, live runtime 상태와 SSE activity 처리는 아직 app-wide 상태에 묶여 있다.

사용자가 관찰한 증상:

- 대화 순서가 자주 엉킨다.
- 다른 화면이나 다른 세션에 갔다가 돌아오면 SSE로 받은 activity 일부가 사라진다.
- 한 세션에서 agent가 로딩 중이면 다른 세션도 로딩 중처럼 보인다.
- 새 세션/삭제된 세션/active session 전환 시 이전 세션의 상태가 화면에 남거나 섞일 수 있다.

이 증상들은 같은 근본 원인에서 나온다.

```text
Transcript persistence: mostly session-scoped
SSE event bus: app-wide, memory-only
Frontend chat state: app-wide
Backend running state: single global variable
Session APIs: active-session centric
```

따라서 개선 방향은 단순히 SSE 필터 하나를 더 넣는 것이 아니라, chat runtime을 "세션이 소유하는 상태"로 재정의하는 것이다.

## 2. 근거 / 현재 코드에서 확인된 문제

### 2.1 Frontend live state is global

`GatewayApp`은 아래 상태를 단일 값으로 가진다.

```text
entries
pendingApproval
busy
turnStart
turnEnd
turnStreamed
entryOrderRef
turnHadAgentRef
turnStreamedRef
turnStartRef
```

근거:

- `frontend/src/components/containers/GatewayApp/index.jsx`
  - `entries`: line 68
  - `pendingApproval`: line 69
  - `busy`: line 70
  - `turnStart`: line 73
  - `turnEnd`: line 74
  - `EventSource`: line 160

문제:

- 세션 A에서 `busy=true`가 되면 ChatView, Statusbar, Composer는 세션 B를 보고 있어도 같은 `busy` 값을 받는다.
- 세션 전환 시 `entries`를 교체하므로 이전 세션의 live-only SSE entries는 별도 cache가 없으면 사라진다.
- active session이 아닌 SSE 이벤트는 현재는 대부분 무시되므로, background session의 live updates를 보존하지 못한다.

### 2.2 Backend running state is global

`create_app()` 내부에 `running_session_id: str | None` 단일 변수가 있다.

근거:

- `src/personal_agent_gateway/app.py`
  - `running_session_id`: line 58
  - `/api/chat` sets it: line 220
  - `/api/sessions` and `/api/status` read it: lines 155, 177, 351, 357

문제:

- 동시에 둘 이상의 session 실행을 표현할 수 없다.
- 현재 active session이 바뀌면 running status 계산이 active 중심으로 흔들릴 수 있다.
- `SessionRail`에서 각 session status를 정확히 표시하려면 session별 running state가 필요하다.

### 2.3 EventBus is memory-only and app-wide

`EventBus`는 bounded deque에 recent event만 보관한다.

근거:

- `src/personal_agent_gateway/events.py`
  - `_history: deque`: line 8
  - `publish()`: line 12
  - `subscribe(last_event_id)`: line 20

문제:

- process restart, browser reload, active session reload 후에는 live activity를 durable하게 복원할 수 없다.
- deque limit 밖으로 밀린 event는 사라진다.
- transcript에 저장되지 않는 activity row는 `/api/history`로 복구되지 않는다.

### 2.4 Event ordering uses mixed clocks

현재 정렬 기준:

- persisted transcript: `created_at`
- live SSE: frontend local `entryOrderRef`
- SSE replay: EventBus `id`
- UI displayed time: `nowHM()` / `nowHMS()` on client

근거:

- `frontend/src/lib/timeline.js`
  - `timelineFromHistory()` sorts by `created_at`: line 14
  - `entryFromSse()` creates local time values: line 56
- `frontend/src/components/containers/GatewayApp/index.jsx`
  - `entryOrderRef`: line 89

문제:

- persisted events and live events cannot be deterministically merged.
- fallback final answer from HTTP response can race with streamed final answer from SSE.
- command update reconciliation uses key matching, but non-command activity rows do not have stable ids.

### 2.5 Active-session centric APIs make state ownership ambiguous

Current chat APIs operate on active session:

```text
GET  /api/history
GET  /api/status
PUT  /api/sessions/active/config
POST /api/chat
POST /api/approvals/{id}/approve
POST /api/approvals/{id}/deny
```

문제:

- Browser UI가 어느 session을 조작하는지 request body/path에 명시하지 않는다.
- 사용자가 session을 바꾼 직후 기존 request가 완료되면 active session 기준 응답 처리와 충돌할 수 있다.
- background session updates를 명확하게 받을 API shape가 없다.

## 3. 목표 / 성공 기준

### Product goals

- 각 session은 자기 transcript, live activity, running state, pending approval, elapsed state를 독립적으로 가진다.
- active session을 바꿔도 다른 session의 live state가 침범하지 않는다.
- 한 session이 running이어도 다른 session의 Composer/Timeline/Statusbar는 running으로 보이지 않는다.
- active session이 아닌 session에서 들어온 SSE event도 해당 session cache/state에 저장되어, 나중에 그 session을 열면 보인다.
- page reload 또는 session 재진입 후에도 중요한 live activity가 복원된다.
- persisted history와 live updates의 순서가 deterministic하다.

### Engineering goals

- Backend event contract는 모든 chat/runtime event에 `session_id`, `event_id`, `created_at`, `type`, `payload/item`을 포함한다.
- Frontend state shape는 `activeSessionId`와 `sessionStateById`를 기준으로 한다.
- Event ordering은 server-assigned `event_id` 또는 server `created_at`을 우선한다.
- Running status는 `running_sessions` 또는 equivalent session-scoped runtime map으로 계산한다.
- Existing Team Run SSE events와 충돌하지 않는다.

## 4. 비목표

- Codex/Claude CLI의 native session resume 구조를 바꾸지 않는다.
- 여러 browser user를 지원하는 multi-user architecture를 만들지 않는다.
- Hosted message broker, Redis, Kafka, external DB를 도입하지 않는다.
- 모든 token-by-token stream을 완전 영구 저장하는 것을 MVP 목표로 두지 않는다.
- Team Run architecture를 이 스펙에서 재설계하지 않는다. 단, event envelope은 Team Run에도 확장 가능해야 한다.
- UX 전체 redesign은 포함하지 않는다. Chat runtime state correctness가 우선이다.

## 5. 핵심 결정

### Decision 1: Session owns chat UI state

Frontend는 아래 구조를 기준으로 한다.

```ts
type ChatSessionState = {
  entries: TimelineEntry[];
  pendingApproval: Approval | null;
  busy: boolean;
  turnStart: number | null;
  turnEnd: number | null;
  turnStreamed: boolean;
  nextLocalOrder: number;
  lastServerEventId: number | null;
  lastLoadedAt: number | null;
};

type GatewayChatState = {
  activeSessionId: string | null;
  bySessionId: Record<string, ChatSessionState>;
};
```

Implication:

- `ChatView` receives only `activeSessionState`.
- `Statusbar` derives phase from active session state, not global state.
- `SessionRail` derives row status from backend session summaries plus local session state.
- Composer disables only when active session is busy or has unresolved active pending approval.

### Decision 2: SSE events update session state by `session_id`

SSE handler rule:

```text
if event has session_id:
  ensure sessionStateById[session_id]
  append/reconcile event into that session state
  if session_id == activeSessionId:
    visible timeline updates
  else:
    session row status/count updates but active view is not polluted
else if event.type starts with "team.":
  route to team state
else:
  ignore or log as unscoped event
```

Implication:

- Non-active session events are not dropped.
- Active view does not receive foreign session events.
- Background session can show "running" in the rail without making active session busy.

### Decision 3: Runtime activity must be durable enough to restore UI

Introduce a durable session activity log for runtime/UI activity that is not already represented as transcript messages.

Recommended model:

```text
session_activity_events

id
session_id
event_seq
event_type
payload_json
created_at
source: runtime | codex | claude | system
transcript_event_id
ttl_policy: durable | ephemeral
```

Minimum durable event types:

```text
runtime.user_message.started
runtime.completed
runtime.error
codex.item.command_execution
codex.item.agent_message
claude.message
approval.requested
approval.resolved
artifact.created
```

`ttl_policy="ephemeral"` may be used later for high-volume progress events, but MVP should persist all event rows currently rendered in Timeline.

Implication:

- `/api/history` can continue returning transcript events, but Chat UI needs a new activity endpoint or an expanded history response.
- Page reload can reconstruct the activity timeline without relying on EventBus memory.

### Decision 4: Server event id is the primary ordering key

Every session activity event gets server-side monotonic ordering within a session.

Required fields:

```json
{
  "id": 123,
  "session_id": "abc",
  "event_seq": 8,
  "created_at": "2026-07-09T04:12:53.123456Z",
  "type": "runtime.completed",
  "payload": {}
}
```

Ordering rule:

```text
1. session activity event_seq when available
2. transcript created_at when loading older transcript-only messages
3. local optimistic order only for unsent user messages
```

Implication:

- Frontend no longer relies on `Date.now()` or array insertion order for server events.
- Fallback HTTP response entries must reconcile with SSE final answer using stable event identity.

### Decision 5: Chat APIs should become session-explicit

Add session-explicit endpoints while preserving active endpoints temporarily.

New APIs:

```text
GET  /api/sessions/{session_id}/history
GET  /api/sessions/{session_id}/activity
GET  /api/sessions/{session_id}/status
POST /api/sessions/{session_id}/chat
POST /api/sessions/{session_id}/approvals/{approval_id}/approve
POST /api/sessions/{session_id}/approvals/{approval_id}/deny
```

Existing active endpoints remain as compatibility wrappers:

```text
GET  /api/history
POST /api/chat
POST /api/approvals/{approval_id}/approve
POST /api/approvals/{approval_id}/deny
```

Compatibility rule:

- Existing endpoints resolve `active_id()` once at request start.
- New frontend should use session-explicit endpoints.

Implication:

- Request and response cannot accidentally target a different active session after session switching.
- Testability improves because each request declares its session owner.

### Decision 6: Backend running state is session-scoped

Replace single `running_session_id` with a session-scoped runtime state.

Recommended shape:

```py
@dataclass
class SessionRunState:
    session_id: str
    started_at: datetime
    status: Literal["running", "waiting_approval"]
    request_id: str

running_sessions: dict[str, SessionRunState]
```

Rules:

- On chat start: `running_sessions[session_id] = SessionRunState(..., status="running")`
- On pending approval: mark `waiting_approval` or derive from transcript pending request
- On completion/error: remove session from `running_sessions`
- `/api/sessions` row status uses `running_sessions` first, then transcript-derived status
- `/api/status` for active session uses only that active session id

Implication:

- Multiple sessions can be represented independently.
- SessionRail can show running/waiting per session.
- Active session status does not leak into all sessions.

## 6. Event Contract

### 6.1 Envelope

All chat/runtime SSE events must use this envelope.

```json
{
  "id": 123,
  "session_id": "session-abc",
  "event_seq": 9,
  "created_at": "2026-07-09T04:12:53.123456Z",
  "type": "runtime.completed",
  "source": "runtime",
  "payload": {
    "pending_approval": null
  }
}
```

For Codex raw events, keep the raw item but wrap it:

```json
{
  "id": 124,
  "session_id": "session-abc",
  "event_seq": 10,
  "created_at": "2026-07-09T04:12:54.123456Z",
  "type": "codex.event",
  "source": "codex",
  "payload": {
    "raw_type": "item.completed",
    "item": {
      "id": "item-1",
      "type": "agent_message",
      "text": "Done"
    }
  }
}
```

Team events may omit `session_id` only if they include `team_run_id`.

### 6.2 Required invariants

- Chat/runtime events without `session_id` are invalid.
- Chat/runtime events without server `created_at` are invalid.
- Frontend must not invent server ordering for scoped events.
- EventBus publish should receive already normalized events, or EventBus should normalize consistently before fanout.

### 6.3 Reconciliation keys

Timeline entries produced from events need stable keys.

```text
runtime.user_message.started -> event:<event_seq>
runtime.completed -> event:<event_seq>
runtime.error -> event:<event_seq>
codex command_execution -> command:<session_id>:<item.id>
codex agent_message -> agent:<session_id>:<item.id or event_seq>
artifact.created -> artifact:<artifact_id>
```

Command update reconciliation should use command item id, not command text.

## 7. Backend API Contract

### 7.1 Session history

`GET /api/sessions/{session_id}/history`

Returns transcript-level conversation events.

```json
{
  "session_id": "session-abc",
  "events": [
    {
      "id": "transcript-event-id",
      "transcript_id": "session-abc",
      "kind": "user",
      "payload": {"content": "hello"},
      "created_at": "2026-07-09T04:12:50Z"
    }
  ]
}
```

### 7.2 Session activity

`GET /api/sessions/{session_id}/activity`

Returns durable UI activity events.

```json
{
  "session_id": "session-abc",
  "events": [
    {
      "id": 123,
      "session_id": "session-abc",
      "event_seq": 1,
      "type": "runtime.user_message.started",
      "source": "runtime",
      "payload": {"message": "hello"},
      "created_at": "2026-07-09T04:12:50Z"
    }
  ]
}
```

### 7.3 Session status

`GET /api/sessions/{session_id}/status`

```json
{
  "session_id": "session-abc",
  "status": "running",
  "pending_approval": false,
  "message_count": 2,
  "last_event_id": 123,
  "session_config": {
    "agent_id": "codex",
    "model": "default",
    "options": {},
    "editable": false,
    "source": "explicit"
  }
}
```

Allowed `status` values:

```text
idle
running
waiting_approval
failed
```

### 7.4 Session chat

`POST /api/sessions/{session_id}/chat`

Request:

```json
{"message": "hello"}
```

Response:

```json
{
  "session_id": "session-abc",
  "request_id": "request-uuid",
  "messages": [{"role": "assistant", "content": "done"}],
  "pending_approval": null,
  "last_event_id": 130
}
```

Rules:

- The server must not switch active session implicitly for this request.
- If `session_id` does not exist, return `404`.
- Starting a new session remains `POST /api/reset` or a dedicated `POST /api/sessions`.

## 8. Frontend State Contract

### 8.1 State shape

`GatewayApp` should stop storing chat live state as top-level singular state.

Target:

```js
const [activeSessionId, setActiveSessionId] = useState(null);
const [sessionStateById, setSessionStateById] = useState({});
```

Session state factory:

```js
function emptyChatSessionState() {
  return {
    entries: [],
    pendingApproval: null,
    busy: false,
    turnStart: null,
    turnEnd: null,
    turnStreamed: false,
    nextLocalOrder: 0,
    lastServerEventId: null,
    lastLoadedAt: null
  };
}
```

### 8.2 Loading a session

When activating a session:

```text
1. set activeSessionId immediately
2. fetch /api/sessions/{id}/history
3. fetch /api/sessions/{id}/activity
4. merge transcript + activity into entries
5. update sessionStateById[id]
6. fetch /api/sessions/{id}/status
```

If state for that session already exists and is fresh, UI may show cached entries immediately and refresh in background.

### 8.3 SSE handling

SSE handler must be independent from active session.

```js
function handleSseEvent(event) {
  if (event.session_id) {
    updateSessionState(event.session_id, (state) => applySessionEvent(state, event));
    refreshSessionSummary(event.session_id);
    return;
  }
  if (event.team_run_id) {
    applyTeamEvent(event);
  }
}
```

Rules:

- Do not drop non-active session events.
- Do not append non-active session events to active session entries.
- Do not set global `busy` from a session event.
- Do not recreate EventSource when `busy` changes. EventSource lifecycle should depend on authentication only.

### 8.4 Optimistic user messages

When sending a message:

```text
1. Append local optimistic user entry to active session state.
2. Mark only active session state busy.
3. POST /api/sessions/{activeSessionId}/chat.
4. Reconcile response using session_id and last_event_id.
5. If SSE already delivered final answer, do not duplicate fallback response.
```

Optimistic entries use local keys:

```text
local:user:<clientRequestId>
```

When persisted transcript/activity returns, optimistic entry should be replaced or left only if server response failed before accepting the message.

## 9. Persistence Model

### 9.1 Why transcript alone is insufficient

Transcript currently stores conversation-level records:

```text
user
assistant
tool_request
approval
tool_result
tool_denial
runtime_error
session_config_set
agent_session_link
```

This is good for model context, but not enough for UI activity:

- `runtime.user_message.started` is not a model message.
- `runtime.completed` is not a model message.
- command live updates may contain intermediate status/output.
- Codex raw events may be useful for timeline display but should not all become model context.

Therefore session activity must be a separate durable read model, not mixed blindly into model transcript context.

### 9.2 Storage recommendation

Use SQLite if available through existing `Database`, because jobs/artifacts/team features already use SQLite.

Table:

```sql
create table if not exists session_activity_events (
  id integer primary key autoincrement,
  session_id text not null,
  event_seq integer not null,
  event_type text not null,
  source text not null,
  payload_json text not null,
  transcript_event_id text,
  created_at text not null,
  unique(session_id, event_seq)
);
```

Index:

```sql
create index if not exists idx_session_activity_events_session_seq
on session_activity_events(session_id, event_seq);
```

### 9.3 Retention

MVP retention:

- keep all session activity events while the session exists
- delete session activity events when the session is deleted

Later:

- compact high-volume command output into artifacts or job events
- retain only final command output for old sessions

## 10. UI Behavior Requirements

### 10.1 Chat transcript

- Timeline shows merged transcript + activity for the active session only.
- If active session has no entries and is not busy, show idle empty state.
- If active session is not busy, never show loader because another session is busy.
- If background session receives updates, only SessionRail row changes.

### 10.2 SessionRail

Each session row should show independent status:

```text
running
waiting_approval
failed
idle
```

If session is running in background:

- show a small running indicator on that row
- do not disable current active Composer unless it is the same session

### 10.3 Statusbar

Statusbar reflects the active session only.

Fields:

```text
SESSION: <active session status> <session short id>
PHASE: derived from active session state
RUNNING: active session running command count
PENDING: active session pending approval count
EVENTS: active session visible event count
SSE: connection state, not session busy state
```

SSE indicator should distinguish:

```text
CONNECTED
DISCONNECTED
RECONNECTING
```

It should not display `STREAMING /api/events` just because any session is busy.

### 10.4 Composer

Composer disabled rule:

```text
disabled = activeSessionState.busy || activeSessionState.pendingApproval != null
```

Other sessions running must not disable active Composer.

## 11. Migration Strategy

### Phase 1: Frontend session state isolation

Purpose:

- Stop cross-session loading/status pollution.
- Keep current backend APIs temporarily.

Changes:

- Introduce `sessionStateById`.
- Route SSE events by `session_id`.
- Keep non-active session events in cache.
- Make ChatView/Statusbar receive active session state.
- Stop recreating EventSource on `busy` change.

Expected result:

- A running session no longer makes all sessions look running.
- Switching away and back preserves live entries during the same browser lifetime.

### Phase 2: Backend session-scoped running state

Purpose:

- Make session list/status truthful.

Changes:

- Replace `running_session_id` with `running_sessions`.
- Update `/api/status`, `/api/sessions`, `/api/sessions/search`.
- Ensure approval waiting state is per session.

Expected result:

- SessionRail row statuses are accurate.
- Active status does not leak to all sessions.

### Phase 3: Session-explicit chat APIs

Purpose:

- Remove active-session race from chat requests.

Changes:

- Add `/api/sessions/{session_id}/history`.
- Add `/api/sessions/{session_id}/status`.
- Add `/api/sessions/{session_id}/chat`.
- Add session-scoped approve/deny endpoints.
- Switch frontend to explicit APIs.

Expected result:

- Session switch during request cannot redirect response handling.

### Phase 4: Durable session activity log

Purpose:

- Restore SSE-derived UI after reload/re-entry.

Changes:

- Add `session_activity_events` table/service.
- Persist runtime and Codex/Claude activity events before publishing to EventBus.
- Add `/api/sessions/{session_id}/activity`.
- Merge activity + transcript on frontend load.

Expected result:

- SSE activity survives reload.
- Timeline ordering becomes deterministic.

### Phase 5: Ordering and reconciliation hardening

Purpose:

- Remove duplicate final answers and unstable ordering.

Changes:

- Normalize event envelope.
- Add stable timeline keys.
- Reconcile command updates by item id.
- Reconcile assistant final answers by event id or transcript event id.

Expected result:

- No duplicate final answer from HTTP fallback + SSE.
- Activity rows render in server event order.

## 12. Verification

### Backend tests

Required tests:

- Starting chat in session A marks only session A running.
- Starting chat in session A does not mark session B running.
- `/api/sessions` returns independent status per session.
- `/api/sessions/{id}/chat` writes only to that session.
- Approve/deny endpoint for session B cannot resolve pending approval from session A.
- Session activity events are persisted before being published to EventBus.
- Deleting a session deletes its activity events.
- Reconnecting SSE with `Last-Event-ID` replays recent events, but reloading session activity uses durable storage.

### Frontend tests

Required tests:

- SSE event for session B updates session B cache but does not render in active session A.
- Switching from session A to B and back preserves session A live entries in memory.
- Busy state in session A does not disable Composer in active session B.
- Statusbar derives phase from active session state only.
- EventSource is created once after authentication and is not recreated when `busy` changes.
- Loading session activity after refresh restores command/activity rows.
- HTTP fallback final answer is not duplicated if SSE final answer was already received.

### Manual smoke

Scenario:

```text
1. Open session A.
2. Send a long-running prompt.
3. Switch to session B before it completes.
4. Verify session B is idle and composer is usable.
5. Verify session A row shows running.
6. When session A completes, switch back.
7. Verify session A contains all live activity in correct order.
8. Hard refresh.
9. Verify session A activity still appears.
```

## 13. Risks / Tradeoffs

### Risk: Persistent activity log duplicates transcript information

Some final assistant messages may exist both as transcript `assistant` and activity `codex.event`.

Mitigation:

- Treat transcript as model context.
- Treat session activity as UI timeline.
- Frontend merge must dedupe by stable keys and event type.

### Risk: More state in frontend

`sessionStateById` is more complex than single `entries`.

Mitigation:

- Encapsulate state transitions in pure helpers:
  - `emptyChatSessionState()`
  - `applySessionEvent(state, event)`
  - `mergeSessionHistoryAndActivity(history, activity)`
  - `deriveActiveSessionView(stateById, activeSessionId)`

### Risk: Event schema migration may break existing tests

Existing tests expect raw `event.item`.

Mitigation:

- `entryFromSse()` can accept both legacy raw shape and new envelope during migration.
- New backend events should emit envelope shape after frontend compatibility is in place.

## 14. Acceptance Criteria

This spec is complete when all of the following are true:

- There is no top-level app-wide `busy` used by ChatView/Statusbar for chat sessions.
- Running state is stored per session on the backend.
- Chat requests can target a session explicitly.
- All chat/runtime SSE events include `session_id`.
- Non-active session SSE updates are retained in that session's state.
- Session activity needed for UI timeline is durable and reloadable.
- Timeline ordering uses server-provided ordering.
- Tests cover cross-session busy isolation, non-active SSE handling, durable activity restoration, and no duplicate final answer.

## 15. Open Questions

- Should `session_activity_events` live in SQLite only, while transcript remains JSONL, or should transcript also move to SQLite later?
- How much Codex command output should be persisted for old sessions?
- Should background running sessions be allowed concurrently, or should MVP still restrict to one active runtime while representing state per session?
- Should session-explicit APIs activate the session as a side effect? This spec recommends no side effect.
- Should `EventBus` itself normalize events, or should callers publish already normalized events? This spec recommends a dedicated `SessionActivityService` to persist and publish normalized events.
