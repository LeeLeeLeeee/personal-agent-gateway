# Team Run: 동적 협업 메시징 + 실행 신뢰성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team Run에 리더 중재 hub-and-spoke 동적 메시징과 실행 신뢰성(백그라운드 실행·실제 취소·부분 실패 격리)을 더하고, codex/claude 혼합 팀을 지원한다.

**Architecture:** `TeamRuntime`이 라운드(yield-and-resume) 기반으로 오케스트레이션한다. codex/claude 클라이언트는 `upstream_session_id`로 resume하며 backend 무관하게 동작한다. 상태는 `TeamRunService`(SQLite), 실행은 `ModelClient`, 실행 수명주기는 `TeamRunRegistry`가 관리한다.

**Tech Stack:** Python 3.13, FastAPI, SQLite, asyncio, pytest/pytest-asyncio, React(Vite)/vitest.

## Global Constraints

- 워커는 **순차 실행**(병렬 없음). 모든 에이전트는 공용 `config.workspace_root` 사용(격리 없음).
- 메시징 라운드 전역 예산 기본값 **8** (`rounds_budget`). 에이전트별 재호출 캡 **3** (`AGENT_REINVOCATION_CAP`).
- honored query 1회 = 1 round(리더 answer 호출 + 워커 resume 호출 포함). 워커 resume마다 그 에이전트 `reinvocations` +1.
- 종료 상태: 전부 성공 `completed` / 일부 실패 `completed_with_failures` / 전부 실패·플래닝 실패 `failed` / 취소 `canceled`.
- backend 값은 `TeamAgent.backend`("codex" | "claude")로 분기. 기본 "codex".
- 토큰 스트리밍·workspace 격리·review_only 실구현·재시작 좀비 정리는 **비목표**.
- 테스트 우선(TDD), 잦은 커밋. 커밋 메시지는 한국어 Conventional Commits.

**작업 브랜치:** 구현은 `main`이 아닌 별도 브랜치/worktree에서 진행한다(실행 스킬이 격리 워크스페이스를 만든다).

**테스트 실행:** `.venv/bin/python -m pytest <path> -v` (Windows venv면 `.venv/Scripts/python -m pytest`). 프런트: `cd frontend && npm run test`.

---

## 파일 구조

| 파일 | 책임 | 변경 |
| --- | --- | --- |
| `src/personal_agent_gateway/db.py` | 스키마·마이그레이션 | team_runs/team_agents 컬럼 추가 |
| `src/personal_agent_gateway/teams.py` | 팀 상태기계·저장소 | 필드/상태/서비스 메서드 추가 |
| `src/personal_agent_gateway/run_state.py` | 실행 수명주기 | `TeamRunRegistry` 추가 |
| `src/personal_agent_gateway/config.py` | 설정 | `claude_permission_mode` 추가 |
| `src/personal_agent_gateway/app.py` | 조립·엔드포인트 | 팩토리 backend 분기, 레지스트리 배선 |
| `src/personal_agent_gateway/api/team_runs.py` | 팀런 API | start 논블로킹, cancel 실동작 |
| `src/personal_agent_gateway/team_runtime.py` | 오케스트레이션 | 라운드 루프·부분실패·취소·종합 재작성 |
| `frontend/.../StatusBadge/index.jsx` | 상태 배지 | `completed_with_failures` 라벨 |
| `tests/test_teams.py`, `test_team_runtime.py`, `test_api_team_runs.py`, `test_run_state.py`(신규), `test_config.py` | 테스트 | 신규·갱신 |

---

## Task 1: 데이터 모델 확장 (DB 마이그레이션 + teams.py)

**Files:**
- Modify: `src/personal_agent_gateway/db.py` (SCHEMA_SQL, `_migrate`)
- Modify: `src/personal_agent_gateway/teams.py` (Literal, 두 dataclass, `create_team_run`, `_from_row` 2개, 신규 메서드)
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces:
  - `TeamRunStatus` += `"completed_with_failures"`
  - `TaskStatus` += `"canceled"`
  - `TeamRun` += `rounds_budget: int`, `rounds_used: int`
  - `TeamAgent` += `reinvocations: int`, `upstream_session_id: str | None`
  - `TeamRunService.create_team_run(goal, leader_persona_id, member_persona_ids, run_mode, max_workers, rounds_budget=8)`
  - `TeamRunService.get_agent(agent_id: str) -> TeamAgent`
  - `TeamRunService.set_agent_session(agent_id: str, upstream_session_id: str | None) -> TeamAgent`
  - `TeamRunService.increment_agent_reinvocations(agent_id: str) -> TeamAgent`
  - `TeamRunService.increment_rounds_used(team_run_id: str) -> TeamRun`

- [ ] **Step 1: Write failing tests**

`tests/test_teams.py`에 추가:

```python
def test_new_run_has_default_budget(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    assert run.rounds_budget == 8
    assert run.rounds_used == 0


def test_agent_session_and_counters(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 2)
    agent = teams.list_agents(run.id)[0]
    assert agent.reinvocations == 0
    assert agent.upstream_session_id is None

    updated = teams.set_agent_session(agent.id, "thread-123")
    assert updated.upstream_session_id == "thread-123"
    assert teams.increment_agent_reinvocations(agent.id).reinvocations == 1

    assert teams.increment_rounds_used(run.id).rounds_used == 1


def test_completed_with_failures_is_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)
    updated = teams.set_run_status(run.id, "completed_with_failures", summary="1/2")
    assert updated.status == "completed_with_failures"
    assert updated.finished_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_teams.py -v -k "budget or counters or terminal"`
