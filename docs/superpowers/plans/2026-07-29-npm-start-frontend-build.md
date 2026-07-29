# npm Start Frontend Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the production frontend before the default root `npm start`, while preserving an explicit no-build start command.

**Architecture:** The root `package.json` composes the existing frontend build and PowerShell runtime launcher. The frontend package remains responsible for Vite, and the PowerShell script remains responsible for the PAG/LMG runtime.

**Tech Stack:** npm package scripts, Vite, Windows PowerShell, pytest, Markdown

## Global Constraints

- `npm start` must stop before runtime startup if the frontend build fails.
- `npm run start:no-build` must invoke the existing launcher directly.
- `npm stop` must remain unchanged.
- Do not change the PowerShell runtime launchers.
- Do not add npm dependencies or a root `package-lock.json`.
- Automated verification must not start PAG or LMG.

---

### Task 1: Define and implement the root npm command contract

**Files:**
- Create: `tests/test_root_package_scripts.py`
- Modify: `package.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: frontend script `npm --prefix frontend run build`
- Consumes: `scripts/start_local_runtime.ps1`
- Produces: `build:frontend`, `start`, and `start:no-build` root npm scripts

- [ ] **Step 1: Write the failing package contract test**

```python
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_npm_scripts_build_frontend_before_default_start() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"] == {
        "build:frontend": "npm --prefix frontend run build",
        "start": "npm run build:frontend && npm run start:no-build",
        "start:no-build": (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            "-File ./scripts/start_local_runtime.ps1"
        ),
        "stop": (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            "-File ./scripts/stop_local_runtime.ps1"
        ),
    }
```

- [ ] **Step 2: Run the test and verify the missing scripts fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_root_package_scripts.py -q
```

Expected: FAIL because `build:frontend` and `start:no-build` do not exist and
`start` still invokes PowerShell directly.

- [ ] **Step 3: Implement the minimal root scripts**

Set the root `package.json` scripts to:

```json
{
  "build:frontend": "npm --prefix frontend run build",
  "start": "npm run build:frontend && npm run start:no-build",
  "start:no-build": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/start_local_runtime.ps1",
  "stop": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/stop_local_runtime.ps1"
}
```

- [ ] **Step 4: Run the package contract test and verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_root_package_scripts.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Document the commands**

Update the Windows runtime block in `README.md` to show:

```powershell
# Frontend production build 후 PAG와 LMG 시작
npm start

# Frontend build 없이 PAG와 LMG 시작
npm run start:no-build

# Frontend production build만 실행
npm run build:frontend

# 런처가 기록한 두 프로세스만 안전하게 종료
npm stop
```

State that `npm start` does not launch the runtime when the frontend build
fails.

- [ ] **Step 6: Verify registration, build, tests, syntax, and diff**

Run:

```powershell
npm run
npm run build:frontend
.\.venv\Scripts\python.exe -m pytest tests\test_root_package_scripts.py tests\test_local_runtime_scripts.py -q
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
git diff --check -- package.json README.md tests/test_root_package_scripts.py
```

Expected: scripts registered, frontend build succeeds, tests pass, PowerShell
parses, no root lockfile exists, and the diff check succeeds. None of these
commands starts PAG or LMG.

- [ ] **Step 7: Commit the scoped implementation**

```powershell
git add package.json README.md tests/test_root_package_scripts.py docs/superpowers/plans/2026-07-29-npm-start-frontend-build.md
git commit -m "feat(runtime): npm start 시 frontend 빌드"
```
