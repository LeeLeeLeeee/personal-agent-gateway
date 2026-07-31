# PAG Team Model Operation Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failed cycle-metadata continuation design with one durable operation row per cycle-backed Team model invocation so safe provider failures can wait and retry without replaying ambiguous or already-completed work.

**Architecture:** `TeamModelOperationService` owns the remote-call lifecycle before and after the network boundary. `TeamModelEffectService` coordinates one SQLite transaction that applies validated results through narrow `TeamRunService` mutations and marks the source operation applied. `TeamRuntime` selects semantic stages but never infers continuation from generic messages or cycle metadata.

**Tech Stack:** Python 3.11+, SQLite, asyncio, httpx, FastAPI application wiring, pytest/pytest-asyncio, Ruff.

## Global Constraints

- Start from PAG commit `c578e12`; do not cherry-pick any failed Task 5 commit from `feat/pag-provider-cycle-recovery`.
- LMG commit `ab58942` is the required provider capability/readiness prerequisite.
- Apply this design only to cycle-backed Team execution; non-cycle Team and normal chat execution retain current behavior.
- A cycle may have at most one operation in `prepared`, `invoking`, `completed`, `waiting_for_provider`, or `ambiguous`.
- Create and persist the operation before any remote model request.
- Retry only `provider_not_ready`, `provider_unavailable`, and `capacity_exceeded` when `pre_stream is True`.
- Total admission attempts are exactly 3 with delays `0.5` and `1.5` seconds.
- Response-open timeout, read timeout/error, stream failure, terminal failure, and unknown outcomes are never automatically replayed.
- An ambiguous operation remains `interrupted` unless one strict matching upstream session can be reused by explicit Resume.
- Only the user-facing Resume route may move an ambiguous operation back to `prepared`; the automatic dispatcher never may.
- Do not store raw prompts, raw model responses, provider stderr, credentials, or local tokens in operation rows, API payloads, or logs.
- Do not store runtime continuation, generation, or receipts in cycle execution metadata.
- Do not run the full test suite. Use only tests named by each task and the final focused command.

---

### Task 1: Add the operation ledger schema and lifecycle service

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py`
- Create: `src/personal_agent_gateway/team_model_operations.py`
- Modify: `tests/test_migrations.py`
- Create: `tests/test_team_model_operations.py`

**Interfaces:**
- Produces:
  - `OperationStage`
  - `OperationStatus`
  - `OperationSpec`
  - `TeamModelOperation`
  - `OperationConflict`
  - `StaleOperation`
  - `TeamModelOperationService.reserve(spec)`
  - `TeamModelOperationService.begin_attempt(operation_id, consumer_run_id)`
  - `TeamModelOperationService.complete(operation_id, expected_version, result, *, upstream_session_id)`
  - `TeamModelOperationService.prepare_retry(operation_id, expected_version, reason_code)`
  - `TeamModelOperationService.mark_failed(...)`
  - `TeamModelOperationService.mark_canceled(...)`
  - `TeamModelOperationService.get(operation_id)`
  - `TeamModelOperationService.get_by_key(operation_key)`
  - `TeamModelOperationService.get_open_for_cycle(cycle_id)`
  - `TeamModelOperationService.list_for_cycle(cycle_id)`

- [ ] **Step 1: Write the migration RED tests**

Add migration 20 imports and these assertions to `tests/test_migrations.py`:

```python
from personal_agent_gateway.migrations import _migration_20_team_model_operations


def test_migration_20_creates_team_model_operation_ledger_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    connection.executescript(
        """
        create table team_runs (id text primary key);
        create table team_run_cycles (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        create table team_tasks (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        create table team_agents (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        """
    )

    _migration_20_team_model_operations(connection)
    _migration_20_team_model_operations(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(team_model_operations)")
    }
    assert {
        "operation_key",
        "stage",
        "status",
        "version",
        "attempts",
        "consumer_run_id",
        "result_json",
        "effect_ref_json",
    } <= columns
    assert any(
        row["name"] == "idx_team_model_operations_one_open_cycle"
        for row in connection.execute(
            "select name from sqlite_master where type = 'index'"
        )
    )