Expected: FAIL (`AttributeError: ... 'rounds_budget'` 등)

- [ ] **Step 3: Add columns to db.py**

`db.py`의 `SCHEMA_SQL`에서 `create table if not exists team_runs (...)` 안 `max_workers integer not null,` 아래 줄에 추가:

```sql
    rounds_budget integer not null default 8,
    rounds_used integer not null default 0,
```

`create table if not exists team_agents (...)` 안 `current_task_id text,` 아래에 추가:

```sql
    reinvocations integer not null default 0,
    upstream_session_id text,
```

`_migrate` 함수 끝에 추가(기존 DB 백필):

```python
    team_run_columns = {
        row["name"] for row in connection.execute("pragma table_info(team_runs)")
    }
    if "rounds_budget" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_budget integer not null default 8"
        )
    if "rounds_used" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_used integer not null default 0"
        )
    team_agent_columns = {
        row["name"] for row in connection.execute("pragma table_info(team_agents)")
    }
    if "reinvocations" not in team_agent_columns:
        connection.execute(
            "alter table team_agents add column reinvocations integer not null default 0"
        )
    if "upstream_session_id" not in team_agent_columns:
        connection.execute(
            "alter table team_agents add column upstream_session_id text"
        )
```

- [ ] **Step 4: Extend teams.py types and dataclasses**

`TeamRunStatus` Literal에 `"completed_with_failures"` 추가. `TaskStatus` Literal에 `"canceled"` 추가.

`_TERMINAL_RUN_STATUSES` 상수를 다음으로 교체:

```python
_TERMINAL_RUN_STATUSES = {"completed", "completed_with_failures", "failed", "canceled"}
```

`TeamRun` dataclass에 필드 추가(`updated_at: str` 위, 순서 무관하나 아래 권장):

```python
    rounds_budget: int
    rounds_used: int
```

`TeamAgent` dataclass에 필드 추가:

```python
    reinvocations: int
    upstream_session_id: str | None
```

`set_task_status`의 `finished_at` 계산을 canceled 포함으로 교체:

```python
        finished_at = _now() if status in ("completed", "failed", "canceled") else None
```

- [ ] **Step 5: Update create_team_run and row mappers**

`create_team_run` 시그니처에 `rounds_budget: int = 8` 파라미터 추가, insert에 컬럼 반영:

```python
    def create_team_run(
        self,
        goal: str,
        leader_persona_id: str,
        member_persona_ids: list[str],
        run_mode: RunMode,
        max_workers: int,
        rounds_budget: int = 8,
    ) -> TeamRun:
        team_run_id = uuid4().hex
        now = _now()
        workspace_root = str(self._workspace_root / team_run_id)
        self._db.execute(
            """
            insert into team_runs (
                id, goal, status, run_mode, leader_agent_id, max_workers,
                rounds_budget, rounds_used, workspace_root, summary, error_message,
                created_at, started_at, finished_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_run_id, goal, "draft", run_mode, None, max_workers,
                rounds_budget, 0, workspace_root, None, None,
                now, None, None, now,
            ),
        )
        leader_agent = self._create_agent(team_run_id, leader_persona_id, "leader")
        for member_persona_id in member_persona_ids:
            self._create_agent(team_run_id, member_persona_id, "member")
        self._db.execute(
            "update team_runs set leader_agent_id = ?, updated_at = ? where id = ?",
            (leader_agent.id, _now(), team_run_id),
        )
        return self.get_team_run(team_run_id)
```

`_create_agent`의 insert에 신규 컬럼 반영(`current_task_id` 다음에 `reinvocations, upstream_session_id`):

```python
        self._db.execute(
            """
            insert into team_agents (
                id, team_run_id, name, role, persona_id, persona_snapshot_json,
                backend, model, status, workspace_path, current_task_id,
                reinvocations, upstream_session_id,
                started_at, finished_at, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id, team_run_id, persona.name, role, persona.id,
                json.dumps(_persona_snapshot(persona), ensure_ascii=False, sort_keys=True),
                persona.default_backend, persona.default_model, "pending", None, None,
                0, None,
                None, None, now, now,
            ),
        )
```

`_team_run_from_row`에 추가: `rounds_budget=row["rounds_budget"], rounds_used=row["rounds_used"],`
`_team_agent_from_row`에 추가: `reinvocations=row["reinvocations"], upstream_session_id=row["upstream_session_id"],`

- [ ] **Step 6: Add service methods**

`teams.py`의 `TeamRunService`에 추가(기존 `set_agent_status` 근처):

```python
    def get_agent(self, agent_id: str) -> TeamAgent:
        return self._get_agent(agent_id)

    def set_agent_session(self, agent_id: str, upstream_session_id: str | None) -> TeamAgent:
        self._get_agent(agent_id)
        self._db.execute(
            "update team_agents set upstream_session_id = ?, updated_at = ? where id = ?",
            (upstream_session_id, _now(), agent_id),
        )
        return self._get_agent(agent_id)

    def increment_agent_reinvocations(self, agent_id: str) -> TeamAgent:
        self._get_agent(agent_id)
        self._db.execute(
            "update team_agents set reinvocations = reinvocations + 1, updated_at = ? where id = ?",
            (_now(), agent_id),
        )
        return self._get_agent(agent_id)

    def increment_rounds_used(self, team_run_id: str) -> TeamRun:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_runs set rounds_used = rounds_used + 1, updated_at = ? where id = ?",
            (_now(), team_run_id),
        )
        return self.get_team_run(team_run_id)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_teams.py -v`
