# CLI SPACE Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep CLI execution requests compatible with LMG's workspace-bound read-root contract.

**Architecture:** Extract a small PAG helper that converts an effective SPACE policy into CLI-safe read roots. Reuse it for session and headless runtimes, retaining the established Team containment rule. Preserve LMG's stable invalid-execution code at the client boundary.

**Tech Stack:** Python 3.12, FastAPI/Pydantic application code, pytest, Go LMG HTTP contract.

## Global Constraints

- Do not relax LMG's external read-root security validation.
- Do not stage or copy external files.
- Default `home` outside an isolated workspace emits no CLI read root.
- Selected external paths fail before a remote request.

---

### Task 1: Define and test CLI read-root normalization

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Test: `tests/test_runtime_factory_headless.py`

**Interfaces:**
- Produces: `_cli_read_roots(workspace_root: Path, policy: SpacePolicy | None) -> list[Path]`
- Consumes: `SpacePolicy.read_mode` and `SpacePolicy.read_path`

- [ ] **Step 1: Write failing tests**

```python
assert client._execution["read_roots"] == []  # home outside isolated workspace
with pytest.raises(ValueError, match="inside the workspace"):
    factory.create_runtime_for_session(session_id)
```

- [ ] **Step 2: Run the focused test file and verify RED**

Run: `python -m pytest -q tests/test_runtime_factory_headless.py`

- [ ] **Step 3: Implement the minimal shared helper and use it for session and Hook runtime creation**

```python
def _cli_read_roots(workspace_root, policy):
    if policy is None or not policy.read_path:
        return []
    read_root = Path(policy.read_path).resolve()
    try:
        read_root.relative_to(workspace_root.resolve())
    except ValueError:
        if policy.read_mode == "home":
            return []
        raise ValueError("CLI read path must be inside the workspace")
    return [read_root]
```

- [ ] **Step 4: Run the focused test file and verify GREEN**

Run: `python -m pytest -q tests/test_runtime_factory_headless.py`

### Task 2: Preserve LMG validation errors

**Files:**
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Test: `tests/test_remote_model_client.py`

**Interfaces:**
- Produces: `RemoteRunFailedError("invalid_execution_path", "remote_invalid_execution_path")`
- Consumes: LMG JSON error code `invalid_execution_path`

- [ ] **Step 1: Write a failing 422 mapping test**

```python
assert raised.value.code == "invalid_execution_path"
assert raised.value.diagnostic == "remote_invalid_execution_path"
```

- [ ] **Step 2: Run the focused mapping test and verify RED**

Run: `python -m pytest -q tests/test_remote_model_client.py -k invalid_execution_path`

- [ ] **Step 3: Add the one stable 422 mapping**

- [ ] **Step 4: Run the focused mapping test and verify GREEN**

Run: `python -m pytest -q tests/test_remote_model_client.py -k invalid_execution_path`

### Task 3: Verify affected PAG behavior

**Files:**
- Test: `tests/test_runtime_factory_headless.py`, `tests/test_remote_model_client.py`, `tests/test_app_team_factory.py`

- [ ] **Step 1: Run all affected tests**

Run: `python -m pytest -q tests/test_runtime_factory_headless.py tests/test_remote_model_client.py tests/test_app_team_factory.py`

- [ ] **Step 2: Build frontend package**

Run: `npm run build`

- [ ] **Step 3: Commit PAG changes after verification**

```bash
git add src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/remote_model_client.py tests docs/superpowers
git commit -m "fix: normalize CLI SPACE execution context"
```
