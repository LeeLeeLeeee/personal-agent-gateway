# PAG Provider Cycle Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze stable provider capabilities per Team cycle, keep transient provider outages out of semantic task failure, and automatically resume the same task and cycle when the provider is ready again.

**Architecture:** PAG parses LMG capability, readiness, admission, and freshness as separate values. A `TeamProviderRecovery` coordinator freezes capabilities into cycle metadata, persists `waiting_for_provider`, and lets `TeamCycleLoop` atomically claim and resume recoverable cycles; `TeamRuntime` continues to send only result-quality failures to Lead review.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, httpx/asyncio, pytest/pytest-asyncio, React 19, Vitest, existing PAG EventBus and Team cycle services.

## Global Constraints

- Implement this plan only after the LMG plan exposes protocol `2.0` fields `snapshot_status`, `refresh_error_code`, `gateway_status`, `admission_status`, provider `ready`, `readiness_error`, and stable provider `execution`.
- Status lookup makes exactly three total attempts with delays of `0.5` seconds and `1.5` seconds.
- Retry status lookup only for connection failure, timeout, HTTP 502/503/504, `capabilities_unavailable`, and gateway `not_ready`.
- Do not retry unauthorized, malformed data, protocol mismatch, unsupported protocol version, invalid execution path, or unsupported execution capability.
- Retry model admission only when no stream has started and the stable code is `provider_not_ready`, `provider_unavailable`, or `capacity_exceeded`.
- Do not blindly replay an ambiguous model timeout or an interrupted stream.
- Freeze every roster provider's execution capability in `team_run_cycles.execution_metadata_json` before planning starts.
- Persist run, cycle, and current task as `waiting_for_provider`; keep the agent at existing status `waiting` and keep the cycle request `dispatching`.
- After `120` seconds, expose a warning and `RESUME`/`CANCEL`; continue 30-second background checks.
- Recovery must use one compare-and-set transition and resume the same cycle at most once.
- Lead review remains limited to produced task-result and acceptance failures.
- Never expose raw gateway diagnostics, provider stderr, tokens, or credentials.
- Run only the focused tests and frontend build listed below. Full-suite execution requires a separate risk-based decision.

---

## File Structure

- Modify `src/personal_agent_gateway/lmg_client.py`: separate typed execution capability from readiness and add bounded status retry.
- Modify `src/personal_agent_gateway/agents.py`: retain a locked last-known-good catalog and expose snapshot/readiness independently.
- Modify `src/personal_agent_gateway/execution_contract.py`: validate only the stable execution contract.
- Create `src/personal_agent_gateway/team_provider_recovery.py`: freeze cycle capabilities, classify provider failures, persist waiting, and atomically claim recovery.
- Modify `src/personal_agent_gateway/teams.py`: add waiting statuses and transactional state transitions.
- Modify `src/personal_agent_gateway/remote_model_client.py`: identify safe pre-stream failures and apply bounded model-admission retry.
- Modify `src/personal_agent_gateway/app.py`: wire frozen capabilities and the recovery coordinator.
- Modify `src/personal_agent_gateway/team_runtime.py`: route provider outages to infrastructure recovery rather than Lead/task failure.
- Modify `src/personal_agent_gateway/team_cycle_dispatcher.py`: freeze before planning and leave waiting requests dispatching.
- Modify `src/personal_agent_gateway/team_cycle_loop.py`: poll and resume waiting cycles.
- Modify `src/personal_agent_gateway/team_cycles.py`: preserve provider-waiting cycles during reconciliation and policy reporting.
- Modify `src/personal_agent_gateway/api/team_runs.py`: expose sanitized recovery state and guarded manual resume.
- Modify `frontend/src/api/client.js`: map recovery payloads.
- Modify `frontend/src/components/atoms/StatusBadge/index.jsx`: label provider waiting.
- Modify `frontend/src/components/molecules/TeamTaskCard/index.jsx`: render task provider-waiting status.
- Modify `frontend/src/components/organisms/TeamRunDetail/index.jsx`: add waiting column, banner, warning, and actions.
- Modify `src/personal_agent_gateway/static/styles.css`: style provider waiting without reusing failed/blocked colors.
- Test only the files named in each task.

### Task 1: Parse the separated LMG contract and retry status lookup

**Files:**
- Modify: `src/personal_agent_gateway/lmg_client.py:1-235`
- Modify: `tests/test_lmg_client.py:1-255`

**Interfaces:**
- Consumes: LMG protocol `2.0` payload from the LMG implementation plan.
- Produces:
  - `ProviderExecutionCapabilities` without readiness fields.
  - `ProviderReadiness(ready: bool, error_code: str | None)`.
  - `parse_provider_readiness(provider: object) -> ProviderReadiness`.
  - `fetch_capabilities(..., sleep, retry_delays=(0.5, 1.5))`.

- [ ] **Step 1: Update the protocol fixture and write failing separation tests**

Make the fixture include:

```python
{
    "protocol_version": "2.0",
    "schema_version": 1,
    "detected_at": "2026-07-30T00:00:00Z",
    "snapshot_status": "fresh",
    "admission_status": "ready",
    "gateway_status": "ready",
    "providers": {
        "codex": {
            "available": True,
            "ready": False,
            "readiness_error": "provider_not_ready",
            "execution": {
                "resume": True,
                "external_read_only_roots": False,
                "network_modes": ["unspecified", "denied", "required"],
                "sandbox_modes": ["read-only", "workspace-write"],
                "permission_modes": [],
            },
        }
    },
}
```

Add:

```python
def test_unready_provider_keeps_usable_execution_capability():
    payload = _protocol_2_payload(ready=False)

    def handler(request):
        return httpx.Response(200, json=payload)

    parsed = fetch_execution_capabilities(
        _cfg(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: None,
    )

    assert parsed["codex"] == ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified", "denied", "required"),
        sandbox_modes=("read-only", "workspace-write"),
        permission_modes=(),
    )
    assert parse_provider_readiness(payload["providers"]["codex"]) == ProviderReadiness(
        ready=False,
        error_code="provider_not_ready",
    )
```

- [ ] **Step 2: Write failing retry-classification tests**

