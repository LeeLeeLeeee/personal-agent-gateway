# Continuous Team Run Cycle Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 신규 Team Run을 CONTINUOUS로 고정하고 AUTO 또는 TRIGGERED 정책의 모든 실행 원인을 영속 FIFO CycleRequest 큐로 직렬 처리한다.

**Architecture:** `TeamCycleService`가 AUTO Series와 CycleRequest의 source of truth를 소유하고, `TeamCycleDispatcher`가 Run별 요청 하나만 claim해 기존 `TeamRunOrchestrator`로 실행한다. `TeamCycleLoop`, Manual API, HookRunner는 모두 같은 enqueue 경로를 사용하며, UI는 정책 상태와 현재 Cycle 상태를 분리해 표시한다.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, asyncio, pytest/pytest-asyncio, React 19, Vite 6, Vitest, Testing Library

## Global Constraints

- 신규 Team Run의 `lifecycle_mode`는 항상 `continuous`, `run_mode`는 항상 `plan_and_execute`, `max_workers`는 항상 `1`이다.
- `execution_policy`는 `auto | triggered` 중 정확히 하나이며 실행 중 변경하지 않는다.
- AUTO 첫 slot은 생성 즉시 enqueue하고 다음 slot은 이전 slot 정산 시각부터 interval을 계산한다.
- AUTO와 TRIGGERED를 한 Run에서 동시에 활성화하지 않는다.
- 한 Run에서는 Cycle 하나만 실행한다. Manual과 Hook 요청은 같은 FIFO를 사용한다.
- `CONTINUE`는 실패 slot을 N에 포함하고, `RETRY`는 같은 slot의 명시적 추가 시도로 lineage를 남긴다.
- `waiting_for_user`와 `interrupted`는 같은 Cycle을 resume하며 다음 요청을 먼저 실행하지 않는다.
- 이전 Cycle summary는 Trigger 시점에 snapshot하고 리더 add-work 프롬프트에만 전달한다.
- 기존 STANDARD 기록은 조회·기존 resume 호환을 유지하되 신규 생성 UI에서는 노출하지 않는다.
- 기존 Continuous Run은 `triggered`로 backfill하며 기존 Hook 연결을 유지한다.
- 기존 테스트 fixture가 `create_team_run(..., lifecycle_mode="continuous")`를 직접
  호출하는 모든 위치에는 기존 의미를 보존하도록 `execution_policy="triggered"`를
  추가하고, AUTO 전용 테스트만 `"auto"`를 사용한다.
- Cron, 다중 Cycle 병렬 실행, policy 변경, Worker transcript 직접 주입은 구현하지 않는다.
- 새 외부 의존성을 추가하지 않는다.

---

## File Structure

### 신규 파일

- `src/personal_agent_gateway/team_cycles.py`
  - `TeamAutoSeries`, `TeamCycleRequest`, `TeamCycleService`
  - Series/Request 생성, FIFO claim, settlement, retry/continue/restart, due 조회
- `src/personal_agent_gateway/team_cycle_dispatcher.py`
  - Run별 단일 Cycle 실행, preparer/settlement observer, queue wake-up
- `src/personal_agent_gateway/team_cycle_loop.py`
  - 주입 가능한 clock으로 due AUTO slot enqueue
- `tests/test_team_cycles.py`
  - 저장소 상태 전이와 idempotency
- `tests/team_cycle_helpers.py`
  - Cycle 정책 테스트가 공유하는 DB/Persona/Run builder와 ISO datetime parser
- `tests/test_team_cycle_dispatcher.py`
  - 직렬 실행, leader-only 이전 summary, pause/resume settlement
- `tests/test_team_cycle_loop.py`
  - fake clock AUTO due 처리
- `tests/test_team_cycle_recovery.py`
  - startup reconciliation 회귀

### 수정 파일

- `src/personal_agent_gateway/db.py`, `src/personal_agent_gateway/migrations.py`
  - schema v11, 레거시 backfill, partial unique index
- `src/personal_agent_gateway/teams.py`
  - `execution_policy`, transactional Run 생성, Cycle `request_id`
- `src/personal_agent_gateway/team_run_orchestrator.py`
  - 기존 observer 계약을 Dispatcher가 사용할 수 있도록 유지
- `src/personal_agent_gateway/hook_runs.py`, `src/personal_agent_gateway/hook_runner.py`, `src/personal_agent_gateway/hooks.py`
  - HookRun→CycleRequest lineage와 TRIGGERED 검증
- `src/personal_agent_gateway/api/team_runs.py`, `src/personal_agent_gateway/api/operations.py`
  - 생성/Manual/AUTO action/detail read model/운영 상태
- `src/personal_agent_gateway/health.py`
  - Cycle Dispatcher/Loop background health
- `src/personal_agent_gateway/app.py`
  - 서비스 배선, startup reconcile, loop/dispatcher lifecycle
- `frontend/src/api/client.js`, `frontend/src/hooks/useTeamRunController.js`
  - 신규 HTTP action과 상세 갱신
- `frontend/src/components/organisms/TeamPicker/index.jsx`
  - CONTINUOUS fixed + AUTO/TRIGGERED form
- `frontend/src/components/organisms/TeamRunDetail/index.jsx`
  - 정책 제어 패널, 이전 Cycle summary, Trigger UI
- `frontend/src/components/organisms/HooksView/index.jsx`
  - TRIGGERED Run만 target으로 표시
- `frontend/src/components/organisms/OperationsView/index.jsx`
  - queue/next run/pause reason 표시
- `frontend/src/components/containers/GatewayApp/index.jsx`
  - 신규 controller action 전달
- `src/personal_agent_gateway/static/styles.css`
  - 정책/큐/summary UI 스타일
- 대응하는 기존 pytest/Vitest 테스트 파일

---

### Task 1: Schema v11과 레거시 마이그레이션

**Files:**
- Modify: `src/personal_agent_gateway/db.py`
- Modify: `src/personal_agent_gateway/migrations.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_db_hooks_schema.py`

**Interfaces:**
- Consumes: 기존 `Database.initialize()`와 `MIGRATIONS` 순차 적용 계약
- Produces: `team_runs.execution_policy`, `team_run_auto_series`, `team_cycle_requests`, `team_run_cycles.request_id`, `hook_runs.team_cycle_request_id`

- [ ] **Step 1: v10 데이터베이스가 v11로 올라가는 실패 테스트 작성**

```python
def test_migration_11_adds_cycle_policy_queue_and_backfills_continuous() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table team_runs (
            id text primary key, goal text not null, status text not null,
            run_mode text not null, lifecycle_mode text not null,
            max_workers integer not null, workspace_root text not null,
            created_at text not null, updated_at text not null
        );
        create table team_run_cycles (
            id text primary key, team_run_id text not null,
            sequence integer not null, source_type text not null,
            source_id text not null, status text not null,
            rounds_budget integer not null, rounds_used integer not null default 0,
            created_at text not null, updated_at text not null
        );
        create table hook_runs (
            id text primary key, hook_id text not null, status text not null
        );
        """
    )
    connection.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, max_workers, workspace_root, created_at, updated_at) "
        "values ('legacy-standard','g','completed','plan_and_execute','standard',1,'w','t','t'), "
        "('legacy-continuous','g','draft','plan_and_execute','continuous',1,'w','t','t')"
    )

    _migration_11_team_cycle_policies(connection)

    rows = connection.execute(
        "select id, execution_policy from team_runs order by id"
    ).fetchall()
    assert [(row["id"], row["execution_policy"]) for row in rows] == [
        ("legacy-continuous", "triggered"),
        ("legacy-standard", None),
    ]
    assert {"team_run_auto_series", "team_cycle_requests"} <= {
        row["name"]
        for row in connection.execute("select name from sqlite_master where type = 'table'")
    }
    assert "request_id" in _columns(connection, "team_run_cycles")
    assert "team_cycle_request_id" in _columns(connection, "hook_runs")
```

- [ ] **Step 2: 마이그레이션 테스트가 실패하는지 확인**

Run: `pytest tests/test_migrations.py::test_migration_11_adds_cycle_policy_queue_and_backfills_continuous -v`

Expected: FAIL with `ImportError: cannot import name '_migration_11_team_cycle_policies'`.

- [ ] **Step 3: base schema와 migration 11 구현**

`db.py`의 `team_runs`에 `execution_policy text`, `team_run_cycles`에
`request_id text`, `hook_runs`에 `team_cycle_request_id text`를 추가하고 아래 partial
unique index로 nullable one-to-one을 강제한다. base schema와 migration에서 동일한
다음 DDL을 사용한다.

```sql
create table if not exists team_run_auto_series (
    id text primary key,
    team_run_id text not null references team_runs(id) on delete cascade,
    series_number integer not null,
    status text not null,
    target_slots integer not null check (target_slots > 0),
    settled_slots integer not null default 0 check (settled_slots >= 0),
    interval_seconds integer not null check (interval_seconds >= 60),
    next_run_at text,
    pause_reason text,
    paused_cycle_id text references team_run_cycles(id) on delete set null,
    created_at text not null,
    started_at text not null,
    completed_at text,
    updated_at text not null,
    unique (team_run_id, series_number)
);

create table if not exists team_cycle_requests (
    id text primary key,
    team_run_id text not null references team_runs(id) on delete cascade,
    auto_series_id text references team_run_auto_series(id) on delete cascade,
    slot_ordinal integer check (slot_ordinal is null or slot_ordinal > 0),
    source_type text not null,
    source_id text not null,
    status text not null,
    instruction text not null,
    previous_cycle_id text references team_run_cycles(id) on delete set null,
    previous_summary_text text,
    retry_of_request_id text references team_cycle_requests(id) on delete set null,
    created_at text not null,
    claimed_at text,
    settled_at text,
    updated_at text not null,
    unique (team_run_id, source_type, source_id)
);
```

`migrations.py`에는 다음 함수를 추가한다.

```python
def _migration_11_team_cycle_policies(connection: sqlite3.Connection) -> None:
    if "execution_policy" not in _columns(connection, "team_runs"):
        connection.execute("alter table team_runs add column execution_policy text")
    connection.executescript(TEAM_CYCLE_POLICY_TABLES_SQL)
    if "request_id" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column request_id text "
            "references team_cycle_requests(id) on delete set null"
        )
    if "team_cycle_request_id" not in _columns(connection, "hook_runs"):
        connection.execute(
            "alter table hook_runs add column team_cycle_request_id text "
            "references team_cycle_requests(id) on delete set null"
        )
    connection.executescript(TEAM_CYCLE_POLICY_INDEXES_SQL)
    connection.execute(
        "update team_runs set execution_policy = 'triggered' "
        "where lifecycle_mode = 'continuous' and execution_policy is null"
    )
```

