# 팀런에 묻기: 정지와 답변 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 진행 중인 팀런에 질문을 던지면 팀이 안전한 자리에서 멈추고 리드가 워크스페이스를 읽고 답하며, 재개하면 하던 일을 이어서 하는 경로를 만든다. 질문은 일감을 만들지 않는다.

**Architecture:** 정지는 `team_runs.pause_requested_at` 플래그로 요청되고, `_execute_batches`의 배치 재충전 지점에서 `RunPaused` 예외로 발현한다. 이 예외는 기존 정지 신호(`ProviderOperationWaiting` 등)와 같은 경로로 dispatcher까지 올라간다. 질문 응답은 사이클과 일감을 만들지 않고 `team_messages`에만 남는 별도 런타임 메서드다.

**Tech Stack:** Python 3 / FastAPI / SQLite(직접 SQL) / pytest, React + Vitest

**Spec:** `docs/superpowers/specs/2026-08-25-team-run-ask-and-pause-design.md`

## Global Constraints

- 백엔드 테스트 권위 실행: `PYTHONPATH=src python -m pytest -q -p no:randomly` (약 12분). 반복 중에는 `-n auto`를 써도 되지만 **권위 실행이 아니다** — 세 개의 worktree delivery 테스트가 xdist에서 실패한다 (AGENTS.md 참조).
- 린트: `python -m ruff check src/ tests/ evaluation/`
- 상태 컬럼에 DB CHECK 제약이 없다. 새 상태 값 추가에 마이그레이션이 필요 없다. **새 컬럼에만** 필요하다.
- `db.initialize()`는 `SCHEMA_SQL` 실행 후 마이그레이션을 돌린다. 새 컬럼은 `migrations.py`에만 추가하고 `db.py`는 건드리지 않는다 (기존 `plan_negotiation_enabled`가 그 선례다).
- 질문/답변 메시지 `kind`: `user_question`, `lead_answer`. 기존 kind는 `agent_output`, `plan_note`뿐이라 충돌하지 않는다.
- 새 런/사이클 상태 문자열: `paused`.
- 커밋 메시지는 한국어 Conventional Commits.
- 작업 트리에 `TeamRunDetail/index.jsx`, `ArchiveView/*`, `styles.css`의 미완 변경이 이미 있다. 덮어쓰지 않는다.

---

### Task 1: 정지 요청 상태를 저장한다

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` (마이그레이션 34, 파일 끝 `MIGRATIONS` 튜플)
- Modify: `src/personal_agent_gateway/team_lifecycle.py:21` `TeamRunStatus`, `:35` `CycleStatus`
- Modify: `src/personal_agent_gateway/teams.py` — `TeamRun` 데이터클래스(`:76` 부근), `_team_run_from_row`(`:4054` 부근), `set_run_status` 아래
- Test: `tests/test_teams.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces:
  - `TeamRun.pause_requested_at: str | None`
  - `TeamRunService.request_pause(team_run_id: str) -> TeamRun`
  - `TeamRunService.clear_pause_request(team_run_id: str) -> TeamRun`
  - 상태 문자열 `"paused"` (런과 사이클 양쪽)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_teams.py` 끝에 추가:

```python
def test_pause_request_is_recorded_and_cleared(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    member = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 1)

    assert teams.get_team_run(run.id).pause_requested_at is None

    paused = teams.request_pause(run.id)
    assert paused.pause_requested_at is not None
    assert teams.get_team_run(run.id).pause_requested_at == paused.pause_requested_at

    cleared = teams.clear_pause_request(run.id)
    assert cleared.pause_requested_at is None


def test_a_second_pause_request_keeps_the_first_time(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    member = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 1)

    first = teams.request_pause(run.id).pause_requested_at
    second = teams.request_pause(run.id).pause_requested_at

    assert first == second


def test_a_run_can_hold_the_paused_status(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    member = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 1)

    # paused 는 활성도 종료도 아니다: finished_at 이 찍히면 안 된다.
    paused = teams.set_run_status(run.id, "paused")
    assert paused.status == "paused"
    assert paused.finished_at is None
```

`tests/test_teams.py` 상단 import에 `Database`, `PersonaService`, `TeamRunService`가 있는지 확인하고 없으면 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_teams.py::test_pause_request_is_recorded_and_cleared -v -p no:randomly`
Expected: FAIL — `AttributeError: 'TeamRunService' object has no attribute 'request_pause'`

- [ ] **Step 3: 마이그레이션 34를 추가한다**

`src/personal_agent_gateway/migrations.py`, `_migration_33_open_operation_per_task` 정의 아래:

```python
def _migration_34_team_run_pause_request(
    connection: sqlite3.Connection,
) -> None:
    if "pause_requested_at" not in _columns(connection, "team_runs"):
        connection.execute(
            "alter table team_runs add column pause_requested_at text"
        )
```

`MIGRATIONS` 튜플 끝에:

```python
    (34, "team-run-pause-request", _migration_34_team_run_pause_request),
```

- [ ] **Step 4: 상태 어휘에 `paused`를 넣는다**

`src/personal_agent_gateway/team_lifecycle.py`:

```python
TeamRunStatus = Literal[
    "draft",
    "planning",
    "running",
    "summarizing",
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
    "interrupted",
    "paused",
    "waiting_for_user",
    "waiting_for_provider",
]
CycleStatus = Literal[
    "queued",
    "running",
    "waiting_for_provider",
    "waiting_for_user",
    "interrupted",
    "paused",
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
]
```

`TERMINAL_RUN_STATUSES`, `TERMINAL_CYCLE_STATUSES`(`team_lifecycle.py:67`), `_ACTIVE_RUN_STATUSES`(`teams.py:46`)는 **건드리지 않는다.** `paused`는 활성도 종료도 아니다 — `set_run_status`가 `started_at`/`finished_at`을 이 두 집합으로 결정하므로, 넣으면 정지가 시각을 잘못 찍는다.

- [ ] **Step 5: `TeamRun`에 필드를 추가한다**

`src/personal_agent_gateway/teams.py`, `TeamRun`의 마지막 필드(`plan_negotiation_enabled: bool = False`) 아래:

```python
    pause_requested_at: str | None = None
```

