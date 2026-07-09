# Task 3 Report: CLI Resume Command Builders

## Status
Completed.

## Scope
Implemented native resume command selection for `CodexModelClient` and `ClaudeModelClient` in `src/personal_agent_gateway/model_client.py`, with tests in `tests/test_model_client.py`.

## What Changed
- Added optional `upstream_session_id` constructor parameters to both model clients.
- Split Codex command construction into start/resume paths.
- Kept Codex start behavior intact for existing callers.
- Built Codex resume commands without unsupported start-only flags.
- Added `cwd=str(self._workspace_root)` to Codex subprocess creation.
- Added Claude resume command support with `--resume <session_id>`.
- Added focused tests for both resume command builders.

## Verification
- `python -m pytest tests/test_model_client.py::test_codex_client_builds_resume_command_when_upstream_session_exists tests/test_model_client.py::test_claude_client_builds_resume_command_when_upstream_session_exists -q`
- `python -m pytest tests/test_model_client.py -q`

## Fix
- Moved `CodexModelClient`'s new `effort` and `upstream_session_id` parameters behind `*` so existing positional arguments still map through `profile`, `timeout_seconds`, and `on_event`.
- Added a regression test that constructs `CodexModelClient` with the old positional shape and confirms `profile` still emits `--profile`, not `model_reasoning_effort`.
- Exact verification result: `python -m pytest tests/test_model_client.py -q` -> `15 passed in 0.63s`

## Commit
- `c8f9e81` - `feat(model): resume native cli sessions`