`MIGRATIONS` 끝에 `(11, "team-cycle-policies", _migration_11_team_cycle_policies)`를
추가한다. 위 table DDL은 `TEAM_CYCLE_POLICY_TABLES_SQL`, 다음 index DDL은
`TEAM_CYCLE_POLICY_INDEXES_SQL`로 분리해 ALTER 이후 실행한다.

```sql
create unique index if not exists idx_team_auto_series_one_active
on team_run_auto_series(team_run_id)
where status in (
    'running', 'waiting_interval', 'paused_failure',
    'paused_user', 'paused_interrupted'
);

create unique index if not exists idx_team_cycle_requests_one_dispatching
on team_cycle_requests(team_run_id)
where status = 'dispatching';

create index if not exists idx_team_cycle_requests_run_status_created
on team_cycle_requests(team_run_id, status, created_at, id);

create unique index if not exists idx_team_run_cycles_request
on team_run_cycles(request_id)
where request_id is not null;

create unique index if not exists idx_hook_runs_cycle_request
on hook_runs(team_cycle_request_id)
where team_cycle_request_id is not null;
```

- [ ] **Step 4: schema와 migration 테스트 실행**

Run: `pytest tests/test_migrations.py tests/test_db_hooks_schema.py -q`

Expected: PASS.

- [ ] **Step 5: Task 1 커밋**

```bash
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/migrations.py tests/test_migrations.py tests/test_db_hooks_schema.py
git commit -m "feat(team-runs): 사이클 정책 스키마 추가"
```

---

### Task 2: TeamCycleService 저장소와 상태 전이

**Files:**
- Create: `src/personal_agent_gateway/team_cycles.py`
- Create: `tests/team_cycle_helpers.py`
- Create: `tests/test_team_cycles.py`
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `tests/test_teams.py`

**Interfaces:**
- Consumes: Task 1의 v11 테이블
- Produces:
  - `enqueue_request(team_run_id: str, source_type: str, source_id: str, instruction: str, *, previous_cycle_id: str | None, auto_series_id: str | None = None, slot_ordinal: int | None = None, retry_of_request_id: str | None = None, now: datetime | None = None) -> TeamCycleRequest`
  - `create_auto_series(team_run_id: str, target_slots: int, interval_seconds: int, now: datetime | None = None) -> tuple[TeamAutoSeries, TeamCycleRequest]`
  - `claim_next(team_run_id: str, now: datetime | None = None) -> TeamCycleRequest | None`
  - `settle_cycle(cycle_id: str, now: datetime | None = None) -> CycleSettlement`
  - `continue_failed(team_run_id: str, series_id: str, now: datetime | None = None) -> TeamAutoSeries`
  - `retry_failed(team_run_id: str, series_id: str, now: datetime | None = None) -> TeamCycleRequest`
  - `restart_series(team_run_id: str, now: datetime | None = None) -> tuple[TeamAutoSeries, TeamCycleRequest]`
  - `latest_settled_cycle(team_run_id: str) -> TeamRunCycle | None`
  - `get_request(request_id: str) -> TeamCycleRequest`
  - `list_requests(team_run_id: str) -> list[TeamCycleRequest]`
  - `get_active_series(team_run_id: str) -> TeamAutoSeries | None`
  - `get_dispatching(team_run_id: str) -> TeamCycleRequest | None`
  - `count_queued(team_run_id: str) -> int`
  - `policy_status(team_run_id: str) -> str`
  - `enqueue_due_auto_requests(now: datetime | None = None) -> list[TeamCycleRequest]`
  - `mark_request_settled(request_id: str, now: datetime | None = None) -> TeamCycleRequest`
  - `queue_position(request_id: str) -> int`
  - `reconcile(teams: TeamRunService, now: datetime | None = None) -> list[str]`
  - `list_dispatching_requests() -> list[TeamCycleRequest]`
  - `requeue_claim(request_id: str) -> TeamCycleRequest`
  - `pause_for_user(cycle_id: str) -> CycleSettlement`
  - `pause_interrupted(cycle_id: str) -> CycleSettlement`
  - `list_runnable_team_run_ids() -> list[str]`
  - `TeamRunService.create_cycle(team_run_id: str, source_type: str, source_id: str, rounds_budget: int | None = None, request_id: str | None = None) -> TeamRunCycle`
  - `TeamRunService.get_cycle_for_request(request_id: str) -> TeamRunCycle | None`

- [ ] **Step 1: FIFO와 idempotency 실패 테스트 작성**

`tests/team_cycle_helpers.py`에 다음 shared helper를 먼저 추가한다.

```python
def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)

def make_cycle_services(
    tmp_path: Path,
    execution_policy: str,
) -> tuple[Database, TeamRunService, TeamCycleService, TeamRun]:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1,
        lifecycle_mode="continuous",
    )
    db.execute(
        "update team_runs set execution_policy = ? where id = ?",
        (execution_policy, run.id),
    )
    return db, teams, cycles, teams.get_team_run(run.id)

def make_triggered_run(tmp_path: Path):
    return make_cycle_services(tmp_path, "triggered")

def make_auto_run(tmp_path: Path):
    return make_cycle_services(tmp_path, "auto")
```

```python
def test_cycle_requests_are_idempotent_and_claimed_fifo(tmp_path: Path) -> None:
    db, teams, cycles, run = make_triggered_run(tmp_path)
    first = cycles.enqueue_request(
        run.id, "manual", "client-1", "first", previous_cycle_id=None
    )
    duplicate = cycles.enqueue_request(
        run.id, "manual", "client-1", "ignored", previous_cycle_id=None
    )
    second = cycles.enqueue_request(
        run.id, "hook", "hook-run-1", "second", previous_cycle_id=None
    )

    assert duplicate.id == first.id
    assert cycles.claim_next(run.id).id == first.id
    assert cycles.claim_next(run.id) is None
    cycles.mark_request_settled(first.id)
    assert cycles.claim_next(run.id).id == second.id
```

- [ ] **Step 2: AUTO slot, failure, retry, continue 실패 테스트 작성**

```python
def test_auto_series_counts_continue_and_keeps_retry_in_same_slot(tmp_path: Path) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series, first = cycles.create_auto_series(
        run.id, target_slots=2, interval_seconds=300, now=dt("2026-07-20T00:00:00+00:00")
    )
    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    teams.set_cycle_status(cycle.id, "failed", error_message="boom")

    paused = cycles.settle_cycle(cycle.id, now=dt("2026-07-20T00:01:00+00:00"))
    assert paused.series.status == "paused_failure"
    retry = cycles.retry_failed(
        run.id, series.id, now=dt("2026-07-20T00:02:00+00:00")
    )
    assert retry.slot_ordinal == 1
    assert retry.retry_of_request_id == first.id

    retry_cycle = teams.create_cycle(
        run.id, "retry", retry.source_id, request_id=retry.id
    )
    teams.set_cycle_status(retry_cycle.id, "failed", error_message="again")
    cycles.settle_cycle(retry_cycle.id, now=dt("2026-07-20T00:03:00+00:00"))
    continued = cycles.continue_failed(
        run.id, series.id, now=dt("2026-07-20T00:04:00+00:00")
    )
    assert continued.settled_slots == 1
    assert continued.status == "waiting_interval"
    assert continued.next_run_at == "2026-07-20T00:09:00+00:00"
```

부분 실패 완료도 정상 정산되는 계약을 추가한다.

```python
@pytest.mark.parametrize("status", ["completed", "completed_with_failures"])
def test_completed_cycle_statuses_settle_auto_slot(
    tmp_path: Path, status: str
) -> None:
    db, teams, cycles, run = make_auto_run(tmp_path)
    series, request = cycles.create_auto_series(run.id, 1, 300)
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id, "auto", request.source_id, request_id=request.id
    )
    teams.set_cycle_status(cycle.id, status, summary="done")

    settled = cycles.settle_cycle(cycle.id)

    assert settled.series.status == "auto_completed"
    assert settled.series.settled_slots == 1
    assert settled.request.status == "settled"
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_team_cycles.py -q`

Expected: FAIL because `personal_agent_gateway.team_cycles` does not exist.

- [ ] **Step 4: dataclass와 최소 저장소 구현**

```python
ExecutionPolicy = Literal["auto", "triggered"]
AutoSeriesStatus = Literal[
    "running", "waiting_interval", "paused_failure",
    "paused_user", "paused_interrupted", "auto_completed", "canceled",
]
CycleRequestStatus = Literal["queued", "dispatching", "settled", "canceled"]

@dataclass(frozen=True)
class TeamCycleRequest:
    id: str
    team_run_id: str
    auto_series_id: str | None
    slot_ordinal: int | None
    source_type: str
    source_id: str
    status: CycleRequestStatus
    instruction: str
    previous_cycle_id: str | None
    previous_summary_text: str | None
    retry_of_request_id: str | None
    created_at: str
    claimed_at: str | None
    settled_at: str | None
    updated_at: str

@dataclass(frozen=True)
class TeamAutoSeries:
    id: str
    team_run_id: str
    series_number: int
    status: AutoSeriesStatus
    target_slots: int
    settled_slots: int
    interval_seconds: int
    next_run_at: str | None
    pause_reason: str | None
    paused_cycle_id: str | None
    created_at: str
    started_at: str
    completed_at: str | None
    updated_at: str

@dataclass(frozen=True)
class CycleSettlement:
    request: TeamCycleRequest
    series: TeamAutoSeries | None
    queue_ready: bool
```

`TeamCycleService`는 모든 상태 전이를 `begin immediate` 트랜잭션 안에서 수행하고
`sqlite3.IntegrityError`가 발생한 idempotency insert는 기존 행을 조회해 반환한다.
`claim_next`는 active `dispatching` 행이 있으면 `None`을 반환한다.
`enqueue_request`는 `previous_cycle_id`가 주어졌을 때 같은 Run의
`completed | completed_with_failures` Cycle인지 검증하고 summary를
`previous_summary_text`에 복사한다.
`source_type in {"manual", "hook"}`은 TRIGGERED Run에서만,
`source_type in {"auto", "retry"}`는 AUTO Run에서만 허용한다.
`retry_failed`와 `continue_failed`는 전달받은 `team_run_id`가 Series의 소유 Run과
일치하는지 먼저 검증한 뒤 상태를 변경한다. 소유권 불일치나 허용되지 않은 상태 전이는
`ValueError`, 없는 Run/Series/Request는 `KeyError`로 통일한다.

- [ ] **Step 5: Cycle과 Request의 단일 연결 구현**