`_team_run_from_row`에서 `plan_negotiation_enabled=(...)` 항목 옆:

```python
        pause_requested_at=(
            row["pause_requested_at"]
            if "pause_requested_at" in row.keys()
            else None
        ),
```

`in row.keys()` 가드는 기존 `plan_negotiation_enabled`와 같은 형태다. 같은 이유이므로 같게 쓴다.

- [ ] **Step 6: 서비스 메서드 두 개를 쓴다**

`src/personal_agent_gateway/teams.py`, `set_run_status` 바로 아래:

```python
    def request_pause(self, team_run_id: str) -> TeamRun:
        """사용자가 정지를 요청했음을 기록한다.

        런타임은 안전한 자리에 닿았을 때 이 칸을 보고 멈춘다. 요청과 정지를
        따로 두는 이유는 둘 사이에 지연이 있기 때문이다 -- 진행 중인 워커
        호출을 끊지 않고 끝나기를 기다린다.

        where 절의 is null 은 두 번 눌러도 첫 요청 시각이 유지되게 한다.
        """
        self.get_team_run(team_run_id)
        now = _now()
        self._db.execute(
            """
            update team_runs
            set pause_requested_at = ?, updated_at = ?
            where id = ? and pause_requested_at is null
            """,
            (now, now, team_run_id),
        )
        return self.get_team_run(team_run_id)

    def clear_pause_request(self, team_run_id: str) -> TeamRun:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_runs set pause_requested_at = null, updated_at = ? where id = ?",
            (_now(), team_run_id),
        )
        return self.get_team_run(team_run_id)
```

- [ ] **Step 7: 테스트 통과를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_teams.py -q -p no:randomly`
Expected: PASS (신규 3개 포함, 기존 실패 없음)

- [ ] **Step 8: 스키마 회귀를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_db_agent_teams_schema.py -q -p no:randomly`
Expected: PASS

`LATEST_SCHEMA_VERSION`을 값으로 단언하는 테스트가 있으면 34로 갱신한다.

- [ ] **Step 9: 린트**

Run: `python -m ruff check src/ tests/`
Expected: 통과

- [ ] **Step 10: 커밋**

```bash
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat: 팀런 정지 요청 상태를 저장한다"
```

---

### Task 2: 배치 경계에서 멈춘다

**Files:**
- Modify: `src/personal_agent_gateway/team_lifecycle.py` (`RunPaused` 정의)
- Modify: `src/personal_agent_gateway/team_runtime.py` — `:3041` 검사 지점, `:2184`·`:4723`·`:4937` 재-raise 절, `:5210` 부근 헬퍼
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py` — `:12` import, `:227`·`:408` except 절
- Test: `tests/test_team_runtime.py`, `tests/test_team_cycle_dispatcher.py`

**Interfaces:**
- Consumes: Task 1의 `TeamRun.pause_requested_at`, `TeamRunService.request_pause`, `TeamRunService.clear_pause_request`, 상태 `"paused"`
- Produces:
  - `personal_agent_gateway.team_lifecycle.RunPaused(RuntimeError)` — 생성자 `RunPaused(team_run_id: str, cycle_id: str | None)`, 속성 `.team_run_id`, `.cycle_id`
  - `TeamRuntime._pause_requested(team_run_id: str) -> bool`
  - `TeamRuntime._enter_pause(run: TeamRun, cycle_id: str | None) -> RunPaused`

**왜 예외인가:** 검사 지점에서 그냥 `return`하면 `_execute_and_synthesize`(`team_runtime.py:4489`)가 곧바로 `_terminal_status`를 계산하고, 미완료 일감이 남아 있으므로 `LifecycleIntegrityError`를 던진다. 조기 반환을 받아주는 가드는 결정 요청 하나뿐이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_team_runtime.py` 끝에 추가. `make_operation_runtime`은 같은 파일 `:418`에 있다.

```python
def _pause_test_task(setup, title):
    return setup.teams.create_task(
        setup.run.id,
        title,
        f"{title} assignment.",
        owner_agent_id=setup.worker.id,
        cycle_id=setup.cycle.id,
        required=True,
        acceptance=TaskAcceptance(
            required_outputs=(),
            required_verifications=(RequiredVerification("done", None),),
        ),
    )


@pytest.mark.asyncio
async def test_a_pause_request_stops_the_run_between_batches(tmp_path):
    """정지는 떠 있는 호출을 끊지 않고, 배치가 빈 자리에서 걸린다."""
    setup = make_operation_runtime(tmp_path)
    teams = setup.teams
    first = _pause_test_task(setup, "First")
    second = _pause_test_task(setup, "Second")

    # 워커 한 명뿐이라 배치는 한 번에 하나. 첫 일감이 끝난 직후 정지를 요청한다.
    original_finish = teams.finish_task

    def finish_and_request_pause(task_id, agent_id, status, **kwargs):
        result = original_finish(task_id, agent_id, status, **kwargs)
        if task_id == first.id:
            teams.request_pause(setup.run.id)
        return result

    teams.finish_task = finish_and_request_pause

    with pytest.raises(RunPaused):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert teams.get_task(first.id).status == "completed"
    assert teams.get_task(second.id).status == "pending"
    assert teams.get_team_run(setup.run.id).status == "paused"
    assert teams.get_cycle(setup.cycle.id).status == "paused"
    # 요청은 정지로 바뀌면서 소진된다. 남겨두면 재개하자마자 또 멈춘다.
    assert teams.get_team_run(setup.run.id).pause_requested_at is None


@pytest.mark.asyncio
async def test_a_pause_does_not_mark_the_run_failed(tmp_path):
    """재-raise 목록 등록을 잊으면 넓은 except 가 정지를 실패로 만든다."""
    setup = make_operation_runtime(tmp_path)
    _pause_test_task(setup, "Only")
    setup.teams.request_pause(setup.run.id)

    with pytest.raises(RunPaused):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    run = setup.teams.get_team_run(setup.run.id)
    assert run.status == "paused"
    assert run.error_message is None
```

`tests/test_team_runtime.py` 상단 `team_lifecycle` import 줄을 확장한다:

```python
from personal_agent_gateway.team_lifecycle import (
    MAX_CONCURRENT_WORKERS,
    TERMINAL_RUN_STATUSES,
    RunPaused,
)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py::test_a_pause_request_stops_the_run_between_batches -v -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'RunPaused'`

- [ ] **Step 3: 예외를 정의한다**

`src/personal_agent_gateway/team_lifecycle.py`, 상태 Literal 정의 아래:

```python
class RunPaused(RuntimeError):
    """사용자 요청으로 런이 안전한 자리에서 멈췄다.

    ProviderOperationWaiting 과 나란한 정지 신호다. 실패가 아니므로
    start()/resume() 의 넓은 except 절보다 앞에서 재-raise 되어야 하고,
    그러지 않으면 정지할 때마다 런이 실패로 표시된다.
    """

    def __init__(self, team_run_id: str, cycle_id: str | None) -> None:
        super().__init__("run_paused")
        self.team_run_id = team_run_id
        self.cycle_id = cycle_id
```

`team_lifecycle.py`는 `collections.abc`/`dataclasses`/`typing`만 import하므로 runtime과 dispatcher 양쪽에서 순환 없이 쓸 수 있다. 이것이 `team_runtime.py`가 아니라 여기에 두는 이유다 — dispatcher는 `team_runtime`을 import하지 않는다.

- [ ] **Step 4: 헬퍼 두 개를 추가한다**

`src/personal_agent_gateway/team_runtime.py`, `_activate_cycle`(`:5210`) 옆:

```python
    def _pause_requested(self, team_run_id: str) -> bool:
        return self._teams.get_team_run(team_run_id).pause_requested_at is not None

    def _enter_pause(self, run: TeamRun, cycle_id: str | None) -> RunPaused:
        """정지를 확정하고 올릴 예외를 만든다.

        요청 칸을 여기서 지우는 이유: 정지가 성립한 순간 그 요청은 소진됐다.
        남겨두면 재개하자마자 다음 배치 경계에서 또 멈춘다.
        """
        self._teams.set_run_status(run.id, "paused")
        if cycle_id is not None:
            self._teams.set_cycle_status(cycle_id, "paused")
        self._teams.clear_pause_request(run.id)
        return RunPaused(run.id, cycle_id)
```

`team_runtime.py`의 `team_lifecycle` import 줄에 `RunPaused`를 더한다.

- [ ] **Step 5: 검사 지점을 넣는다**

`src/personal_agent_gateway/team_runtime.py:3041`, 기존 `if not batch:` 바로 앞에 삽입:

```python
            if not batch and self._pause_requested(run.id):
                # 여기가 유일하게 안전한 자리다. batch 는 비었을 때만 다시
                # 채워지므로(바로 아래) not batch 인 순간은 떠 있는 프로바이더
                # 호출이 하나도 없는 시점이다. 호출이 떠 있는 채로 빠져나가면
                # _execute 의 finally 가 그것을 취소하고 operation 이 invoking
                # 으로 남는데, 그 상태는 _recover_open_operation 이
                # OperationConflict 로 거절한다(:1320) -- 자동 재개가 아니라
                # 운영자 복구 대상이 된다.
                raise self._enter_pause(run, cycle_id)
```

- [ ] **Step 6: 재-raise 목록 세 곳에 등록한다**

`grep -n "except (ProviderOperationWaiting, AmbiguousModelOperation):" src/personal_agent_gateway/team_runtime.py` 로 확인하면 `:2184`(`start`), `:4723`, `:4937` 세 군데다. **모두** 다음으로 바꾼다:

```python
        except (ProviderOperationWaiting, AmbiguousModelOperation, RunPaused):
            raise
```

한 곳이라도 빠지면 그 경로의 정지가 `_settle_failed`에 잡혀 런이 실패로 표시된다. Step 1의 두 번째 테스트가 그것을 잡는다.

- [ ] **Step 7: 테스트 통과를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -k "pause" -v -p no:randomly`
Expected: PASS

- [ ] **Step 8: dispatcher가 정지를 조용히 받게 한다**

`src/personal_agent_gateway/team_cycle_dispatcher.py:12`:

```python
from personal_agent_gateway.team_lifecycle import RunPaused, TERMINAL_CYCLE_STATUSES
```

`:227`과 `:408`의 `except ProviderOperationWaiting:` **아래에** 각각 추가:

```python
        except RunPaused:
            # 정지는 사이클을 그대로 둔다. 사이클 요청도 dispatching 에
            # 남는데 그것이 맞다 -- 멈춰서 묻는 사이에 다른 사이클이
            # 끼어들면 안 된다(claim_next 는 런당 dispatching 요청이 하나
            # 있으면 새 요청을 잡지 않는다, team_cycles.py:456).
            # 재개가 이 요청을 이어받는다.
            return
```

- [ ] **Step 9: dispatcher 테스트를 쓴다**

먼저 `grep -n "TeamCycleDispatcher(" tests/test_team_cycle_dispatcher.py`로 이 파일이 dispatcher를 어떻게 조립하는지 읽고 그 관행을 그대로 따른다. 단언할 것은 셋이다:

```python
@pytest.mark.asyncio
async def test_a_paused_run_leaves_the_cycle_and_request_alone(tmp_path):
    """정지는 사이클을 실패로 만들지 않고, 요청을 dispatching 에 남긴다."""
    # 이 파일의 기존 dispatcher 조립을 그대로 쓰되, orchestrator 스텁의
    # run_cycle 이 RunPaused(team_run_id, cycle_id) 를 던지게 한다.
    #
    # 단언 셋:
    #   1. dispatcher 호출이 예외를 밖으로 내보내지 않는다
    #   2. 사이클 상태가 "failed" 가 아니다
    #   3. 사이클 요청이 여전히 "dispatching" 이다
