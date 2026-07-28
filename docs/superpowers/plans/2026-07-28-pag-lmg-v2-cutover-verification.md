# PAG–LMG Protocol 2.0 Cutover and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut PAG and LMG over atomically to protocol 2.0 and prove the original false-completion and Windows sandbox failures cannot recur unnoticed.

**Architecture:** Shared JSON fixtures and live opt-in contract tests verify both repositories. The integrated launcher fails closed on protocol/readiness mismatch; a user-run native Windows smoke script verifies the real sandbox without starting services from Codex.

**Tech Stack:** Python/pytest/httpx, Go tests, PowerShell, existing PAG integrated launcher.

## Global Constraints

- Depends on all three implementation plans dated 2026-07-28.
- Protocol 1.1 and 2.0 services must never run together.
- Intake remains closed until protocol, readiness, regression, and artifact gates pass.
- Native Windows long-running services are started by the user in a normal PowerShell window.
- Automated checks may inspect already-running services but may not launch them from Codex.
- Rollback restores both service versions and the pre-migration PAG database together.

---

### Task 1: Add shared protocol fixtures and repository contract tests

**Files:**
- Create: `tests/fixtures/lmg-protocol-v2/models-ready.json`
- Create: `tests/fixtures/lmg-protocol-v2/models-not-ready.json`
- Create: `tests/fixtures/lmg-protocol-v2/run-request.json`
- Create: `tests/test_lmg_protocol_v2_contract.py`
- Create: `../local-model-gateway/internal/httpapi/testdata/models-ready.json`
- Create: `../local-model-gateway/internal/httpapi/testdata/run-request.json`
- Modify: `../local-model-gateway/internal/httpapi/models_test.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`

**Interfaces:**
- Verifies: protocol 2.0 JSON field names and values in both repositories

- [ ] **Step 1: Add exact canonical fixtures**

The ready fixture must include protocol `2.0`, Codex readiness, and execution
capabilities. The run request must include provider, model, messages,
correlation IDs, and:

```json
{
  "execution": {
    "workspace_root": "C:\\workspace",
    "read_roots": ["C:\\workspace\\_inputs"],
    "sandbox": "workspace-write",
    "approval_policy": "never",
    "network": "required"
  }
}
```

Use forward-neutral JSON escaping; tests normalize platform paths before
validation.

- [ ] **Step 2: Write failing PAG fixture tests**

Assert PAG accepts the ready fixture, rejects the not-ready fixture for
execution, rejects protocol `1.1`, and serializes a request matching the
canonical field set.

- [ ] **Step 3: Write failing LMG fixture tests**

Unmarshal the same logical fixture shapes into LMG structs, exercise the
handlers, and compare normalized JSON keys and capability values.

- [ ] **Step 4: Run both focused suites**

```powershell
uv run pytest tests/test_lmg_protocol_v2_contract.py -q
Set-Location ..\local-model-gateway
go test ./internal/httpapi
Set-Location ..\personal-agent-gateway
```

Expected: PASS.

- [ ] **Step 5: Commit in each repository**

PAG:

```powershell
git add tests/fixtures/lmg-protocol-v2 tests/test_lmg_protocol_v2_contract.py
git commit -m "test: lock LMG protocol 2 contract"
```

LMG:

```powershell
Set-Location ..\local-model-gateway
git add internal/httpapi/testdata internal/httpapi/models_test.go internal/httpapi/runs_test.go
git commit -m "test: lock execution protocol 2 fixtures"
Set-Location ..\personal-agent-gateway
```

### Task 2: Fail the integrated launcher on readiness or protocol mismatch

**Files:**
- Modify: `scripts/start_local_runtime.ps1`
- Create: `tests/test_start_local_runtime_script.py`
- Modify: `README.md`

**Interfaces:**
- Produces: launcher validation of `/readyz` and `/v1/models`

- [ ] **Step 1: Write failing static launcher tests**

Assert the script:

- waits for LMG `/readyz`;
- requests authenticated `/v1/models`;
- requires `protocol_version -eq "2.0"`;
- stops the newly started LMG child if validation fails;
- does not start PAG after failed validation;
- prints the stable readiness/protocol code, not secrets.

- [ ] **Step 2: Confirm failure**

Run:

```powershell
uv run pytest tests/test_start_local_runtime_script.py -q
```

Expected: FAIL because the launcher does not enforce protocol 2.0.

- [ ] **Step 3: Implement the gate**

After LMG starts, poll readiness with the existing bounded timeout. Then fetch
models with the configured bearer token and require:

```powershell
if ($models.protocol_version -ne "2.0") {
    throw "LMG protocol mismatch: expected 2.0"
}
```

Require at least the selected provider to report `ready = $true`. PAG starts
only after both checks.

- [ ] **Step 4: Run static tests**

```powershell
uv run pytest tests/test_start_local_runtime_script.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add scripts/start_local_runtime.ps1 tests/test_start_local_runtime_script.py README.md
git commit -m "fix: gate local runtime on protocol readiness"
```

### Task 3: Reproduce the `fe8fa463` false-completion scenario