```python
def test_fetch_capabilities_retries_transient_status_twice_then_succeeds():
    responses = iter([
        httpx.Response(503, json={"code": "capabilities_unavailable"}),
        httpx.Response(502, json={"code": "bad_gateway"}),
        httpx.Response(200, json=_protocol_2_payload()),
    ])
    delays = []

    result = fetch_capabilities(
        _cfg(),
        transport=httpx.MockTransport(lambda _request: next(responses)),
        sleep=delays.append,
    )

    assert result.status == "ready"
    assert delays == [0.5, 1.5]


def test_fetch_capabilities_does_not_retry_hard_failure():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"code": "unauthorized"})

    result = fetch_capabilities(
        _cfg(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: pytest.fail("hard failure slept"),
    )

    assert result.status == "unauthorized"
    assert calls == 1
```

Also add a gateway-`not_ready` sequence test that returns the final valid payload with `status == "not_ready"` after three attempts.

Add a `caplog` assertion to the transient sequence test. Emit only
`attempt=<1..3>` and the stable result status for each lookup; do not log the
response body or `LmgQueryResult.message`.

- [ ] **Step 3: Run the focused tests to verify they fail**

Run:

```powershell
pytest tests/test_lmg_client.py -q
```

Expected: FAIL because readiness is embedded in `ProviderExecutionCapabilities`, freshness/admission fields are not validated, and no bounded retry exists.

- [ ] **Step 4: Split the dataclasses and strict parsers**

Use these types:

```python
@dataclass(frozen=True)
class ProviderExecutionCapabilities:
    resume: bool
    external_read_only_roots: bool
    network_modes: tuple[str, ...]
    sandbox_modes: tuple[str, ...]
    permission_modes: tuple[str, ...]


@dataclass(frozen=True)
class ProviderReadiness:
    ready: bool
    error_code: str | None
```

`parse_provider_execution_capabilities()` must parse only `execution`. Move `ready` and `readiness_error` validation to `parse_provider_readiness()`. Require top-level:

```python
snapshot_status in {"fresh", "stale"}
admission_status in {"ready", "not_ready"}
gateway_status in {"ready", "not_ready"}
isinstance(detected_at, str) and bool(detected_at)
refresh_error_code is None or isinstance(refresh_error_code, str)
```

- [ ] **Step 5: Implement bounded status retry**

Keep public calls synchronous and injectable:

```python
def fetch_capabilities(
    config,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retry_delays: tuple[float, ...] = (0.5, 1.5),
) -> LmgQueryResult[dict[str, object]]:
    for attempt in range(len(retry_delays) + 1):
        result = _fetch_capabilities_once(config, transport=transport)
        if result.status not in {"unreachable", "not_ready"}:
            return result
        if attempt == len(retry_delays):
            return result
        sleep(retry_delays[attempt])
    raise AssertionError("unreachable retry state")
```

Map only 502/503/504 and transport failures to a retryable result. Keep 401 as `unauthorized`; keep JSON/schema/protocol failures as `protocol_error`.

- [ ] **Step 6: Run and commit the LMG client contract**

Run:

```powershell
pytest tests/test_lmg_client.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/personal_agent_gateway/lmg_client.py tests/test_lmg_client.py
git commit -m "feat: LMG capability와 readiness 계약 분리"
```

### Task 2: Keep a locked last-known-good `AgentRegistry` catalog

**Files:**
- Modify: `src/personal_agent_gateway/agents.py:1-450`
- Modify: `tests/test_agents.py:130-330`
- Modify: `tests/test_execution_contract.py`
- Modify: `src/personal_agent_gateway/execution_contract.py:39-77`

**Interfaces:**
- Consumes: Task 1 `LmgQueryResult` and separated parsers.
- Produces `AgentDescriptor.ready`, `readiness_error`, `snapshot_status`, and `detected_at` while retaining `execution_capabilities`.

- [ ] **Step 1: Write failing registry tests**

Add this fixture beside `make_config()` in `tests/test_agents.py`:

```python
def _agent_protocol_payload(*, snapshot_status: str = "fresh"):
    return {
        "protocol_version": "2.0",
        "schema_version": 1,
        "detected_at": "2026-07-30T00:00:00Z",
        "snapshot_status": snapshot_status,
        "refresh_error_code": None,
        "gateway_status": "ready",
        "admission_status": "ready",
        "providers": {
            "codex": {
                "available": True,
                "ready": True,
                "readiness_error": None,
                "models": [{"id": "default", "label": "Default"}],
                "execution": {
                    "resume": True,
                    "external_read_only_roots": False,
                    "network_modes": ["unspecified", "denied", "required"],
                    "sandbox_modes": ["read-only", "workspace-write"],
                    "permission_modes": [],
                },
            }
        },
    }
```

```python
def test_registry_keeps_last_good_capability_when_refresh_is_unreachable(tmp_path):
    results = iter([
        LmgQueryResult(
            data=_agent_protocol_payload(snapshot_status="fresh"),
            status="ready",
        ),
        LmgQueryResult(data=None, status="unreachable", message="offline"),
    ])
    clock = [0.0]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: next(results),
        cache_ttl_seconds=1,
        failure_ttl_seconds=2,
        clock=lambda: clock[0],
    )

    first = registry.get("codex")
    clock[0] = 2.0
    stale = registry.get("codex")

    assert first.execution_capabilities == stale.execution_capabilities
    assert stale.snapshot_status == "stale"
    assert stale.ready is False
    assert stale.readiness_error == "gateway_unreachable"


def test_registry_hard_protocol_failure_is_not_recoverable(tmp_path):
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: LmgQueryResult(
            data=None,
            status="protocol_error",
            message="bad protocol",
        ),
    )

    descriptor = registry.get("codex")
    assert descriptor.snapshot_status == "unavailable"
    assert descriptor.readiness_error == "gateway_protocol_error"
    assert descriptor.execution_capabilities is None
```

Add a `ThreadPoolExecutor` test where concurrent `catalog()` calls after expiry produce one refresh and cannot replace a newer successful catalog with fallback descriptors.

- [ ] **Step 2: Write a failing execution-contract test**

```python
def test_compile_execution_ignores_transient_provider_readiness(tmp_path):
    capabilities = ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified",),
        sandbox_modes=("workspace-write",),
        permission_modes=(),
    )

    compiled = compile_execution(
        ExecutionRequirements(
            source_roots=(),
            requires_sources=False,
            workspace_mode="isolated",
            workspace_root=tmp_path,
            network="unspecified",
        ),
        _policy("none", None),
        capabilities,
        FakeStaging(tmp_path),
    )

    assert compiled.workspace_root == tmp_path.resolve()
```

