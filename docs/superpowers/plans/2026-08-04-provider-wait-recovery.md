# Provider Waiting Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically resume a Team Run paused for a ready-again provider, and safely repair the corrupt Codex sandbox ACL state that currently prevents Codex readiness.

**Architecture:** `TeamProviderRecovery` will decide whether a persisted provider wait is due and either reschedule it or atomically restore its existing model operation to `prepared`. `TeamCycleLoop` will invoke that recovery during its existing 30-second tick and pass successful claims to the dispatcher, which already resumes a recovered operation. The one-time Codex state repair remains an explicit, backup-first operational procedure outside PAG source code.

**Tech Stack:** Python 3.13, asyncio, SQLite, pytest/pytest-asyncio, PowerShell, Codex CLI 0.146.0.

## Global Constraints

- Preserve the existing immediate admission retry policy: three total attempts with 0.5- and 1.5-second delays.
- Poll persisted `waiting_for_provider` cycles on the existing 30-second loop interval.
- Resume only the original provider, task, and cycle; do not reassign personnel or providers.
- Use `TeamProviderRecovery.claim_operation()` as the only state-restoration compare-and-set transition.
- A still-unready provider stays waiting and receives a new `next_retry_at` 30 seconds after the current check.
- Keep raw provider stderr, LMG tokens, and sandbox diagnostics out of API payloads and database messages.
- Do not start PAG or LMG from a Codex-managed command; the runtime restart must be run by the user in a normal PowerShell window.

---

## File Structure

- Modify `src/personal_agent_gateway/team_provider_recovery.py`: add due-time parsing, readiness inspection, deferred retry scheduling, and atomic recovery claims.
- Modify `src/personal_agent_gateway/team_cycle_loop.py`: execute provider recovery on each scheduled tick and hand claims to the dispatcher.
- Modify `src/personal_agent_gateway/app.py`: inject the existing recovery service into the cycle loop.
- Modify `tests/test_team_provider_recovery.py`: test ready and unready due-cycle recovery behavior against SQLite state.
- Modify `tests/test_team_cycle_loop.py`: test scheduler integration and preserve existing automatic-series behavior.
- Create `docs/reports/2026-08-04-codex-sandbox-state-repair.md`: record the backup path, canary result, and post-restart provider status without credentials.

## Task 1: Make persisted provider waits recoverable

**Files:**
- Modify: `src/personal_agent_gateway/team_provider_recovery.py:93-283`
- Modify: `tests/test_team_provider_recovery.py:1-180`

**Interfaces:**
- Consumes: `TeamRunService.list_waiting_provider_cycles()`, `TeamProviderRecovery.claim_operation(cycle_id, now=<datetime>)`, and an `AgentRegistry` descriptor with `ready: bool`.
- Produces: `TeamProviderRecovery.recover_due(now: datetime) -> list[ProviderRecoveryClaim]`.

- [ ] **Step 1: Write failing recovery tests**

```python
def test_recover_due_claims_ready_provider_once(tmp_path):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    setup.recovery.wait_for_operation(
        setup.operation.id,
        reason_code="provider_not_ready",
        now=dt("2026-08-04T00:00:00+00:00"),
    )
    setup.recovery._registry = ReadyRegistry("codex", ready=True)

    claims = setup.recovery.recover_due(
        now=dt("2026-08-04T00:00:30+00:00")
    )

    assert [claim.operation_id for claim in claims] == [setup.operation.id]
    assert setup.operations.get(setup.operation.id).status == "prepared"
    assert setup.teams.get_cycle(setup.cycle.id).status == "running"
    assert setup.recovery.recover_due(
        now=dt("2026-08-04T00:01:00+00:00")
    ) == []


def test_recover_due_reschedules_unready_provider(tmp_path):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    setup.recovery.wait_for_operation(
        setup.operation.id,
        reason_code="provider_not_ready",
        now=dt("2026-08-04T00:00:00+00:00"),
    )
    setup.recovery._registry = ReadyRegistry("codex", ready=False)

    assert setup.recovery.recover_due(
        now=dt("2026-08-04T00:00:30+00:00")
    ) == []

    cycle = setup.teams.get_cycle(setup.cycle.id)
    assert cycle.status == "waiting_for_provider"
    assert cycle.execution_metadata["provider_recovery"]["next_retry_at"] == (
        "2026-08-04T00:01:00+00:00"
    )
```

