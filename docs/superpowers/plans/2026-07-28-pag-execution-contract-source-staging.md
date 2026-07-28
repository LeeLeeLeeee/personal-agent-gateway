# PAG Execution Contract and Source Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile every Chat, Hook, and Team execution into one enforceable protocol 2.0 specification without silently dropping requested source access.

**Architecture:** PAG separates domain requirements from effective execution values. Explicit external source roots are copied into an integrity-checked isolated staging area; an unbounded `home` scope is never copied and produces an actionable selection error when source evidence is required.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, hashlib, existing FastAPI/httpx client.

## Global Constraints

- Depends on the completed LMG protocol 2.0 plan.
- Chat, Hook, and Team must call the same execution compiler.
- Never stage an entire home directory.
- Never silently remove a source root or required network mode.
- Only explicitly selected existing directories may be staged.
- Source staging must complete before any LMG request.
- Do not launch PAG or LMG as a long-running process from a Codex-managed command.

---

### Task 1: Parse and require LMG protocol 2.0 capabilities

**Files:**
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `tests/test_lmg_client.py`
- Modify: `src/personal_agent_gateway/agents.py`
- Modify: `tests/test_agents.py`

**Interfaces:**
- Produces: `ProviderExecutionCapabilities`
- Produces: `LMGProtocolMismatch`
- Produces: `fetch_execution_capabilities(config) -> dict[str, ProviderExecutionCapabilities]`

- [ ] **Step 1: Write failing parser tests**

Use an LMG response fixture containing:

```python
{
    "protocol_version": "2.0",
    "providers": {
        "codex": {
            "ready": True,
            "execution": {
                "resume": True,
                "external_read_only_roots": False,
                "network_modes": ["unspecified", "denied", "required"],
                "sandbox_modes": ["read-only", "workspace-write"],
            },
        }
    },
}
```

Assert protocol `1.1`, missing protocol, missing execution data, and
`ready=False` cannot be returned as usable capabilities.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_lmg_client.py tests/test_agents.py -q
```

Expected: FAIL because the typed execution capability parser is absent.

- [ ] **Step 3: Implement the types and parser**

Add:

```python
@dataclass(frozen=True)
class ProviderExecutionCapabilities:
    ready: bool
    readiness_error: str | None
    resume: bool
    external_read_only_roots: bool
    network_modes: tuple[str, ...]
    sandbox_modes: tuple[str, ...]
    permission_modes: tuple[str, ...]


class LMGProtocolMismatch(RuntimeError):
    pass
```

Require major version `2`; reject malformed capability collections rather than
using empty defaults. Preserve the existing model catalog fields separately.

- [ ] **Step 4: Wire capability refresh into agent detection**

Store the parsed capabilities with the detected provider record so runtime
factories use the same snapshot shown by the model catalog. A provider that is
installed but not ready remains visible but cannot create an execution client.

- [ ] **Step 5: Run tests**

Run:

```powershell
uv run pytest tests/test_lmg_client.py tests/test_agents.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/lmg_client.py src/personal_agent_gateway/agents.py tests/test_lmg_client.py tests/test_agents.py
git commit -m "feat: require LMG execution protocol 2"
```

### Task 2: Define PAG execution requirements and compiler

**Files:**
- Create: `src/personal_agent_gateway/execution_contract.py`
- Create: `tests/test_execution_contract.py`
- Modify: `src/personal_agent_gateway/space_policies.py`
- Modify: `tests/test_space_policies.py`
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `tests/test_migrations.py`
- Modify: `src/personal_agent_gateway/api/spaces.py`
- Modify: `tests/test_api_spaces.py`
- Modify: `frontend/src/components/organisms/SpacesView/index.jsx`
- Modify: `frontend/src/components/organisms/SpacesView/SpacesView.test.jsx`

**Interfaces:**
- Produces: `ExecutionRequirements`
- Produces: `CompiledExecution`
- Produces: `ExecutionContractError`
- Produces: `compile_execution(requirements, policy, capabilities, staging) -> CompiledExecution`

- [ ] **Step 1: Write failing compiler tests**

Cover:

```python
def test_home_isolated_source_requirement_requires_selection(tmp_path):
    with pytest.raises(ExecutionContractError) as exc:
        compile_execution(
            ExecutionRequirements(
                source_roots=(),
                requires_sources=True,
                workspace_mode="isolated",
                workspace_root=None,
                network="unspecified",
            ),
            home_policy(),
            codex_capabilities(),
            staging=fake_staging(tmp_path),
        )
    assert exc.value.code == "source_scope_requires_selection"
```

Also assert:

- a task that does not require sources may use an empty isolated staging area;
- `read_mode="none"` produces no source requirement;
- a selected external root is staged;
- `home` and `all` cannot be compiled as bounded isolated source access;
- staging failure returns `source_staging_failed`;
- required network rejected by capability data returns
  `unsupported_execution_capability`;
- worktree/full-access roots are not copied;
- no path is silently removed.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_execution_contract.py tests/test_space_policies.py -q
```