- [ ] **Step 3: Run the focused tests to verify they fail**

Run:

```powershell
pytest tests/test_agents.py tests/test_execution_contract.py -q
```

Expected: FAIL because descriptor state is conflated and `compile_execution` still reads `capabilities.ready`.

- [ ] **Step 4: Add descriptor state and lock the refresh**

Add:

```python
class AgentDescriptor(BaseModel):
    # existing fields remain
    ready: bool = False
    readiness_error: str | None = None
    snapshot_status: Literal["fresh", "stale", "unavailable"] = "unavailable"
    detected_at: str = ""


class AgentRegistry:
    def __init__(...):
        # existing fields remain
        self._lock = RLock()
```

Run the full refresh decision under `_lock`. Accept valid data for both `ready` and `not_ready`. For retryable failures with a prior catalog, return `model_copy()` descriptors with stable capabilities and stale/unknown readiness. For `unauthorized` and `protocol_error`, set a hard `gateway_<status>` readiness code and never label it stale success.

Change `_availability()` so `available` represents detected/probed provider presence and valid execution capability only; remove `detected_ready` from the boolean expression.

- [ ] **Step 5: Remove readiness validation from `compile_execution`**

Delete only:

```python
if not capabilities.ready:
    raise ExecutionContractError(...)
```

Keep network, sandbox, permission, path, SPACE, and staging validation unchanged.

- [ ] **Step 6: Run and commit the registry/compiler boundary**

Run:

```powershell
pytest tests/test_agents.py tests/test_execution_contract.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/personal_agent_gateway/agents.py src/personal_agent_gateway/execution_contract.py tests/test_agents.py tests/test_execution_contract.py
git commit -m "feat: provider snapshot과 readiness 상태 분리"
```

### Task 3: Freeze all roster provider capabilities before cycle planning

**Files:**
- Create: `src/personal_agent_gateway/team_provider_recovery.py`
- Modify: `src/personal_agent_gateway/app.py:576-693`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py:33-157`
- Modify: `tests/test_app_team_factory.py:1-390`
- Modify: `tests/test_team_cycle_dispatcher.py`

**Interfaces:**
- Consumes: `AgentRegistry`, `TeamRunService.list_agents()`, and cycle `execution_metadata`.
- Produces:
  - `ProviderRecoveryRequired(provider: str, reason_code: str)`.
  - `TeamProviderRecovery.freeze_cycle(cycle_id: str) -> TeamRunCycle`.
  - `execution_metadata["provider_capabilities"][provider]["execution"]`.
  - `capabilities_for_cycle(cycle, provider) -> ProviderExecutionCapabilities`.

- [ ] **Step 1: Write a failing freeze test**

Add these test helpers to `tests/test_team_cycle_dispatcher.py`; they use the
real request/cycle lineage and a minimal registry double:

```python
def _dispatching_cycle(teams, cycles, run):
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "provider-freeze",
        "work",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None and claimed.id == request.id
    return teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )


def _execution_capability():
    return ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified", "denied", "required"),
        sandbox_modes=("read-only", "workspace-write"),
        permission_modes=(),
    )


class _FrozenRegistry:
    def get(self, provider):
        return SimpleNamespace(
            ready=True,
            readiness_error=None,
            snapshot_status="fresh",
            detected_at="2026-07-30T00:00:00Z",
            execution_capabilities=_execution_capability(),
        )
```

```python
def test_freeze_cycle_persists_every_roster_provider(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    worker = next(
        agent for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    db.execute(
        "update team_agents set backend = 'claude' where id = ?",
        (worker.id,),
    )
    cycle = _dispatching_cycle(teams, cycles, run)
    registry = _FrozenRegistry()
    recovery = TeamProviderRecovery(teams, registry)

    frozen = recovery.freeze_cycle(cycle.id)

    snapshots = frozen.execution_metadata["provider_capabilities"]
    assert set(snapshots) == {"codex", "claude"}
    assert snapshots["codex"]["execution"]["network_modes"] == [
        "unspecified", "denied", "required"
    ]
    assert snapshots["claude"]["snapshot_status"] == "fresh"
```

Add a test where the registry has no execution snapshot:

```python
registry.get = lambda provider: SimpleNamespace(
    ready=False,
    readiness_error="capabilities_unavailable",
    snapshot_status="unavailable",
    detected_at="",
    execution_capabilities=(
        None if provider == "claude" else _execution_capability()
    ),
)
with pytest.raises(ProviderRecoveryRequired) as error:
    recovery.freeze_cycle(cycle.id)
assert error.value.provider == "claude"
assert error.value.reason_code == "capabilities_unavailable"
```

- [ ] **Step 2: Write a failing factory test proving no task-hot-path registry refresh**

```python
def test_team_factory_uses_frozen_cycle_capability_without_registry_lookup(tmp_path):
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "artifacts"),
        space_policy=_space_policy(None, read_mode="none"),
    )
    team_runs.execution_metadata = {
        "provider_capabilities": {
            "codex": {
                "snapshot_status": "fresh",
                "detected_at": "2026-07-30T00:00:00Z",
                "execution": {
                    "resume": True,
                    "external_read_only_roots": False,
                    "network_modes": ["unspecified"],
                    "sandbox_modes": ["workspace-write"],
                    "permission_modes": [],
                },
            }
        }
    }
    registry = SimpleNamespace(
        get=lambda _provider: pytest.fail("registry refreshed on cycle hot path")
    )

    client = _team_model_factory(
        _config(tmp_path),
        team_runs,
        agent_registry=registry,
    )(_agent("codex", workspace_path=str(tmp_path / "workspace")), "cycle-1")

    assert isinstance(client, HttpModelClient)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:

```powershell
pytest tests/test_app_team_factory.py tests/test_team_cycle_dispatcher.py -q
```

Expected: FAIL because no coordinator exists and `_team_model_factory` always calls `AgentRegistry.get()`.

- [ ] **Step 4: Implement exact capability serialization**

In `team_provider_recovery.py`, serialize only the stable contract:

```python
def capability_payload(capabilities: ProviderExecutionCapabilities) -> dict[str, object]:
    return {
        "resume": capabilities.resume,
        "external_read_only_roots": capabilities.external_read_only_roots,
        "network_modes": list(capabilities.network_modes),
        "sandbox_modes": list(capabilities.sandbox_modes),
        "permission_modes": list(capabilities.permission_modes),
    }
```

`freeze_cycle()` must:

1. Load the cycle and its Team agents.
2. Deduplicate `agent.backend`.
3. Resolve every descriptor before writing anything.
4. Raise `ProviderRecoveryRequired` for a missing capability snapshot.
5. Merge `provider_capabilities` into existing execution metadata in one call to `set_cycle_execution_metadata`.

- [ ] **Step 5: Use frozen capabilities in `_team_model_factory`**

For a cycle:

```python
capabilities = capabilities_for_cycle(cycle, agent.backend)
```

Only non-cycle sessions may use:

```python
descriptor = agents.get(agent.backend)
capabilities = descriptor.execution_capabilities
```

Keep per-agent compiled execution metadata under the existing `"agents"` key.

- [ ] **Step 6: Freeze before hook preparation or Lead planning**

Inject `TeamProviderRecovery` into `TeamCycleDispatcher` and call:

```python
cycle = self._provider_recovery.freeze_cycle(cycle.id)
```

immediately after `create_cycle()` and before any preparer or `orchestrator.run_cycle()`. Do not handle `ProviderRecoveryRequired` as a failed cycle yet; Task 4 adds the persistent wait transition.

- [ ] **Step 7: Run and commit cycle freezing**

Run:

```powershell
pytest tests/test_app_team_factory.py tests/test_team_cycle_dispatcher.py -q
```

Expected: PASS for freeze/factory tests and existing dispatcher behavior.

Commit:

```powershell
git add src/personal_agent_gateway/team_provider_recovery.py src/personal_agent_gateway/app.py src/personal_agent_gateway/team_cycle_dispatcher.py tests/test_app_team_factory.py tests/test_team_cycle_dispatcher.py
git commit -m "feat: Team cycle provider capability snapshot 고정"
```

### Task 4: Persist provider waiting and atomically claim recovery

**Files:**
- Modify: `src/personal_agent_gateway/teams.py:24-62, 541-590, 949-968, 1203-1230, 1352-1420, 2008-2031`
- Modify: `src/personal_agent_gateway/team_provider_recovery.py`
- Modify: `tests/team_cycle_helpers.py`
- Modify: `tests/test_teams.py`
- Create: `tests/test_team_provider_recovery.py`

**Interfaces:**
- Produces:
  - `waiting_for_provider` in `TeamRunStatus`, `CycleStatus`, and `TaskStatus`.
  - `ProviderRecoveryClaim(team_run_id: str, cycle_id: str, task_id: str | None)`.
  - `TeamRunService.mark_waiting_for_provider(cycle_id, *, provider, reason_code, attempts, task_id, agent_id, now) -> TeamRunCycle`.
  - `TeamRunService.list_waiting_provider_cycles() -> list[TeamRunCycle]`.
  - `TeamRunService.claim_provider_recovery(cycle_id, now) -> ProviderRecoveryClaim | None`.

- [ ] **Step 1: Write a failing transactional state test**

First add this reusable real-state helper to `tests/team_cycle_helpers.py`:

```python
def make_running_task_in_cycle(teams, cycles, run):
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "provider-recovery",
        "work",
        previous_cycle_id=None,
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None and claimed.id == request.id
    cycle = teams.create_cycle(
        run.id,
        claimed.source_type,
        claimed.source_id,
        request_id=claimed.id,
    )
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    agent = next(
        candidate for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )
    task = teams.create_task(
        run.id,
        "current",
        "provider work",
        owner_agent_id=agent.id,
        cycle_id=cycle.id,
    )
    task, agent = teams.start_task(task.id, agent.id)
    return cycle, task, agent
```

```python
def test_mark_waiting_for_provider_preserves_dispatching_request_and_current_work(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)

    waiting = teams.mark_waiting_for_provider(
        cycle.id,
        provider="claude",
        reason_code="provider_not_ready",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )

    assert waiting.status == "waiting_for_provider"
    assert teams.get_team_run(run.id).status == "waiting_for_provider"
    assert teams.get_task(task.id).status == "waiting_for_provider"
    assert teams.get_agent(agent.id).status == "waiting"
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    state = waiting.execution_metadata["provider_recovery"]
    assert state["provider"] == "claude"
    assert state["attempts"] == 3
    assert state["next_retry_at"] == "2026-07-30T00:00:30+00:00"
    assert state["warning_visible_at"] == "2026-07-30T00:02:00+00:00"
```

- [ ] **Step 2: Write a failing compare-and-set test**

```python
def test_claim_provider_recovery_resumes_same_cycle_once(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    teams.mark_waiting_for_provider(
        cycle.id,
        provider="codex",
        reason_code="provider_unavailable",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )

    first = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:30+00:00"),
    )
    second = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:30+00:00"),
    )

    assert first == ProviderRecoveryClaim(run.id, cycle.id, task.id)
    assert second is None
    assert teams.get_task(task.id).status == "pending"
    assert teams.get_agent(agent.id).status == "pending"
```

- [ ] **Step 3: Run the new state tests to verify they fail**

Run:

```powershell
pytest tests/test_teams.py tests/test_team_provider_recovery.py -q
```

Expected: FAIL because waiting statuses and transactional methods do not exist.

- [ ] **Step 4: Add the status literals without a schema migration**

SQLite status columns are plain `text`; change only Python status contracts and active/terminal sets. Add `"waiting_for_provider"` to run, cycle, and task literals. Do not mark provider waiting as terminal and do not set `finished_at`.

- [ ] **Step 5: Implement one transaction for entering provider wait**

Merge this exact metadata shape into existing cycle execution metadata:

```python
{
    "provider_recovery": {
        "provider": provider,
        "task_id": task_id,
        "agent_id": agent_id,
        "reason_code": reason_code,
        "attempts": attempts,
        "first_failed_at": timestamp,
        "next_retry_at": timestamp_plus_30_seconds,
        "warning_visible_at": timestamp_plus_120_seconds,
    }
}
```

