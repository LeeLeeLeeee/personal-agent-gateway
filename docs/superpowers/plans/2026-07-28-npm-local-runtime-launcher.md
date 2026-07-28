# npm Local Runtime Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Windows-only `npm start` and `npm stop` commands at the PAG repository root that control the existing PAG/LMG runtime bundle.

**Architecture:** A dependency-free root `package.json` delegates lifecycle operations to the existing PowerShell launchers. The PowerShell scripts remain the sole owners of identity checks, configuration, health checks, process tracking, and shutdown; npm only provides short user-facing aliases.

**Tech Stack:** npm package scripts, Windows PowerShell 5.1, Markdown

## Global Constraints

- The npm entry point exists only in the PAG repository.
- `npm start` always starts PAG and LMG as one bundle.
- `npm stop` delegates to the existing tracked-process stop script.
- No standalone LMG npm command is added.
- The commands are Windows-only and must be run by the user in a normal PowerShell or cmd session.
- No npm dependencies or root `package-lock.json` are added.
- Automated verification must not start PAG or LMG.

---

### Task 1: Add the PAG npm lifecycle aliases

**Files:**
- Create: `package.json`
- Modify: `README.md:126`
- Test: one-shot PowerShell and npm metadata checks; no persistent test file

**Interfaces:**
- Consumes: `scripts/start_local_runtime.ps1` and `scripts/stop_local_runtime.ps1`
- Produces: root commands `npm start` and `npm stop`

- [ ] **Step 1: Run the package contract check and verify it fails**

Run:

```powershell
$package = Get-Content -Raw .\package.json | ConvertFrom-Json
if ($package.private -ne $true) { throw "root package must be private" }
if ($package.scripts.start -ne 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/start_local_runtime.ps1') {
    throw "unexpected start script"
}
if ($package.scripts.stop -ne 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/stop_local_runtime.ps1') {
    throw "unexpected stop script"
}
```

Expected: FAIL because the PAG root does not yet contain `package.json`.

- [ ] **Step 2: Add the minimal root package**

Create `package.json`:

```json
{
  "name": "personal-agent-gateway-runtime",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "start": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/start_local_runtime.ps1",
    "stop": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/stop_local_runtime.ps1"
  }
}
```

- [ ] **Step 3: Re-run the package contract check**

Run:

```powershell
$package = Get-Content -Raw .\package.json | ConvertFrom-Json
if ($package.private -ne $true) { throw "root package must be private" }
if ($package.scripts.start -ne 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/start_local_runtime.ps1') {
    throw "unexpected start script"
}
if ($package.scripts.stop -ne 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/stop_local_runtime.ps1') {
    throw "unexpected stop script"
}
```

Expected: PASS with exit code 0 and no output.

- [ ] **Step 4: Document the npm aliases**

Replace the Windows command block in the local runtime section of `README.md`
with:

````markdown
```powershell
# Windows: PAG와 LMG를 함께 필요할 때 시작
npm start

# 런처가 기록한 두 프로세스만 안전하게 종료
npm stop

# PowerShell 스크립트를 직접 실행해도 동일합니다.
.\scripts\start_local_runtime.ps1
.\scripts\stop_local_runtime.ps1
```
````

Add one sentence after the block:

```markdown
루트 npm 명령은 Windows 전용이며 일반 PowerShell 또는 cmd에서 실행해야 합니다.
```

- [ ] **Step 5: Verify registration and PowerShell syntax without starting servers**

Run:

```powershell
npm run
$errors = @()
[void][Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\scripts\start_local_runtime.ps1),
    [ref]$null,
    [ref]$errors
)
[void][Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\scripts\stop_local_runtime.ps1),
    [ref]$null,
    [ref]$errors
)
if ($errors.Count -gt 0) { throw ($errors | Out-String) }
if (Test-Path .\package-lock.json) { throw "unexpected root package-lock.json" }
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object LocalPort -In 8787, 8788
if ($listeners) { throw "runtime listener unexpectedly exists" }
git -C ..\local-model-gateway status --short
```

Expected:

- `npm run` lists `start` and `stop`.
- PowerShell parsing reports no errors.
- No root `package-lock.json` exists.
- LMG status is unchanged and clean.
- No listener is created on port 8787 or 8788.

- [ ] **Step 6: Review and commit**

Run:

```powershell
git diff --check
git diff -- package.json README.md
git add package.json README.md
git commit -m "feat(runtime): npm 통합 실행 명령 추가"
```