Expected: FAIL because the compiler does not exist.

- [ ] **Step 3: Add the exact domain types**

```python
WorkspaceMode = Literal["isolated", "worktree", "full_access"]
NetworkMode = Literal["unspecified", "denied", "required"]


@dataclass(frozen=True)
class ExecutionRequirements:
    source_roots: tuple[Path, ...]
    requires_sources: bool
    workspace_mode: WorkspaceMode
    workspace_root: Path | None
    network: NetworkMode


@dataclass(frozen=True)
class CompiledExecution:
    workspace_root: Path
    read_roots: tuple[Path, ...]
    sandbox: str
    permission_mode: str
    approval_policy: str
    network: NetworkMode
    input_manifest_path: Path | None
    input_manifest_sha256: str | None
```

`ExecutionContractError` carries a stable `code` and a safe diagnostic.

- [ ] **Step 4: Make no-source access explicit**

Extend `ReadMode` and `SpacePolicyRequest` with `"none"`. For `none`,
`read_path` must be null. Change new-install global defaults to:

```python
read_mode="none"
read_path=None
write_mode="isolated"
```

Add migration 16, `explicit-no-source-space`, with:

```sql
update space_policies
set read_mode = 'none', read_path = null
where read_mode = 'home' and write_mode = 'isolated';
```

This preserves the old effective CLI behavior, which already supplied no read
root, while making it visible. Do not rewrite `home` policies paired with
worktree or full-access modes.

Add a “No source access” option to `SpacesView`. Hide the path input for
`none` and `all`; retain current home/selected presentation. Update API and UI
tests for the new default and migration. Every newly constructed isolated
policy form—global, persona, and team—must initialize to `none`; switching an
existing worktree/full-access policy to isolated must preserve an explicit
bounded selection or require the operator to choose `none`.

Every caller derives the domain requirement with the same rule:

```python
requires_sources = space_policy.read_mode != "none"
```

Callers must not override that value independently. `selected` resolves
`source_roots` to exactly the canonical `read_path`; `none` resolves it to an
empty tuple. `home` and `all` remain explicit unbounded policies and are
handled by the workspace-mode rules below.

- [ ] **Step 5: Replace `cli_read_roots` semantics**

Remove the rule that maps `home` outside workspace to `[]`. Keep SPACE policy
resolution, but make the compiler decide:

- `none` supplies no source roots;
- `selected` supplies exactly the canonical selected directory;
- `home/all + isolated + requires_sources` raises
  `source_scope_requires_selection`;
- no-source executions have no source roots;
- worktree/full-access use the configured workspace directly.

- [ ] **Step 6: Run tests**

Run:

```powershell
uv run pytest tests/test_execution_contract.py tests/test_space_policies.py tests/test_migrations.py tests/test_api_spaces.py -q
Set-Location frontend
npm test -- SpacesView
Set-Location ..
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/personal_agent_gateway/execution_contract.py src/personal_agent_gateway/space_policies.py src/personal_agent_gateway/migrations.py src/personal_agent_gateway/api/spaces.py tests/test_execution_contract.py tests/test_space_policies.py tests/test_migrations.py tests/test_api_spaces.py frontend/src/components/organisms/SpacesView
git commit -m "feat: compile enforceable SPACE executions"
```

### Task 3: Implement deterministic source staging and integrity checks

**Files:**
- Create: `src/personal_agent_gateway/source_staging.py`
- Create: `tests/test_source_staging.py`
- Modify: `src/personal_agent_gateway/team_results.py`

**Interfaces:**
- Produces: `SourceStager.stage(roots, workspace_root) -> StagedInputs`
- Produces: `SourceStager.verify(staged_inputs) -> None`
- Produces: `InputSnapshotModified`

- [ ] **Step 1: Write failing staging tests**

Create a small source tree and assert:

```python
staged = stager.stage((source,), workspace)
assert (workspace / "_inputs" / "01-source" / "package.json").is_file()
assert staged.manifest_path == workspace / "_inputs" / "manifest.json"
assert staged.read_roots == (workspace / "_inputs",)
stager.verify(staged)
```