Within one `begin immediate` transaction:

- update cycle and run to `waiting_for_provider`;
- update the current task to `waiting_for_provider` if present;
- update the current agent to `waiting` while retaining `current_task_id`;
- leave the cycle request `dispatching`.

- [ ] **Step 6: Implement the compare-and-set claim**

Start with:

```sql
update team_run_cycles
set status = 'running', updated_at = ?
where id = ? and status = 'waiting_for_provider'
```

Check `cursor.rowcount == 1` before updating related rows. Restore the same task to `pending`, clear its terminal fields, restore the agent to `pending` with `current_task_id = null`, restore the run to `running`, and remove only `"provider_recovery"` from cycle metadata. Keep `"provider_capabilities"` and `"agents"`.

Define the claim in `teams.py` so the service and tests share one type:

```python
@dataclass(frozen=True)
class ProviderRecoveryClaim:
    team_run_id: str
    cycle_id: str
    task_id: str | None
```

- [ ] **Step 7: Run and commit persistent state**

Run:

```powershell
pytest tests/test_teams.py tests/test_team_provider_recovery.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_provider_recovery.py tests/team_cycle_helpers.py tests/test_teams.py tests/test_team_provider_recovery.py
git commit -m "feat: provider waiting 상태와 atomic recovery 저장"
```

### Task 5: Retry safe model admission and route exhaustion to provider waiting

**Files:**
- Modify: `src/personal_agent_gateway/remote_model_client.py:1-440`
- Modify: `src/personal_agent_gateway/team_provider_recovery.py`
- Modify: `src/personal_agent_gateway/team_runtime.py:228-372, 443-615, 1135-1173`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py:90-157`
- Modify: `src/personal_agent_gateway/app.py:203-230, 576-693`
- Modify: `tests/test_remote_model_client.py`
- Modify: `tests/test_team_provider_recovery.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_team_cycle_dispatcher.py`

**Interfaces:**
- Produces:
  - `ProviderRunUnavailable(provider, reason_code, attempts)`.
  - `RetryingModelClient(delegate, provider, retry_delays=(0.5, 1.5), sleep=asyncio.sleep)`.
  - `TeamProviderRecovery.wait_for_failure(...)`.
  - `TeamProviderRecovery.interrupt_ambiguous_run(...)`.
  - `RemoteRunError.consumer_run_id` for LMG session reconciliation.

- [ ] **Step 1: Write failing model-admission retry tests**

```python
async def _record_delay(delays, delay):
    delays.append(delay)


@pytest.mark.asyncio
async def test_retrying_client_retries_only_safe_pre_stream_provider_failure():
    delegate = AsyncMock()
    delegate.complete.side_effect = [
        RemoteRunFailedError(
            "provider_not_ready",
            "remote_provider_not_ready",
            pre_stream=True,
        ),
        RemoteRunFailedError(
            "provider_not_ready",
            "remote_provider_not_ready",
            pre_stream=True,
        ),
        ModelResponse(content="done", tool_calls=[]),
    ]
    delays = []
    client = RetryingModelClient(
        delegate,
        "claude",
        sleep=lambda delay: _record_delay(delays, delay),
    )

    response = await client.complete([{"role": "user", "content": "work"}])

    assert response.content == "done"
    assert delegate.complete.await_count == 3
    assert delays == [0.5, 1.5]
```

Add:

```python
@pytest.mark.asyncio
async def test_retrying_client_does_not_replay_started_or_timed_out_run():
    for error in [
        RemoteRunFailedError("provider_unavailable", "failed", pre_stream=False),
        RemoteRunAbortedError("run_timeout", "timeout"),
    ]:
        delegate = AsyncMock()
        delegate.complete.side_effect = error
        client = RetryingModelClient(delegate, "codex")
        with pytest.raises(type(error)):
            await client.complete([{"role": "user", "content": "work"}])
        assert delegate.complete.await_count == 1
```

- [ ] **Step 2: Write a failing runtime provider-wait test**

Use the real continuous Team setup; the Lead creates one task and the worker
raises the typed infrastructure error:

```python
@pytest.mark.asyncio
async def test_provider_failure_waits_same_task_without_lead_review(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        tmp_path / "workspace",
        cycle_service=cycles,
    )
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "provider-wait")
    plan = '[{"title":"T1","description":"d1"}]'
    unavailable = ProviderRunUnavailable(
        "codex",
        "provider_not_ready",
        attempts=3,
    )
    models = _factory_by_role([plan], [unavailable])
    recovery = TeamProviderRecovery(
        teams,
        SimpleNamespace(get=lambda _provider: pytest.fail("unexpected status lookup")),
    )
    runtime = TeamRuntime(
        teams,
        models,
        provider_recovery=recovery,
    )

    result = await runtime.start(run.id, cycle.id)
    task = teams.list_tasks(run.id, cycle.id)[0]
    worker_agent = teams.get_agent(task.owner_agent_id)

    assert result.status == "waiting_for_provider"
    assert teams.get_task(task.id).status == "waiting_for_provider"
    assert worker_agent.status == "waiting"
    assert not any(message.kind == "acceptance_review" for message in teams.list_messages(run.id))