```

**뼈대를 그대로 두고 넘어가지 않는다.** 세 단언을 실제 코드로 채운다.

- [ ] **Step 10: 관련 테스트를 돌린다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_team_cycle_recovery.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 11: 린트하고 커밋**

```bash
python -m ruff check src/ tests/
git add src/personal_agent_gateway/team_lifecycle.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_cycle_dispatcher.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py
git commit -m "feat: 배치 경계에서 팀런을 정지한다"
```

---

### Task 3: 정지 요청의 수명을 마감한다

**Files:**
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py` (`on_team_run_settled`)
- Modify: `src/personal_agent_gateway/api/team_runs.py` (`cancel_team_run`, `:1046` 부근)
- Test: `tests/test_team_cycle_dispatcher.py`, `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: Task 1의 `clear_pause_request`, Task 2의 `RunPaused`
- Produces: 없음 (기존 경로에 정리 한 줄씩)

**왜 필요한가:** 스펙 「요청이 소진되는 조건」과 「어긋났을 때」가 요구하는 것이다. 요청 칸이 남아 있으면 다음에 런이 돌 때 아무도 누르지 않은 정지가 걸린다. `_enter_pause`(Task 2)는 정지가 **성립한** 경우만 지우므로, 성립하지 못한 경우들이 여기 남는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_team_runs.py`:

```python
def test_canceling_a_run_drops_a_pending_pause_request(tmp_path):
    # 기존 관행대로 client 와 돌고 있는 run 을 세운다.
    client.post(f"/api/team-runs/{run_id}/pause")
    assert client.get(f"/api/team-runs/{run_id}").json()["team_run"]["pause_requested_at"]

    client.post(f"/api/team-runs/{run_id}/cancel")

    run = client.get(f"/api/team-runs/{run_id}").json()["team_run"]
    assert run["pause_requested_at"] is None
```

`tests/test_team_cycle_dispatcher.py`:

```python
@pytest.mark.asyncio
async def test_a_settled_cycle_drops_a_pause_request_nobody_could_honor(tmp_path):
    """정지를 눌렀는데 그 사이 팀이 끝나면, 요청은 소진된다.

    남겨두면 다음 사이클이 시작하자마자 아무도 누르지 않은 정지가 걸린다.
    """
    # 이 파일의 기존 조립을 쓴다. 사이클이 completed 로 settle 되는 흐름에서
    # 미리 teams.request_pause(run.id) 를 걸어두고, settle 뒤에
    # get_team_run(run.id).pause_requested_at 이 None 임을 단언한다.
```

**뼈대를 그대로 두지 않는다.** 실제 조립 코드로 채운다.

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -k "pause_request" -p no:randomly`
Expected: FAIL — `pause_requested_at` 이 여전히 남아 있다

- [ ] **Step 3: 사이클이 끝나면 요청을 지운다**

`src/personal_agent_gateway/team_cycle_dispatcher.py`의 `on_team_run_settled`, `result = self._cycles.settle_cycle(cycle_id)` 앞:

```python
        if cycle.status in TERMINAL_CYCLE_STATUSES:
            # 정지를 눌렀는데 그 사이 팀이 끝났다. 멈출 것이 없으므로 요청은
            # 소진된다. 남겨두면 다음 사이클이 시작하자마자 아무도 누르지
            # 않은 정지가 걸린다.
            self._teams.clear_pause_request(run.id)
```

`waiting_for_provider` 조기 반환(`:258`)보다 **뒤**, `settle_cycle` 호출보다 **앞**에 둔다.

- [ ] **Step 4: 취소하면 요청을 지운다**

`src/personal_agent_gateway/api/team_runs.py`의 `cancel_team_run`, `record_domain_audit` 호출 직전:

```python
    if run.pause_requested_at is not None:
        run = service.clear_pause_request(team_run_id)
```

`_settle_canceled`(`team_runtime.py:4519` 부근)가 아니라 여기에 두는 이유: 취소는 API에서 시작하고 런타임 태스크는 이미 취소됐을 수 있어서, 런타임 안쪽은 실행이 보장되지 않는다.

- [ ] **Step 5: 서버 재시작을 확인한다**

재시작은 새 코드가 필요 없다. 재시작 시 돌던 런은 `interrupted`로 정리되고 `_interrupt_cycle`이 `on_team_run_settled`를 부르는데, 그 사이클은 종료 상태가 아니므로 Step 3의 정리는 걸리지 않는다. 요청은 남고, 재개하면 첫 배치 경계에서 정지가 성립한다 — **사용자가 눌렀던 정지가 재시작을 건너 살아남는 것이므로 맞는 동작이다.**

이것을 문장으로만 두지 말고 테스트로 고정한다:

```python
@pytest.mark.asyncio
async def test_a_pause_request_survives_an_interrupt(tmp_path):
    """재시작을 건너도 사용자가 누른 정지는 살아남는다."""
    # 사이클을 interrupted 로 만들고 on_team_run_settled 를 태운 뒤,
    # pause_requested_at 이 그대로 남아 있음을 단언한다.