**Files:**
- Create: `tests/test_team_run_false_completion_regression.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_team_results.py`

**Interfaces:**
- Verifies: explanatory failure text cannot complete a task/run
- Verifies: undeclared Windows cache files are not packaged

- [ ] **Step 1: Build the regression fixture**

Use fake model clients with the observed sequence:

1. planner returns four required tasks with acceptance;
2. workers emit failed shell activity and explanatory text rather than valid
   `TaskOutcome`;
3. QA returns structured `failed` with `Not Ready`;
4. workspace contains
   `%SystemDrive%/ProgramData/Microsoft/Windows/Caches/example.db`;
5. no declared integrated Markdown exists.

- [ ] **Step 2: Write the failing assertions**

Assert:

```python
assert run.status in {"blocked", "failed"}
assert all(task.status != "completed" for task in required_tasks)
assert "%SystemDrive%" not in json.dumps(result_manifest)
assert not (artifact_root / "workspace.zip").exists()
```

Also assert the cycle objective, provider diagnostics, and acceptance reason are
present in `run-result.json`.

- [ ] **Step 3: Run the regression**

```powershell
uv run pytest tests/test_team_run_false_completion_regression.py -q
```

Expected: PASS only after all prior plans are implemented.

- [ ] **Step 4: Run adjacent Team tests**

```powershell
uv run pytest tests/test_team_runtime.py tests/test_team_results.py tests/test_team_run_false_completion_regression.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_team_run_false_completion_regression.py tests/test_team_runtime.py tests/test_team_results.py
git commit -m "test: prevent false Team completion regression"
```

### Task 4: Add opt-in live integration verification

**Files:**
- Create: `scripts/verify_local_runtime_v2.ps1`
- Create: `tests/test_verify_local_runtime_v2_script.py`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: already-running PAG on 8787 and LMG on 8788
- Produces: non-destructive readiness and execution verification report

- [ ] **Step 1: Write static script tests**

Assert the script:

- never starts or stops either server;
- verifies listener health and protocol 2.0;
- verifies Codex provider readiness;
- verifies the supplied Team already has an explicit selected source policy;
- creates a temporary smoke input only below that selected source root;
- creates a triggered Team run and queues exactly one cycle;
- checks the declared artifact and terminal status;
- scans returned agent workspaces for a literal `%SystemDrive%` directory;
- exits non-zero on any failed gate.

- [ ] **Step 2: Implement the verification script**

Parameters:

```powershell
param(
    [string]$PagBaseUrl = "http://127.0.0.1:8787",
    [string]$LmgBaseUrl = "http://127.0.0.1:8788",
    [Parameter(Mandatory = $true)]
    [string]$LocalToken,
    [Parameter(Mandatory = $true)]
    [string]$PagSessionToken,
    [Parameter(Mandatory = $true)]
    [string]$TeamId,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot
)
```

Use `Authorization: Bearer $LocalToken` only for LMG and
`Cookie: agent_session=$PagSessionToken` only for PAG. Never echo either
secret. Use `Invoke-RestMethod` with finite timeouts.

Before writing anything, resolve `$SourceRoot`, GET `/api/spaces`, and require
the matching `teams` entry for `$TeamId` to have `read_mode == "selected"` and
the same canonical `read_path`. Do not change SPACE policy in this script.
Create only `_pag-lmg-v2-smoke-{guid}/request.json` below that root and remove
only that resolved child directory in `finally`.

Take an artifact-ID snapshot from `GET /api/artifacts`, then execute the exact
PAG sequence:

```text
POST /api/team-runs
{"team_id":TeamId,"goal":"Protocol 2 smoke verification","execution_policy":"triggered","auto_repeat_count":null,"auto_interval_minutes":null}

POST /api/team-runs/{run_id}/cycle-requests
{"instruction":"Read _inputs/**/request.json and publish the declared artifacts/protocol-v2-smoke/result.json after verification.","client_request_id":"protocol-v2-smoke-{guid}","previous_cycle_id":null}
```

Poll `GET /api/team-runs/{run_id}/detail` until the created cycle reaches one
of `completed`, `completed_with_failures`, `blocked`, `failed`, or `canceled`,
with a fixed overall deadline. Success requires `completed`, all required
tasks accepted, and a newly listed artifact whose metadata carries the run ID
and declared relative path. Resolve every returned agent `workspace_path`,
require it to remain under PAG's configured workspace root exposed by the
detail/diagnostic contract, and scan it for a literal `%SystemDrive%`
directory. A blocked or failed cycle must print only stable codes/statuses and
exit non-zero.

- [ ] **Step 3: Run static tests**

```powershell
uv run pytest tests/test_verify_local_runtime_v2_script.py -q
```

Expected: PASS.

- [ ] **Step 4: Update instructions**

Document that the user starts the integrated runtime in normal PowerShell, then
runs:

```powershell
$lmgToken = Read-Host "LMG local token"
$pagSessionToken = Read-Host "PAG agent_session value"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\verify_local_runtime_v2.ps1" -LocalToken $lmgToken -PagSessionToken $pagSessionToken -TeamId "protocol-v2-smoke" -SourceRoot "C:\pag-smoke-source"
```