```

- [ ] **Step 3: Write failing ambiguous-timeout reconciliation tests**

Add `consumer_run_id` to the error fixture and verify timeout never enters
provider waiting or semantic failure:

```python
@pytest.mark.asyncio
async def test_ambiguous_timeout_with_session_interrupts_without_replay(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    recovery = TeamProviderRecovery(
        teams,
        SimpleNamespace(get=lambda _provider: pytest.fail("unexpected status lookup")),
        session_loader=lambda: pytest.fail("known session must not query LMG"),
    )
    error = RemoteRunAbortedError(
        "run_timeout",
        "remote_run_timeout",
        upstream_session_id="claude-session-1",
        consumer_run_id="consumer-run-1",
    )

    await recovery.interrupt_ambiguous_run(
        run.id,
        cycle.id,
        task_id=task.id,
        agent_id=agent.id,
        provider="claude",
        error=error,
    )

    assert teams.get_team_run(run.id).status == "interrupted"
    assert teams.get_cycle(cycle.id).status == "interrupted"
    assert teams.get_task(task.id).status == "pending"
    assert teams.get_agent(agent.id).upstream_session_id == "claude-session-1"
```

Add a second coordinator test with no `upstream_session_id`. Its
`session_loader` returns one strict LMG session row matching provider,
`consumer_session_id == run.id`, and `consumer_run_id`; assert that upstream ID
is persisted before interruption. Add an unknown-outcome case returning `[]`;
assert the run remains `interrupted` with no automatic model call.

Add one `TeamRuntime` test using the same one-task setup as Step 2, but make the
worker raise `RemoteRunAbortedError(code="run_timeout", ...)`. Assert the
coordinator is called once, the task is returned to `pending`, the run/cycle
become `interrupted`, and neither a failed task nor `acceptance_review` is
created.

- [ ] **Step 4: Run the focused tests to verify they fail**

Run:

```powershell
pytest tests/test_remote_model_client.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py -q
```

Expected: FAIL because pre-stream safety is not represented and runtime converts every exception to task/run failure.

- [ ] **Step 5: Mark pre-stream failures and add the retry decorator**

Generate `consumer_run_id` before the request body and retain it on every
`RemoteRunError`. Add `pre_stream: bool = False` and
`consumer_run_id: str | None = None` to the base error. Set `pre_stream` true
only for:

- HTTP non-success responses returned before SSE starts;
- connect/write/pool timeout before a response opens;
- request errors before a response opens.

Do not set it for terminal `run.failed`, stream read failure, missing terminal, or timeout after response open.

After retry exhaustion, raise:

```python
raise ProviderRunUnavailable(
    provider=self._provider,
    reason_code=error.code,
    attempts=len(self._retry_delays) + 1,
) from error
```

Only wrap codes:

```python
{"provider_not_ready", "provider_unavailable", "capacity_exceeded"}
```

- [ ] **Step 6: Route provider exhaustion around generic runtime failure**

Inject `provider_recovery` into `TeamRuntime`.

In `_execute`, catch `ProviderRunUnavailable` before `Exception`, call `wait_for_failure()` with the current task/worker, publish `team.provider.waiting`, then return control by raising an internal `ProviderWaiting` marker.

In `start()` and `resume()`, catch `ProviderWaiting` before generic failure and return `self._teams.get_team_run(run.id)`. For provider failure during planning or synthesis, call `wait_for_failure()` with the leader and `task_id=None`.

Do not call `_review_acceptance`, `finish_task(..., "failed")`, `_package_results`, or `team.run.failed` for this path.

- [ ] **Step 7: Reconcile ambiguous timeouts without replay**

Catch `RemoteRunAbortedError(code="run_timeout")` before generic runtime
failure. `interrupt_ambiguous_run()` must:

1. use `error.upstream_session_id` when present;
2. otherwise call the injected strict LMG `session_loader` off the event loop
   with `asyncio.to_thread`;
3. accept only one row matching provider, Team run as `consumer_session_id`,
   and `error.consumer_run_id`;
4. persist a matched `upstream_id` on the current agent;
5. call the existing `TeamRunService.interrupt_run()` so the same task becomes
   pending and the cycle becomes interrupted;
6. leave an unmatched or failed reconciliation interrupted, never replayed.

The existing explicit Team resume path will then reuse the persisted upstream
session. Do not classify this path as `waiting_for_provider`, and do not invoke
Lead acceptance review.

- [ ] **Step 8: Keep dispatcher requests active while waiting**

Handle `ProviderRecoveryRequired` from cycle freezing by calling:

```python
self._provider_recovery.wait_for_failure(
    team_run_id,
    cycle.id,
    task_id=None,
    agent_id=None,
    provider=exc.provider,
    reason_code=exc.reason_code,
    attempts=3,
)
return
```

In `on_team_run_settled()`, return immediately when the cycle status is `waiting_for_provider`; never call `settle_cycle()` for that state.

- [ ] **Step 9: Wrap Team clients in `RetryingModelClient`**

In `_team_model_factory`, return:

```python
RetryingModelClient(
    HttpModelClient(...),
    provider=agent.backend,
)
```

Keep non-Team session behavior unchanged. Update the existing Team factory tests
to assert the wrapper provider and its `HttpModelClient` delegate instead of
continuing to expect a bare `HttpModelClient`.

- [ ] **Step 10: Run and commit runtime recovery routing**

Run:

```powershell
pytest tests/test_remote_model_client.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/personal_agent_gateway/remote_model_client.py src/personal_agent_gateway/team_provider_recovery.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/app.py tests/test_remote_model_client.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py
git commit -m "feat: provider pre-stream failure 자동 대기 처리"
```

### Task 6: Poll, recover, survive restart, and support guarded manual resume

**Files:**
- Modify: `src/personal_agent_gateway/team_provider_recovery.py`
- Modify: `src/personal_agent_gateway/team_cycle_loop.py:12-69`
- Modify: `src/personal_agent_gateway/team_cycles.py:14-39, 332-374, 615-638, 800-816`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py:212-243`
- Modify: `src/personal_agent_gateway/api/team_runs.py:33-42, 351-390, 604-645, 823-856, 1250-1311`
- Modify: `src/personal_agent_gateway/app.py:114-145, 203-240`
- Modify: `tests/test_team_cycle_loop.py`
- Modify: `tests/test_team_cycle_recovery.py`
- Modify: `tests/test_api_team_runs.py`

**Interfaces:**
- Produces:
  - `TeamProviderRecovery.recover_due(now) -> list[ProviderRecoveryClaim]`.
  - `TeamProviderRecovery.recover_cycle(cycle_id, now, *, force=True, trigger: Literal["auto", "manual"]) -> ProviderRecoveryClaim | None`.
  - sanitized `provider_recovery` in Team run detail cycle payload.

- [ ] **Step 1: Write a failing loop single-resume test**

Add these narrow doubles to `tests/test_team_cycle_loop.py`:

```python
class RecordingRecovery:
    def __init__(self, claims):
        self.claims = claims
        self.calls = 0

    def recover_due(self, _now):
        self.calls += 1
        if self.calls == 1:
            return self.claims
        return []


class RecordingResumeOrchestrator:
    def __init__(self):
        self.resume_calls = []

    def resume(self, team_run_id, cycle_id):
        self.resume_calls.append((team_run_id, cycle_id))
```

```python
@pytest.mark.asyncio
async def test_loop_recovers_ready_provider_cycle_once(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    teams.mark_waiting_for_provider(
        cycle.id,
        provider="codex",
        reason_code="provider_not_ready",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )
    recovery = RecordingRecovery([ProviderRecoveryClaim(run.id, cycle.id, task.id)])
    orchestrator = RecordingResumeOrchestrator()
    loop = TeamCycleLoop(cycles, RecordingDispatcher(), recovery, orchestrator)

    await loop.tick()
    await loop.tick()

    assert orchestrator.resume_calls == [(run.id, cycle.id)]
```

- [ ] **Step 2: Write a failing restart preservation test**

```python
def test_restart_reconcile_preserves_provider_waiting_and_dispatching_request(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    teams.mark_waiting_for_provider(
        cycle.id,
        provider="claude",
        reason_code="provider_not_ready",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )

    second = reopen_services(tmp_path)
    runnable = second.dispatcher.reconcile()

    assert second.teams.get_cycle(cycle.id).status == "waiting_for_provider"
    assert second.teams.get_team_run(cycle.team_run_id).status == "waiting_for_provider"
    assert second.cycles.get_request(cycle.request_id).status == "dispatching"
    assert cycle.team_run_id not in runnable
```

- [ ] **Step 3: Write failing API tests for sanitized detail and guarded resume**

Assert detail returns only:

```python
{
    "provider": "claude",
    "reason_code": "provider_not_ready",
    "first_failed_at": "2026-07-30T00:00:00+00:00",
    "next_retry_at": "2026-07-30T00:00:30+00:00",
    "warning_visible_at": "2026-07-30T00:02:00+00:00",
    "warning_visible": False,
}
```

Do not expose `agent_id`, task internals, raw exceptions, or frozen execution paths.

For `POST /api/team-runs/{id}/resume`, assert:

- unavailable provider: HTTP 200, run remains `waiting_for_provider`, no orchestrator call;
- ready provider: HTTP 200, one orchestrator `resume(run.id, cycle.id)` call;
- concurrent second request: no second resume.

- [ ] **Step 4: Write a failing sanitized observability test**

Use a marked waiting cycle and a ready descriptor detected at
`2026-07-30T00:00:00Z`, then call:

```python
claim = recovery.recover_cycle(
    cycle.id,
    dt("2026-07-30T00:01:00+00:00"),
    force=True,
    trigger="manual",
)
assert claim is not None
record = next(
    item for item in caplog.records
    if getattr(item, "transition", None) == "waiting_for_provider->running"
)
assert record.provider == "codex"
assert record.snapshot_status == "fresh"
assert record.snapshot_age_seconds == 60
assert record.recovery_trigger == "manual"
assert "token" not in record.getMessage().lower()
```

Add the corresponding wait-entry assertion for
`transition="running->waiting_for_provider"`, stable `reason_code`, and
`attempts=3`. Logging must use stable structured fields; do not include raw
exceptions, response payloads, local tokens, or provider stderr.

- [ ] **Step 5: Run the recovery/API tests to verify they fail**

Run:

```powershell
pytest tests/test_team_provider_recovery.py tests/test_team_cycle_loop.py tests/test_team_cycle_recovery.py tests/test_api_team_runs.py -q
```

Expected: FAIL because the loop handles only auto scheduling and resume accepts only interrupted runs.

- [ ] **Step 6: Implement readiness polling and due filtering**

`recover_due(now)` must:

1. Read `list_waiting_provider_cycles()`.
2. Skip cycles whose `next_retry_at` is later than `now`.
3. Refresh `AgentRegistry.get(provider)`, which performs Task 1 bounded status retry.
4. Require `descriptor.ready is True` and a non-null execution capability.
5. Call `claim_provider_recovery()` and collect only non-null claims.
6. If unavailable, update only `next_retry_at = now + 30 seconds`; preserve `first_failed_at`.

Hard `gateway_unauthorized`, `gateway_protocol_error`, invalid capability, and unsupported protocol errors must not auto-claim.

- [ ] **Step 7: Extend `TeamCycleLoop.tick()`**

After due auto requests:

```python
for claim in self._provider_recovery.recover_due(self._now()):
    self._orchestrator.resume(claim.team_run_id, claim.cycle_id)
```

The CAS in Task 4 is the duplicate-scheduling guard.

- [ ] **Step 8: Preserve waiting during reconciliation**

Update hard-coded active-cycle SQL/status sets to include `waiting_for_provider`. `settle_cycle()` must reject it as nonterminal without mutating request/series state. `policy_status()` must return `waiting_for_provider` when the dispatching cycle has that status. Dispatcher restart reconciliation must not convert it to `interrupted`.

- [ ] **Step 9: Implement guarded manual resume and sanitized payload**

Allow `resume_team_run()` for `waiting_for_provider`. Call
`recover_cycle(..., force=True, trigger="manual")`. `recover_due()` passes
`trigger="auto"`. Schedule the orchestrator only for a returned claim. Return
the current run either way.

Add:

```python
def _provider_recovery_payload(cycle: TeamRunCycle) -> dict[str, object] | None:
    # read cycle.execution_metadata["provider_recovery"]
    # copy only provider/reason/timestamps and compute warning_visible from UTC now
```

Include it as `"provider_recovery"` in `_cycle_payload`.

Treat provider waiting as active for cancellation and deletion guards.

- [ ] **Step 10: Add stable recovery logs**

On wait entry, unavailable poll, auto claim, and manual claim, log only:

```text
provider, snapshot_status, snapshot_age_seconds, reason_code, attempts,
transition, recovery_trigger
```

Omit fields that do not apply rather than filling them from raw diagnostics.
Task 1 owns per-attempt status lookup logging; this step owns Team state
transition and auto/manual trigger logging.

- [ ] **Step 11: Wire startup and loop dependencies**

Create one `TeamProviderRecovery` instance in `create_app`, inject it into `TeamRuntime`, dispatcher, and loop, and let the existing loop startup perform the first recovery tick. Do not create a second coordinator or in-memory retry registry.

- [ ] **Step 12: Run and commit loop/API recovery**

Run:

```powershell
pytest tests/test_team_provider_recovery.py tests/test_team_cycle_loop.py tests/test_team_cycle_recovery.py tests/test_api_team_runs.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/personal_agent_gateway/team_provider_recovery.py src/personal_agent_gateway/team_cycle_loop.py src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/app.py tests/test_team_provider_recovery.py tests/test_team_cycle_loop.py tests/test_team_cycle_recovery.py tests/test_api_team_runs.py
git commit -m "feat: waiting provider cycle 자동 복구"
```

### Task 7: Show provider waiting distinctly in Team Run UI

**Files:**
- Modify: `frontend/src/api/client.js:542-579`
- Modify: `frontend/src/api/client.test.js:199-225, 328-346`
- Modify: `frontend/src/components/atoms/StatusBadge/index.jsx:1-51`
- Modify: `frontend/src/components/molecules/TeamTaskCard/index.jsx:11-68`
- Modify: `frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx:10-42, 782-833, 1101-1120, 1248-1295`
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css:2816-2835, 3079-3160`

**Interfaces:**
- Consumes: cycle `provider_recovery` payload from Task 6.
- Produces: `detail.providerRecovery`, `WAITING FOR PROVIDER` task column/status, pre-warning next-check text, and post-120-second `RESUME`/`CANCEL` warning actions.

- [ ] **Step 1: Use `component-inspector` on both React components**

Inspect:

```text
frontend/src/components/organisms/TeamRunDetail/index.jsx
frontend/src/components/molecules/TeamTaskCard/index.jsx
```

Confirm props remain unchanged: the existing `onResume` and `onCancel` actions are reused. Do not create a new container or global state store.

- [ ] **Step 2: Write failing API mapping and UI tests**

API mapping:

```javascript
expect(await api.teamRunDetail("r1")).toEqual(expect.objectContaining({
  cycles: [expect.objectContaining({
    provider_recovery: expect.objectContaining({ provider: "claude" })
  })],
  providerRecovery: expect.objectContaining({
    provider: "claude",
    warning_visible: false
  })
}));
```

Detail before warning:

```javascript
expect(screen.getByRole("status", { name: "Provider recovery status" }))
  .toHaveTextContent("WAITING FOR PROVIDER");
expect(screen.getByText(/CLAUDE/)).toBeInTheDocument();
expect(screen.getByText(/NEXT CHECK/)).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
```

Detail after warning:

```javascript
expect(screen.getByText(/PROVIDER IS STILL UNAVAILABLE/)).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "Resume" }));
expect(onResume).toHaveBeenCalledTimes(1);
expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
```

Task card:

```javascript
expect(rendered.getByText("WAITING FOR PROVIDER")).toBeInTheDocument();
```

- [ ] **Step 3: Run the focused frontend tests to verify they fail**

Run:

```powershell
npm --prefix frontend test -- --run frontend/src/api/client.test.js frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
```

Expected: FAIL because provider waiting is not mapped or rendered.

- [ ] **Step 4: Map the active recovery**

In `teamRunDetail()`:

```javascript
const cycles = body?.cycles || [];
const providerRecovery = [...cycles]
  .sort((left, right) => Number(right.sequence || 0) - Number(left.sequence || 0))
  .find((cycle) => cycle.provider_recovery)?.provider_recovery || null;