`TeamRunCycle`에 `request_id: str | None = None`을 추가하고 `create_cycle`을 다음 계약으로
확장한다.

정확한 signature는
`create_cycle(self, team_run_id: str, source_type: str, source_id: str,
rounds_budget: int | None = None, request_id: str | None = None) ->
TeamRunCycle`이다.

신규 Cycle insert에 `request_id`를 포함하며, 같은 Request로 Cycle을 다시 만들면 기존
Cycle을 반환한다.

- [ ] **Step 6: 서비스와 기존 Team 테스트 실행**

Run: `pytest tests/test_team_cycles.py tests/test_teams.py -q`

Expected: PASS.

- [ ] **Step 7: Task 2 커밋**

```bash
git add src/personal_agent_gateway/team_cycles.py src/personal_agent_gateway/teams.py tests/team_cycle_helpers.py tests/test_team_cycles.py tests/test_teams.py
git commit -m "feat(team-runs): 사이클 요청과 자동 시리즈 상태 추가"
```

---

### Task 3: 신규 Run의 원자적 정책 생성

**Files:**
- Modify: `src/personal_agent_gateway/teams.py`
- Modify: `src/personal_agent_gateway/team_cycles.py`
- Modify: `tests/team_cycle_helpers.py`
- Modify: `tests/test_teams.py`
- Modify: `tests/test_team_cycles.py`

**Interfaces:**
- Consumes: `TeamCycleService.initialize_auto_series(connection: sqlite3.Connection, team_run_id: str, target_slots: int, interval_seconds: int, now: str) -> tuple[TeamAutoSeries, TeamCycleRequest]`
- Produces: `TeamRun.execution_policy`, 원자적인 Continuous Run + AUTO Series + 첫 Request 생성,
  `create_team_run_from_team(self, team_service, rule_set_service, team_id: str,
  goal: str, run_mode: RunMode, max_workers: int, rounds_budget: int = 8,
  lifecycle_mode: LifecycleMode = "standard", execution_policy:
  ExecutionPolicy | None = None, auto_repeat_count: int | None = None,
  auto_interval_seconds: int | None = None) -> TeamRun` forwarding

- [ ] **Step 1: 정책별 Run 생성 실패 테스트 작성**

기존 `make_services`는 그대로 두고 정책 테스트 전용 helper를 추가한다.

```python
def make_policy_services(tmp_path: Path):
    db = Database(tmp_path / "policy.db")
    db.initialize()
    personas = PersonaService(db)
    cycle_service = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "policy-workspace",
        cycle_service=cycle_service,
    )
    leader = personas.create_persona("Policy Lead", "lead", "d", [], [])
    worker = personas.create_persona("Policy Worker", "worker", "d", [], [])
    return db, teams, cycle_service, leader.id, worker.id
```

```python
def test_new_auto_run_is_continuous_and_creates_first_request_atomically(tmp_path: Path) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    run = teams.create_team_run(
        "goal", leader_id, [worker_id], "plan_and_execute", 1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=3,
        auto_interval_seconds=600,
    )

    assert run.lifecycle_mode == "continuous"
    assert run.execution_policy == "auto"
    series = cycle_service.get_active_series(run.id)
    assert (series.target_slots, series.interval_seconds) == (3, 600)
    assert [request.slot_ordinal for request in cycle_service.list_requests(run.id)] == [1]
```

트랜잭션 rollback도 검증한다.

```python
def test_auto_initialization_failure_rolls_back_team_run_rows(tmp_path: Path, monkeypatch) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    monkeypatch.setattr(
        cycle_service, "initialize_auto_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        teams.create_team_run(
            "goal", leader_id, [worker_id], "plan_and_execute", 1,
            lifecycle_mode="continuous", execution_policy="auto",
            auto_repeat_count=2, auto_interval_seconds=60,
        )
    assert db.fetchone("select id from team_runs") is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_teams.py -k "auto_run or initialization_failure" -v`

Expected: FAIL because `execution_policy` and AUTO parameters are unsupported.

- [ ] **Step 3: TeamRun과 생성 transaction 구현**

`TeamRun`에 다음 필드를 추가한다.

```python
execution_policy: Literal["auto", "triggered"] | None = None
```

`TeamRunService.__init__`은 `cycle_service: TeamCycleService | None = None`을 받고,
`create_team_run`과 `_create_agent`가 같은 SQLite connection을 사용하도록 private
`_insert_agent(connection: sqlite3.Connection, team_run_id: str, persona_id: str,
role: str, now: str) -> TeamAgent` helper를 만든다. AUTO이면 commit 전에 다음을 호출한다.

```python
self._cycle_service.initialize_auto_series(
    connection,
    team_run_id,
    target_slots=auto_repeat_count,
    interval_seconds=auto_interval_seconds,
    now=now,
)
```

예외가 발생하면 transaction rollback 후 이번 호출이 만든 workspace 디렉터리만
`shutil.rmtree(workspace_root_path)`로 제거한다.

`create_team_run_from_team`에도 동일한 세 정책 인자를 추가하고 `create_team_run`으로
그대로 전달한다. API가 Team Directory 기반 생성 경로를 사용하므로 이 forwarding을
생략하면 첫 AUTO Request가 생성되지 않는다.

Task 2에서 구 API로 Run을 만든 뒤 DB를 갱신하던 `make_cycle_services`와
`make_auto_run`도 이 시점에 다음처럼 실제 정책 생성 경로로 교체한다.

```python
def make_cycle_services(
    tmp_path: Path,
    execution_policy: str,
    *,
    auto_repeat_count: int = 2,
    auto_interval_seconds: int = 300,
):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "workspace",
        cycle_service=cycles,
    )
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [worker.id], "plan_and_execute", 1,
        lifecycle_mode="continuous",
        execution_policy=execution_policy,
        auto_repeat_count=(
            auto_repeat_count if execution_policy == "auto" else None
        ),
        auto_interval_seconds=(
            auto_interval_seconds if execution_policy == "auto" else None
        ),
    )
    return db, teams, cycles, run

def make_auto_run(
    tmp_path: Path,
    target_slots: int = 2,
    interval_seconds: int = 300,
):
    return make_cycle_services(
        tmp_path,
        "auto",
        auto_repeat_count=target_slots,
        auto_interval_seconds=interval_seconds,
    )
```

AUTO helper는 생성 시 이미 Series와 slot 1 Request를 가지므로 Task 2의 AUTO 테스트는
`get_active_series`와 `list_requests(run.id)[0]`을 사용하도록 바꾼다.
1-slot 완료 테스트는 `make_auto_run(tmp_path, target_slots=1)`을 사용해
active-Series unique index와 충돌하지 않게 한다.

- [ ] **Step 4: policy validation 구현**

```python
if lifecycle_mode == "continuous" and execution_policy not in {"auto", "triggered"}:
    raise ValueError("Continuous Team Run requires an execution policy")
if execution_policy == "auto":
    if not auto_repeat_count or auto_repeat_count < 1:
        raise ValueError("AUTO repeat count must be positive")
    if not auto_interval_seconds or auto_interval_seconds < 60:
        raise ValueError("AUTO interval must be at least 60 seconds")
elif auto_repeat_count is not None or auto_interval_seconds is not None:
    raise ValueError("TRIGGERED Team Run does not accept AUTO settings")
```

기존 STANDARD 생성 경로는 `execution_policy=None`으로 유지한다.

- [ ] **Step 5: 생성 테스트 실행**

Run: `pytest tests/test_teams.py tests/test_team_cycles.py -q`

Expected: PASS.

- [ ] **Step 6: Task 3 커밋**

```bash
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_cycles.py tests/team_cycle_helpers.py tests/test_teams.py tests/test_team_cycles.py
git commit -m "feat(team-runs): 실행 정책과 첫 자동 사이클 원자적 생성"
```

---

### Task 4: Dispatcher, AUTO Loop, leader-only 이전 컨텍스트

**Files:**
- Create: `src/personal_agent_gateway/team_cycle_dispatcher.py`
- Create: `src/personal_agent_gateway/team_cycle_loop.py`
- Create: `tests/test_team_cycle_dispatcher.py`
- Create: `tests/test_team_cycle_loop.py`
- Modify: `tests/team_cycle_helpers.py`
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `TeamCycleService`, `TeamRunService.create_cycle`, `TeamRunOrchestrator.run_cycle`
- Produces:
  - `TeamCycleDispatcher.enqueue_run(team_run_id)`
  - `TeamCycleDispatcher.on_team_run_settled(run, cycle_id)`
  - `TeamCycleDispatcher.reconcile()`
  - `TeamCycleLoop.tick() -> None` (`now`는 constructor callable로 주입)

- [ ] **Step 1: 직렬 실행과 leader context 실패 테스트 작성**

`RecordingOrchestrator`는 `tests/team_cycle_helpers.py`에 추가하고,
`DispatcherServices`와 builder는 `tests/test_team_cycle_dispatcher.py`에 둔다.

```python
class RecordingOrchestrator:
    def __init__(self, teams: TeamRunService) -> None:
        self.teams = teams
        self.calls: list[tuple[str, str, str]] = []

    async def run_cycle(
        self, team_run_id: str, cycle_id: str, instruction: str
    ) -> TeamRun:
        self.calls.append((team_run_id, cycle_id, instruction))
        return self.teams.get_team_run(team_run_id)

@dataclass
class DispatcherServices:
    run: TeamRun
    teams: TeamRunService
    cycles: TeamCycleService
    orchestrator: RecordingOrchestrator
    dispatcher: TeamCycleDispatcher

def make_dispatcher_services(tmp_path: Path) -> DispatcherServices:
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    orchestrator = RecordingOrchestrator(teams)
    dispatcher = TeamCycleDispatcher(
        cycles, teams, orchestrator, EventBus()
    )
    return DispatcherServices(run, teams, cycles, orchestrator, dispatcher)
```

```python
@pytest.mark.asyncio
async def test_dispatcher_runs_fifo_and_passes_previous_summary_to_leader_only(tmp_path: Path) -> None:
    services = make_dispatcher_services(tmp_path)
    previous = services.teams.create_cycle(
        services.run.id, "manual", "old"
    )
    services.teams.set_cycle_status(previous.id, "completed", summary="previous result")
    first = services.cycles.enqueue_request(
        services.run.id, "manual", "client-1", "next work",
        previous_cycle_id=previous.id,
    )
    second = services.cycles.enqueue_request(
        services.run.id, "hook", "hook-1", "hook work",
        previous_cycle_id=previous.id,
    )

    await services.dispatcher.run_one(services.run.id)

    call = services.orchestrator.calls[0]
    assert call[0] == services.run.id
    assert call[2] == "next work\n\nPREVIOUS CYCLE SUMMARY\nprevious result"
    assert services.cycles.get_request(second.id).status == "queued"

    first_cycle = services.teams.get_cycle_for_request(first.id)
    services.teams.set_cycle_status(first_cycle.id, "completed", summary="first done")
    await services.dispatcher.on_team_run_settled(services.run, first_cycle.id)
    await services.dispatcher.run_one(services.run.id)
    assert services.orchestrator.calls[1][2].startswith("hook work")
    assert services.cycles.get_request(second.id).status == "dispatching"
```