Expected: PASS (신규 3개 포함 전체 통과)

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/db.py src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat: 팀런 라운드 예산·에이전트 세션 필드와 completed_with_failures 상태 추가"
```

---

## Task 2: TeamRunRegistry (실행 수명주기)

**Files:**
- Modify: `src/personal_agent_gateway/run_state.py`
- Test: `tests/test_run_state.py` (신규)

**Interfaces:**
- Produces: `TeamRunRegistry` with `register(team_run_id: str, task: asyncio.Task) -> None`, `is_running(team_run_id: str) -> bool`, `cancel(team_run_id: str) -> bool`, `finish(team_run_id: str) -> None`

- [ ] **Step 1: Write failing test**

`tests/test_run_state.py` 생성:

```python
import asyncio
import pytest
from personal_agent_gateway.run_state import TeamRunRegistry


@pytest.mark.asyncio
async def test_register_cancel_finish():
    registry = TeamRunRegistry()

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    registry.register("run-1", task)
    assert registry.is_running("run-1") is True

    assert registry.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    registry.finish("run-1")
    assert registry.is_running("run-1") is False
    assert registry.cancel("run-1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_state.py -v`
Expected: FAIL (`ImportError: cannot import name 'TeamRunRegistry'`)

- [ ] **Step 3: Implement TeamRunRegistry**

`run_state.py` 끝에 추가:

```python
class TeamRunRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = Lock()

    def register(self, team_run_id: str, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks[team_run_id] = task

    def is_running(self, team_run_id: str) -> bool:
        with self._lock:
            return team_run_id in self._tasks

    def cancel(self, team_run_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(team_run_id)
        if task is None:
            return False
        task.cancel()
        return True

    def finish(self, team_run_id: str) -> None:
        with self._lock:
            self._tasks.pop(team_run_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_run_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/run_state.py tests/test_run_state.py
git commit -m "feat: 팀런 실행 task 등록·취소용 TeamRunRegistry 추가"
```

---

## Task 3: backend-aware 모델 팩토리 + config

**Files:**
- Modify: `src/personal_agent_gateway/config.py` (`AppConfig` 필드, `load_config`)
- Modify: `src/personal_agent_gateway/app.py` (`_team_model_factory`)
- Test: `tests/test_config.py`, `tests/test_app_team_factory.py` (신규)

**Interfaces:**
- Consumes: `TeamAgent.backend`, `TeamAgent.model`, `TeamAgent.upstream_session_id` (Task 1)
- Produces: `AppConfig.claude_permission_mode: str`; `_team_model_factory(config)` 가 `agent.backend`에 따라 `CodexModelClient`/`ClaudeModelClient` 반환

- [ ] **Step 1: Write failing tests**

`tests/test_app_team_factory.py` 생성:

```python
from pathlib import Path
from personal_agent_gateway.app import _team_model_factory
from personal_agent_gateway.config import load_config
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient
from personal_agent_gateway.teams import TeamAgent


def _agent(backend: str, session: str | None = None) -> TeamAgent:
    return TeamAgent(
        id="a1", team_run_id="r1", name="A", role="member", persona_id="p1",
        persona_snapshot={}, backend=backend, model="default", status="pending",
        workspace_path=None, current_task_id=None, reinvocations=0,
        upstream_session_id=session, created_at="t", updated_at="t",
    )


def test_factory_picks_codex_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    factory = _team_model_factory(load_config())
    assert isinstance(factory(_agent("codex")), CodexModelClient)


def test_factory_picks_claude_when_backend_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    factory = _team_model_factory(load_config())
    assert isinstance(factory(_agent("claude")), ClaudeModelClient)
```

`tests/test_config.py`에 추가:

```python
def test_claude_permission_mode_default(tmp_path, monkeypatch):
    from personal_agent_gateway.config import load_config
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    assert load_config().claude_permission_mode == "acceptEdits"
```

> 참고: `load_config`가 `AGENT_WORKSPACE_ROOT`를 요구하면 위처럼 setenv. 다른 필수 env가 있으면 기존 `tests/test_config.py` 패턴을 따를 것.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_team_factory.py tests/test_config.py -v -k "factory or permission"`
Expected: FAIL (`AttributeError: 'AppConfig' object has no attribute 'claude_permission_mode'` / claude 팩토리가 Codex 반환)

- [ ] **Step 3: Add config field**

`config.py` `AppConfig`에 `claude_binary` 근처 추가:

```python
    claude_permission_mode: str = "acceptEdits"
```

`load_config`의 `AppConfig(...)` 생성에 추가(`claude_binary=...` 다음 줄):

```python
                claude_permission_mode=env.get("AGENT_CLAUDE_PERMISSION_MODE") or "acceptEdits",
```

- [ ] **Step 4: Make factory backend-aware**

`app.py`의 `_team_model_factory`를 교체. 상단 import에 `ClaudeModelClient` 추가(`from personal_agent_gateway.model_client import CodexModelClient` → `ClaudeModelClient, CodexModelClient`):

```python
def _team_model_factory(config: AppConfig) -> Callable[[TeamAgent], ModelClient]:
    def team_model_factory(agent: TeamAgent) -> ModelClient:
        session = agent.upstream_session_id or None
        if agent.backend == "claude":
            return ClaudeModelClient(
                binary=config.claude_binary,
                model=agent.model,
                workspace_root=config.workspace_root,
                effort="high",
                permission_mode=config.claude_permission_mode,
                upstream_session_id=session,
            )
        return CodexModelClient(
            binary=config.codex_binary,
            model=agent.model,
            workspace_root=config.workspace_root,
            sandbox=config.codex_sandbox,
            approval_policy=config.codex_approval_policy,
            effort="high",
            timeout_seconds=config.codex_timeout_seconds,
            upstream_session_id=session,
        )

    return team_model_factory
```

`app.py` 상단 타입 import에 `ModelClient` 추가: `from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient, ModelClient`. 반환 타입 애노테이션도 `Callable[[TeamAgent], ModelClient]`로. `_team_model_factory`가 쓰이는 `TeamRuntime(...)` 생성부는 그대로 동작(팩토리 시그니처 호환).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_team_factory.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/config.py src/personal_agent_gateway/app.py tests/test_app_team_factory.py tests/test_config.py
git commit -m "feat: 팀 실행 모델 팩토리를 backend(codex/claude)별로 분기"
```

---

## Task 4: team_runtime 재구조화 — 부분 실패 격리 + 종료 상태

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (전면 재구조화)
- Test: `tests/test_team_runtime.py` (신규 + 기존 갱신)

**Interfaces:**
- Consumes: Task 1 서비스 메서드, `ModelResponse.upstream_session_id`
- Produces: `TeamRuntime.start` 유지. 내부 helper `_plan`, `_execute`, `_run_task(run, leader, worker, task)`, `_synthesize`, `_settle_canceled`, 모듈 함수 `_terminal_status(tasks) -> str`, `_worker_prompt` (Task 5에서 확장)

> 이 태스크는 순차 워커 루프에 **task별 try/except**를 도입하고 종료 상태를 task 결과로 계산한다. 동적 메시징은 Task 5, 리더 종합 호출은 Task 6에서 추가한다. `_run_task`는 최종 시그니처(`run, leader, worker, task`)로 지금 정의하되 `leader`는 Task 5까지 미사용.

- [ ] **Step 1: Write failing tests**

`tests/test_team_runtime.py`에 스크립트형 FakeModel과 테스트 추가. 파일 상단 `FakeModel` 아래에 추가:

```python
@dataclass
class ScriptedModel:
    """호출마다 responses에서 순서대로 반환. 소진되면 마지막 값 반복."""
    responses: list

    def __post_init__(self):
        self._calls = 0

    async def complete(self, messages):
        idx = min(self._calls, len(self.responses) - 1)
        self._calls += 1
        value = self.responses[idx]
        if isinstance(value, Exception):
            raise value
        return ModelResponse(content=value, tool_calls=[], upstream_session_id=f"sess-{self._calls}")


def _factory_by_role(leader_responses, worker_responses):
    from personal_agent_gateway.teams import TeamAgent
    def factory(agent: TeamAgent):
        if agent.role == "leader":
            return ScriptedModel(list(leader_responses))
        return ScriptedModel(list(worker_responses))
    return factory
```

> 주의: `ScriptedModel`은 에이전트별 인스턴스가 필요하다. `_factory_by_role`는 매 호출 새 인스턴스를 만들어 리더/워커 각각 독립 스크립트를 준다.

테스트 추가:

```python
@pytest.mark.asyncio
async def test_partial_failure_yields_completed_with_failures(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"},{"title":"T2","description":"d2"}]'
    # 워커: T1 성공, T2 예외
    def factory(agent):
        if agent.role == "leader":
            return ScriptedModel([plan, "summary"])
        return ScriptedModel(["ok result", RuntimeError("boom")])

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed_with_failures"
    tasks = teams.list_tasks(run.id)
    assert {t.title: t.status for t in tasks} == {"T1": "completed", "T2": "failed"}


@pytest.mark.asyncio
async def test_all_workers_fail_yields_failed(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    def factory(agent):
        if agent.role == "leader":
            return ScriptedModel([plan, "summary"])
        return ScriptedModel([RuntimeError("boom")])
    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)
    assert result.status == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v -k "partial or all_workers"`
Expected: FAIL (현재 구현은 워커 예외 시 전체 failed, `completed_with_failures` 미존재)

- [ ] **Step 3: Rewrite team_runtime.py core**

`team_runtime.py`의 `TeamRuntime.start`를 아래 구조로 교체하고 helper를 추가한다(기존 `_find_leader/_find_workers/_parse_task_plan`는 유지). 상단에 `AGENT_REINVOCATION_CAP = 3` 추가.

```python
    async def start(self, team_run_id: str) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        leader: TeamAgent | None = None
        try:
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "planning")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.started", "team_run_id": run.id})

            tasks = await self._plan(run, leader)

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                self._teams.set_agent_status(leader.id, "completed")
                run = self._teams.set_run_status(run.id, "completed")
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return run

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run

            await self._execute(run, leader, workers)
            return await self._synthesize(run, leader)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run)
            raise
        except Exception as exc:  # noqa: BLE001
            run = self._teams.set_run_status(run.id, "failed", error_message=str(exc))
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": str(exc)})
            return run

    async def _plan(self, run: TeamRun, leader: TeamAgent) -> list[dict[str, str]]:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        prompt = PLANNING_PROMPT.format(
            goal=run.goal,
            persona_snapshot_json=json.dumps(leader_agent.persona_snapshot, ensure_ascii=False),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            tasks = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            tasks = _parse_task_plan(retry.content)
        for task in tasks:
            created = self._teams.create_task(run.id, task["title"], task["description"])
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": created.id})
        self._teams.append_message(
            run.id, leader.id, None, "plan_note", f"Planning completed with {len(tasks)} tasks.", {}
        )
        return tasks

    async def _execute(self, run: TeamRun, leader: TeamAgent, workers: list[TeamAgent]) -> None:
        tasks = self._teams.list_tasks(run.id)
        for index, task in enumerate(tasks):
            worker = workers[index % len(workers)]
            self._teams.set_task_status(task.id, "in_progress")
            self._teams.set_agent_status(worker.id, "running")
            try:
                result = await self._run_task(run, leader, worker, task)
                self._teams.append_message(
                    run.id, worker.id, None, "agent_output", result, {"task_id": task.id}
                )
                self._teams.set_task_status(task.id, "completed", result=result)
                self._teams.set_agent_status(worker.id, "completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._teams.set_task_status(task.id, "failed", error_message=str(exc))
                self._teams.set_agent_status(worker.id, "failed")
            await self._publish(
                {"type": "team.task.updated", "team_run_id": run.id, "task_id": task.id}
            )

    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> str:
        worker_agent = self._teams.get_agent(worker.id)
        model = self._model_factory(worker_agent)
        response = await model.complete([{"role": "user", "content": self._worker_prompt(run, task)}])
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        return response.content

    def _worker_prompt(self, run: TeamRun, task: TeamTask) -> str:
        worker_snapshot = ""
        return WORKER_PROMPT.format(
            persona_snapshot_json=worker_snapshot,
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )

    async def _synthesize(self, run: TeamRun, leader: TeamAgent) -> TeamRun:
        tasks = self._teams.list_tasks(run.id)
        status = _terminal_status(tasks)
        if status == "failed":
            run = self._teams.set_run_status(run.id, "failed", error_message="All tasks failed")
            self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": "All tasks failed"})
            return run
        run = self._teams.set_run_status(run.id, "summarizing")
        done = sum(1 for t in tasks if t.status == "completed")
        summary = f"{done}/{len(tasks)} tasks completed."
        run = self._teams.set_run_status(run.id, status, summary=summary)
        self._teams.set_agent_status(leader.id, "completed")
        await self._publish({"type": "team.run.completed", "team_run_id": run.id})
        return run

    def _settle_canceled(self, run: TeamRun) -> None:
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "canceled")
        for task in self._teams.list_tasks(run.id):
            if task.status == "in_progress":
                self._teams.set_task_status(task.id, "canceled")
        self._teams.set_run_status(run.id, "canceled")
```

`_worker_prompt`는 워커 페르소나 스냅샷을 넣어야 하므로 `worker` 인자가 필요하다. 시그니처를 `_worker_prompt(self, run, worker, task)`로 하고 `_run_task`에서 `self._worker_prompt(run, worker_agent, task)`로 호출하도록 수정한다:

```python
    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        return WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )
```
(그리고 `_run_task`에서 `self._worker_prompt(run, worker_agent, task)` 호출.)

모듈 함수 추가(파일 하단, `_find_workers` 근처):

```python
def _terminal_status(tasks: list[TeamTask]) -> str:
    if not tasks:
        return "completed"
    statuses = [task.status for task in tasks]
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status == "failed" for status in statuses):
        return "completed_with_failures"
    return "completed"
```

상단 import에 `TeamTask` 추가: `from personal_agent_gateway.teams import TeamAgent, TeamRun, TeamRunService, TeamTask`.

- [ ] **Step 4: Update existing tests that asserted old behavior**

기존 `test_team_runtime.py`의 plan_and_execute 관련 테스트에서 summary 문자열이 `"Completed N tasks."`를 기대하면 `"/ tasks completed."` 형식으로 갱신한다. planning_only/planning_failure 테스트는 그대로 통과해야 한다(“Planning completed” 유지). 실행 후 실패 테스트로 확인.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v`
Expected: PASS (신규 partial/all_workers 포함)

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "refactor: 팀런 실행 루프에 task별 실패 격리와 completed_with_failures 종료 상태 도입"
```

---

## Task 5: 동적 메시징 라운드 (needs_info + 리더 중재 + resume + 예산/캡)

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_run_task` 확장, 신규 helper·프롬프트·파서)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: Task 1 (`increment_rounds_used`, `increment_agent_reinvocations`, `set_agent_session`, `get_agent`), Task 4 (`_run_task`, `_execute`)
- Produces: `_parse_needs_info(content) -> dict | None`, `_mediate`, `_resume_worker`, `_collect_outputs`; 상수 `MEDIATION_PROMPT`, 갱신된 `WORKER_PROMPT`

- [ ] **Step 1: Write failing tests**

`tests/test_team_runtime.py`에 추가:

```python
@pytest.mark.asyncio
async def test_worker_query_consumes_round_and_reinvokes(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'

    def factory(agent):
        if agent.role == "leader":
            # 1) plan  2) mediation answer  3) synthesis
            return ScriptedModel([plan, "use schema X", "summary"])
        # 1) needs_info  2) final result after answer
        return ScriptedModel([
            'Working...\n```json\n{"needs_info":{"topic":"schema","question":"what schema?"}}\n```',
            "final result using schema X",
        ])

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 1
    agent = [a for a in teams.list_agents(run.id) if a.role == "member"][0]
    assert agent.reinvocations == 1
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "query" in kinds and "answer" in kinds


@pytest.mark.asyncio
async def test_budget_exhausted_rejects_and_best_effort(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    # 예산 0으로 생성 → 즉시 거절 경로
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1, rounds_budget=0)
    plan = '[{"title":"T1","description":"d1"}]'
    needs = 'x\n```json\n{"needs_info":{"topic":"t","question":"q"}}\n```'

    def factory(agent):
        if agent.role == "leader":
            return ScriptedModel([plan, "summary"])
        # 1) needs_info  2) best-effort final (거절 후 resume 응답)
        return ScriptedModel([needs, "best effort final"])

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 0
    task = teams.list_tasks(run.id)[0]
    assert task.result == "best effort final"
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "answer" not in kinds  # 중재 없음
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v -k "query or budget"`
Expected: FAIL (needs_info 미파싱 → 요청이 최종 결과로 저장됨)

- [ ] **Step 3: Add prompts and parser**

`team_runtime.py` 상단, 기존 `WORKER_PROMPT`를 아래로 교체하고 `MEDIATION_PROMPT` 추가:

```python
WORKER_PROMPT = """You are an agent in a personal-agent-gateway Team Run.
Persona:
{persona_snapshot_json}
Goal: {goal}
Assigned task: {task_title}
Task description: {task_description}

If you need information from another team member to proceed, end your reply with
ONLY this fenced block and nothing after it:
```json
{{"needs_info": {{"topic": "<short topic>", "question": "<your question>"}}}}
```
Otherwise, return your concise final result: changed files and verification evidence."""

MEDIATION_PROMPT = """You are the leader mediating a Team Run.
Goal: {goal}
A worker on task "{task_title}" asks: {question}

Team outputs so far:
{outputs}

Answer concisely to unblock the worker. If the information is unavailable, say so plainly."""
```

파일 하단에 파서 추가:

```python
def _parse_needs_info(content: str) -> dict[str, str] | None:
    block = _last_json_block(content)
    if block is None:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    req = data.get("needs_info")
    if not isinstance(req, dict):
        return None
    question = req.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    topic = req.get("topic")
    return {"topic": topic if isinstance(topic, str) else "", "question": question.strip()}


def _last_json_block(content: str) -> str | None:
    fence = "```json"
    idx = content.rfind(fence)
    if idx != -1:
        rest = content[idx + len(fence):]
        end = rest.find("```")
        if end != -1:
            return rest[:end].strip()
    # 펜스가 없으면 마지막 중괄호 그룹 시도
    start = content.rfind("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start:end + 1].strip()
    return None
```

- [ ] **Step 4: Extend `_run_task` with the round loop**

Task 4에서 만든 `_run_task`를 교체:

```python
    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> str:
        worker_agent = self._teams.get_agent(worker.id)
        model = self._model_factory(worker_agent)
        response = await model.complete(
            [{"role": "user", "content": self._worker_prompt(run, worker_agent, task)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        content = response.content

        while True:
            req = _parse_needs_info(content)
            if req is None:
                return content
            run = self._teams.get_team_run(run.id)
            worker_agent = self._teams.get_agent(worker.id)
            if run.rounds_used >= run.rounds_budget or worker_agent.reinvocations >= AGENT_REINVOCATION_CAP:
                return await self._resume_worker(
                    worker.id,
                    "No more consultation is available. Produce your best-effort final "
                    "result now, without a needs_info block.",
                )
            self._teams.append_message(
                run.id, worker.id, leader.id, "query", req["question"],
                {"task_id": task.id, "topic": req["topic"]},
            )
            answer = await self._mediate(run, leader, task, req["question"])
            run = self._teams.increment_rounds_used(run.id)
            self._teams.append_message(
                run.id, leader.id, worker.id, "answer", answer, {"round": run.rounds_used}
            )
            content = await self._resume_worker(
                worker.id,
                f"Answer to your question: {answer}\n\nContinue and produce your final "
                "result, or ask again only if essential.",
            )
            self._teams.increment_agent_reinvocations(worker.id)

    async def _resume_worker(self, worker_id: str, instruction: str) -> str:
        worker_agent = self._teams.get_agent(worker_id)
        model = self._model_factory(worker_agent)
        response = await model.complete([{"role": "user", "content": instruction}])
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        return response.content

    async def _mediate(self, run: TeamRun, leader: TeamAgent, task: TeamTask, question: str) -> str:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        prompt = MEDIATION_PROMPT.format(
            goal=run.goal,
            task_title=task.title,
            question=question,
            outputs=self._collect_outputs(run),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        return response.content

    def _collect_outputs(self, run: TeamRun) -> str:
        lines = [
            f"[{task.title}]\n{task.result}"
            for task in self._teams.list_tasks(run.id)
            if task.status == "completed" and task.result
        ]
        return "\n\n".join(lines) if lines else "(no completed task outputs yet)"
```

> 정산: honored query에서만 `increment_rounds_used`가 호출된다(거절 경로는 예산 소진). resume 성공 후 `increment_agent_reinvocations`. 예산/캡 초과 시 중재 없이 best-effort resume만 하고 반환.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v`
Expected: PASS (query/budget 신규 포함 전체)

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 리더 중재 hub-and-spoke 동적 메시징 라운드(예산·캡·resume) 구현"
```

---

## Task 6: 리더 최종 종합 (synthesis)

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_synthesize` 확장, `SYNTHESIS_PROMPT` 추가)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: Task 4 `_synthesize`, Task 1 `get_agent`/`set_agent_session`
- Produces: `_leader_synthesis(run, leader, tasks) -> str`; `synthesis` 메시지 영속화; `run.summary`가 리더 응답

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_synthesis_summary_from_leader(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    def factory(agent):
        if agent.role == "leader":
            return ScriptedModel([plan, "SYNTHESIZED SUMMARY"])
        return ScriptedModel(["result"])
    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)
    assert result.summary == "SYNTHESIZED SUMMARY"
    assert [m.kind for m in teams.list_messages(run.id)].count("synthesis") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v -k synthesis`
Expected: FAIL (summary가 `"1/1 tasks completed."`)

- [ ] **Step 3: Add synthesis call**

`team_runtime.py` 상단에 프롬프트 추가:

```python
SYNTHESIS_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Summarize the outcome for the user.
Goal: {goal}
Task results:
{results}
Write a concise summary of what was accomplished and note any failures."""
```

`_synthesize`에서 요약 생성부를 교체(`summary = f"{done}/..."` 두 줄을 아래로):

```python
        run = self._teams.set_run_status(run.id, "summarizing")
        summary = await self._leader_synthesis(run, leader, tasks)
        run = self._teams.set_run_status(run.id, status, summary=summary)
```

helper 추가:

```python
    async def _leader_synthesis(self, run: TeamRun, leader: TeamAgent, tasks: list[TeamTask]) -> str:
        results = "\n\n".join(
            f"[{task.status}] {task.title}\n{task.result or task.error_message or ''}"
            for task in tasks
        )
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        response = await model.complete(
            [{"role": "user", "content": SYNTHESIS_PROMPT.format(goal=run.goal, results=results)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        self._teams.append_message(run.id, leader.id, None, "synthesis", response.content, {})
        return response.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py -v`
Expected: PASS (전체)

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 리더가 워커 결과를 종합해 run summary를 생성"
```

---

## Task 7: 백그라운드 실행 + 실제 취소 배선

**Files:**
- Modify: `src/personal_agent_gateway/app.py` (`app.state.team_run_registry` 배선)
- Modify: `src/personal_agent_gateway/api/team_runs.py` (start 논블로킹, cancel 실동작)
- Test: `tests/test_api_team_runs.py`, `tests/test_team_runtime.py` (취소)

**Interfaces:**
- Consumes: Task 2 `TeamRunRegistry`, Task 4 `_settle_canceled`/`start`
- Produces: `POST /team-runs/{id}/start` 즉시 반환(백그라운드 task 등록), `POST /team-runs/{id}/cancel` 실행 중이면 취소

- [ ] **Step 1: Write failing tests**

`tests/test_team_runtime.py`에 취소 단위 테스트 추가:

```python
@pytest.mark.asyncio
async def test_cancel_settles_run_and_task(tmp_path):
    import asyncio
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db"); db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    started = asyncio.Event()

    class HangingModel:
        def __init__(self, role): self.role = role
        async def complete(self, messages):
            from personal_agent_gateway.model_client import ModelResponse
            if self.role == "leader":
                return ModelResponse(content=plan, tool_calls=[], upstream_session_id="s")
            started.set()
            await asyncio.sleep(60)  # 워커 실행 중 매달림

    runtime = TeamRuntime(teams=teams, model_factory=lambda a: HangingModel(a.role))
    task = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert teams.get_team_run(run.id).status == "canceled"
    assert teams.list_tasks(run.id)[0].status == "canceled"
```

`tests/test_api_team_runs.py`에 API 테스트 추가(기존 파일의 클라이언트/픽스처 패턴 재사용). 핵심 검증:

```python
def test_start_returns_immediately_without_blocking(client, seeded_personas):
    # 팀런 생성
    created = client.post("/api/team-runs", json={
        "goal": "g", "leader_persona_id": seeded_personas["leader"],
        "member_persona_ids": [seeded_personas["member"]],
        "run_mode": "planning_only", "max_workers": 1,
    }).json()["team_run"]
    resp = client.post(f"/api/team-runs/{created['id']}/start")
    assert resp.status_code == 200
    # 즉시 반환된 payload는 team_run을 포함
    assert resp.json()["team_run"]["id"] == created["id"]


def test_double_start_conflicts(client, seeded_personas):
    created = client.post("/api/team-runs", json={
        "goal": "g", "leader_persona_id": seeded_personas["leader"],
        "member_persona_ids": [], "run_mode": "planning_only", "max_workers": 1,
    }).json()["team_run"]
    client.post(f"/api/team-runs/{created['id']}/start")
    second = client.post(f"/api/team-runs/{created['id']}/start")
    assert second.status_code in (200, 409)  # 이미 끝났으면 finished 409, 실행중이면 409
```

> `test_api_team_runs.py`의 기존 픽스처(`client`, 인증 쿠키, 페르소나 시드)를 따르라. 없으면 기존 테스트가 쓰는 방식대로 앱을 구성하고 `agent_session` 쿠키를 세팅한다. 실제 모델 호출을 피하려면 `create_app`에 주입 가능한 team_runtime을 쓰거나 planning_only + fake를 쓸 것 — 기존 파일 패턴 확인 후 결정.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py::test_cancel_settles_run_and_task tests/test_api_team_runs.py -v`
Expected: FAIL (취소 시 status가 canceled로 안 바뀜 / start가 블로킹)

- [ ] **Step 3: Wire registry in app.py**

`app.py` import에 추가: `from personal_agent_gateway.run_state import SessionAlreadyRunningError, SessionRunRegistry, TeamRunRegistry`.

`create_app` 안, `run_registry = SessionRunRegistry()` 아래에:

```python
    team_run_registry = TeamRunRegistry()
    app.state.team_run_registry = team_run_registry
```

- [ ] **Step 4: Make start non-blocking and cancel real**

`api/team_runs.py` 상단에 `import asyncio` 추가. `start_team_run`/`cancel_team_run` 교체:

```python
_ACTIVE = {"planning", "running", "summarizing"}
_TERMINAL = {"completed", "completed_with_failures", "failed", "canceled"}


@router.post("/{team_run_id}/start")
async def start_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id) or run.status in _ACTIVE:
        raise HTTPException(status_code=409, detail="Team run already running")
    if run.status in _TERMINAL:
        raise HTTPException(status_code=409, detail="Team run already finished")

    async def _run_and_finish() -> None:
        try:
            await runtime.start(team_run_id)
        finally:
            registry.finish(team_run_id)

    task = asyncio.create_task(_run_and_finish())
    registry.register(team_run_id, task)
    return {"team_run": _team_run_payload(run)}


@router.post("/{team_run_id}/cancel")
def cancel_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.cancel(team_run_id):
        return {"team_run": _team_run_payload(service.get_team_run(team_run_id))}
    run = service.set_run_status(team_run_id, "canceled")
    return {"team_run": _team_run_payload(run)}
```

> `_settle_canceled`(Task 4)가 `CancelledError` 처리에서 run/task를 canceled로 만든다. cancel 엔드포인트는 실행 중이면 task.cancel()만 하고 현재 상태를 반환한다(핸들러가 곧 canceled로 전이, SSE로 갱신). 비실행 상태면 즉시 DB canceled.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_team_runtime.py tests/test_api_team_runs.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py tests/test_team_runtime.py
git commit -m "feat: 팀런 백그라운드 실행과 실제 취소(TeamRunRegistry) 배선"
```

---

## Task 8: 프런트엔드 — 상태 배지 + 논블로킹 확인

**Files:**
- Modify: `frontend/src/components/atoms/StatusBadge/index.jsx`
- Test: `frontend/src/components/atoms/StatusBadge/StatusBadge.test.jsx`, `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: 백엔드가 반환하는 `status: "completed_with_failures"`
- Produces: 배지 라벨 `COMPLETED*`

> `handleCreateTeamRun`은 이미 create→start만 하고 SSE로 갱신하므로 로직 변경 불필요(백엔드 start가 즉시 반환하면 자동으로 논블로킹). 배지 라벨만 추가한다.

- [ ] **Step 1: Write failing test**

`StatusBadge.test.jsx`에 추가:

```jsx
it("renders completed_with_failures label", () => {
  render(<StatusBadge kind="completed_with_failures" />);
  expect(screen.getByText("COMPLETED*")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- StatusBadge`
Expected: FAIL (라벨이 기본 "IDLE")

- [ ] **Step 3: Add label**

`StatusBadge/index.jsx`의 `LABELS`에 추가:

```js
  completed_with_failures: "COMPLETED*",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- StatusBadge`
Expected: PASS

- [ ] **Step 5: Run full frontend suite**

Run: `cd frontend && npm run test`
Expected: PASS (TeamRunDetail 등 기존 통과)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/atoms/StatusBadge/index.jsx frontend/src/components/atoms/StatusBadge/StatusBadge.test.jsx
git commit -m "feat: completed_with_failures 상태 배지 라벨 추가"
```

---

## Task 9: 통합 검증

**Files:** 없음(실행/검증만)

- [ ] **Step 1: 전체 백엔드 테스트**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (기존 + 신규 전체)

- [ ] **Step 2: lint**

Run: `.venv/bin/python -m ruff check .`
Expected: 통과(신규 코드 clean)

- [ ] **Step 3: 전체 프런트 테스트 + 빌드**

Run: `cd frontend && npm run test && npm run build`
Expected: PASS, 빌드 성공

- [ ] **Step 4: 수동 스모크 (선택)**

`make dev`로 게이트웨이 기동 → 페르소나 리더+멤버 생성 → plan_and_execute 팀런 생성(자동 시작, **즉시 UI 복귀 확인**) → 상세에서 tasks/messages 실시간 갱신, 실행 중 cancel 클릭 시 `canceled` 전이 확인.

- [ ] **Step 5: 최종 커밋(있으면)**

```bash
git add -A
git commit -m "test: 팀 협업·신뢰성 통합 검증"
```

---

## Self-Review 결과

- **스펙 커버리지**: 라운드 메시징(T5)·예산/캡(T1,T5)·리더 중재(T5)·synthesis(T6)·백그라운드(T7)·취소(T2,T7)·부분실패+completed_with_failures(T4)·backend-aware(T3)·프런트(T8) 모두 태스크 존재. review_only/병렬/격리/스트리밍/좀비정리는 비목표로 명시.
- **Placeholder**: 없음. 모든 코드/명령/기대출력 구체화.
- **타입 일관성**: `_run_task(run, leader, worker, task)` 시그니처가 T4·T5 동일. `_worker_prompt(run, worker, task)` T4에서 확정. 서비스 메서드명(`get_agent`, `set_agent_session`, `increment_agent_reinvocations`, `increment_rounds_used`)이 T1 정의와 T4~T6 사용처 일치. 상태 문자열 `completed_with_failures`/`canceled` 전 구간 일치.
- **주의**: `test_api_team_runs.py`는 기존 픽스처 확인 후 시드/쿠키 방식을 맞출 것. `_worker_prompt`는 T4 Step3 후반 수정 지시(`worker` 인자 포함)를 반드시 반영.