```

Return both `cycles` and `providerRecovery`. The legacy fallback returns `providerRecovery: null`.

- [ ] **Step 5: Add status labels and the board column**

Add `waiting_for_provider: "WAITING FOR PROVIDER"` to `StatusBadge` labels and active statuses.

Change:

```javascript
const TEAM_TASK_COLUMNS = [
  "pending",
  "in_progress",
  "waiting_for_provider",
  "blocked",
  "completed",
  "failed"
];
```

Make `TeamTaskCard.statusLabel("waiting_for_provider")` return exactly `"WAITING FOR PROVIDER"`. Do not render it as failed or blocked and do not show an acceptance reason.

- [ ] **Step 6: Render one provider recovery banner**

When `run.status === "waiting_for_provider"`:

- render provider name and stable `reason_code`;
- before warning, show `NEXT CHECK · <formatted next_retry_at>`;
- after warning, show `PROVIDER IS STILL UNAVAILABLE`;
- show existing `onResume` and `onCancel` buttons only after `warning_visible`;
- set `canResume` and `canCancel` to include provider waiting;
- add provider waiting to the phase pause handling alongside interrupted/user waiting.

- [ ] **Step 7: Add neutral waiting styles**

Create `.team-provider-waiting-banner` using the existing Team detail typography and amber active-state token. Do not use red failed borders or blocked card styling. Add a responsive rule beside the existing `.team-interrupted-banner` and Task Board media query.

- [ ] **Step 8: Run tests and production build**

Run:

```powershell
npm --prefix frontend test -- --run frontend/src/api/client.test.js frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
npm run build:frontend
```

Expected: focused tests PASS and Vite production build succeeds.

- [ ] **Step 9: Commit the UI**

```powershell
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/components/atoms/StatusBadge/index.jsx frontend/src/components/molecules/TeamTaskCard/index.jsx frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/personal_agent_gateway/static/styles.css src/personal_agent_gateway/static
git commit -m "feat: Team provider waiting 복구 UI 추가"
```

Verify the staged static files are only the frontend build output generated by `npm run build:frontend`.

## PAG Completion Check

- [ ] Run the focused backend checks:

```powershell
pytest tests/test_lmg_client.py tests/test_agents.py tests/test_execution_contract.py tests/test_app_team_factory.py tests/test_remote_model_client.py tests/test_teams.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_loop.py tests/test_team_cycle_recovery.py tests/test_api_team_runs.py -q
```

- [ ] Run the focused frontend checks:

```powershell
npm --prefix frontend test -- --run frontend/src/api/client.test.js frontend/src/components/molecules/TeamTaskCard/TeamTaskCard.test.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
npm run build:frontend
```

- [ ] Inspect only this feature's final diff:

```powershell
git diff --check
git status --short
git log --oneline --max-count=8
```

Expected:

- transient LMG catalog/status failure cannot erase a usable capability snapshot;
- each Team cycle uses frozen provider capabilities;
- safe pre-stream failures receive exactly three attempts;
- exhaustion persists provider waiting without a failed task or Lead review;
- loop/manual recovery resumes the same cycle once;
- restart retains provider waiting and the dispatching request;
- warning/actions appear only after 120 seconds;
- all focused tests and frontend build pass.