사용자 결정 대기는 같은 Request를 유지하는 테스트로 고정한다.

```python
@pytest.mark.asyncio
async def test_waiting_for_user_keeps_request_until_same_cycle_completes(
    tmp_path: Path,
) -> None:
    services = make_dispatcher_services(tmp_path)
    request = services.cycles.enqueue_request(
        services.run.id, "manual", "client-1", "work", previous_cycle_id=None
    )
    await services.dispatcher.run_one(services.run.id)
    cycle = services.teams.get_cycle_for_request(request.id)

    services.teams.set_cycle_status(cycle.id, "waiting_for_user")
    await services.dispatcher.on_team_run_settled(services.run, cycle.id)
    assert services.cycles.get_request(request.id).status == "dispatching"

    services.teams.set_cycle_status(cycle.id, "completed", summary="answered")
    await services.dispatcher.on_team_run_settled(services.run, cycle.id)
    assert services.cycles.get_request(request.id).status == "settled"
```

`test_team_runtime.py`에는 Worker prompt에 `PREVIOUS CYCLE SUMMARY`가 직접 추가되지
않는 기존 계약을 고정한다.

- [ ] **Step 2: AUTO fake-clock 실패 테스트 작성**

`tests/test_team_cycle_loop.py`에는 enqueue만 기록하는 fake를 둔다.

```python
class RecordingDispatcher:
    def __init__(self) -> None:
        self.enqueued_run_ids: list[str] = []

    async def enqueue_run(self, team_run_id: str) -> None:
        self.enqueued_run_ids.append(team_run_id)
```

```python
@pytest.mark.asyncio
async def test_loop_enqueues_due_auto_slot_once(tmp_path: Path) -> None:
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    series = cycles.get_active_series(run.id)
    first = cycles.list_requests(run.id)[0]
    first_cycle = teams.create_cycle(
        run.id, "auto", first.source_id, request_id=first.id
    )
    teams.set_cycle_status(first_cycle.id, "completed", summary="done")
    cycles.settle_cycle(
        first_cycle.id, now=dt("2026-07-20T00:55:00+00:00")
    )
    dispatcher = RecordingDispatcher()
    loop = TeamCycleLoop(
        cycles,
        dispatcher,
        now=lambda: dt("2026-07-20T01:00:00+00:00"),
    )

    await loop.tick()
    await loop.tick()

    requests = cycles.list_requests(run.id)
    assert [request.slot_ordinal for request in requests] == [1, 2]
    assert dispatcher.enqueued_run_ids == [run.id]
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_team_cycle_dispatcher.py tests/test_team_cycle_loop.py -q`

Expected: FAIL because dispatcher and loop modules do not exist.

- [ ] **Step 4: Dispatcher 구현**

핵심 계약은 다음과 같다.

```python
CyclePreparer = Callable[
    [TeamCycleRequest, TeamRunCycle],
    Awaitable[str | None],
]

class TeamCycleDispatcher:
    def __init__(
        self,
        cycles: TeamCycleService,
        teams: TeamRunService,
        orchestrator: TeamRunOrchestrator,
        event_bus: EventBus,
    ) -> None:
        self._cycles = cycles
        self._teams = teams
        self._orchestrator = orchestrator
        self._event_bus = event_bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._preparers: list[CyclePreparer] = []
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def add_preparer(self, preparer: CyclePreparer) -> None:
        self._preparers.append(preparer)

    async def enqueue_run(self, team_run_id: str) -> None:
        await self._queue.put(team_run_id)

    async def run_one(self, team_run_id: str) -> None:
        request = self._cycles.claim_next(team_run_id)
        if request is None:
            return
        try:
            cycle = self._teams.create_cycle(
                team_run_id,
                request.source_type,
                request.source_id,
                request_id=request.id,
            )
        except Exception:
            self._cycles.requeue_claim(request.id)
            raise
        try:
            instruction = request.instruction
            for preparer in self._preparers:
                replacement = await preparer(request, cycle)
                if replacement is not None:
                    instruction = replacement
            if request.previous_summary_text:
                instruction += (
                    "\n\nPREVIOUS CYCLE SUMMARY\n"
                    + request.previous_summary_text
                )
            await self._event_bus.publish({
                "type": "team.cycle.started",
                "team_run_id": team_run_id,
                "cycle_id": cycle.id,
                "cycle_request_id": request.id,
            })
            await self._orchestrator.run_cycle(
                team_run_id, cycle.id, instruction
            )
        except Exception as exc:
            self._teams.set_cycle_status(
                cycle.id, "failed", error_message=str(exc)
            )
            await self.on_team_run_settled(
                self._teams.get_team_run(team_run_id), cycle.id
            )

    async def on_team_run_settled(
        self, run: TeamRun, cycle_id: str | None
    ) -> None:
        if cycle_id is None:
            return
        result = self._cycles.settle_cycle(cycle_id)
        await self._event_bus.publish({
            "type": "team.cycle.settled",
            "team_run_id": run.id,
            "cycle_id": cycle_id,
            "cycle_request_id": result.request.id,
            "policy_status": self._cycles.policy_status(run.id),
        })
        if result.series is not None and result.series.status in {
            "paused_failure", "paused_user", "paused_interrupted",
        }:
            await self._event_bus.publish({
                "type": "team.auto_series.paused",
                "team_run_id": run.id,
                "auto_series_id": result.series.id,
                "status": result.series.status,
            })
        if (
            result.series is not None
            and result.series.status == "auto_completed"
        ):
            await self._event_bus.publish({
                "type": "team.auto_series.completed",
                "team_run_id": run.id,
                "auto_series_id": result.series.id,
            })
        if result.queue_ready:
            await self.enqueue_run(run.id)

    def reconcile(self) -> list[str]:
        return self._cycles.reconcile(self._teams)

    async def _run_loop(self) -> None:
        while True:
            team_run_id = await self._queue.get()
            try:
                await self.run_one(team_run_id)
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = redact_text(exc) or type(exc).__name__
            finally:
                self._queue.task_done()
```

`run_one`은 `claim_next` → `create_cycle(request_id=request.id)` → preparer 적용 → 이전 summary
블록 추가 → `orchestrator.run_cycle()` 순서다. Preparer/Orchestrator가 예외를 던지면
생성된 Cycle을 `failed`로 정산해 Request가 `dispatching`에 고착되지 않게 한다.
`waiting_for_user`와 `interrupted`에서는 Request를 `dispatching`으로 유지한다. terminal
settlement만 Request를 정산하고 다음 queued Run ID를 enqueue한다.
`_run_loop`의 예외 문자열에는 `personal_agent_gateway.redaction.redact_text`를 적용하며,
`start/stop/alive/last_error`는 위 코드 그대로 Operations health에 노출한다.

- [ ] **Step 5: TeamCycleLoop 구현**

```python
class TeamCycleLoop:
    def __init__(
        self,
        cycles: TeamCycleService,
        dispatcher: TeamCycleDispatcher,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._cycles = cycles
        self._dispatcher = dispatcher
        self._interval_seconds = interval_seconds
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    async def tick(self) -> None:
        for request in self._cycles.enqueue_due_auto_requests(now=self._now()):
            await self._dispatcher.enqueue_run(request.team_run_id)

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = redact_text(exc) or type(exc).__name__
            await asyncio.sleep(self._interval_seconds)
```

`redact_text`는 Dispatcher와 동일하게
`personal_agent_gateway.redaction`에서 import한다.

- [ ] **Step 6: Dispatcher/Loop/Runtime 테스트 실행**

Run: `pytest tests/test_team_cycle_dispatcher.py tests/test_team_cycle_loop.py tests/test_team_runtime.py -q`

Expected: PASS.

- [ ] **Step 7: Task 4 커밋**

```bash
git add src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/team_cycle_loop.py tests/team_cycle_helpers.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_loop.py tests/test_team_runtime.py
git commit -m "feat(team-runs): 사이클 디스패처와 자동 반복 루프 추가"
```

---

### Task 5: HookRunner를 공통 CycleRequest 큐로 전환

**Files:**
- Modify: `src/personal_agent_gateway/hook_runs.py`
- Modify: `src/personal_agent_gateway/hook_runner.py`
- Modify: `src/personal_agent_gateway/hooks.py`
- Modify: `tests/test_hook_runs.py`
- Modify: `tests/test_hook_runner.py`
- Modify: `tests/test_hooks_service.py`
- Modify: `tests/test_api_hooks.py`
- Modify: `tests/test_mail_knowledge.py`

**Interfaces:**
- Consumes: `TeamCycleService.enqueue_request`, Dispatcher `CyclePreparer`
- Produces:
  - `HookRunService.link_cycle_request(run_id, request_id)`
  - `HookRunService.get_run_for_cycle_request(request_id: str) -> HookRun | None`
  - `HookRunner.prepare_team_cycle(request, cycle) -> str | None`
  - Hook target은 `execution_policy == "triggered"`만 허용

- [ ] **Step 1: Hook이 Cycle 대신 Request를 만드는 실패 테스트 작성**

```python
@pytest.mark.asyncio
async def test_team_hook_enqueues_shared_cycle_request(tmp_path: Path) -> None:
    runner, hook_runs, teams, team_run, hook_run, cycles, dispatcher = (
        _setup_team_hook(tmp_path)
    )

    await runner.run_one(hook_run.id)

    linked = hook_runs.get_run(hook_run.id)
    request = cycles.get_request(linked.team_cycle_request_id)
    assert request.source_type == "hook"
    assert request.source_id == hook_run.id
    assert linked.team_run_cycle_id is None
```

TRIGGERED 검증도 추가한다.

```python
def test_hook_rejects_auto_team_run_target(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hooks, _ = _service(tmp_path, adapter)
    hooks._db.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, execution_policy, "
        "max_workers, workspace_root, created_at, updated_at) "
        "values ('auto-run','goal','draft','plan_and_execute','continuous',"
        "'auto',1,'w','t','t')"
    )
    with pytest.raises(ValueError, match="TRIGGERED"):
        hooks.create_hook(
            name="mail",
            source_type="email",
            connection={"host": "imap.example.com", "port": 993, "username": "u"},
            secret="app-password",
            filter={"folder": "INBOX"},
            target_backend="",
            target_model="",
            target_options={},
            prompt_template="{{subject}}",
            poll_interval_seconds=300,
            target_kind="team_run",
            target_team_run_id="auto-run",
        )
```