The smoke Team must be configured in advance with `selected` read mode pointing
to `SourceRoot`. Do not place real tokens in documentation, shell history, or
logs; prefer prompting into variables in the user's PowerShell session.

- [ ] **Step 5: Commit**

```powershell
git add scripts/verify_local_runtime_v2.ps1 tests/test_verify_local_runtime_v2_script.py AGENTS.md README.md
git commit -m "test: add local protocol 2 smoke verification"
```

### Task 5: Update operational and architecture documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-cli-space-contract-design.md`
- Modify: `docs/superpowers/specs/2026-07-26-pag-lmg-local-integration-hardening-design.md`
- Modify: `docs/knowledge/gateway-architecture-guide.md`
- Modify: `docs/knowledge/2026-07-15-operations-diagnostics-guide.md`
- Modify: `README.md`
- Modify: `../local-model-gateway/README.md`

**Interfaces:**
- Documents: supersession, blocked-state diagnosis, rollback

- [ ] **Step 1: Mark superseded behavior**

At the top of the old CLI SPACE design, add a status note pointing to the
approved 2026-07-28 design. Do not rewrite historical decisions.

In the integration-hardening design, note that protocol 1.1 process
termination remains historical and protocol 2.0 adds semantic acceptance in
PAG.

- [ ] **Step 2: Document operator diagnosis**

Include a lookup table:

| Code/status | Operator action |
|---|---|
| `source_scope_requires_selection` | Select an explicit source root |
| `source_staging_failed` | Check source path and filesystem permissions |
| `unsupported_execution_capability` | Change provider or requirement |
| `provider_not_ready` | Review native sandbox readiness/logs |
| `invalid_task_outcome` | Retry task after prompt/provider review |
| `input_snapshot_modified` | Treat run as blocked; recreate staging |
| `artifact_publication_failed` | Check artifact storage and retry |

- [ ] **Step 3: Document atomic rollback**

Specify:

1. close intake;
2. stop both services from the user's PowerShell window;
3. restore the pre-migration database backup;
4. restore both PAG and LMG revisions;
5. restart LMG then PAG;
6. verify the restored protocol before reopening intake.

- [ ] **Step 4: Check docs**

```powershell
git diff --check
Set-Location ..\local-model-gateway
git diff --check
Set-Location ..\personal-agent-gateway
```

Expected: no output.

- [ ] **Step 5: Commit in each repository**

PAG:

```powershell
git add docs/superpowers/specs/2026-07-27-cli-space-contract-design.md docs/superpowers/specs/2026-07-26-pag-lmg-local-integration-hardening-design.md docs/knowledge/gateway-architecture-guide.md docs/knowledge/2026-07-15-operations-diagnostics-guide.md README.md
git commit -m "docs: describe protocol 2 execution operations"
```

LMG:

```powershell
Set-Location ..\local-model-gateway
git add README.md
git commit -m "docs: describe protocol 2 readiness operations"
Set-Location ..\personal-agent-gateway
```

### Task 6: Execute release gates

**Files:**
- No planned source changes; fix only failures caused by protocol 2.0 work.

**Interfaces:**
- Verifies: complete atomic release

- [ ] **Step 1: Stop intake and back up state**

Use the existing emergency-stop/intake controls. Record the current PAG and
LMG commit SHAs and create a verified PAG backup before migration.

- [ ] **Step 2: Run LMG verification**

```powershell
Set-Location ..\local-model-gateway
gofmt -w (Get-ChildItem -Path internal,cmd -Recurse -Filter *.go | ForEach-Object { $_.FullName })
go test ./...
go vet ./...
git diff --check
Set-Location ..\personal-agent-gateway
```

Expected: all commands exit 0.

- [ ] **Step 3: Run PAG verification**

```powershell
uv run pytest -q
uv run ruff check .
Set-Location frontend
npm test
npm run build
Set-Location ..
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Have the user start the integrated runtime**

Provide, but do not execute from Codex:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\playground\personal-agent-gateway\scripts\start_local_runtime.ps1"
```

- [ ] **Step 5: Run the read-only/live smoke verifier**

After the user confirms the services are running, run or ask the user to run
`scripts/verify_local_runtime_v2.ps1` with a disposable workspace. Repeat once
after a full user-controlled service restart.

Expected both times:

- protocol `2.0`;
- LMG and Codex ready;
- no `orchestrator_helper_incomplete`;
- no literal `%SystemDrive%`;
- declared artifact exists;
- run status agrees with QA.

- [ ] **Step 6: Reopen intake**

Reopen only after both smoke passes. If a gate fails, keep intake closed and
perform the documented two-service/database rollback.

- [ ] **Step 7: Record release evidence**

Add a short implementation report under
`docs/reports/2026-07-28-pag-lmg-protocol-v2-implementation.md` containing exact
commit SHAs, test counts, smoke timestamps, and any non-blocking follow-ups.
Commit the report separately:

```powershell
git add docs/reports/2026-07-28-pag-lmg-protocol-v2-implementation.md
git commit -m "docs: record protocol 2 release evidence"
```
