# Isolated Workspace Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every isolated PAG execution gives LMG an existing workspace directory without creating configured direct-access paths.

**Architecture:** `ExecutionContextFactory.for_session()` owns preparation of PAG-generated isolated workspaces before cache lookup and execution compilation. LMG remains validation-only, while full-access and worktree paths remain caller-prepared.

**Tech Stack:** Python 3.13, pathlib, pytest

## Global Constraints

- Create directories only when `policy.write_mode == "isolated"`.
- Preserve `full_access` and `worktree` path validation behavior.
- Convert workspace creation failures to `ExecutionContractError` with code `invalid_execution_path`.
- Do not expose the underlying filesystem error in the diagnostic.
- Run only focused execution-context tests.

---

### Task 1: Prepare isolated execution workspaces

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py:35-80`
- Test: `tests/test_runtime_factory_headless.py`

**Interfaces:**
- Consumes: `ExecutionContextFactory.for_session(policy, capabilities, consumer_workspace, network="unspecified")`
- Produces: an existing `CompiledExecution.workspace_root` for isolated writes, or `ExecutionContractError("invalid_execution_path", ...)` when preparation fails

- [x] **Step 1: Write the failing mode-boundary test**

Add a parameterized test that calls the real execution context factory for
`read_mode="all"` and `read_mode="none"`. For each case, assert that an
isolated consumer workspace is created and that a missing configured
full-access workspace is not created:

```python
@pytest.mark.parametrize("read_mode", ["all", "none"])
def test_execution_context_prepares_only_isolated_workspace(
    tmp_path: Path,
    read_mode: str,
) -> None:
    contexts = ExecutionContextFactory()
    capabilities = _AgentRegistry().get("codex").execution_capabilities
    isolated = tmp_path / f"{read_mode}-isolated"
    direct = tmp_path / f"{read_mode}-direct"

    compiled = contexts.for_session(
        _policy(read_mode=read_mode, read_path=None),
        capabilities,
        isolated,
    )
    contexts.for_session(
        _policy(
            read_mode=read_mode,
            read_path=None,
            write_mode="full_access",
            workspace_path=direct,
        ),
        capabilities,
        tmp_path / "unused-consumer",
    )

    assert compiled.workspace_root == isolated.resolve()
    assert isolated.is_dir()
    assert not direct.exists()
```

- [x] **Step 2: Write the failing preparation-error test**

Patch `Path.mkdir` at the filesystem boundary to raise `OSError`, then assert
that isolated preparation reports only the stable execution-contract error:

```python
def test_isolated_workspace_creation_failure_has_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_mkdir(*_args, **_kwargs) -> None:
        raise OSError("sensitive filesystem detail")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(ExecutionContractError) as error:
        ExecutionContextFactory().for_session(
            _policy(read_mode="all", read_path=None),
            _AgentRegistry().get("codex").execution_capabilities,
            tmp_path / "isolated",
        )

    assert error.value.code == "invalid_execution_path"
    assert str(error.value) == "Failed to prepare isolated workspace"
```

- [x] **Step 3: Run the tests to verify RED**

Run:

```powershell
& 'C:\Users\Administrator\playground\personal-agent-gateway\.venv\Scripts\python.exe' `
  -m pytest -q tests\test_runtime_factory_headless.py `
  -k "prepares_only_isolated_workspace or isolated_workspace_creation_failure"
```

Expected: three failures because isolated workspaces are not created and
`OSError` is not translated.

- [x] **Step 4: Implement the minimal isolated preparation**

At the start of `ExecutionContextFactory.for_session()`, before cache lookup,
create only the isolated consumer workspace and translate `OSError`:

```python
if policy.write_mode == "isolated":
    try:
        consumer_workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExecutionContractError(
            "invalid_execution_path",
            "Failed to prepare isolated workspace",
        ) from exc
```

- [x] **Step 5: Run the focused tests to verify GREEN**

Run:

```powershell
& 'C:\Users\Administrator\playground\personal-agent-gateway\.venv\Scripts\python.exe' `
  -m pytest -q tests\test_runtime_factory_headless.py
```

Expected: all tests in the focused file pass.

- [x] **Step 6: Inspect the diff and commit**

Run:

```powershell
git diff --check
git diff -- src/personal_agent_gateway/runtime_factory.py tests/test_runtime_factory_headless.py
git add -- src/personal_agent_gateway/runtime_factory.py tests/test_runtime_factory_headless.py docs/superpowers/plans/2026-07-30-isolated-workspace-preparation.md
git commit -m "fix(runtime): isolated workspace 실행 전 생성"
```