- [ ] **Step 2: Run the new tests and verify the expected RED failure**

Run: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_team_provider_recovery.py -q`

Expected: FAIL because `TeamProviderRecovery` has no `recover_due` method.

- [ ] **Step 3: Implement the minimal recovery service**

```python
def recover_due(self, *, now: datetime) -> list[ProviderRecoveryClaim]:
    claims: list[ProviderRecoveryClaim] = []
    for cycle in self._teams.list_waiting_provider_cycles():
        recovery = _provider_recovery_metadata(cycle)
        if _parse_timestamp(recovery["next_retry_at"]) > now:
            continue
        try:
            descriptor = self._registry.get(recovery["provider"])
        except ValueError:
            self._reschedule_waiting_operation(cycle.id, now=now)
            continue
        if not descriptor.ready:
            self._reschedule_waiting_operation(cycle.id, now=now)
            continue
        claim = self.claim_operation(cycle.id, now=now)
        if claim is not None:
            claims.append(claim)
    return claims
```

Implement `_reschedule_waiting_operation()` in the same service using one
`begin immediate` transaction. It must validate that the operation and its
source are still waiting, replace only `provider_recovery.next_retry_at` with
`now + timedelta(seconds=30)`, and leave all run/cycle/task/agent statuses
unchanged. Treat malformed metadata and unavailable registry descriptors as
not ready; do not raise an unredacted provider error into the loop.

- [ ] **Step 4: Run the focused recovery tests and verify GREEN**

Run: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_team_provider_recovery.py -q`

Expected: PASS, including the new ready, reschedule, and no-duplicate checks.

- [ ] **Step 5: Commit the recovery service change**

```powershell
git add src/personal_agent_gateway/team_provider_recovery.py tests/test_team_provider_recovery.py
git commit -m "fix: recover due provider waits"
```

## Task 2: Schedule provider recovery from the cycle loop

**Files:**
- Modify: `src/personal_agent_gateway/team_cycle_loop.py:10-36`
- Modify: `src/personal_agent_gateway/app.py:265-268`
- Modify: `tests/test_team_cycle_loop.py:11-64`

**Interfaces:**
- Consumes: `TeamProviderRecovery.recover_due(now: datetime) -> list[ProviderRecoveryClaim]` from Task 1.
- Consumes: `TeamCycleDispatcher.resume_recovered_operation(claim: ProviderRecoveryClaim)`.
- Produces: a cycle-loop tick that resumes every successfully claimed provider wait once before enqueueing due automatic-series requests.

- [ ] **Step 1: Write the failing loop integration test**

```python
@pytest.mark.asyncio
async def test_loop_resumes_claimed_provider_recovery(tmp_path):
    _db, _teams, cycles, _run = make_cycle_services(tmp_path, "triggered")
    claim = ProviderRecoveryClaim("run-1", "cycle-1", "task-1", "op-1")
    recovery = RecordingRecovery([claim])
    dispatcher = RecordingDispatcher()
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        provider_recovery=recovery,
        now=lambda: dt("2026-08-04T00:00:30+00:00"),
    )

    await loop.tick()

    assert recovery.checked_at == [dt("2026-08-04T00:00:30+00:00")]
    assert dispatcher.recovered_operation_ids == ["op-1"]
```

Extend `RecordingDispatcher` with `resume_recovered_operation()` and add a
small `RecordingRecovery` test double that returns its configured claims.

- [ ] **Step 2: Run the loop test and verify the expected RED failure**

