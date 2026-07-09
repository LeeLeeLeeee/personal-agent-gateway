# Task 4 Report: Frontend API and Timeline Model for Session Activity

## What I implemented

- Added explicit session-scoped frontend API client methods in `frontend/src/api/client.js`:
  - `api.sessionHistory(id)`
  - `api.sessionActivity(id)`
  - `api.sessionStatus(id)`
  - `api.sendSessionChat(id, message)`
  - `api.approveSession(id, approvalId)`
  - `api.denySession(id, approvalId)`
- Added API client coverage in `frontend/src/api/client.test.js` for the new explicit endpoints.
- Added `timelineFromSession(historyEvents, activityEvents)` in `frontend/src/lib/timeline.js`.
- Updated `entryFromSse(event)` to:
  - read normalized durable envelopes from `event.payload`
  - keep legacy raw Codex event support (`event.item`)
  - attach stable keys for command, agent, and runtime user-message rows
  - attach `serverOrder` from `event.event_seq` where available
- Added timeline coverage in `frontend/src/lib/timeline.test.js` for:
  - deterministic merge of transcript history and durable activity rows
  - normalized SSE envelopes
  - legacy raw Codex event compatibility
- Updated the existing command-event assertion to match the new stable command key format required by the task.

## Tests and results

- Focused test command:
  - `cd frontend`
  - `npm test -- --run src/api/client.test.js src/lib/timeline.test.js`
- Final result:
  - PASS
  - `2` test files passed
  - `11` tests passed

## RED/GREEN evidence

### RED

After adding the new tests first, the focused run failed with:

- `api.sessionHistory is not a function`
- `timelineFromSession is not a function`
- normalized envelope `entryFromSse(...)` returned `null` for durable activity

There was one follow-up failure after the initial implementation:

- existing command SSE test still expected the old key format (`ccmd-1`) while the task now requires stable keys (`command::cmd-1` for the legacy raw event case)

### GREEN

After implementing the client methods and timeline changes, and updating that stale key assertion, the same focused run passed:

- `npm test -- --run src/api/client.test.js src/lib/timeline.test.js`
- Result: `11 passed`

## Files changed

- `frontend/src/api/client.js`
- `frontend/src/api/client.test.js`
- `frontend/src/lib/timeline.js`
- `frontend/src/lib/timeline.test.js`

## Commit

- `3de7e49 feat: add session explicit frontend timeline model`

## Self-review findings

- Scope stayed within the Task 4 write set only.
- The client additions are minimal and match the existing fetch helpers and endpoint style already used in the file.
- `timelineFromSession(...)` is deterministic based on `serverOrder` when present and falls back to existing `order` values for persisted transcript entries.
- Legacy raw Codex events still map through `entryFromSse(...)` because payload fallback preserves the old `event.item` shape.
- I did not modify GatewayApp or UI state, per task scope.

## Concerns

- `entryFromSse(...)` now adds stable keys and `serverOrder` for command, agent, and `runtime.user_message.started`, which is what the brief specified. Other event types such as `runtime.completed` and `runtime.error` still use the pre-existing shape because the task did not ask for broader normalization.

## Review follow-up fix

- Updated `entryFromSse(...)` so `runtime.completed` now returns an `event_row` with:
  - stable key shape `event:<event_seq>`
  - `serverOrder` from `event.event_seq`
  - display time derived from `event.created_at` when present
- Updated `runtime.error` normalization so it now:
  - prefers `event.payload.message` over the raw top-level message
  - preserves stable event key shape `event:<event_seq>`
  - includes `serverOrder` and server-created display time
- Updated normalized agent message keys to include session scope:
  - `agent:<session_id>:<item.id or event_seq>`
  - legacy raw SSE events without `session_id` fall back to `agent:legacy:<...>`
- Added timeline assertions covering:
  - normalized `runtime.completed`
  - normalized `runtime.error`
  - session-scoped agent keys
- Added the minor GET path assertions in `frontend/src/api/client.test.js` for:
  - `/api/sessions/:id/history`
  - `/api/sessions/:id/activity`
  - `/api/sessions/:id/status`

## Follow-up test output

- Command:
  - `cd frontend`
  - `npm test -- --run src/api/client.test.js src/lib/timeline.test.js`
- Result:
  - PASS
  - `2` test files passed
  - `12` tests passed
