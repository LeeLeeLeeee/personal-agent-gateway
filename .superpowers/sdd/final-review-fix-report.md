# Final Review Fix Report

## Finding

Default-source Codex sessions were creating session runtimes from `SessionAgentConfigService.effective_config()`, which returns `model="default"` and `options={}` for sessions without an explicit override. After `/api/chat` started creating an active session before runtime creation, normal Codex chats stopped inheriting app-config model selection for runtime construction, and session-link lookup/recording keyed off the raw default session config instead of the actual client args.

## Fix

- Kept the fix in `src/personal_agent_gateway/runtime_factory.py`.
- Added effective runtime-config resolution before link lookup and client construction.
- Default-source Codex sessions now resolve to:
  - `model=self._config.model`
  - `sandbox=self._config.codex_sandbox`
  - `approval_policy=self._config.codex_approval_policy`
- Explicit session configs still keep their explicit agent/model selection, while link lookup and link recording now use the same effective options passed into the actual client.
- Applied the same effective-options normalization to Claude session runtime construction so link matching stays aligned with real client defaults there as well.

## Regression Coverage

Added `test_chat_uses_app_config_model_for_default_codex_sessions_and_reuses_upstream_id` in `tests/test_app.py`:

- app config sets `model="gpt-5.5"`
- no explicit session config is stored
- first and second Codex chats both instantiate the fake client with `model="gpt-5.5"`
- second chat reuses the recorded upstream session id

## Verification

- Red: `python -m pytest tests/test_app.py::test_chat_uses_app_config_model_for_default_codex_sessions_and_reuses_upstream_id -q`
  - failed with `['default', 'default'] != ['gpt-5.5', 'gpt-5.5']`
- Green: `python -m pytest tests/test_app.py::test_chat_uses_app_config_model_for_default_codex_sessions_and_reuses_upstream_id tests/test_app.py::test_chat_reuses_codex_upstream_session_after_first_response tests/test_app.py::test_chat_reuses_claude_upstream_session_after_first_response -q`
  - `3 passed`
- Final: `python -m pytest tests/test_app.py -q`
  - `36 passed in 10.33s`