```

- [ ] **Step 6: 테스트 통과를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py tests/test_team_cycle_dispatcher.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 7: 린트하고 커밋**

```bash
python -m ruff check src/ tests/
git add src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/api/team_runs.py tests/test_team_cycle_dispatcher.py tests/test_api_team_runs.py
git commit -m "fix: 성립하지 못한 정지 요청을 소진시킨다"
```

---

### Task 4: 리드가 질문에 답한다

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `QUESTION_PROMPT` 상수(`:333` `CONTEST_PROMPT` 아래), `answer_question` 메서드
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: Task 1의 상태 `"paused"`
- Produces:
  - `personal_agent_gateway.team_runtime.QUESTION_PROMPT: str`
  - `TeamRuntime.answer_question(team_run_id: str, question: str, cycle_id: str | None = None) -> str` — 리드의 답 본문을 반환하고 `user_question`/`lead_answer` 메시지 두 개를 남긴다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
@pytest.mark.asyncio
async def test_a_question_produces_an_answer_and_no_tasks(tmp_path):
    """이 조각의 본론: 물어도 일감이 생기지 않는다."""
    setup = make_operation_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("src/foo.py:12 에서 그렇게 하고 있습니다.", [])
    ]

    before = len(setup.teams.list_tasks(setup.run.id, setup.cycle.id))
    answer = await setup.runtime.answer_question(
        setup.run.id,
        "이 값은 어디서 정해지나요?",
        setup.cycle.id,
    )

    assert "src/foo.py:12" in answer
    assert len(setup.teams.list_tasks(setup.run.id, setup.cycle.id)) == before

    kinds = [message.kind for message in setup.teams.list_messages(setup.run.id)]
    assert "user_question" in kinds
    assert "lead_answer" in kinds


@pytest.mark.asyncio
async def test_a_question_can_be_asked_more_than_once_while_paused(tmp_path):
    setup = make_operation_runtime(tmp_path)
    setup.teams.set_run_status(setup.run.id, "paused")
    setup.lead_client.responses = [
        ModelResponse("첫 번째 답.", []),
        ModelResponse("두 번째 답.", []),
    ]

    first = await setup.runtime.answer_question(setup.run.id, "첫 질문", setup.cycle.id)
    second = await setup.runtime.answer_question(setup.run.id, "둘째 질문", setup.cycle.id)

    assert first == "첫 번째 답."
    assert second == "두 번째 답."
    assert setup.teams.get_team_run(setup.run.id).status == "paused"


def test_the_question_prompt_forbids_planning_and_demands_grounding():
    """프롬프트가 지켜야 하는 네 가지를 고정한다.

    WORKER_PROMPT 가 같은 근거 규칙을 갖는 이유와 같다: 확인하지 않은 주장을
    사실처럼 쓰면 답변과 구분되지 않는다. 질문 답변에는 acceptance 검수가
    없으므로 워커보다 오히려 더 필요하다.
    """
    assert "Do not break this into tasks" in QUESTION_PROMPT
    assert "do not return JSON" in QUESTION_PROMPT
    assert "read the workspace" in QUESTION_PROMPT
    assert "name the file that shows it" in QUESTION_PROMPT
    assert "say so plainly instead of" in QUESTION_PROMPT
```

`tests/test_team_runtime.py`의 `team_runtime` import 목록에 `QUESTION_PROMPT`를 추가한다.

**`setup.lead_client.responses` 확인:** `make_operation_runtime`은 `lead_client = OperationModel([ModelResponse("summary", [])])`를 만든다. `OperationModel`이 `responses` 속성을 재할당 가능한 형태로 갖는지 먼저 읽고(`grep -n "class OperationModel" -A 20 tests/test_team_runtime.py`), 아니면 그 클래스의 관행에 맞춰 응답을 주입한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py::test_a_question_produces_an_answer_and_no_tasks -v -p no:randomly`
Expected: FAIL — `ImportError` 또는 `AttributeError: 'TeamRuntime' object has no attribute 'answer_question'`

- [ ] **Step 3: 프롬프트를 쓴다**

`src/personal_agent_gateway/team_runtime.py`, `CONTEST_PROMPT`(`:333`) 아래:

```python
QUESTION_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
The user is asking you a question. They are not adding work and not contesting the plan.

Goal: {goal}
Current tasks:
{tasks}
The question:
{question}

Answer it. Do not break this into tasks, do not assign anything to anyone, and
do not return JSON. Reply in plain prose, in the language the question was asked in.

Before you answer, read the workspace. Open the files the question is about rather
than answering from what you remember of this run.