`HooksService._validate_team_run_target`의 조건을
`continuous + plan_and_execute + execution_policy == "triggered"`로 바꾸고 create와
update가 모두 이 검증을 사용하게 한다. `HookRunner._run_team_cycle`도 enqueue 직전에
같은 조건을 다시 확인해 마이그레이션 전 비정상 target 실행을 막는다.
`tests/test_api_hooks.py`의 기존 성공 fixture에는 `execution_policy="triggered"`를
추가하고 AUTO target create/update가 `400`인지 각각 검증한다.

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_hook_runs.py tests/test_hook_runner.py tests/test_hooks_service.py tests/test_mail_knowledge.py -q`

Expected: FAIL because HookRun has no `team_cycle_request_id`.

- [ ] **Step 3: HookRun lineage와 enqueue 구현**

`HookRun`에 `team_cycle_request_id: str | None`을 추가하고 다음 메서드를 구현한다.

```python
def link_cycle_request(self, run_id: str, request_id: str) -> HookRun:
    run = self.get_run(run_id)
    if run.team_cycle_request_id not in {None, request_id}:
        raise ValueError("Hook Run is already linked to another CycleRequest")
    self._db.execute(
        "update hook_runs set team_cycle_request_id = ? where id = ?",
        (request_id, run_id),
    )
    return self.get_run(run_id)
```

`HookRunner._run_team_cycle`은 직접 `create_cycle/run_cycle`하지 않고 다음 호출로
Request를 만들고 Dispatcher를 wake한다.

```python
previous = self._cycles.latest_settled_cycle(hook.target_team_run_id)
request = self._cycles.enqueue_request(
    hook.target_team_run_id,
    "hook",
    run.id,
    render_prompt(hook.prompt_template, run.trigger_payload),
    previous_cycle_id=previous.id if previous is not None else None,
)
self._hook_runs.link_cycle_request(run.id, request.id)
await self._event_bus.publish({
    "type": "team.cycle_request.queued",
    "team_run_id": hook.target_team_run_id,
    "cycle_request_id": request.id,
    "source_type": "hook",
})
await self._team_dispatcher.enqueue_run(hook.target_team_run_id)
```

기존 `_setup_team_hook`은 `TeamCycleService`와 `TeamCycleDispatcher`를 만들고
`runner.attach_team_cycle_queue(cycles, dispatcher)` 및
`dispatcher.add_preparer(runner.prepare_team_cycle)`를 호출한 뒤
`runner, runs, teams, team_run, run, cycles, dispatcher`를 반환하도록 확장한다.

- [ ] **Step 4: 이메일 Cycle preparer 구현**

```python
async def prepare_team_cycle(
    self,
    request: TeamCycleRequest,
    cycle: TeamRunCycle,
) -> str | None:
    if request.source_type != "hook":
        return None
    hook_run = self._hook_runs.get_run(request.source_id)
    hook_run = self._hook_runs.link_cycle(hook_run.id, cycle.id)
    hook = self._hooks.get_hook(hook_run.hook_id)
    if hook.source_type != "email":
        return request.instruction
    try:
        message = self._mail_knowledge.ingest_hook_run(
            hook, hook_run,
            self._teams.get_team_run(cycle.team_run_id),
            cycle.id,
        )
        projected = self._mail_projector.project_safely(message)
        if projected.projection_status != "projected":
            raise RuntimeError("Email Team Hook context projection failed")
        return build_mail_team_instruction(projected, hook.prompt_template)
    except Exception as exc:
        self._hook_runs.mark_failed(hook_run.id, str(exc))
        raise