```

- [ ] **Step 2: Run the migration test to verify RED**

Run:

```powershell
pytest tests/test_migrations.py::test_migration_20_creates_team_model_operation_ledger_idempotently -q
```

Expected: FAIL because migration 20 does not exist.

- [ ] **Step 3: Implement migration 20**

Add `_migration_20_team_model_operations()` and append it to `MIGRATIONS`:

```python
def _migration_20_team_model_operations(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_model_operations (
            id text primary key,
            operation_key text not null unique,
            team_run_id text not null
                references team_runs(id) on delete cascade,
            cycle_id text not null
                references team_run_cycles(id) on delete cascade,
            task_id text references team_tasks(id) on delete cascade,
            agent_id text not null
                references team_agents(id) on delete cascade,
            provider text not null,
            stage text not null,
            stage_ordinal integer not null check (stage_ordinal >= 0),
            status text not null,
            version integer not null default 0 check (version >= 0),
            attempts integer not null default 0 check (attempts >= 0),
            consumer_run_id text,
            upstream_session_id text,
            request_digest text not null,
            result_kind text,
            result_json text,
            result_digest text,
            effect_type text,
            effect_ref_json text,
            reason_code text,
            created_at text not null,
            started_at text,
            completed_at text,
            applied_at text,
            updated_at text not null
        );

        create unique index if not exists
        idx_team_model_operations_one_open_cycle
        on team_model_operations(cycle_id)
        where status in (
            'prepared', 'invoking', 'completed',
            'waiting_for_provider', 'ambiguous'
        );

        create index if not exists idx_team_model_operations_run_cycle
        on team_model_operations(team_run_id, cycle_id, created_at, id);
        """
    )
```

Register:

```python
(20, "team-model-operations", _migration_20_team_model_operations),
```

- [ ] **Step 4: Write lifecycle RED tests**

Create `tests/test_team_model_operations.py` with a real `Database`, cycle, and agents from `make_cycle_services()`:

```python
def operation_spec(run, cycle, agent, *, key="worker:0"):
    return OperationSpec(
        operation_key=f"{cycle.id}:{key}",
        team_run_id=run.id,
        cycle_id=cycle.id,
        task_id=None,
        agent_id=agent.id,
        provider=agent.backend,
        stage="cycle_planning",
        stage_ordinal=0,
        request_digest="request-digest",
    )


def test_reserve_is_idempotent_and_rejects_second_open_operation(tmp_path):
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)

    first = service.reserve(operation_spec(run, cycle, agent))
    duplicate = service.reserve(operation_spec(run, cycle, agent))

    assert duplicate.id == first.id
    assert duplicate.status == "prepared"
    with pytest.raises(OperationConflict):
        service.reserve(operation_spec(run, cycle, agent, key="other:0"))
```

Add a same-key mismatch test: changing actor, provider, stage, ordinal, request
digest, or supplying a different non-null session seed for an existing
`operation_key` must raise `OperationConflict` rather than return the old
operation.

Add:

```python
def test_lifecycle_uses_version_cas_and_completed_result_is_immutable(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    result = ValidatedOperationResult("task_plan", {"tasks": []})

    completed = service.complete(invoking.id, invoking.version, result)
    same = service.complete(invoking.id, invoking.version, result)

    assert completed.status == "completed"
    assert same.result_digest == completed.result_digest
    with pytest.raises(StaleOperation):
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", {"tasks": [{"id": "changed"}]}),
        )
```

Add a transaction rollback test showing a stale version leaves status, result, and timestamps unchanged.

- [ ] **Step 5: Implement the lifecycle service**

Use these exact public types:

```python
OperationStage = Literal[
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "worker_execution",
    "mediation_lead",
    "mediation_worker",
    "acceptance_lead",
    "acceptance_worker",
    "cycle_synthesis",
]

OperationStatus = Literal[
    "prepared",
    "invoking",
    "completed",
    "applied",
    "waiting_for_provider",
    "ambiguous",
    "failed",
    "canceled",
]


@dataclass(frozen=True)
class ValidatedOperationResult:
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class OperationSpec:
    operation_key: str
    team_run_id: str
    cycle_id: str
    task_id: str | None
    agent_id: str
    provider: str
    stage: OperationStage
    stage_ordinal: int
    request_digest: str
    upstream_session_id: str | None = None
```

Every write method must:

1. start `begin immediate`;
2. read the operation inside that transaction;
3. validate exact source status and version;
4. update with `where id = ? and status = ? and version = ?`;
5. require `rowcount == 1`;
6. return the reloaded immutable dataclass.

`reserve()` may return an existing row only when every immutable field matches
the incoming spec. It must validate the operation provider against the actor
agent backend and validate run/cycle/task/agent ownership before insertion. A
null session seed does not conflict with a session learned by an existing
operation; a supplied non-null seed must match it.

Compute result digests with canonical JSON:

```python
serialized = json.dumps(
    {"kind": result.kind, "payload": result.payload},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
```

- [ ] **Step 6: Run Task 1 focused verification**

Run:

```powershell
pytest tests/test_migrations.py tests/test_team_model_operations.py -q
ruff check src/personal_agent_gateway/migrations.py src/personal_agent_gateway/team_model_operations.py tests/test_migrations.py tests/test_team_model_operations.py
git diff --check
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/team_model_operations.py tests/test_migrations.py tests/test_team_model_operations.py
git commit -m "feat(team): 모델 실행 원장 추가"
```

---

### Task 2: Make the remote boundary operation-aware and classify retry safety

**Files:**
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Create: `src/personal_agent_gateway/team_model_invoker.py`
- Modify: `tests/test_remote_model_client.py`
- Create: `tests/test_team_model_invoker.py`

**Interfaces:**
- Consumes:
  - `OperationSpec`
  - `ValidatedOperationResult`
  - `TeamModelOperationService`
- Produces:
  - `OperationRemoteClient.complete_operation(messages, *, consumer_run_id)`
  - `RemoteRunError.pre_stream`
  - `RemoteRunError.consumer_run_id`
  - `ProviderOperationUnavailable`
  - `AmbiguousModelOperation`
  - `InvalidOperationResult`
  - `TeamModelInvoker.invoke(operation, client, messages, parser)`

- [ ] **Step 1: Write RemoteRunError and exact consumer ID RED tests**

Add:

```python
@pytest.mark.asyncio
async def test_complete_operation_uses_supplied_consumer_run_id():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return sse_response(
            run_started("run-1"),
            message_completed("run-1", "done"),
            run_completed("run-1"),
        )

    client = HttpModelClient(
        "http://lmg",
        "claude",
        "sonnet",
        execution={},
        transport=httpx.MockTransport(handler),
    )

    await client.complete_operation(
        [{"role": "user", "content": "work"}],
        consumer_run_id="operation-attempt-1",
    )

    assert captured["consumer_run_id"] == "operation-attempt-1"
```

Add parameterized tests proving:

- connect/write/pool failure before response open: `pre_stream is True`;
- HTTP admission codes before SSE: `pre_stream is True`;
- `ReadTimeout`, `ReadError`, response-open timeout, terminal failure, and missing terminal: `pre_stream is False`;
- every raised `RemoteRunError` retains the supplied `consumer_run_id`.

- [ ] **Step 2: Run the remote boundary tests to verify RED**

Run the new tests by exact node IDs. Expected: FAIL because the API and fields do not exist.

- [ ] **Step 3: Implement the operation-aware HttpModelClient entry point**

Keep the existing protocol compatible:

```python
async def complete(self, messages):
    return await self.complete_operation(
        messages,
        consumer_run_id=str(uuid.uuid4()),
    )


async def complete_operation(
    self,
    messages: list[dict[str, object]],
    *,
    consumer_run_id: str,
) -> ModelResponse:
    ...
```

Extend the base error:

```python
class RemoteRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        diagnostic: str,
        *,
        pre_stream: bool = False,
        consumer_run_id: str | None = None,
        partial_content: str = "",
        upstream_session_id: str | None = None,
    ) -> None:
        ...
```

Only connect, write, pool, and explicit HTTP admission failures before SSE are safe. Treat all read failures as ambiguous even before response headers.

- [ ] **Step 4: Write invoker RED tests**

Create a client double implementing `complete_operation()` and use a real operation service:

```python
@pytest.mark.asyncio
async def test_invoker_retries_only_safe_admission_with_same_operation(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [
            RemoteRunFailedError(
                "provider_not_ready",
                "not_ready",
                pre_stream=True,
            ),
            RemoteRunFailedError(
                "capacity_exceeded",
                "busy",
                pre_stream=True,
            ),
            ModelResponse(content='{"ok":true}', tool_calls=[]),
        ]
    )
    delays = []
    invoker = TeamModelInvoker(
        service,
        sleep=lambda delay: record_delay(delays, delay),
    )
    reserved = service.reserve(spec)

    operation = await invoker.invoke(
        reserved,
        client,
        [{"role": "user", "content": "work"}],
        lambda response: ValidatedOperationResult(
            "test",
            json.loads(response.content),
        ),
    )

    assert operation.status == "completed"
    assert operation.attempts == 3
    assert len(set(client.consumer_run_ids)) == 3
    assert service.get(operation.id).id == operation.id
    assert delays == [0.5, 1.5]
```

Add:

```python
@pytest.mark.asyncio
async def test_invoker_never_replays_ambiguous_read_timeout(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [RemoteRunAbortedError("run_timeout", "timeout")]
    )
    reserved = service.reserve(spec)

    with pytest.raises(AmbiguousModelOperation) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert client.calls == 1
    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.consumer_run_id == raised.value.consumer_run_id
```

Add exhausted safe retry coverage asserting `ProviderOperationUnavailable`,
operation status `invoking`, attempts 3, the last stable reason code, and no
fourth client call. Task 6 owns the atomic operation+Team waiting transition.

Add a parser-failure test asserting `InvalidOperationResult`, operation status
`failed`, stable reason `invalid_structured_output`, and preservation of the
response `upstream_session_id` on the operation without copying it to an agent.

- [ ] **Step 5: Implement TeamModelInvoker**

Use:

```python
class OperationRemoteClient(Protocol):
    async def complete_operation(
        self,
        messages: list[dict[str, object]],
        *,
        consumer_run_id: str,
    ) -> ModelResponse: ...


class TeamModelInvoker:
    async def invoke(
        self,
        operation: TeamModelOperation,
        client: OperationRemoteClient,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
    ) -> TeamModelOperation:
        ...
```

The invoker must:

- reuse an existing `completed` operation without a model call;
- reject `applied`, `ambiguous`, and `waiting_for_provider` as new calls;
- generate and persist each `consumer_run_id` before awaiting the client;
- keep the operation ID stable across safe attempts;
- call `prepare_retry()` only for the exact safe code set and `pre_stream=True`;
- leave the final operation `invoking` and raise
  `ProviderOperationUnavailable` after safe retry exhaustion so the recovery
  coordinator can atomically persist operation and Team waiting state;
- leave ambiguous errors `invoking` and raise `AmbiguousModelOperation` with
  operation and consumer-run identity so the recovery coordinator can
  atomically persist operation and Team interruption;
- pass the validated result and response session to `complete()`;
- convert parser failure to `InvalidOperationResult`, close the operation as
  `failed`, and never classify it as provider retry.

- [ ] **Step 6: Run Task 2 focused verification**

Run:

```powershell
pytest tests/test_remote_model_client.py tests/test_team_model_invoker.py -q
ruff check src/personal_agent_gateway/remote_model_client.py src/personal_agent_gateway/team_model_invoker.py tests/test_remote_model_client.py tests/test_team_model_invoker.py
git diff --check
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/personal_agent_gateway/remote_model_client.py src/personal_agent_gateway/team_model_invoker.py tests/test_remote_model_client.py tests/test_team_model_invoker.py
git commit -m "feat(team): 모델 호출 retry 경계 영속화"
```

---

### Task 3: Apply planning and Worker results atomically with their operations

**Files:**
- Create: `src/personal_agent_gateway/team_model_effects.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Create: `tests/test_team_model_effects.py`
- Modify: `tests/test_teams.py`

**Interfaces:**
- Consumes:
  - completed `TeamModelOperation`
  - existing task plan and `TaskOutcome` parsers
- Produces:
  - `WorkerEffectResult`
  - `TeamModelEffectService.apply_plan(operation_id, specs)`
  - `TeamModelEffectService.apply_worker_outcome(operation_id, outcome, acceptance, changes)`
  - `TeamModelEffectService.apply_synthesis(operation_id, summary)`

- [ ] **Step 1: Write atomic plan apply RED tests**

Create:

```python
def test_apply_plan_and_operation_are_atomic_and_idempotent(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult(
            "task_plan",
            {"tasks": [valid_task_spec("Research")]},
        ),
    )

    first = services.effects.apply_plan(services.operation.id)
    second = services.effects.apply_plan(services.operation.id)

    assert [task.id for task in second] == [task.id for task in first]
    assert len(services.teams.list_tasks(services.run.id, services.cycle.id)) == 1
    applied = services.operations.get(services.operation.id)
    assert applied.status == "applied"
    assert applied.effect_type == "task_plan"
```

Add a rollback test that injects an invalid owner agent in the validated plan payload and asserts:

- zero tasks created;
- operation remains `completed`;
- no plan note inserted.

- [ ] **Step 2: Write Worker apply RED tests**

Add:

```python
def test_worker_result_apply_is_atomic_and_does_not_finish_recoverable_rejection(
    tmp_path,
):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )
    rejected = AcceptanceResult(
        accepted=False,
        status="failed",
        reason_code="undeclared_deliverable",
        evidence={},
    )

    result = services.effects.apply_worker_outcome(
        services.operation.id,
        rejected,
        workspace_changes={"created": ["draft.md"], "modified": [], "deleted": []},
    )

    task = services.teams.get_task(services.task.id)
    assert result.next_stage == "acceptance_lead"
    assert task.status == "in_progress"
    assert task.outcome is not None
    assert task.acceptance_result is not None
    assert len(
        [
            message
            for message in services.teams.list_messages(services.run.id)
            if message.kind == "agent_output"
        ]
    ) == 1
    assert services.operations.get(services.operation.id).status == "applied"
```

Also cover:

- accepted outcome finishes task and worker in the same transaction;
- non-recoverable rejection finishes failed in the same transaction;
- `UserDecisionResolution` creates exactly one decision request and blocks the task;
- duplicate apply does not append another message or decision.

- [ ] **Step 3: Implement transaction-local effect application**

`TeamModelEffectService` receives `Database`, `TeamRunService`, and the operation service. It must not call public Team methods that open a second connection from inside an apply transaction.

Add narrow connection-aware helpers to `TeamRunService` only where needed:

```python
def _task_from_connection(
    self,
    connection: sqlite3.Connection,
    task_id: str,
) -> TeamTask:
    ...


def _agent_from_connection(
    self,
    connection: sqlite3.Connection,
    agent_id: str,
) -> TeamAgent:
    ...
```

Implement one internal operation finalizer:

```python
def _mark_applied(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    *,
    effect_type: str,
    effect_ref: dict[str, object],
    now: str,
) -> None:
    ...
```

Each public apply method starts `begin immediate`, reloads the completed operation, validates its exact stage and actor/task ownership, applies domain rows, promotes the operation's confirmed upstream session to the actor agent, and marks the operation applied before commit.

Do not infer successful replay from generic messages. Duplicate apply reads `effect_ref_json`, validates referenced rows, and returns them.

- [ ] **Step 4: Add safe metadata merge methods**

The current provider freeze and app factory perform whole-object metadata replacement. Add:

```python
def set_cycle_provider_capabilities(
    self,
    cycle_id: str,
    snapshots: dict[str, object],
) -> TeamRunCycle: ...


def set_cycle_agent_execution_metadata(
    self,
    cycle_id: str,
    agent_id: str,
    metadata: dict[str, object],
) -> TeamRunCycle: ...
```

Both must use `begin immediate`, reload current JSON, replace only their owned key, and require one updated row. Keep `set_cycle_execution_metadata()` only for controlled test setup until all production callers are migrated in Task 6.

Add an interleaving test proving both methods preserve unrelated provider recovery metadata.

- [ ] **Step 5: Run Task 3 focused verification**

Run:

```powershell
pytest tests/test_team_model_effects.py tests/test_teams.py tests/test_team_provider_recovery.py -q
ruff check src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/teams.py tests/test_team_model_effects.py tests/test_teams.py
git diff --check
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```powershell
git add src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/teams.py tests/test_team_model_effects.py tests/test_teams.py
git commit -m "feat(team): 모델 결과와 Team 상태 원자 적용"
```

---

### Task 4: Route planning, Worker, and synthesis through the operation ledger

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_team_cycle_dispatcher.py`

**Interfaces:**
- Consumes:
  - `TeamModelInvoker`
  - `TeamModelEffectService`
- Produces:
  - `TeamRuntime` operation-aware planning, add-work, Worker, and synthesis paths
  - deterministic operation-key helpers

- [ ] **Step 1: Write planning/add-work RED tests**

Add a real continuous-cycle runtime test:

```python
@pytest.mark.asyncio
async def test_add_work_repair_uses_separate_operation_and_defers_lead_session(
    tmp_path,
):
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("not-json", [], upstream_session_id="lead-session-1"),
        ModelResponse(valid_plan_json(), [], upstream_session_id="lead-session-1"),
    ]

    await setup.runtime.add_work(
        setup.run.id,
        "research",
        setup.cycle.id,
    )

    operations = setup.operations.list_for_cycle(setup.cycle.id)
    assert [item.stage for item in operations] == [
        "cycle_add_work",
        "cycle_planning_repair",
    ]
    assert all(item.status in {"failed", "applied"} for item in operations)
    assert setup.teams.get_agent(setup.run.leader_agent_id).upstream_session_id == (
        "lead-session-1"
    )
```

- [ ] **Step 2: Write completed-before-apply restart RED test**

```python
@pytest.mark.asyncio
async def test_completed_worker_operation_applies_without_second_model_call(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)

    result = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.calls == 0
    assert setup.operations.get(setup.worker_operation.id).status == "applied"
    assert setup.teams.get_task(setup.task.id).outcome is not None
    assert result.status in {"running", "completed", "completed_with_failures"}
```

- [ ] **Step 3: Refactor Runtime model calls through one helper**

Add a private helper:

```python
async def _invoke_operation(
    self,
    spec: OperationSpec,
    agent: TeamAgent,
    messages: list[dict[str, object]],
    parser: Callable[[ModelResponse], ValidatedOperationResult],
) -> TeamModelOperation:
    operation = self._operations.reserve(spec)
    client = self._model(
        agent,
        spec.cycle_id,
        upstream_session_id=operation.upstream_session_id,
    )
    return await self._model_invoker.invoke(
        operation,
        client,
        messages,
        parser,
    )
```

Update `_plan()`, `add_work()`, initial `_run_task()`, Worker repair calls, and `_leader_synthesis()` to use it only when `cycle_id` is non-null. Preserve the existing direct client path for non-cycle calls. The session override is operation-owned and must not mutate the agent before effect application.

Build `request_digest` from canonical JSON of the stage, ordinal, actor ID, and
exact outbound messages. Persist only the SHA-256 digest. Re-reserving the same
operation key with changed messages must fail before a model call.

Every structured-output repair is a new semantic operation after the invalid
operation is closed: planning/add-work uses `cycle_planning_repair:1`; Worker
JSON repair uses `worker_execution:<next ordinal>` with the failed operation's
session seed. A repair never reopens the failed operation.

Before selecting a new semantic stage, Runtime must check:

```python
open_operation = self._operations.get_open_for_cycle(cycle_id)
```

It must:

- apply `completed` locally;
- invoke `prepared`;
- refuse to invoke `waiting_for_provider` or `ambiguous`;
- never create a new operation while another is open.

Task 6 adds the atomic Team-state transition and persisted-state markers for
the refused waiting/ambiguous cases.

- [ ] **Step 4: Keep planning session ownership deferred**

For invalid planning output:

- mark the first operation failed with `invalid_structured_output`;
- preserve upstream session only on that operation;
- create one repair `OperationSpec` with the failed operation's upstream session;
- set the Lead agent session only when a valid task plan is atomically applied.

No third planning call is allowed.

- [ ] **Step 5: Route synthesis through a separate operation**

Reserve `cycle_synthesis:0` only after required tasks are terminal. Apply the summary and operation status atomically. `UserDecisionResolution` from synthesis uses the existing run-decision flow but must mark the synthesis operation applied in the same transaction.

- [ ] **Step 6: Run Task 4 focused verification**

Run:

```powershell
pytest tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_team_model_operations.py tests/test_team_model_effects.py -q
ruff check src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py
git diff --check
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py
git commit -m "feat(team): cycle 모델 호출을 실행 원장으로 라우팅"
```

---

### Task 5: Persist Lead mediation and acceptance as separate operations

**Files:**
- Modify: `src/personal_agent_gateway/team_model_effects.py`
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `tests/test_team_model_effects.py`
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Produces:
  - operation stages `mediation_lead`, `mediation_worker`
  - operation stages `acceptance_lead`, `acceptance_worker`
  - atomic Lead audit/attempt/decision effects

- [ ] **Step 1: Write the separate acceptance-operation RED test**

```python
@pytest.mark.asyncio
async def test_lead_acceptance_retry_uses_separate_worker_operation(tmp_path):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.worker_client.responses = [
        completed_outcome_json("draft.md"),
        completed_outcome_json("draft-fixed.md"),
    ]
    setup.lead_client.responses = [
        acceptance_retry_worker_json("fix citation"),
        "summary",
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_client.initial_execution_calls == 1
    assert setup.worker_client.revision_calls == 1
    assert setup.operations.get_by_key(
        acceptance_lead_key(setup.cycle.id, setup.task.id, 1)
    ).status == "applied"
    assert setup.operations.get_by_key(
        acceptance_worker_key(setup.cycle.id, setup.task.id, 1)
    ).status == "applied"
```

The Worker revision may create one `acceptance_worker:1` operation; it must not reuse the initial `worker_execution:0` operation.

- [ ] **Step 2: Write the Lead ownership RED test**

Add:

```python
@pytest.mark.asyncio
async def test_lead_review_session_is_owned_by_lead_and_keeps_worker_applied(
    tmp_path,
):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(
            acceptance_fail_json("requirements_not_met"),
            [],
            upstream_session_id="lead-session",
        )
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    operation = setup.operations.get_by_key(
        acceptance_lead_key(setup.cycle.id, setup.task.id, 1)
    )
    assert operation.stage == "acceptance_lead"
    assert operation.agent_id == setup.run.leader_agent_id
    assert operation.status == "applied"
    assert setup.teams.get_agent(setup.run.leader_agent_id).upstream_session_id == (
        "lead-session"
    )
    assert setup.teams.get_agent(setup.worker.id).upstream_session_id is None
    assert setup.worker_client.initial_execution_calls == 1
```

- [ ] **Step 3: Implement atomic mediation effects**

Add to `TeamModelEffectService`:

```python
def apply_mediation_lead(
    self,
    operation_id: str,
    resolution: MediationResolution,
) -> MediationEffectResult:
    ...
```

The transaction must validate:

- operation stage and Lead actor;
- task ownership by the Worker;
- exact current `rounds_used`;
- resolution schema.

For an answer, increment the round, append one Lead-to-Worker answer, and apply the operation. For `ask_user`, create one decision request, block the task, and apply the operation. Generic message lookup is never a replay source.

- [ ] **Step 4: Implement atomic acceptance effects**

Add:

```python
def apply_acceptance_lead(
    self,
    operation_id: str,
    resolution: AcceptanceReviewResolution,
) -> AcceptanceEffectResult:
    ...
```

The same transaction must:

- bind Lead and Worker from the operation/task;
- validate the current rejected outcome and acceptance result;
- append one `acceptance_review`;
- increment attempts only for `retry_worker` or `revise_acceptance`;
- update acceptance only for `revise_acceptance`;
- create a user decision for `ask_user`;
- finish terminal failed for `fail`;
- mark the operation applied.

The returned result tells Runtime whether to reserve `acceptance_worker:<attempt>`, defer, or stop.

- [ ] **Step 5: Resume the exact open Lead/Worker operation first**

At the start of `_execute()`, inspect the open operation before selecting a pending task. If the stage is Lead-owned, Runtime invokes/applies only that Lead stage. If it is a Worker revision stage, invoke only that revision operation using the stored task and acceptance attempt.

Never call the initial Worker operation after it is applied.

- [ ] **Step 6: Add crash-boundary tests**

Cover:

- completed Lead operation applies after restart without a second Lead call;
- applied Lead decision creates exactly one audit row;
- accepted Worker revision finishes task exactly once;
- user decision apply creates exactly one request;
- Lead session application never writes the session to the Worker.

- [ ] **Step 7: Run Task 5 focused verification**

Run:

```powershell
pytest tests/test_team_model_effects.py tests/test_team_runtime.py -q
ruff check src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py tests/test_team_model_effects.py tests/test_team_runtime.py
git diff --check
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```powershell
git add src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py tests/test_team_model_effects.py tests/test_team_runtime.py
git commit -m "feat(team): Lead 복구 단계를 별도 operation으로 보존"
```

---

### Task 6: Connect provider waiting, ambiguous reconciliation, dispatcher, and app wiring

**Files:**
- Modify: `src/personal_agent_gateway/team_provider_recovery.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py`
- Modify: `src/personal_agent_gateway/team_run_orchestrator.py`
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `tests/test_team_provider_recovery.py`
- Modify: `tests/test_team_cycle_dispatcher.py`
- Modify: `tests/test_team_runtime.py`
- Modify: `tests/test_app_team_factory.py`
- Modify: `tests/test_api_team_runs.py`
- Modify: `tests/team_cycle_helpers.py`

**Interfaces:**
- Consumes:
  - `ProviderOperationUnavailable`
  - `AmbiguousModelOperation`
  - operation lifecycle/effects
- Produces:
  - `ProviderRecoveryClaim.operation_id`
  - `TeamProviderRecovery.wait_for_operation(...)`
  - `TeamProviderRecovery.interrupt_ambiguous_operation(...)`
  - `TeamProviderRecovery.prepare_explicit_resume(...)`
  - `TeamProviderRecovery.claim_operation(...)`
  - startup operation reconciliation

- [ ] **Step 1: Write operation-aware provider waiting RED tests**

Extend `ProviderRecoveryClaim` to include `operation_id` and add:

```python
def test_claim_lead_waiting_operation_restores_stage_without_pending_worker(
    tmp_path,
):
    setup = make_waiting_acceptance_operation(tmp_path)

    claim = setup.recovery.claim_operation(
        setup.cycle.id,
        now=dt("2026-07-31T00:00:30+00:00"),
    )

    assert claim.operation_id == setup.operation.id
    assert setup.operations.get(setup.operation.id).status == "prepared"
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    assert setup.teams.get_task(setup.task.id).owner_agent_id == setup.worker.id
    assert setup.teams.get_agent(setup.run.leader_agent_id).status == "running"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "dispatching"
```

Add Worker and preplanning variants. A concurrent second claim must return `None`.

- [ ] **Step 2: Implement operation-aware waiting transition**

`wait_for_operation()` must run one transaction that:

- validates the operation is the cycle's single open operation;
- validates operation stage, task, actor, provider, and source statuses;
- changes operation from `invoking` to `waiting_for_provider`;
- sets run/cycle/task/calling-agent waiting statuses;
- preserves request `dispatching`;
- stores sanitized provider recovery metadata containing `operation_id`.

`claim_operation()` must:

- CAS the operation from waiting to prepared;
- restore stage-specific source statuses;
- never reset a Lead-stage task to pending;
- remove only provider recovery metadata;
- return the same claim once.

- [ ] **Step 3: Write ambiguous reconciliation RED tests**

Add:

```python
@pytest.mark.asyncio
async def test_ambiguous_operation_without_one_strict_session_stays_interrupted(
    tmp_path,
):
    setup = make_invoking_worker_operation(tmp_path)
    setup.session_loader.result = []

    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id=None,
    )

    assert setup.operations.get(setup.operation.id).status == "ambiguous"
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"
    assert setup.teams.get_cycle(setup.cycle.id).status == "interrupted"
    assert setup.orchestrator.resume_calls == []
```

Add exact-one matching session, duplicate sessions, provider mismatch, consumer-run mismatch, and loader exception. Only exact-one may persist upstream session. None automatically invokes a model.

Add API-level coverage for `POST /team-runs/{id}/resume`:

- an exact session match atomically restores the original semantic source state,
  moves only that operation `ambiguous -> prepared`, and schedules Runtime;
- no match, duplicate match, identity mismatch, or loader failure returns `409`
  with `ambiguous_operation_not_reconcilable`, leaves run/cycle/operation
  interrupted, and does not schedule Runtime;
- a resume triggered internally after a user decision never claims an ambiguous
  operation.

`prepare_explicit_resume()` must validate an already-recorded
`upstream_session_id` against LMG identity, or perform the strict
provider/team-run/consumer-run lookup when it is absent. It must never create a
session. On success, Runtime constructs the client with the operation's stored
session override (without first copying it to the agent row) and resumes that
same semantic operation. The agent session is persisted only with the validated
domain effect.

- [ ] **Step 4: Implement startup reconciliation**

Add:

```python
def reconcile_startup(self) -> OperationReconcileResult:
    ...
```

Rules:

- `invoking` -> `ambiguous` and run/cycle interrupted;
- `prepared` -> runnable;
- `completed` -> locally applicable;
- waiting/ambiguous remain unchanged;
- applied/failed/canceled are ignored.

Wire this before dispatcher startup. Do not schedule ambiguous operations.

- [ ] **Step 5: Route dispatcher markers before generic failure**

`TeamRuntime` catches `ProviderOperationUnavailable`, calls
`wait_for_operation()`, and raises an internal `ProviderOperationWaiting`
marker only after the atomic waiting transition commits. It catches
`AmbiguousModelOperation`, calls `interrupt_ambiguous_operation()`, and
re-raises only after the atomic interruption commits.

Every Runtime catch boundary that currently converts arbitrary exceptions into
task/run failure (`_execute()`, `start()`, and `resume()`) must re-raise
`ProviderOperationWaiting` and `AmbiguousModelOperation` before its generic
`Exception` handler. `add_work()` must likewise let these markers propagate.

`TeamCycleDispatcher.run_one()` must catch the persisted-state
`ProviderOperationWaiting` and `AmbiguousModelOperation` markers before
`Exception`.

For waiting:

- return without setting cycle failed;
- do not call `settle_cycle()`;
- keep request dispatching.

For ambiguous:

- preserve interrupted state;
- use existing interrupted series policy;
- never convert to failed.

`on_team_run_settled()` must return immediately for `waiting_for_provider`.

The user-facing resume endpoint must call `prepare_explicit_resume()` before
`TeamRunOrchestrator.resume()`. Automatic cycle dispatch and other internal
resume callers must not call it.

- [ ] **Step 6: Wire one shared service graph**

In `create_app()` create exactly one each:

```python
operation_service = TeamModelOperationService(database)
effect_service = TeamModelEffectService(
    database,
    app.state.team_run_service,
    operation_service,
)
operation_invoker = TeamModelInvoker(operation_service)
provider_recovery = TeamProviderRecovery(
    app.state.team_run_service,
    app.state.agent_registry,
    operation_service,
    session_loader=fetch_sessions_strict,
)
```

Inject those exact objects into Runtime, dispatcher, and startup reconciliation.

Update `_team_model_factory()` only to:

- return an `HttpModelClient` implementing `complete_operation()`;
- use `set_cycle_agent_execution_metadata()` instead of whole metadata replacement.

Update provider freeze to use `set_cycle_provider_capabilities()`.

Non-Team model factory behavior must remain unchanged.

- [ ] **Step 7: Add final integration regressions**

Cover:

- production dispatcher → orchestrator → `add_work()` provider wait;
- invalid planning operation closed, repair operation waiting, Lead session still
  unpromoted, and run/cycle/request not failed or settled;
- Worker applied then Lead wait then claim/resume with Worker initial call count 1;
- Worker applied then Lead ambiguous interruption with no automatic replay;
- completed operation startup local apply with model call count 0;
- invoking operation startup interruption;
- app creates one shared operation/recovery graph;
- no production `set_cycle_execution_metadata()` call remains:

```powershell
rg -n "set_cycle_execution_metadata" src/personal_agent_gateway
```

Expected: no call site outside the method definition.

- [ ] **Step 8: Run the final focused verification**

Run:

```powershell
pytest tests/test_migrations.py tests/test_team_model_operations.py tests/test_remote_model_client.py tests/test_team_model_invoker.py tests/test_team_model_effects.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_app_team_factory.py tests/test_api_team_runs.py -q
ruff check src/personal_agent_gateway/migrations.py src/personal_agent_gateway/team_model_operations.py src/personal_agent_gateway/team_model_invoker.py src/personal_agent_gateway/team_model_effects.py src/personal_agent_gateway/remote_model_client.py src/personal_agent_gateway/team_provider_recovery.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/team_run_orchestrator.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py tests/test_migrations.py tests/test_team_model_operations.py tests/test_remote_model_client.py tests/test_team_model_invoker.py tests/test_team_model_effects.py tests/test_team_provider_recovery.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_app_team_factory.py tests/test_api_team_runs.py
git diff --check
```

Expected: PASS. Do not run the full suite.

- [ ] **Step 9: Commit Task 6**

```powershell
git add src/personal_agent_gateway/team_provider_recovery.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/team_run_orchestrator.py src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py tests/test_team_provider_recovery.py tests/test_team_cycle_dispatcher.py tests/test_team_runtime.py tests/test_app_team_factory.py tests/test_api_team_runs.py tests/team_cycle_helpers.py
git commit -m "feat(team): operation 기반 provider 복구 연결"
```

## Completion Gate

Before starting the existing polling/manual-resume/UI follow-up plan:

- every task above has a clean independent review;
- no Important-or-higher finding remains;
- final focused tests pass from a clean worktree;
- `git diff --check` passes;
- operation result/application never depends on generic message lookup;
- cycle execution metadata contains no runtime continuation, generation, or receipt;
- ambiguous operations have no automatic model call path.