When you state something as fact about this repository, name the file that shows it,
with the line when you can. If you could not confirm something -- a file you cannot
reach, a place outside what this run is allowed to read -- say so plainly instead of
asserting it. A claim nobody checked, written as fact, is worse than a stated gap: it
reads as an answer and cannot be told apart from one. Nothing reviews this answer
before the user reads it, so that distinction is yours to keep."""
```

- [ ] **Step 4: `answer_question`을 쓴다**

`TeamRuntime`에 추가 (`adjudicate_contest` 근처):

```python
    async def answer_question(
        self,
        team_run_id: str,
        question: str,
        cycle_id: str | None = None,
    ) -> str:
        """리드에게 묻고 답을 받는다. 일감도 사이클도 만들지 않는다.

        런 상태를 건드리지 않는 것이 요점이다. 정지 중이면 정지인 채로,
        끝난 런이면 끝난 채로 답만 돌려준다. 답변이 실패해도 팀런의
        일감·사이클·수용 상태는 그대로다 -- 질문은 팀런 바깥의 일이다.

        operation 원장을 쓰지 않는 이유: 원장은 사이클의 일감 실행을
        복구하기 위한 것이고, 질문은 사이클에 속하지 않는다. 실패하면
        사용자가 다시 물으면 되므로 복구할 상태 자체가 없다.
        """
        run = self._teams.get_team_run(team_run_id)
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        tasks = self._teams.list_tasks(run.id, cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, cycle_id),
            cycle_id,
        ) + _rules_block(
            self._rules_snapshot(run, cycle_id), include_persona_baseline=False
        ) + QUESTION_PROMPT.format(
            goal=self._goal_context(run, cycle_id),
            tasks="\n".join(
                f"- {task.title} ({task.status})" for task in tasks
            ) or "(no tasks yet)",
            question=question,
        )
        self._teams.append_message(
            run.id, None, leader.id, "user_question", question, {}, cycle_id=cycle_id
        )
        model = self._model(leader_agent, cycle_id)
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        answer = response.content.strip()
        self._teams.append_message(
            run.id, leader.id, None, "lead_answer", answer, {}, cycle_id=cycle_id
        )
        return answer
```

- [ ] **Step 5: 테스트 통과를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -k "question" -p no:randomly`
Expected: PASS

- [ ] **Step 6: 린트하고 커밋**

```bash
python -m ruff check src/ tests/
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 리드가 일감을 만들지 않고 질문에 답한다"
```

---

### Task 5: 서버 경로를 낸다

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` — 요청 모델, 엔드포인트 3개, `/resume` 가드(`:788`, `:796`)
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: Task 1의 `request_pause`, Task 4의 `TeamRuntime.answer_question`
- Produces:
  - `POST /api/team-runs/{id}/pause` → `{"team_run": {...}}`
  - `POST /api/team-runs/{id}/questions` body `{"question": "..."}` → `{"answer": "...", "team_run": {...}}`
  - `GET /api/team-runs/{id}/questions` → `{"messages": [{"id","kind","content","created_at"}]}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

먼저 `grep -n "def test_add_work" -A 25 tests/test_api_team_runs.py`로 이 파일이 `TestClient`와 팀런을 어떻게 세우는지 읽고, 그 세팅을 그대로 쓴다. 아래는 단언 내용이고, 세팅은 기존 관행에 맞춘다.

```python
def test_pausing_an_idle_run_marks_it_paused_immediately(tmp_path):
    # 기존 관행대로 client 와 run 을 세운다 (돌고 있지 않은 런).
    response = client.post(f"/api/team-runs/{run_id}/pause")
    assert response.status_code == 200
    assert response.json()["team_run"]["status"] == "paused"


def test_a_question_returns_an_answer_and_creates_no_tasks(tmp_path):
    before = client.get(f"/api/team-runs/{run_id}/tasks").json()["tasks"]

    response = client.post(
        f"/api/team-runs/{run_id}/questions",
        json={"question": "이 값은 어디서 정해지나요?"},
    )
    assert response.status_code == 200
    assert response.json()["answer"]

    after = client.get(f"/api/team-runs/{run_id}/tasks").json()["tasks"]
    assert len(after) == len(before)


def test_the_question_log_holds_both_sides(tmp_path):
    client.post(f"/api/team-runs/{run_id}/questions", json={"question": "왜죠"})

    messages = client.get(f"/api/team-runs/{run_id}/questions").json()["messages"]
    kinds = [message["kind"] for message in messages]
    assert kinds == ["user_question", "lead_answer"]


def test_a_blank_question_is_refused(tmp_path):
    response = client.post(
        f"/api/team-runs/{run_id}/questions", json={"question": "   "}
    )
    assert response.status_code == 422


def test_a_paused_run_can_be_resumed(tmp_path):
    client.post(f"/api/team-runs/{run_id}/pause")
    response = client.post(f"/api/team-runs/{run_id}/resume")
    assert response.status_code != 409
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -k "pause or question" -p no:randomly`
Expected: FAIL — 404 (경로 없음)

- [ ] **Step 3: 요청 모델을 추가한다**

`src/personal_agent_gateway/api/team_runs.py`, 다른 `BaseModel` 옆:

```python
class AskQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str

    @field_validator("question")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question is required")
        return value
```

이 파일의 기존 검증자(`:93`, `:106`)와 같은 모양이다.

- [ ] **Step 4: 정지 엔드포인트를 쓴다**

```python
@router.post("/{team_run_id}/pause")
async def pause_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if run.status == "paused":
        return {"team_run": _team_run_payload(run, service)}
    if run.status in _TERMINAL:
        raise HTTPException(
            status_code=409, detail="Settled team runs cannot be paused"
        )
    if registry.is_running(team_run_id):
        # 돌고 있으면 요청만 건다. 런타임이 배치 경계에서 집는다.
        run = service.request_pause(team_run_id)
    else:
        # 돌고 있지 않으면 기다릴 것이 없다.
        run = service.set_run_status(team_run_id, "paused")
    record_domain_audit(
        request,
        principal,
        event_type="team.run_paused",
        action="team_runs.pause",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run, service)}
```

- [ ] **Step 5: 질문 엔드포인트 둘을 쓴다**

```python
@router.post("/{team_run_id}/questions")
async def ask_team_run(
    request: Request,
    team_run_id: str,
    payload: AskQuestionRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id):
        raise HTTPException(
            status_code=409,
            detail="Pause the run before asking; it is still working",
        )
    answer = await request.app.state.team_runtime.answer_question(
        team_run_id,
        payload.question,
    )
    record_domain_audit(
        request,
        principal,
        event_type="team.question_asked",
        action="team_runs.ask",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
        metadata={"question_length": len(payload.question)},
    )
    return {
        "answer": answer,
        "team_run": _team_run_payload(service.get_team_run(team_run_id), service),
    }


@router.get("/{team_run_id}/questions")
def list_team_run_questions(
    request: Request,
    team_run_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    try:
        messages = service.list_messages(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {
        "messages": [
            {
                "id": message.id,
                "kind": message.kind,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in messages
            if message.kind in {"user_question", "lead_answer"}
        ]
    }
```

- [ ] **Step 6: `/resume` 가드를 넓힌다**

`src/personal_agent_gateway/api/team_runs.py:788`:

```python
    if run.status not in {"interrupted", "paused"}:
        raise HTTPException(
            status_code=409,
            detail="Only interrupted or paused team runs can be resumed",
        )
```

`:796`의 재개할 사이클 선택:

```python
                if candidate.status in {"interrupted", "paused"}
```

그 아래 `"No interrupted cycle to resume"` 문구를 `"No interrupted or paused cycle to resume"`으로 바꾼다.

- [ ] **Step 7: 테스트 통과를 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 8: 린트하고 커밋**

```bash
python -m ruff check src/ tests/
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py
git commit -m "feat: 팀런 정지와 질문 경로를 낸다"
```

---

### Task 6: 화면에 물어보기를 붙인다

**Files:**
- Modify: `frontend/src/api/client.js` (`:512` `addWork` 옆)
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` — `:50`, `:956`, `:959`, `:961`, `:1336` 부근, 그리고 새 대화상자
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: Task 5의 세 엔드포인트
- Produces: `TeamRunDetail`의 새 props `onPause(runId) -> Promise`, `onAskQuestion(runId, question) -> Promise<{answer: string}>`

**주의:** 작업 트리에 `TeamRunDetail/index.jsx`와 `styles.css`의 미완 변경이 이미 있다. `git diff`로 먼저 확인하고 덮어쓰지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

이 파일의 기존 렌더 헬퍼를 먼저 읽고(`grep -n "function render\|const render" frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`) 그대로 쓴다.

```jsx
it("정지된 런에서는 재개 버튼이 보인다", () => {
  renderDetail({ run: { ...baseRun, status: "paused" } });
  expect(screen.getByRole("button", { name: /재개/ })).toBeInTheDocument();
});

it("정지된 런에서는 일감 추가를 막는다", () => {
  renderDetail({ run: { ...baseRun, status: "paused" } });
  expect(screen.queryByRole("button", { name: /일감 추가/ })).not.toBeInTheDocument();
});

it("물어보기를 누르면 질문이 전달된다", async () => {
  const onAskQuestion = vi.fn().mockResolvedValue({ answer: "답입니다" });
  renderDetail({ run: { ...baseRun, status: "paused" }, onAskQuestion });

  await userEvent.click(screen.getByRole("button", { name: /물어보기/ }));
  await userEvent.type(screen.getByLabelText(/QUESTION/i), "이건 왜 이렇죠");
  await userEvent.click(screen.getByRole("button", { name: /보내기/ }));

  expect(onAskQuestion).toHaveBeenCalledWith(baseRun.id, "이건 왜 이렇죠");
});

it("답을 받아도 대화상자가 닫히지 않는다", async () => {
  const onAskQuestion = vi.fn().mockResolvedValue({ answer: "답입니다" });
  renderDetail({ run: { ...baseRun, status: "paused" }, onAskQuestion });

  await userEvent.click(screen.getByRole("button", { name: /물어보기/ }));
  await userEvent.type(screen.getByLabelText(/QUESTION/i), "질문");
  await userEvent.click(screen.getByRole("button", { name: /보내기/ }));

  expect(await screen.findByText("답입니다")).toBeInTheDocument();
  expect(screen.getByLabelText(/QUESTION/i)).toBeInTheDocument();
});

it("정지를 기다리는 동안 요청 중임을 보여준다", () => {
  renderDetail({
    run: { ...baseRun, status: "running", pause_requested_at: "2026-08-25T00:00:00Z" },
  });
  expect(screen.getByText(/정지 요청됨/)).toBeInTheDocument();
});

it("계획 중 정지 요청이면 오래 걸리는 이유를 말한다", () => {
  renderDetail({
    run: { ...baseRun, status: "planning", pause_requested_at: "2026-08-25T00:00:00Z" },
  });
  expect(screen.getByText(/계획이 끝날 때까지/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: FAIL — 버튼과 문구를 찾지 못함

- [ ] **Step 3: API 클라이언트 메서드를 추가한다**

`frontend/src/api/client.js`, `addWork`(`:512`) 옆. `addWork`의 정확한 모양(헤더, `jsonOrNull` 사용)을 먼저 읽고 맞춘다.

```js
  async pauseRun(id) {
    return jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/pause`, {
      method: "POST",
    }));
  },

  async askQuestion(id, question) {
    return jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/questions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    }));
  },

  async listQuestions(id) {
    return jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/questions`));
  },
```

- [ ] **Step 4: 상태 목록 네 곳을 갱신한다**

`frontend/src/components/organisms/TeamRunDetail/index.jsx`:

```jsx
// :50 정렬 우선순위
if (["interrupted", "waiting_for_user", "paused"].includes(status)) return -1;

// :956 일감 추가 가능 조건 — 제외 목록에 paused 를 더한다
&& run.status !== "interrupted"
&& run.status !== "waiting_for_user"
&& run.status !== "paused"

// :959 재개 가능 조건
const canResume = Boolean(
  onResume && ["interrupted", "paused"].includes(run.status)
);

// :961 취소 가능 조건
onCancel && ["planning", "running", "summarizing", "waiting_for_user", "paused"].includes(run.status)
```

`:988`의 `canResumeFailure`는 실패한 일감을 되살리는 경로라 `interrupted` 전용이 맞다. **건드리지 않는다.**

- [ ] **Step 5: 배너 두 개를 추가한다**

`:1336`의 중단 배너 **옆에** (교체가 아니라 추가):

```jsx
{run.status === "paused" ? (
  <div className="team-paused-banner" role="status">
    <span className="headline team-paused-title">정지됨</span>
    <span className="team-paused-copy">
      물어보기로 리드에게 질문할 수 있습니다. 재개하면 하던 일을 이어서 합니다.
    </span>
  </div>
) : null}

{run.status !== "paused" && run.pause_requested_at ? (
  <div className="team-paused-banner" role="status">
    <span className="headline team-paused-title">정지 요청됨</span>
    <span className="team-paused-copy">
      돌고 있는 작업이 끝나면 멈춥니다.
      {run.status === "planning"
        ? " 계획 단계라 계획이 끝날 때까지 걸립니다."
        : ""}
    </span>
  </div>
) : null}
```

계획 단계 문구는 스펙 「대가」 표의 두 번째 줄을 화면에서 갚는 것이다 — 오래 걸리는 이유가 드러나야 한다.

- [ ] **Step 6: 물어보기 버튼과 대화상자를 추가한다**

일감 추가 대화상자(`:390` `team-add-work-dialog`)를 본떠 만든다.

- 버튼 노출 조건: `run.status === "paused"` 이거나 런이 돌고 있지 않을 때
- 대화상자는 지금까지의 질문·답변을 위에 쌓아 보여주고 아래에 입력칸을 둔다
- 라벨은 `QUESTION`, 보내기 버튼 이름은 `보내기`
- **보낸 뒤 대화상자를 닫지 않는다.** 이어서 더 물을 수 있어야 한다 (Step 1의 네 번째 테스트)
- 답변 대기 중에는 보내기 버튼을 비활성화하고, 실패하면 "답을 받지 못했습니다"를 대화상자 안에 띄운다. 대화상자는 열린 채로 둔다

- [ ] **Step 7: 스타일을 추가한다**

`src/personal_agent_gateway/static/styles.css`에 `.team-paused-banner`, `.team-paused-title`, `.team-paused-copy`, 질문 대화상자 클래스를 추가한다. 기존 `.team-interrupted-banner`와 `.team-add-work-dialog` 규칙을 본떠 쓴다. **작업 트리의 미완 변경을 덮어쓰지 않는다.**

- [ ] **Step 8: 프론트 테스트를 돌린다**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: 팀런 화면에 물어보기와 정지를 붙인다"
```

---

### Task 7: 스펙의 확인 목록을 채우고 전체를 돌린다

**Files:**
- Modify: `tests/test_team_runtime.py` (빠진 테스트 4개)

**Interfaces:**
- Consumes: Task 1–6 전부
- Produces: 없음 (검증 전용)

- [ ] **Step 1: 덮인 것과 안 덮인 것을 가른다**

| # | 스펙 「확인할 것」 | 테스트 |
| --- | --- | --- |
| 1 | 질문을 던져도 일감이 생기지 않는다 | Task 4 `test_a_question_produces_an_answer_and_no_tasks`, Task 5 `test_a_question_returns_an_answer_and_creates_no_tasks` |
| 2 | 배치가 끝난 뒤 멈추고 떠 있는 호출이 없다 | Task 2 `test_a_pause_request_stops_the_run_between_batches` |
| 3 | 재개하면 이어서 돈다 | Task 5 `test_a_paused_run_can_be_resumed` + 아래 Step 3 |
| 4 | 멈춘 채로 여러 번 물어도 답이 온다 | Task 4 `test_a_question_can_be_asked_more_than_once_while_paused` |
| 5 | 안 돌고 있을 때 바로 답이 온다 | Task 5 `test_pausing_an_idle_run_marks_it_paused_immediately` |
| 6 | 먼저 끝난 경우에도 답은 온다 | **없음 → Step 2** |
| 7 | 답변 실패가 런을 망가뜨리지 않는다 | **없음 → Step 2** |
| 8 | 정지해도 실패로 표시되지 않는다 | Task 2 `test_a_pause_does_not_mark_the_run_failed` |
| 9 | 정지해도 LifecycleIntegrityError가 안 난다 | Task 2 `test_a_pause_request_stops_the_run_between_batches` |
| 10 | 재개 후 같은 사이클에서 이어진다 | **없음 → Step 3** |
| 11 | 계획 중 정지는 계획이 끝난 뒤 멈춘다 | **없음 → Step 3** |
| 추가 | 팀이 먼저 끝나면 요청이 소진된다 | Task 3 `test_a_settled_cycle_drops_a_pause_request_nobody_could_honor` |
| 추가 | 취소가 대기 중인 요청을 지운다 | Task 3 `test_canceling_a_run_drops_a_pending_pause_request` |
| 추가 | 재시작을 건너 요청이 살아남는다 | Task 3 `test_a_pause_request_survives_an_interrupt` |

- [ ] **Step 2: 6번과 7번을 쓴다**

`tests/test_team_runtime.py`에 추가:

```python
@pytest.mark.asyncio
async def test_a_settled_run_still_answers(tmp_path):
    setup = make_operation_runtime(tmp_path)
    setup.teams.set_run_status(setup.run.id, "completed")
    setup.lead_client.responses = [ModelResponse("끝난 뒤에도 답합니다.", [])]

    answer = await setup.runtime.answer_question(setup.run.id, "무엇을 했나요?")

    assert answer == "끝난 뒤에도 답합니다."
    assert setup.teams.get_team_run(setup.run.id).status == "completed"


@pytest.mark.asyncio
async def test_a_failed_answer_leaves_the_run_alone(tmp_path):
    """질문은 팀런 바깥의 일이다. 실패해도 런 상태를 건드리지 않는다."""
    setup = make_operation_runtime(tmp_path)
    setup.teams.set_run_status(setup.run.id, "paused")
    setup.lead_client.responses = [RuntimeError("provider down")]

    with pytest.raises(RuntimeError):
        await setup.runtime.answer_question(setup.run.id, "왜 이렇죠")

    run = setup.teams.get_team_run(setup.run.id)
    assert run.status == "paused"
    assert run.error_message is None
```

`OperationModel`이 리스트 항목의 `Exception`을 raise하는지 확인한다. `ScriptedModel`(`tests/test_team_runtime.py:104` 부근)은 그렇게 하므로, `OperationModel`이 아니면 그 관행에 맞춰 실패를 주입한다.

- [ ] **Step 3: 10번과 11번을 쓴다**

Task 2의 `test_a_pause_request_stops_the_run_between_batches` 세팅(`_pause_test_task`와 `finish_task` 감싸기)을 그대로 가져와 채운다.

```python
@pytest.mark.asyncio
async def test_resuming_a_paused_cycle_keeps_the_same_cycle(tmp_path):
    """새 사이클이 생기면 정지 전 일감이 다른 사이클에 남는다."""
    setup = make_operation_runtime(tmp_path)
    teams = setup.teams
    first = _pause_test_task(setup, "First")
    second = _pause_test_task(setup, "Second")

    original_finish = teams.finish_task

    def finish_and_request_pause(task_id, agent_id, status, **kwargs):
        result = original_finish(task_id, agent_id, status, **kwargs)
        if task_id == first.id:
            teams.request_pause(setup.run.id)
        return result

    teams.finish_task = finish_and_request_pause
    with pytest.raises(RunPaused):
        await setup.runtime.resume(setup.run.id, setup.cycle.id)

    teams.finish_task = original_finish
    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert len(teams.list_cycles(setup.run.id)) == 1
    assert teams.get_task(second.id).cycle_id == setup.cycle.id
    assert teams.get_task(second.id).status == "completed"


@pytest.mark.asyncio
async def test_a_pause_requested_during_planning_lands_after_planning(tmp_path):
    """계획 중에는 검사 지점이 없다. 계획이 끝난 뒤 첫 배치 경계에서 멈춘다."""
    setup = make_operation_runtime(tmp_path, cycle_instruction="work")
    # 리드가 계획을 반환하기 직전에 정지를 건다: model_factory 를 감싸서
    # 리드 호출이 일어날 때 teams.request_pause(setup.run.id) 를 호출한다.
    #
    # 단언 셋:
    #   1. RunPaused 가 올라온다
    #   2. 계획이 정상적으로 만들어졌다 (list_tasks 가 비어 있지 않다)
    #   3. 계획된 일감이 전부 pending 이다 -- 하나도 실행되지 않았다
```

**뼈대를 그대로 두고 넘어가지 않는다.** 두 번째 테스트의 세 단언을 실제 코드로 채운다. 채우지 않으면 스펙 확인 목록의 11번이 비게 된다.

- [ ] **Step 4: 권위 실행**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: 전부 통과. 약 12분 걸린다.

- [ ] **Step 5: 린트와 프론트 전체**

```bash
python -m ruff check src/ tests/ evaluation/
cd frontend && npx vitest run
```
Expected: 둘 다 통과

- [ ] **Step 6: 커밋**

```bash
git add tests/test_team_runtime.py
git commit -m "test: 팀런 정지·질문 확인 목록을 채운다"
```