```

Dispatcher가 Cycle을 만든 직후 이 preparer를 호출하므로 기존
`CYCLES/{cycle_id}/MAIL_CONTEXT.md` 경로와 HookRun→Cycle lineage 계약을 유지한다.
Projection 전에 실패해도 HookRun과 Cycle이 모두 `failed`로 끝나는 회귀 테스트를
`tests/test_hook_runner.py`에 추가한다.

- [ ] **Step 5: Hook settlement 투영 전환**

`on_team_run_settled`은 Cycle의 `request_id`로 HookRun을 찾고 기존 성공/대기/중단/실패
상태 투영을 유지한다. HookRunner 내부의 별도 “다음 HookRun 찾기” 로직은 제거하고
Dispatcher가 공통 FIFO를 계속 처리하게 한다.

- [ ] **Step 6: Hook 테스트 실행**

Run: `pytest tests/test_hook_runs.py tests/test_hook_runner.py tests/test_hooks_service.py tests/test_api_hooks.py tests/test_mail_knowledge.py -q`

Expected: PASS.

- [ ] **Step 7: Task 5 커밋**

```bash
git add src/personal_agent_gateway/hook_runs.py src/personal_agent_gateway/hook_runner.py src/personal_agent_gateway/hooks.py tests/test_hook_runs.py tests/test_hook_runner.py tests/test_hooks_service.py tests/test_api_hooks.py tests/test_mail_knowledge.py
git commit -m "refactor(hooks): 팀 실행을 공통 사이클 큐로 전환"
```

---

### Task 6: API, startup recovery, Operations 배선

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `src/personal_agent_gateway/api/operations.py`
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `src/personal_agent_gateway/health.py`
- Modify: `tests/test_api_team_runs.py`
- Modify: `tests/test_api_operations.py`
- Modify: `tests/test_app_lifecycle.py`
- Modify: `tests/test_health.py`

**Interfaces:**
- Consumes: TeamCycleService/Dispatcher/Loop, 기존 Audit와 decision answer API
- Produces: 생성, Manual Trigger, retry/continue/restart, detail policy read model, lifespan reconcile

- [ ] **Step 1: 생성과 Manual Trigger API 실패 테스트 작성**

```python
def test_create_auto_run_enqueues_first_cycle_and_manual_trigger_snapshots_preview(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    team_id = create_team(client, leader_id, [worker_id])
    created = client.post("/api/team-runs", json={
        "team_id": team_id,
        "goal": "Maintain gateway",
        "execution_policy": "auto",
        "auto_repeat_count": 3,
        "auto_interval_minutes": 5,
    })
    assert created.status_code == 200
    assert created.json()["team_run"]["lifecycle_mode"] == "continuous"
    assert created.json()["team_run"]["execution_policy"] == "auto"

    triggered = client.post("/api/team-runs", json={
        "team_id": team_id,
        "goal": "Triggered maintenance",
        "execution_policy": "triggered",
    }).json()["team_run"]
    previous = client.app.state.team_run_service.create_cycle(
        triggered["id"], "manual", "previous"
    )
    previous = client.app.state.team_run_service.set_cycle_status(
        previous.id, "completed", summary="previous"
    )
    response = client.post(
        f"/api/team-runs/{triggered['id']}/cycle-requests",
        json={
            "instruction": "next",
            "client_request_id": "ui-1",
            "previous_cycle_id": previous.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["cycle_request"]["previous_summary_text"] == "previous"
```

입력 조합은 API 경계에서 `422`로 차단한다.

```python
@pytest.mark.parametrize("payload", [
    {
        "team_id": "team",
        "goal": "g",
        "execution_policy": "auto",
        "auto_repeat_count": 2,
    },
    {
        "team_id": "team",
        "goal": "g",
        "execution_policy": "triggered",
        "auto_repeat_count": 2,
        "auto_interval_minutes": 5,
    },
])
def test_create_run_rejects_incomplete_or_mixed_policy_settings(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    client = authenticated_client(tmp_path)
    response = client.post("/api/team-runs", json=payload)
    assert response.status_code == 422
```

- [ ] **Step 2: AUTO action과 detail 실패 테스트 작성**

```python
def test_auto_actions_and_detail_read_model(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    worker_id = create_persona(client, "Worker")
    team_id = create_team(client, leader_id, [worker_id])
    run = client.post("/api/team-runs", json={
        "team_id": team_id,
        "goal": "AUTO maintenance",
        "execution_policy": "auto",
        "auto_repeat_count": 2,
        "auto_interval_minutes": 5,
    }).json()["team_run"]
    cycle_service = client.app.state.team_cycle_service
    team_service = client.app.state.team_run_service
    series = cycle_service.get_active_series(run["id"])
    first = cycle_service.list_requests(run["id"])[0]
    first = cycle_service.claim_next(run["id"])
    failed_cycle = team_service.create_cycle(
        run["id"], "auto", first.source_id, request_id=first.id
    )
    team_service.set_cycle_status(
        failed_cycle.id, "failed", error_message="boom"
    )
    cycle_service.settle_cycle(failed_cycle.id)

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()
    assert detail["policy_status"] == "paused_failure"
    assert detail["active_auto_series"]["settled_slots"] == 0

    continued = client.post(
        f"/api/team-runs/{run['id']}/auto-series/{series.id}/continue"
    )
    assert continued.status_code == 200
    assert continued.json()["auto_series"]["settled_slots"] == 1

    operations = client.get("/api/operations").json()
    item = next(
        value for value in operations["items"]
        if value["domain"] == "team_run" and value["id"] == run["id"]
    )
    assert item["execution_policy"] == "auto"
    assert item["policy_status"] == "waiting_interval"
    assert item["queue_count"] == 0
    assert item["next_run_at"] is not None
```

정책 우회용 기존 endpoint와 반대 정책 action도 차단한다.

```python
def test_continuous_run_rejects_legacy_start_add_work_and_wrong_policy_actions(
    tmp_path: Path,
) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Leader")
    team_id = create_team(client, leader_id)
    triggered = client.post("/api/team-runs", json={
        "team_id": team_id,
        "goal": "Triggered",
        "execution_policy": "triggered",
    }).json()["team_run"]
    auto = client.post("/api/team-runs", json={
        "team_id": team_id,
        "goal": "Auto",
        "execution_policy": "auto",
        "auto_repeat_count": 2,
        "auto_interval_minutes": 5,
    }).json()["team_run"]

    assert client.post(f"/api/team-runs/{triggered['id']}/start").status_code == 409
    assert client.post(
        f"/api/team-runs/{triggered['id']}/add-work",
        json={"instruction": "bypass"},
    ).status_code == 409
    assert client.post(
        f"/api/team-runs/{auto['id']}/cycle-requests",
        json={"instruction": "wrong", "client_request_id": "ui-wrong"},
    ).status_code == 409
    assert client.post(
        f"/api/team-runs/{triggered['id']}/auto-series/restart"
    ).status_code == 409
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_api_team_runs.py -k "auto_run or manual_trigger or auto_actions" -v`

Expected: FAIL because request models and routes do not exist.

- [ ] **Step 4: Pydantic 모델과 API route 구현**

```python
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

class CreateTeamRunRequest(BaseModel):
    team_id: str
    goal: str
    execution_policy: Literal["auto", "triggered"]
    auto_repeat_count: Annotated[int | None, Field(ge=1)] = None
    auto_interval_minutes: Annotated[int | None, Field(ge=1)] = None

    @model_validator(mode="after")
    def validate_policy_settings(self) -> Self:
        auto_fields = (
            self.auto_repeat_count,
            self.auto_interval_minutes,
        )
        if self.execution_policy == "auto" and None in auto_fields:
            raise ValueError("AUTO requires repeat count and interval")
        if self.execution_policy == "triggered" and any(
            value is not None for value in auto_fields
        ):
            raise ValueError("TRIGGERED does not accept AUTO settings")
        return self

class TriggerCycleRequest(BaseModel):
    instruction: Annotated[str, Field(min_length=1)]
    client_request_id: Annotated[str, Field(min_length=1)]
    previous_cycle_id: str | None = None

    @field_validator("instruction", "client_request_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value
```

신규 route는 다음 service/action 매핑을 그대로 사용한다.

```python
@router.post("")
async def create_team_run(
    request: Request,
    payload: CreateTeamRunRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.create_team_run_from_team(
            request.app.state.team_directory_service,
            request.app.state.rule_set_service,
            team_id=payload.team_id,
            goal=payload.goal,
            run_mode="plan_and_execute",
            max_workers=1,
            lifecycle_mode="continuous",
            execution_policy=payload.execution_policy,
            auto_repeat_count=payload.auto_repeat_count,
            auto_interval_seconds=(
                payload.auto_interval_minutes * 60
                if payload.auto_interval_minutes is not None
                else None
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if payload.execution_policy == "auto":
        first = request.app.state.team_cycle_service.list_requests(run.id)[0]
        await request.app.state.event_bus.publish({
            "type": "team.cycle_request.queued",
            "team_run_id": run.id,
            "cycle_request_id": first.id,
            "source_type": "auto",
        })
        await request.app.state.team_cycle_dispatcher.enqueue_run(run.id)
    record_domain_audit(
        request, principal,
        event_type="team.run_created",
        action="team_runs.create",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={
            "run_mode": run.run_mode,
            "team_id": run.team_id,
            "execution_policy": run.execution_policy,
        },
    )
    return {"team_run": _team_run_payload(run)}

@router.post("/{team_run_id}/cycle-requests")
async def trigger_cycle(
    request: Request,
    team_run_id: str,
    payload: TriggerCycleRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        created = request.app.state.team_cycle_service.enqueue_request(
            team_run_id,
            "manual",
            payload.client_request_id,
            payload.instruction,
            previous_cycle_id=payload.previous_cycle_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish({
        "type": "team.cycle_request.queued",
        "team_run_id": team_run_id,
        "cycle_request_id": created.id,
        "source_type": "manual",
    })
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request, principal,
        event_type="team.cycle_request.queued",
        action="team_runs.trigger_cycle",
        resource_type="team_cycle_request",
        resource_id=created.id,
        team_run_id=team_run_id,
    )
    return {
        "cycle_request": _cycle_request_payload(created),
        "queue_position": request.app.state.team_cycle_service.queue_position(created.id),
    }

@router.post("/{team_run_id}/auto-series/{series_id}/retry")
async def retry_auto_cycle(
    request: Request,
    team_run_id: str,
    series_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        created = request.app.state.team_cycle_service.retry_failed(
            team_run_id, series_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="AUTO Series not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish({
        "type": "team.cycle_request.queued",
        "team_run_id": team_run_id,
        "cycle_request_id": created.id,
        "source_type": "retry",
    })
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request, principal,
        event_type="team.auto_series.retried",
        action="team_runs.retry_auto_cycle",
        resource_type="team_auto_series",
        resource_id=series_id,
        team_run_id=team_run_id,
    )
    return {"cycle_request": _cycle_request_payload(created)}

@router.post("/{team_run_id}/auto-series/{series_id}/continue")
async def continue_auto_cycle(
    request: Request,
    team_run_id: str,
    series_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        series = request.app.state.team_cycle_service.continue_failed(
            team_run_id, series_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="AUTO Series not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request, principal,
        event_type="team.auto_series.continued",
        action="team_runs.continue_auto_cycle",
        resource_type="team_auto_series",
        resource_id=series_id,
        team_run_id=team_run_id,
    )
    return {"auto_series": _auto_series_payload(series)}

@router.post("/{team_run_id}/auto-series/restart")
async def restart_auto_series(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        series, created = (
            request.app.state.team_cycle_service.restart_series(team_run_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish({
        "type": "team.cycle_request.queued",
        "team_run_id": team_run_id,
        "cycle_request_id": created.id,
        "source_type": "auto",
    })
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request, principal,
        event_type="team.auto_series.restarted",
        action="team_runs.restart_auto_series",
        resource_type="team_auto_series",
        resource_id=series.id,
        team_run_id=team_run_id,
    )
    return {
        "auto_series": _auto_series_payload(series),
        "cycle_request": _cycle_request_payload(created),
    }
```

각 mutation은 `require_intake_open`, policy/status 검증, `record_domain_audit`, Dispatcher
wake-up을 수행한다. `ValueError` 상태 충돌은 `409`, Pydantic 범위 오류는 `422`로 둔다.
기존 `/start`와 `/add-work` route는 `run.lifecycle_mode == "continuous"`일 때
`409`를 반환해 공통 CycleRequest 큐를 우회하지 못하게 하고 STANDARD 호환 경로만
유지한다.

- [ ] **Step 5: detail/operations read model 구현**

Detail 응답에 다음을 추가한다.

```python
"policy_status": cycle_service.policy_status(team_run_id),
"active_auto_series": _auto_series_payload(
    cycle_service.get_active_series(team_run_id)
),
"queue_count": cycle_service.count_queued(team_run_id),
"active_request": _cycle_request_payload(
    cycle_service.get_dispatching(team_run_id)
),
```

Operations의 Team Run item에도 `execution_policy`, `policy_status`, `queue_count`,
`next_run_at`, `pause_reason`, `active_cycle_id`를 추가한다. 기존 run status가 terminal이어도
`policy_status in {"queued", "running", "waiting_interval", "paused_failure",
"paused_user", "paused_interrupted"}`이면 Operations item에 포함한다.

- [ ] **Step 6: app lifespan 배선과 reconcile 구현**

`_attach_local_services`에서 `TeamCycleService` → `TeamRunService` →
`TeamRunOrchestrator` → `TeamCycleDispatcher` → `TeamCycleLoop` 순서로 만들고,
HookRunner preparer와 settlement observer를 등록한다.

`HealthService.__init__`에는 `team_cycle_dispatcher`, `team_cycle_loop`를 추가하고
`components()`에 `_background_health("team_cycle_dispatcher", dispatcher)`와
`_background_health("team_cycle_loop", loop)`를 포함한다.

startup 순서:

```python
application.state.team_run_service.interrupt_active_runs()
run_ids = application.state.team_cycle_dispatcher.reconcile()
await application.state.team_cycle_dispatcher.start()
for team_run_id in run_ids:
    await application.state.team_cycle_dispatcher.enqueue_run(team_run_id)
await application.state.team_cycle_loop.start()
```

shutdown은 loop → dispatcher → registry 순서로 중단한다.

- [ ] **Step 7: API/lifecycle/operations 테스트 실행**

Run: `pytest tests/test_api_team_runs.py tests/test_api_operations.py tests/test_app_lifecycle.py tests/test_health.py -q`

Expected: PASS.

- [ ] **Step 8: Task 6 커밋**

```bash
git add src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/api/operations.py src/personal_agent_gateway/app.py src/personal_agent_gateway/health.py tests/test_api_team_runs.py tests/test_api_operations.py tests/test_app_lifecycle.py tests/test_health.py
git commit -m "feat(team-runs): 사이클 정책 API와 복구 배선 추가"
```

---

### Task 7: TeamPicker와 프론트엔드 API/controller

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`
- Modify: `frontend/src/hooks/useTeamRunController.js`
- Modify: `frontend/src/components/organisms/TeamPicker/index.jsx`
- Modify: `frontend/src/components/organisms/TeamPicker/TeamPicker.test.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**Interfaces:**
- Consumes: Task 6 HTTP API
- Produces: CONTINUOUS fixed 생성 form과 controller action

- [ ] **Step 1: TeamPicker payload 실패 테스트 작성**

```jsx
it("creates an AUTO continuous run with repeat and interval settings", async () => {
  const onStart = vi.fn();
  render(<TeamPicker teams={teams} onStart={onStart} />);

  expect(screen.getByText("CONTINUOUS")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "STANDARD" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "AUTO" }));
  await userEvent.clear(screen.getByLabelText("Repeat count"));
  await userEvent.type(screen.getByLabelText("Repeat count"), "5");
  await userEvent.clear(screen.getByLabelText("Interval minutes"));
  await userEvent.type(screen.getByLabelText("Interval minutes"), "30");
  await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

  expect(onStart).toHaveBeenCalledWith({
    team_id: "t1",
    goal: "",
    execution_policy: "auto",
    auto_repeat_count: 5,
    auto_interval_minutes: 30,
  });
});
```

- [ ] **Step 2: client 신규 endpoint 실패 테스트 작성**

```javascript
await api.triggerTeamCycle("run-1", {
  instruction: "next",
  client_request_id: "ui-1",
  previous_cycle_id: "cycle-7"
});
expect(fetch).toHaveBeenCalledWith(
  "/api/team-runs/run-1/cycle-requests",
  expect.objectContaining({ method: "POST" })
);
```

- [ ] **Step 3: 실패 확인**

Run: `npm test -- --run src/components/organisms/TeamPicker/TeamPicker.test.jsx src/api/client.test.js`

Workdir: `frontend`

Expected: FAIL because lifecycle UI and API methods still use the legacy contract.

- [ ] **Step 4: TeamPicker 최소 UI 구현**

상태는 다음으로 축소한다.

```jsx
const [executionPolicy, setExecutionPolicy] = useState("triggered");
const [repeatCount, setRepeatCount] = useState("3");
const [intervalMinutes, setIntervalMinutes] = useState("5");
```

submit payload:

```jsx
const payload = {
  team_id: team.id,
  goal: goal.trim(),
  execution_policy: executionPolicy,
};
if (executionPolicy === "auto") {
  payload.auto_repeat_count = Number(repeatCount);
  payload.auto_interval_minutes = Number(intervalMinutes);
}
onStart(payload);
```

Run mode selector는 제거하고 Preview에는 `CONTINUOUS · FIXED`, policy, AUTO 설정을
표시한다.

- [ ] **Step 5: client와 controller action 구현**

`client.js`에 다음 메서드를 추가한다.

```javascript
async triggerTeamCycle(id, payload) {
  return jsonOrNull(await fetch(
    `/api/team-runs/${encodeURIComponent(id)}/cycle-requests`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }
  ));
},
async retryAutoCycle(id, seriesId) {
  return jsonOrNull(await fetch(
    `/api/team-runs/${encodeURIComponent(id)}/auto-series/`
      + `${encodeURIComponent(seriesId)}/retry`,
    { method: "POST" }
  ));
},
async continueAutoCycle(id, seriesId) {
  return jsonOrNull(await fetch(
    `/api/team-runs/${encodeURIComponent(id)}/auto-series/`
      + `${encodeURIComponent(seriesId)}/continue`,
    { method: "POST" }
  ));
},
async restartAutoSeries(id) {
  return jsonOrNull(await fetch(
    `/api/team-runs/${encodeURIComponent(id)}/auto-series/restart`,
    { method: "POST" }
  ));
}
```

`useTeamRunController`에는 각각 호출 후 `teamRunDetail`, `teamRuns`를 병렬 refresh하는
다음 handler를 추가한다.

```javascript
async function refreshSelectedRun() {
  const [detail, runs] = await Promise.all([
    api.teamRunDetail(selectedTeamRunId),
    api.teamRuns()
  ]);
  setTeamRunDetail(detail);
  setTeamRuns(runs);
}

async function handleTriggerTeamCycle(payload) {
  if (!selectedTeamRunId) return false;
  try {
    await api.triggerTeamCycle(selectedTeamRunId, {
      ...payload,
      client_request_id: crypto.randomUUID()
    });
    await refreshSelectedRun();
    toast("Cycle을 대기열에 추가했습니다", "success");
    return true;
  } catch (_error) {
    toast("Failed to trigger cycle", "error");
    return false;
  }
}

async function handleRetryAuto(seriesId) {
  if (!selectedTeamRunId || !seriesId) return false;
  try {
    await api.retryAutoCycle(selectedTeamRunId, seriesId);
    await refreshSelectedRun();
    return true;
  } catch (_error) {
    toast("Failed to retry AUTO cycle", "error");
    return false;
  }
}

async function handleContinueAuto(seriesId) {
  if (!selectedTeamRunId || !seriesId) return false;
  try {
    await api.continueAutoCycle(selectedTeamRunId, seriesId);
    await refreshSelectedRun();
    return true;
  } catch (_error) {
    toast("Failed to continue AUTO series", "error");
    return false;
  }
}

async function handleRestartAuto() {
  if (!selectedTeamRunId) return false;
  try {
    await api.restartAutoSeries(selectedTeamRunId);
    await refreshSelectedRun();
    return true;
  } catch (_error) {
    toast("Failed to restart AUTO series", "error");
    return false;
  }
}
```

`handleCreateTeamRun`은 더 이상 `/start`를 호출하지 않고 생성된 Run을 바로 선택하며,
success copy는 AUTO면 `AUTO Team Run started`, TRIGGERED면
`TRIGGERED Team Run created`를 사용한다. 위 네 handler를 hook return object와
`GatewayApp` destructuring/props에 모두 추가한다.

`api.teamRunDetail`은 신규 snake_case 응답을 다음처럼 camelCase로 매핑한다.

```javascript
return {
  run: body?.team_run || null,
  agents: body?.agents || [],
  tasks: body?.tasks || [],
  messages: body?.messages || [],
  cycles: body?.cycles || [],
  decisionRequest: body?.decision_request || null,
  documentSummary: body?.document_summary || null,
  policyStatus: body?.policy_status || "ready",
  activeAutoSeries: body?.active_auto_series || null,
  queueCount: body?.queue_count || 0,
  activeRequest: body?.active_request || null
};
```

- [ ] **Step 6: TeamPicker/client/GatewayApp 생성 테스트 실행**

Run: `npm test -- --run src/components/organisms/TeamPicker/TeamPicker.test.jsx src/api/client.test.js src/components/containers/GatewayApp/GatewayApp.test.jsx`

Workdir: `frontend`

Expected: PASS.

- [ ] **Step 7: Task 7 커밋**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/hooks/useTeamRunController.js frontend/src/components/organisms/TeamPicker/index.jsx frontend/src/components/organisms/TeamPicker/TeamPicker.test.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx
git commit -m "feat(team-runs): 연속 실행 정책 생성 UI 추가"
```

---

### Task 8: TeamRunDetail 정책 제어와 Hook target 필터

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `frontend/src/components/organisms/HooksView/index.jsx`
- Modify: `frontend/src/components/organisms/HooksView/HooksView.test.jsx`
- Modify: `frontend/src/components/organisms/OperationsView/index.jsx`
- Modify: `frontend/src/components/organisms/OperationsView/OperationsView.test.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: controller의 Trigger/Retry/Continue/Restart handler와 detail read model
- Produces: AUTO control panel, TRIGGERED summary composer, TRIGGERED-only Hook selector
- Produces: Operations의 Cycle policy 상태 메타데이터

- [ ] **Step 1: TRIGGERED 이전 summary와 enqueue UI 실패 테스트 작성**

```jsx
it("shows previous summary and triggers with the preview cycle id", async () => {
  const onTriggerCycle = vi.fn(async () => true);
  render(<TeamRunDetail
    detail={{
      run: { id: "r1", execution_policy: "triggered", lifecycle_mode: "continuous" },
      policyStatus: "ready",
      queueCount: 0,
      cycles: [{
        id: "c7", sequence: 7, status: "completed",
        summary: "previous result", finished_at: "2026-07-20T05:00:00Z"
      }],
      tasks: [], agents: [], messages: []
    }}
    onTriggerCycle={onTriggerCycle}
  />);

  expect(screen.getByText("previous result")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Cycle instruction"), "next work");
  await userEvent.click(screen.getByRole("button", { name: "Trigger cycle" }));
  expect(onTriggerCycle).toHaveBeenCalledWith({
    instruction: "next work",
    previous_cycle_id: "c7"
  });
});
```

- [ ] **Step 2: AUTO action과 Hook 필터 실패 테스트 작성**

```jsx
it("shows AUTO progress and invokes the paused-failure actions", async () => {
  const onContinueAuto = vi.fn(async () => true);
  const onRetryAuto = vi.fn(async () => true);
  render(<TeamRunDetail
    detail={{
      run: {
        id: "r1", goal: "Maintain", status: "failed",
        execution_policy: "auto", lifecycle_mode: "continuous"
      },
      policyStatus: "paused_failure",
      queueCount: 0,
      activeAutoSeries: {
        id: "s1", target_slots: 5, settled_slots: 2,
        status: "paused_failure", next_run_at: null
      },
      cycles: [], tasks: [], agents: [], messages: []
    }}
    onContinueAuto={onContinueAuto}
    onRetryAuto={onRetryAuto}
  />);

  expect(screen.getByText("2 / 5 SETTLED")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(onContinueAuto).toHaveBeenCalledWith("s1");
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetryAuto).toHaveBeenCalledWith("s1");
});
```

HooksView:

```jsx
render(<HooksView teamRuns={[
  { id: "auto", goal: "AUTO run", execution_policy: "auto", lifecycle_mode: "continuous" },
  { id: "triggered", goal: "Triggered run", execution_policy: "triggered", lifecycle_mode: "continuous" },
]}
  hooks={[]}
  agents={[]}
  personas={[]}
  onCreate={vi.fn()}
  onToggle={vi.fn()}
  onRunNow={vi.fn()}
  onDelete={vi.fn()}
  onOpenRuns={vi.fn()}
  onCloseRuns={vi.fn()}
  onTestConnection={vi.fn()}
/>);
expect(screen.queryByRole("option", { name: /auto/i })).not.toBeInTheDocument();
expect(screen.getByRole("option", { name: /triggered/i })).toBeInTheDocument();
```

OperationsView:

```jsx
it("shows Team Run queue timing and pause metadata", () => {
  const viewData = {
    ...data,
    items: [{
      ...data.items[0],
      execution_policy: "auto",
      policy_status: "paused_failure",
      queue_count: 2,
      next_run_at: "2026-07-20T06:00:00Z",
      pause_reason: "worker failed",
      active_cycle_id: "cycle-7"
    }]
  };
  render(<OperationsView {...props({ data: viewData })} />);

  expect(screen.getByText(/AUTO · PAUSED FAILURE/)).toBeInTheDocument();
  expect(screen.getByText(/QUEUE 2/)).toBeInTheDocument();
  expect(screen.getByText(/worker failed/)).toBeInTheDocument();
  expect(screen.getByText(/cycle-7/)).toBeInTheDocument();
});
```

- [ ] **Step 3: 실패 확인**

Run: `npm test -- --run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/components/organisms/HooksView/HooksView.test.jsx src/components/organisms/OperationsView/OperationsView.test.jsx`

Workdir: `frontend`

Expected: FAIL because policy controls are not rendered.

- [ ] **Step 4: TeamRunDetail control panel 구현**

Props에 `onTriggerCycle`, `onRetryAuto`, `onContinueAuto`, `onRestartAuto`를 추가하고
TRIGGERED composer는 다음 submit 계약을 사용한다.

```jsx
async function submitTriggeredCycle() {
  const instruction = cycleInstruction.trim();
  if (!instruction || !onTriggerCycle) return;
  const accepted = await onTriggerCycle({
    instruction,
    previous_cycle_id: previousCycle?.id || null
  });
  if (accepted) setCycleInstruction("");
}
```

`execution_policy === "triggered"`이면 마지막
`completed | completed_with_failures` Cycle을 찾아 summary composer를 표시한다.
AUTO이면 `activeAutoSeries`와 `policyStatus`에 따라 progress/countdown 및 정확히 하나의
action group을 표시한다. `paused_interrupted`는 기존 Resume 버튼을 사용한다.
기존 Add Work button의 `canAddWork` 조건에는
`run.lifecycle_mode !== "continuous"`를 추가해 CycleRequest UI를 우회하지 못하게 한다.

- [ ] **Step 5: GatewayApp wiring과 SSE refresh 구현**

controller handler를 TeamRunDetail에 전달하고 `useTeamRunController.handleTeamEvent`의
refresh event에 다음을 추가한다.

```javascript
"team.cycle_request.queued",
"team.cycle.started",
"team.cycle.settled",
"team.auto_series.paused",
"team.auto_series.completed"
```

- [ ] **Step 6: HooksView 필터와 스타일 구현**

```javascript
const triggeredRuns = teamRuns.filter(
  (run) => run.lifecycle_mode === "continuous"
    && run.run_mode === "plan_and_execute"
    && run.execution_policy === "triggered"
);
```

기존 `continuousRuns` 참조를 모두 `triggeredRuns`로 바꾸고 empty copy도
`Create a TRIGGERED Team Run to enable TEAM RUN target.`으로 변경한다. `styles.css`에는
`.team-policy-panel`, `.team-cycle-trigger`, `.team-previous-cycle`,
`.team-auto-progress`, `.team-queue-count` 전용 스타일만 추가한다.

- [ ] **Step 7: Operations 정책 메타데이터 구현**

`OperationItems`의 Team Run row에서만 두 번째 meta line을 렌더한다.

```jsx
{item.domain === "team_run" && item.execution_policy ? (
  <div className="operations-row-meta mono">
    {item.execution_policy.toUpperCase()}
    {" · "}
    {String(item.policy_status || "ready").replaceAll("_", " ").toUpperCase()}
    {` · QUEUE ${item.queue_count || 0}`}
    {item.active_cycle_id ? ` · CYCLE ${item.active_cycle_id}` : ""}
    {item.next_run_at ? ` · NEXT ${fmtDateTime(item.next_run_at)}` : ""}
    {item.pause_reason ? ` · ${item.pause_reason}` : ""}
  </div>
) : null}
```

- [ ] **Step 8: 컴포넌트와 Gateway 통합 테스트 실행**

Run: `npm test -- --run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/components/organisms/HooksView/HooksView.test.jsx src/components/organisms/OperationsView/OperationsView.test.jsx src/components/containers/GatewayApp/GatewayApp.test.jsx`

Workdir: `frontend`

Expected: PASS.

- [ ] **Step 9: 프론트엔드 build 실행**

Run: `npm run build`

Workdir: `frontend`

Expected: Vite build exits 0. 기존 vendor asset runtime-resolution warning은 허용하되 새
compile error는 없어야 한다.

- [ ] **Step 10: Task 8 커밋**

```bash
git add frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx frontend/src/components/organisms/HooksView/index.jsx frontend/src/components/organisms/HooksView/HooksView.test.jsx frontend/src/components/organisms/OperationsView/index.jsx frontend/src/components/organisms/OperationsView/OperationsView.test.jsx frontend/src/components/containers/GatewayApp/index.jsx frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat(team-runs): 사이클 정책 제어와 이전 요약 UI 추가"
```

---

### Task 9: 재시작 복구, 전체 회귀, 운영 문서

**Files:**
- Create: `tests/test_team_cycle_recovery.py`
- Modify: `tests/test_app_lifecycle.py`
- Create: `docs/reports/2026-07-20-continuous-team-run-cycle-policies-implementation.md`
- Modify: `docs/registry.json` (generator output)

**Interfaces:**
- Consumes: Tasks 1–8의 완성된 end-to-end 계약
- Produces: idempotent startup recovery 증명과 완료 보고서

- [ ] **Step 1: restart 상태별 실패 테스트 작성**

`tests/test_team_cycle_recovery.py`에 재개용 builder를 둔다.

```python
from tests.team_cycle_helpers import (
    RecordingOrchestrator,
    dt,
    make_cycle_services,
)

@dataclass
class ReopenedServices:
    teams: TeamRunService
    cycles: TeamCycleService
    dispatcher: TeamCycleDispatcher

def reopen_services(tmp_path: Path) -> ReopenedServices:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(
        db, personas, tmp_path / "workspace", cycle_service=cycles
    )
    orchestrator = RecordingOrchestrator(teams)
    dispatcher = TeamCycleDispatcher(cycles, teams, orchestrator, EventBus())
    return ReopenedServices(teams, cycles, dispatcher)
```

```python
@pytest.mark.parametrize(
    ("cycle_status", "series_status", "expected_policy_status"),
    [
        ("running", "running", "paused_interrupted"),
        ("waiting_for_user", "paused_user", "paused_user"),
    ],
)
def test_reconcile_preserves_blocking_cycle_without_dispatching_next(
    tmp_path: Path,
    cycle_status: str,
    series_status: str,
    expected_policy_status: str,
) -> None:
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    series = cycles.get_active_series(run.id)
    request = cycles.list_requests(run.id)[0]
    request = cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id, "auto", request.source_id, request_id=request.id
    )
    teams.set_cycle_status(cycle.id, cycle_status)
    teams.set_run_status(run.id, cycle_status)
    db.execute(
        "update team_run_auto_series set status = ? where id = ?",
        (series_status, series.id),
    )
    restarted = reopen_services(tmp_path)
    restarted.teams.interrupt_active_runs()

    queued_run_ids = restarted.dispatcher.reconcile()

    assert restarted.cycles.policy_status(run.id) == expected_policy_status
    assert restarted.cycles.get_request(request.id).status == "dispatching"
    assert run.id not in queued_run_ids
```

Due/idempotency:

```python
def test_reconcile_enqueues_due_and_queued_requests_once(tmp_path: Path) -> None:
    db, teams, cycles, auto_run = make_cycle_services(tmp_path, "auto")
    series = cycles.get_active_series(auto_run.id)
    request = cycles.list_requests(auto_run.id)[0]
    request = cycles.claim_next(auto_run.id)
    cycle = teams.create_cycle(
        auto_run.id, "auto", request.source_id, request_id=request.id
    )
    teams.set_cycle_status(cycle.id, "completed", summary="done")
    cycles.settle_cycle(cycle.id, now=dt("2026-07-20T00:05:00+00:00"))
    db.execute(
        "update team_run_auto_series set next_run_at = ? where id = ?",
        ("2026-07-20T00:00:00+00:00", series.id),
    )

    first_services = reopen_services(tmp_path)
    first = first_services.cycles.reconcile(
        first_services.teams, now=dt("2026-07-20T00:10:00+00:00")
    )
    second_services = reopen_services(tmp_path)
    second = second_services.cycles.reconcile(
        second_services.teams, now=dt("2026-07-20T00:10:00+00:00")
    )

    assert first == [auto_run.id]
    assert second == [auto_run.id]
    requests = second_services.cycles.list_requests(auto_run.id)
    assert [item.slot_ordinal for item in requests] == [1, 2]
```

- [ ] **Step 2: recovery 테스트 실패 확인**

Run: `pytest tests/test_team_cycle_recovery.py -q`

Expected: 초기 구현에서 누락된 reconcile case가 있으면 해당 assertion으로 FAIL.

- [ ] **Step 3: reconcile을 테스트 계약에 맞게 완성**

`TeamCycleDispatcher.reconcile()`은 다음 순서를 한 transaction 단위로 지킨다.

```python
for request in cycles.list_dispatching_requests():
    cycle = teams.get_cycle_for_request(request.id)
    if cycle is None:
        cycles.requeue_claim(request.id)
    elif cycle.status in TERMINAL_CYCLE_STATUSES:
        cycles.settle_cycle(cycle.id, now=now)
    elif cycle.status == "interrupted":
        cycles.pause_interrupted(cycle.id)
    elif cycle.status == "waiting_for_user":
        cycles.pause_for_user(cycle.id)

cycles.enqueue_due_auto_requests(now=now)
return cycles.list_runnable_team_run_ids()
```

같은 함수를 반복 호출해도 Request/Cycle 수가 증가하지 않게 unique source ID를 사용한다.

- [ ] **Step 4: 전체 백엔드 검증**

Run: `pytest -q`

Expected: 전체 suite PASS.

Run: `ruff check src tests`

Expected: exits 0.

- [ ] **Step 5: 전체 프론트엔드 검증**

Run: `npm test -- --run`

Workdir: `frontend`

Expected: 전체 Vitest suite PASS.

Run: `npm run build`

Workdir: `frontend`

Expected: exits 0.

- [ ] **Step 6: 구현 보고서 작성**

`docs/reports/2026-07-20-continuous-team-run-cycle-policies-implementation.md`를 다음
frontmatter와 섹션으로 작성한다.

```markdown
---
title: Continuous Team Run Cycle Policies Implementation
type: report
domain: team-run
feature: continuous-cycle-policies
status: done
aliases:
  - AUTO TRIGGERED 구현 결과
tags:
  - team-run
  - cycle
  - automation
updated_at: 2026-07-20
---

# Continuous Team Run Cycle Policies 구현 결과

## 구현 범위
## 상태 및 데이터 마이그레이션
## API와 UI
## 재시작 복구
## 검증 결과
## 남은 제한
```

각 섹션에는 실제 구현 파일과 실행한 명령의 결과를 기록하고 설계 범위 밖 기능을
완료로 주장하지 않는다.

- [ ] **Step 7: 문서 registry 갱신과 diff 검증**

Run: `node C:/Users/Administrator/.claude/skills/dev-docs/scripts/build_docs_registry.mjs`

Expected: `docs/registry.json`에 새 report가 포함된다.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 8: Task 9 커밋**

```bash
git add tests/test_team_cycle_recovery.py tests/test_app_lifecycle.py docs/reports/2026-07-20-continuous-team-run-cycle-policies-implementation.md docs/registry.json
git commit -m "test(team-runs): 사이클 정책 복구와 전체 회귀 검증"
```

---

## Final Acceptance Checklist

- [ ] 신규 Team Run 생성 UI에 STANDARD 또는 run mode selector가 없다.
- [ ] AUTO는 첫 slot을 즉시 실행하고 fake-clock 기준으로 N slot에서 종료한다.
- [ ] TRIGGERED Manual과 Hook이 하나의 FIFO를 사용한다.
- [ ] Run별 `dispatching` Request와 active Cycle이 하나를 넘지 않는다.
- [ ] FAILED, WAITING_USER, INTERRUPTED가 다음 요청을 차단한다.
- [ ] CONTINUE, RETRY, decision answer, Resume가 올바른 slot/Cycle을 사용한다.
- [ ] Trigger UI에서 본 previous Cycle ID/summary가 Request snapshot과 일치한다.
- [ ] previous summary는 leader add-work 프롬프트에만 들어간다.
- [ ] startup reconcile을 반복해도 Request/Cycle이 중복되지 않는다.
- [ ] 기존 STANDARD 기록과 기존 Continuous Hook target이 조회 가능하다.
- [ ] `pytest -q`, `ruff check src tests`, 전체 Vitest, Vite build가 모두 통과한다.