Run: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_team_cycle_loop.py -q`

Expected: FAIL because `TeamCycleLoop.__init__` does not accept
`provider_recovery` and `tick()` never resumes a claim.

- [ ] **Step 3: Implement loop wiring and scheduling**

```python
class TeamCycleLoop:
    def __init__(
        self,
        cycles: TeamCycleService,
        dispatcher: TeamCycleDispatcher,
        provider_recovery: TeamProviderRecovery,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._provider_recovery = provider_recovery

    async def tick(self) -> None:
        now = self._now()
        for claim in self._provider_recovery.recover_due(now=now):
            self._dispatcher.resume_recovered_operation(claim)
        for request in self._cycles.enqueue_due_auto_requests(now=now):
            await self._dispatcher.enqueue_run(request.team_run_id)
```

Pass `provider_recovery=provider_recovery` from `create_app()`. Keep the
dispatcher scheduling call non-blocking, matching the existing
`resume_recovered_operation()` behavior.

- [ ] **Step 4: Run the loop and recovery suites and verify GREEN**

Run: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_team_cycle_loop.py tests\\test_team_provider_recovery.py tests\\test_team_cycle_dispatcher.py -q`

Expected: PASS with the recovery claim resumed once and existing auto-series
enqueue behavior unchanged.

- [ ] **Step 5: Commit the scheduler change**

```powershell
git add src/personal_agent_gateway/team_cycle_loop.py src/personal_agent_gateway/app.py tests/test_team_cycle_loop.py
git commit -m "fix: schedule provider wait recovery"
```

## Task 3: Repair the Codex sandbox state and verify the affected Team Run

**Files:**
- Create: `docs/reports/2026-08-04-codex-sandbox-state-repair.md`
- External state: `C:\\Users\\Administrator\\.codex\\.sandbox\\deny_read_acl_state.json`

**Interfaces:**
- Consumes: the corrupt sandbox state file and the Codex canary command.
- Produces: a timestamped backup, a valid regenerated state file, and an LMG provider report with `providers.codex.ready == true`.

- [ ] **Step 1: Back up and remove only the corrupt state file**

Run in a normal PowerShell window:

```powershell
$state = 'C:\Users\Administrator\.codex\.sandbox\deny_read_acl_state.json'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$state.$stamp.bak"
Copy-Item -LiteralPath $state -Destination $backup -ErrorAction Stop
Remove-Item -LiteralPath $state -Force -ErrorAction Stop
Write-Host "Backup: $backup"
```

Expected: the backup exists and only the corrupt original is removed.

- [ ] **Step 2: Regenerate and validate the state with the sandbox canary**

```powershell
codex sandbox -- cmd.exe /d /c exit 0
if ($LASTEXITCODE -ne 0) { throw "Codex sandbox canary failed: $LASTEXITCODE" }
Get-Content -Raw -LiteralPath $state | ConvertFrom-Json | Out-Null
```

Expected: exit code `0`; the regenerated state parses as JSON.

- [ ] **Step 3: Restart the local runtime outside Codex**

Run in a normal PowerShell window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\playground\personal-agent-gateway\scripts\stop_local_runtime.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\playground\personal-agent-gateway\scripts\start_local_runtime.ps1"
```

Expected: `start` returns `started` or `already_running`; the fresh LMG
process does not retain the previous `sync.Once` readiness failure.

- [ ] **Step 4: Verify provider readiness and Run recovery without exposing the local token**

Run from the PAG repository:

```powershell
$token = (Get-Content .\.env | Where-Object { $_ -match '^LMG_LOCAL_TOKEN=' } | Select-Object -First 1).Substring(16)
$models = Invoke-RestMethod -Uri 'http://127.0.0.1:8788/v1/models' -Headers @{ Authorization = "Bearer $token" }
[pscustomobject]@{
  codex_ready = $models.providers.codex.ready
  codex_error = $models.providers.codex.readiness_error
  snapshot_status = $models.snapshot_status
}
```

Expected: `codex_ready` is `True`, `codex_error` is empty, and the next cycle
loop tick resumes the existing Run `dfbf20632ae84213b07bf339df33d5d3` exactly
once after the code change is deployed.

- [ ] **Step 5: Record the redacted operational result and commit it**

Write only timestamps, the backup filename, canary exit status, LMG readiness
booleans, and Team Run status to
`docs/reports/2026-08-04-codex-sandbox-state-repair.md`. Do not include the
token, raw LMG response, or sandbox stderr.

```powershell
git add docs/reports/2026-08-04-codex-sandbox-state-repair.md
git commit -m "docs: record Codex sandbox state repair"
```

## Final Verification

- [ ] Run: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_team_model_invoker.py tests\\test_team_provider_recovery.py tests\\test_team_cycle_loop.py tests\\test_team_cycle_dispatcher.py -q`
- [ ] Expected: all focused tests pass with no failures.
- [ ] Run: `git diff --check` and `git status --short` in the worktree.
- [ ] Expected: no whitespace errors and only the commits described above.