Then modify a staged file and assert `InputSnapshotModified`. Also cover
symlinks, `.env*`, VCS directories, duplicate roots, missing roots, and a source
equal to or nested inside the workspace.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_source_staging.py -q
```

Expected: FAIL because `SourceStager` does not exist.

- [ ] **Step 3: Implement staging**

Rules:

- reject a home-directory root by comparing it to `Path.home().resolve()`;
- canonicalize and deduplicate roots;
- copy to `_inputs/{ordinal:02d}-{sanitized_name}`;
- skip symlinks, `.git`, `.hg`, `.svn`, dependency caches, virtualenvs, and
  `.env*`;
- write sorted JSON entries with origin, staged relative path, size, SHA-256;
- write the manifest atomically through a sibling temporary file and
  `Path.replace`;
- compute and return the manifest SHA-256.

The verification scan excludes `_inputs/manifest.json` itself but no other
unlisted file below `_inputs`.

Reuse a shared file-sensitivity helper extracted from `team_results.py`; do not
duplicate the exclusion list.

- [ ] **Step 4: Implement integrity verification**

Re-hash every manifest entry and fail on new, changed, deleted, symlinked, or
non-regular files below `_inputs`. Do not silently repair the snapshot.

- [ ] **Step 5: Run tests**

Run:

```powershell
uv run pytest tests/test_source_staging.py tests/test_team_results.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/source_staging.py src/personal_agent_gateway/team_results.py tests/test_source_staging.py tests/test_team_results.py
git commit -m "feat: stage immutable execution inputs"
```

### Task 4: Use one execution factory for Chat and Hook runtimes

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `tests/test_remote_model_client.py`

**Interfaces:**
- Consumes: `compile_execution`
- Produces: `ExecutionContextFactory.for_session(...)`
- Produces: protocol 2.0 request body

- [ ] **Step 1: Replace old omission tests with failing contract tests**

Delete expectations that home/selected external paths become empty read roots.
Add assertions that:

- a no-source Chat builds an isolated empty context;
- a source-requiring Hook with an explicit selected root receives `_inputs`;
- unselected source-required execution fails before `HttpModelClient.complete`;
- the wire body contains `network`.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_runtime_factory_headless.py tests/test_remote_model_client.py -q
```

Expected: FAIL on old factories and request shape.

- [ ] **Step 3: Add `ExecutionContextFactory`**

The factory receives the resolved SPACE policy, provider capability snapshot,
consumer workspace, `requires_sources`, and network mode. It calls the compiler
and creates `HttpModelClient` only from `CompiledExecution`.

The factory, not the Chat or Hook caller, calculates `requires_sources` and
`source_roots` from the frozen SPACE policy using Task 2's exact rule. This
keeps no-source, selected-source, and unbounded-source behavior identical
across both entry points.

Both session and headless Hook paths must call this factory. Remove direct
construction of `workspace_root`, `read_roots`, sandbox, and permission mode
from `_remote_client`.

- [ ] **Step 4: Send the protocol 2.0 execution shape**

`HttpModelClient` sends:

```python
"execution": {
    "workspace_root": str(compiled.workspace_root),
    "read_roots": [str(path) for path in compiled.read_roots],
    "sandbox": compiled.sandbox,
    "approval_policy": compiled.approval_policy,
    "permission_mode": compiled.permission_mode,
    "network": compiled.network,
}
```

Keep provider terminal parsing unchanged.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_runtime_factory_headless.py tests/test_remote_model_client.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/remote_model_client.py tests/test_runtime_factory_headless.py tests/test_remote_model_client.py
git commit -m "feat: share protocol 2 execution factory"
```

### Task 5: Use the shared execution factory for Team agents

**Files:**
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `tests/test_app_team_factory.py`
- Modify: `src/personal_agent_gateway/space_policies.py`
- Modify: `tests/test_space_policies.py`

**Interfaces:**
- Consumes: `ExecutionContextFactory`
- Produces: Team agent `HttpModelClient` with compiled execution

- [ ] **Step 1: Write failing Team factory tests**

Assert:

- explicit selected source is staged and passed inside workspace;
- `home + isolated + source required` raises
  `source_scope_requires_selection`;
- sibling artifact root is never exposed to LMG;
- full-access and worktree paths remain direct;
- Codex required network is accepted;
- Claude required network is rejected before the model call.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_app_team_factory.py tests/test_space_policies.py -q
```

Expected: FAIL against `_team_cli_read_roots`.

- [ ] **Step 3: Replace `_team_cli_read_roots`**

Inject `ExecutionContextFactory` into `_team_model_factory`. Team planning and
worker clients both use the frozen run SPACE snapshot and the same staged
workspace. Do not add the system artifact root to `read_roots`.

Team uses the same factory-owned derivation as Chat and Hook:
`read_mode != "none"` means sources are required, and only `selected` supplies
an external source root for isolated staging. Team-specific code must not
recalculate or weaken this rule.

Store the compiled execution metadata and staged manifest hash through
`TeamRunService` for the current cycle; the Team completion plan adds the
columns and final persistence.

- [ ] **Step 4: Run tests**

Run:

```powershell
uv run pytest tests/test_app_team_factory.py tests/test_space_policies.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the PAG backend suite**

Run:

```powershell
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/space_policies.py tests/test_app_team_factory.py tests/test_space_policies.py
git commit -m "feat: compile Team execution contexts"
```
