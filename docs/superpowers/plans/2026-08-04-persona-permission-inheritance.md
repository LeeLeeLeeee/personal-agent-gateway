# Persona Permission Inheritance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the persona's requested permission mode in every Team Run execution, and fail before a model call when the selected provider cannot support it.

**Architecture:** Add the requested permission mode to the execution requirements passed from the Team Run factory through `ExecutionContextFactory` into `compile_execution`. The compiler validates the original value against provider capabilities and emits it unchanged in the LMG payload; workspace mode remains responsible only for workspace and sandbox choices.

**Tech Stack:** Python 3, pytest, FastAPI Team Run runtime, LMG HTTP client contract.

## Global Constraints

- `default_options.permission_mode` is the sole source of the provider permission mode.
- Do not downgrade or substitute a persona permission mode.
- A provider advertising permission modes must reject unsupported persona modes before a model call.
- A persona with no configured mode continues to receive an empty `permission_mode` field.
- A persona with a configured mode fails before model invocation when the provider advertises no permission capability or lacks that mode.
- Preserve workspace, sandbox, read-root, approval-policy, and network behavior.

---

## File Structure

- Modify: `src/personal_agent_gateway/execution_contract.py` — carry and validate requested permission mode.
- Modify: `src/personal_agent_gateway/runtime_factory.py` — accept and forward the persona option into execution compilation.
- Modify: `src/personal_agent_gateway/app.py` — pass the selected agent's persona permission option to the execution context.
- Modify: `tests/test_execution_contract.py` — prove compiler preservation and fail-fast behavior.
- Modify: `tests/test_app_team_factory.py` — prove Team Run factory forwards persona configuration into the LMG payload.

### Task 1: Preserve permission mode in the execution contract

**Files:**
- Modify: `src/personal_agent_gateway/execution_contract.py:11-72`
- Modify: `src/personal_agent_gateway/runtime_factory.py:36-89`
- Test: `tests/test_execution_contract.py:48-79`

**Interfaces:**
- Consumes: `permission_mode: str` from the caller.
- Produces: `ExecutionRequirements.permission_mode: str` and unchanged `CompiledExecution.permission_mode: str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_persona_permission_mode_is_preserved_for_supported_provider(tmp_path: Path) -> None:
    compiled = compile_execution(
        _requirements(requires_sources=False, permission_mode="plan"),
        _policy("none", None),
        _capabilities(permission_modes=("default", "acceptEdits", "plan")),
        FakeStaging(tmp_path / "run"),
    )

    assert compiled.permission_mode == "plan"


def test_unsupported_persona_permission_mode_fails_before_execution(tmp_path: Path) -> None:
    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(requires_sources=False, permission_mode="bypassPermissions"),
            _policy("none", None),
            _capabilities(permission_modes=("default", "acceptEdits", "plan")),
            FakeStaging(tmp_path / "run"),
        )

    assert error.value.code == "unsupported_execution_capability"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_execution_contract.py -q`

Expected: FAIL because `_requirements()` and `ExecutionRequirements` do not accept `permission_mode`.

- [ ] **Step 3: Implement the minimal contract change**

```python
@dataclass(frozen=True)
class ExecutionRequirements:
    source_roots: tuple[Path, ...]
    requires_sources: bool
    workspace_mode: WorkspaceMode
    workspace_root: Path | None
    network: NetworkMode
    permission_mode: str


if capabilities.permission_modes and requirements.permission_mode not in capabilities.permission_modes:
    raise ExecutionContractError(
        "unsupported_execution_capability",
        "The selected provider does not support the persona permission mode",
    )
permission_mode = requirements.permission_mode if capabilities.permission_modes else ""
```

Extend `ExecutionContextFactory.for_session()` with `permission_mode: str = ""`, include it in the cache key, and pass it to `ExecutionRequirements`. Validate every non-empty requested mode against the provider capability list, then delete the workspace-mode-derived `requested_permission` branch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_execution_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/execution_contract.py src/personal_agent_gateway/runtime_factory.py tests/test_execution_contract.py
git commit -m "fix: 페르소나 권한을 실행 계약에 보존"
```

### Task 2: Forward persona permission through the Team Run factory

**Files:**
- Modify: `src/personal_agent_gateway/app.py:680-704`
- Test: `tests/test_app_team_factory.py:227-242`

**Interfaces:**
- Consumes: `agent.persona_snapshot["default_options"]["permission_mode"]`.
- Produces: `HttpModelClient._execution["permission_mode"]` equal to that value when the provider supports it.

- [ ] **Step 1: Write the failing tests**

```python
def test_factory_preserves_supported_claude_persona_permission_mode(tmp_path):
    client = _factory(_config(tmp_path))(
        _agent("claude", options={"permission_mode": "plan"})
    )

    assert client._execution["permission_mode"] == "plan"


def test_factory_rejects_unsupported_claude_persona_permission_mode(tmp_path):
    with pytest.raises(ExecutionContractError) as error:
        _factory(_config(tmp_path))(
            _agent("claude", options={"permission_mode": "bypassPermissions"})
        )

    assert error.value.code == "unsupported_execution_capability"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_app_team_factory.py -q`

Expected: the supported-mode assertion fails because the factory still sends `acceptEdits`; the unsupported-mode case does not raise.

- [ ] **Step 3: Implement the minimal factory change**

```python
compiled = contexts.for_session(
    space_policy,
    capabilities,
    workspace_root,
    network=str(options.get("network") or "unspecified"),
    permission_mode=str(options.get("permission_mode") or ""),
)
```

Do not pass a configured permission option through any separate path; the `CompiledExecution` payload remains the single serialized value.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_execution_contract.py tests/test_app_team_factory.py -q`

Expected: PASS.

- [ ] **Step 5: Run repository verification**

Run: `pytest -q`

Expected: PASS. If the suite exceeds the command timeout, report the exact timeout and rerun the two changed test modules to retain focused verification evidence.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/app.py tests/test_app_team_factory.py
git commit -m "fix: 팀 실행에 페르소나 권한 전달"
```

## Self-Review

- Spec coverage: Task 1 preserves and validates the persona mode; Task 2 forwards it from the Team Run factory; both required supported and unsupported cases are covered.
- Placeholder scan: no unresolved placeholders, implicit error handling, or undefined task references remain.
- Type consistency: `permission_mode: str` is the same field name from persona option through `ExecutionRequirements`, `CompiledExecution`, and the LMG payload.
