# Task 5 Report: Runtime Factory Integration For Codex And Claude

## Status

Complete.

## What Changed

### `src/personal_agent_gateway/runtime_factory.py`

- Integrated `AgentSessionLinkService` into `create_runtime_for_active_session()`.
- Look up the latest matching native session link by:
  - `session_id`
  - `agent_id`
  - `model`
  - options fingerprint
- Pass `upstream_session_id` into:
  - `CodexModelClient(...)`
  - `ClaudeModelClient(...)`
- Switch runtime history mode to:
  - `"full"` when no matching link exists
  - `"latest_user"` when a matching link exists
- Pass `on_upstream_session_id` callback into `AgentRuntime` so the first returned upstream id is recorded back onto the active gateway session.
- Preserve existing app-config fallback behavior for non-session-scoped providers, while allowing default Codex sessions to participate in session-link reuse.

### `src/personal_agent_gateway/app.py`

- Made `/api/chat` ensure there is an active gateway session before runtime creation:
  - `transcript.active_id() or transcript.start_new()`
- This fixes the first-turn gap where runtime creation previously happened before any transcript session existed, which prevented the first returned upstream id from being recorded against the gateway session.

### `tests/test_app.py`

- Added app-level Codex coverage proving:
  1. first chat creates/uses a gateway session,
  2. first response records `agent_session_link`,
  3. second turn reuses `upstream_session_id`,
  4. second turn sends only the latest user message.
- Added app-level Claude coverage proving:
  1. first Claude response records `agent_session_link`,
  2. second Claude turn reuses `upstream_session_id`,
  3. first turn uses full-history bootstrap,
  4. second turn sends only the latest user message.

## Behavioral Outcome

1. First Codex response with `upstream_session_id` now records an `agent_session_link` on the active gateway session.
2. Second Codex turn in the same gateway session passes that upstream id into `CodexModelClient` and uses `history_mode="latest_user"`.
3. First Claude response records a Claude link, and the second Claude turn passes it into `ClaudeModelClient`.
4. Existing sessions without a matching link still bootstrap from full transcript history.
5. Once a matching link exists, full gateway history is no longer replayed for subsequent turns.
6. No frontend behavior changed.

## Notes On Full-History Bootstrap

Claude sessions with an explicit `session_config_set` event still include that system bootstrap content on the first turn before any native link exists. This is expected and consistent with the requirement that unmatched sessions continue to use full-history bootstrap. After the link is recorded, subsequent turns switch to latest-user-only replay.

## Test Evidence

Focused red/green cycle:

```bash
python -m pytest tests/test_app.py::test_chat_reuses_codex_upstream_session_after_first_response tests/test_app.py::test_chat_reuses_claude_upstream_session_after_first_response -q
```

Final verification:

```bash
python -m pytest tests/test_app.py -q
```

Result:

- `35 passed`

## Commit

Planned commit message:

```text
feat(runtime): connect gateway sessions to native cli sessions
```
