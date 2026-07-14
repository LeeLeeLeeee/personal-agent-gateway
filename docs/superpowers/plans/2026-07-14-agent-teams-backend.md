# Agent Teams — 백엔드 기반 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 팀(Team) 엔티티와 규칙(Rules)을 도입하고, 실행을 팀에서 시작하며 규칙을 실행 시작 시 스냅샷으로 고정해 런타임 프롬프트에 주입한다. 목록 enrich와 워크스페이스 문서 조회 API를 추가한다.

**Architecture:** 기존 SQLite + 서비스 클래스 + FastAPI 라우터 패턴을 그대로 따른다. `teams`, `rule_sets` 테이블을 추가하고 `team_runs`에 `team_id`, `rules_snapshot_json` 컬럼을 backfill한다. `TeamService`, `RuleSetService`를 신설하고 `TeamRunService`에 팀 기반 생성과 목록 enrich를 추가한다. `team_runtime.py`는 스냅샷된 규칙 텍스트를 프롬프트 블록으로 주입한다(soft). 문서 조회는 `team_runs` 라우터에 추가하며 워크스페이스 경계 검증을 재사용한다.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, sqlite3, pytest.

## Global Constraints

- Python 3.12 문법 사용(`X | None`, `Self`). 새 무거운 의존성 추가 금지.
- DB 스키마 변경은 `SCHEMA_SQL`에 테이블 추가 + `db.py::_migrate`에 additive `alter table` 백필. 기존 행 파괴 금지.
- 서비스는 `Database.execute/fetchone/fetchall`와 `connect()` 트랜잭션 패턴 사용. `_now()`는 `datetime.now(timezone.utc).isoformat()`.
- API 라우터는 `require_session` 쿠키 의존성, `{key: payload}` 응답 형태, `request.app.state.<service>` 접근 규칙 준수.
- 규칙 레벨은 `REQUIRED` 또는 `GUIDELINE`만 허용. 규칙 적용은 프롬프트 주입(soft), 하드 게이트 없음.
- 커밋 메시지는 한국어 Conventional Commits. 각 커밋 끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- 브랜치 `agent-teams-rules`에서 작업.
- 테스트 실행: 저장소 루트에서 `python -m pytest <path> -v`.

---

## 파일 구조

- Create: `src/personal_agent_gateway/rule_sets.py` — `RuleSetService`, `RuleSet` 데이터클래스, 시드.
- Create: `src/personal_agent_gateway/team_directory.py` — `TeamService`, `Team` 데이터클래스(팀 로스터 CRUD). (이름은 실행 서비스 `teams.py`와 구분하기 위해 `team_directory`.)
- Create: `src/personal_agent_gateway/api/teams.py` — 팀 CRUD 라우터.
- Create: `src/personal_agent_gateway/api/rules.py` — 규칙 조회/수정 라우터.
- Create: `tests/test_rule_sets.py`, `tests/test_team_directory.py`, `tests/test_api_teams.py`, `tests/test_api_rules.py`, `tests/test_team_documents.py`.
- Modify: `src/personal_agent_gateway/db.py` — 스키마 + 마이그레이션.
- Modify: `src/personal_agent_gateway/teams.py` — `team_id`/`rules_snapshot`, 팀 기반 생성, 목록 enrich.
- Modify: `src/personal_agent_gateway/team_runtime.py` — 규칙 프롬프트 주입.
- Modify: `src/personal_agent_gateway/api/team_runs.py` — 팀 기반 생성, enrich 응답, 문서 엔드포인트.
- Modify: `src/personal_agent_gateway/app.py` — 서비스 wiring, 시드, 라우터 등록.
- Modify: `tests/test_teams.py`, `tests/test_api_team_runs.py`, `tests/test_team_runtime.py` — 기존 테스트 보강.

---

## Task 1: DB 스키마 + 마이그레이션

**Files:**
- Modify: `src/personal_agent_gateway/db.py` (`SCHEMA_SQL`, `_migrate`)
- Test: `tests/test_db_agent_teams_schema.py` (Create)

**Interfaces:**
- Produces: `teams`, `rule_sets` 테이블. `team_runs.team_id`(text null), `team_runs.rules_snapshot_json`(text null).

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_agent_teams_schema.py`:

```python
from personal_agent_gateway.db import Database


def _columns(db, table):
    with db.connect() as connection:
        return {row["name"] for row in connection.execute(f"pragma table_info({table})")}


def test_new_tables_and_columns_exist(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()

    assert "teams" in _columns.__globals__  # placeholder to force import
    teams_cols = _columns(db, "teams")
    assert {"id", "name", "description", "leader_persona_id", "member_persona_ids_json",
            "created_at", "updated_at"} <= teams_cols

    rule_cols = _columns(db, "rule_sets")
    assert {"id", "scope", "team_id", "personality", "rules_json", "updated_at"} <= rule_cols

    run_cols = _columns(db, "team_runs")
    assert {"team_id", "rules_snapshot_json"} <= run_cols


def test_migration_adds_columns_to_existing_team_runs(tmp_path):
    db = Database(tmp_path / "app.db")
    with db.connect() as connection:
        connection.execute(
            "create table team_runs (id text primary key, goal text not null, status text not null, "
            "run_mode text not null, leader_agent_id text, max_workers integer not null, "
            "workspace_root text not null, summary text, error_message text, created_at text not null, "
            "started_at text, finished_at text, updated_at text not null)"
        )
    db.initialize()
    run_cols = _columns(db, "team_runs")
    assert "team_id" in run_cols
    assert "rules_snapshot_json" in run_cols
```

Remove the placeholder assertion line before finalizing; it only documents intent. Replace it with `assert "teams" in _columns(db, "teams") or True` is wrong — delete that line entirely.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_agent_teams_schema.py -v`
Expected: FAIL (no `teams`/`rule_sets` tables, no new columns).

- [ ] **Step 3: Add tables to `SCHEMA_SQL`**

In `src/personal_agent_gateway/db.py`, append inside the `SCHEMA_SQL` string (before the closing `"""`, after the `session_activity_events` index):

```sql
create table if not exists teams (
    id text primary key,
    name text not null,
    description text not null default '',
    leader_persona_id text not null,
    member_persona_ids_json text not null default '[]',
    created_at text not null,
    updated_at text not null
);

create table if not exists rule_sets (
    id text primary key,
    scope text not null,
    team_id text,
    personality text not null default '',
    rules_json text not null default '[]',
    updated_at text not null,
    unique(scope, team_id)
);
```

Add `team_id`/`rules_snapshot_json` to the `team_runs` create statement as well (for fresh DBs):

```sql
    team_id text,
    rules_snapshot_json text,
```
Insert those two lines right before `created_at text not null,` in the existing `team_runs` block.

- [ ] **Step 4: Add migration backfill**

In `_migrate`, after the existing `team_agent_columns` block, add:

```python
    team_run_columns = {
        row["name"] for row in connection.execute("pragma table_info(team_runs)")
    }
    if "team_id" not in team_run_columns:
        connection.execute("alter table team_runs add column team_id text")
    if "rules_snapshot_json" not in team_run_columns:
        connection.execute("alter table team_runs add column rules_snapshot_json text")
```

Note: a `team_run_columns` variable already exists earlier in `_migrate`; reuse a new name `team_run_columns_v2` to avoid shadowing, or recompute is fine since values differ after earlier alters. Use `team_run_columns_v2`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_db_agent_teams_schema.py -v`
Expected: PASS.

- [ ] **Step 6: Run full DB-adjacent suite to ensure no regressions**

Run: `python -m pytest tests/test_teams.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/db.py tests/test_db_agent_teams_schema.py
git commit -m "feat: teams·rule_sets 테이블과 team_runs 컬럼 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: RuleSetService (규칙 세트 CRUD + 시드)

**Files:**
- Create: `src/personal_agent_gateway/rule_sets.py`
- Test: `tests/test_rule_sets.py`

**Interfaces:**
- Produces:
  - `Rule = dict` with keys `level` (`"REQUIRED"|"GUIDELINE"`), `text` (str).
  - `RuleSet` dataclass: `id, scope, team_id, personality, rules: list[dict], updated_at`.
  - `RuleSetService(db)` methods:
    - `get_global() -> RuleSet`
    - `get_persona_baseline() -> RuleSet`
    - `get_team(team_id) -> RuleSet` (없으면 빈 세트 생성 후 반환)
    - `list_team_rule_sets() -> list[RuleSet]`
    - `upsert(scope, team_id, personality, rules) -> RuleSet`
    - `delete_team(team_id) -> None`
    - `seed_defaults() -> None`
    - `snapshot_for_team(team_id) -> dict` (global + team + persona_baseline 합성)

- [ ] **Step 1: Write the failing test**

Create `tests/test_rule_sets.py`:

```python
import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.rule_sets import RuleSetService


def make_service(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    return RuleSetService(db)


def test_seed_creates_global_and_persona_baseline(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    g = service.get_global()
    pb = service.get_persona_baseline()
    assert g.scope == "global"
    assert g.personality
    assert any(r["level"] == "REQUIRED" for r in g.rules)
    assert pb.scope == "persona_baseline"
    assert pb.rules


def test_seed_is_idempotent(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    service.upsert("global", None, "custom", [{"level": "GUIDELINE", "text": "keep custom"}])
    service.seed_defaults()
    assert service.get_global().personality == "custom"


def test_upsert_validates_level(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.upsert("global", None, "x", [{"level": "MAYBE", "text": "bad"}])


def test_get_team_creates_empty_when_missing(tmp_path):
    service = make_service(tmp_path)
    rs = service.get_team("team-1")
    assert rs.scope == "team"
    assert rs.team_id == "team-1"
    assert rs.rules == []


def test_snapshot_composes_layers(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    service.upsert("team", "team-1", "team voice",
                   [{"level": "REQUIRED", "text": "team rule"}])
    snap = service.snapshot_for_team("team-1")
    assert snap["global"]["personality"]
    assert snap["team"]["personality"] == "team voice"
    assert snap["persona_baseline"]["rules"]
    assert snap["team"]["rules"][0]["text"] == "team rule"


def test_delete_team_removes_rule_set(tmp_path):
    service = make_service(tmp_path)
    service.upsert("team", "team-1", "v", [])
    service.delete_team("team-1")
    assert service.get_team("team-1").rules == []  # recreated empty, not the deleted one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rule_sets.py -v`
Expected: FAIL (`ModuleNotFoundError: rule_sets`).

- [ ] **Step 3: Implement `rule_sets.py`**

Create `src/personal_agent_gateway/rule_sets.py`:

```python
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database

_LEVELS = {"REQUIRED", "GUIDELINE"}

DEFAULT_GLOBAL_PERSONALITY = (
    "팀은 규율 있는 릴리스 크루처럼 움직인다. 사실을 단정적으로 말하고, 위험을 일찍 드러내며, "
    "추측으로 배포하지 않는다. 결정은 기록하고, 이견은 합의 표류가 아니라 리더가 정리한다."
)
DEFAULT_GLOBAL_RULES = [
    {"level": "REQUIRED", "text": "선언된 워크스페이스 루트와 ./data/artifacts 밖에는 쓰지 않는다."},
    {"level": "REQUIRED", "text": "파일 삭제·덮어쓰기 명령은 실행 전에 승인을 받는다."},
    {"level": "REQUIRED", "text": "검증 근거가 붙기 전에는 실행을 완료로 표시하지 않는다."},
    {"level": "REQUIRED", "text": "페르소나 스냅샷은 실행 시작 시 동결되며 실행 중 교체하지 않는다."},
    {"level": "GUIDELINE", "text": "목표를 만족하는 가장 작은 변경을 선호하고, 투기적 작업을 피한다."},
    {"level": "GUIDELINE", "text": "막히면 조용히 재시도하지 말고 한 턴 안에 블로커를 보고한다."},
]
DEFAULT_PERSONA_PERSONALITY = (
    "모든 에이전트는 정밀하고 간결하며 근거 중심이다. 무엇을 할지 먼저 말하고, 건드린 파일이나 "
    "명령을 인용하며, 추측 대신 불확실성을 인정한다."
)
DEFAULT_PERSONA_RULES = [
    {"level": "REQUIRED", "text": "맡은 역할 범위를 지키고, 다른 페르소나의 일은 넘긴다."},
    {"level": "REQUIRED", "text": "모든 보고에 실제 경로·명령·라인을 인용한다."},
    {"level": "REQUIRED", "text": "입력이 모호하면 추측하지 말고 확인 질문을 한다."},
    {"level": "GUIDELINE", "text": "명령이나 diff를 보일 때가 아니면 메시지는 몇 줄로 유지한다."},
    {"level": "GUIDELINE", "text": "위험은 즉시 리더에게 알리고 심각도를 낮추지 않는다."},
]


@dataclass(frozen=True)
class RuleSet:
    id: str
    scope: str
    team_id: str | None
    personality: str
    rules: list[dict]
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_rules(rules: list[dict]) -> list[dict]:
    validated: list[dict] = []
    for rule in rules:
        level = rule.get("level")
        text = rule.get("text")
        if level not in _LEVELS:
            raise ValueError(f"Invalid rule level: {level!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Rule text is required")
        validated.append({"level": level, "text": text})
    return validated


class RuleSetService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def seed_defaults(self) -> None:
        if self._db.fetchone("select id from rule_sets where scope = 'global'") is None:
            self.upsert("global", None, DEFAULT_GLOBAL_PERSONALITY, DEFAULT_GLOBAL_RULES)
        if self._db.fetchone("select id from rule_sets where scope = 'persona_baseline'") is None:
            self.upsert("persona_baseline", None, DEFAULT_PERSONA_PERSONALITY, DEFAULT_PERSONA_RULES)

    def get_global(self) -> RuleSet:
        return self._get_or_empty("global", None)

    def get_persona_baseline(self) -> RuleSet:
        return self._get_or_empty("persona_baseline", None)

    def get_team(self, team_id: str) -> RuleSet:
        return self._get_or_empty("team", team_id)

    def list_team_rule_sets(self) -> list[RuleSet]:
        rows = self._db.fetchall("select * from rule_sets where scope = 'team' order by updated_at desc")
        return [_from_row(row) for row in rows]

    def upsert(self, scope: str, team_id: str | None, personality: str, rules: list[dict]) -> RuleSet:
        validated = _validate_rules(rules)
        now = _now()
        existing = self._db.fetchone(
            "select id from rule_sets where scope = ? and team_id is ?", (scope, team_id)
        )
        if existing is None:
            self._db.execute(
                "insert into rule_sets (id, scope, team_id, personality, rules_json, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (uuid4().hex, scope, team_id, personality,
                 json.dumps(validated, ensure_ascii=False), now),
            )
        else:
            self._db.execute(
                "update rule_sets set personality = ?, rules_json = ?, updated_at = ? where id = ?",
                (personality, json.dumps(validated, ensure_ascii=False), now, existing["id"]),
            )
        return self._get_or_empty(scope, team_id)

    def delete_team(self, team_id: str) -> None:
        self._db.execute("delete from rule_sets where scope = 'team' and team_id = ?", (team_id,))

    def snapshot_for_team(self, team_id: str | None) -> dict:
        team = self.get_team(team_id) if team_id else None
        return {
            "global": _as_dict(self.get_global()),
            "team": _as_dict(team) if team else None,
            "persona_baseline": _as_dict(self.get_persona_baseline()),
        }

    def _get_or_empty(self, scope: str, team_id: str | None) -> RuleSet:
        row = self._db.fetchone(
            "select * from rule_sets where scope = ? and team_id is ?", (scope, team_id)
        )
        if row is None:
            return RuleSet(id="", scope=scope, team_id=team_id, personality="", rules=[], updated_at="")
        return _from_row(row)


def _from_row(row) -> RuleSet:
    return RuleSet(
        id=row["id"],
        scope=row["scope"],
        team_id=row["team_id"],
        personality=row["personality"],
        rules=list(json.loads(row["rules_json"])),
        updated_at=row["updated_at"],
    )


def _as_dict(rule_set: RuleSet) -> dict:
    return {"personality": rule_set.personality, "rules": rule_set.rules}
```

Note: SQLite `team_id is ?` with a Python value binds equality for non-null and IS NULL semantics is NOT automatic — use `team_id is ?` only works for NULL as `is NULL`. For parameter binding, use explicit branching. Replace the `where scope = ? and team_id is ?` queries with a helper:

```python
    def _find_row(self, scope: str, team_id: str | None):
        if team_id is None:
            return self._db.fetchone(
                "select * from rule_sets where scope = ? and team_id is null", (scope,)
            )
        return self._db.fetchone(
            "select * from rule_sets where scope = ? and team_id = ?", (scope, team_id)
        )
```

Use `self._find_row(...)` in `upsert` (existence check) and `_get_or_empty`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_rule_sets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/rule_sets.py tests/test_rule_sets.py
git commit -m "feat: RuleSetService(전역·팀·페르소나 규칙 CRUD와 시드)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: TeamService (팀 로스터 CRUD)

**Files:**
- Create: `src/personal_agent_gateway/team_directory.py`
- Test: `tests/test_team_directory.py`

**Interfaces:**
- Consumes: `PersonaService.get_persona` (존재 검증), `RuleSetService.get_team/delete_team`.
- Produces:
  - `Team` dataclass: `id, name, description, leader_persona_id, member_persona_ids: list[str], created_at, updated_at`.
  - `TeamService(db, personas)` methods: `create_team(name, description, leader_persona_id, member_persona_ids) -> Team`, `get_team(team_id) -> Team`, `list_teams() -> list[Team]`, `update_team(team_id, **fields) -> Team`, `delete_team(team_id) -> None`.
  - `KeyError` for missing team/persona resolution; `ValueError` for invalid roster.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_directory.py`:

```python
import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_directory import TeamService


def make(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    return personas, TeamService(db, personas)


def test_create_and_get_team(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    m1 = personas.create_persona("M1", "eng", "d", [], [])
    team = teams.create_team("Release Crew", "ships", lead.id, [m1.id])
    got = teams.get_team(team.id)
    assert got.name == "Release Crew"
    assert got.leader_persona_id == lead.id
    assert got.member_persona_ids == [m1.id]


def test_create_rejects_unknown_persona(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    with pytest.raises(ValueError):
        teams.create_team("X", "", lead.id, ["nope"])


def test_update_team_roster(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    m1 = personas.create_persona("M1", "eng", "d", [], [])
    m2 = personas.create_persona("M2", "qa", "d", [], [])
    team = teams.create_team("T", "", lead.id, [m1.id])
    updated = teams.update_team(team.id, member_persona_ids=[m1.id, m2.id])
    assert updated.member_persona_ids == [m1.id, m2.id]


def test_delete_team(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    team = teams.create_team("T", "", lead.id, [])
    teams.delete_team(team.id)
    with pytest.raises(KeyError):
        teams.get_team(team.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_team_directory.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `team_directory.py`**

Create `src/personal_agent_gateway/team_directory.py`:

```python
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    description: str
    leader_persona_id: str
    member_persona_ids: list[str]
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamService:
    def __init__(self, db: Database, personas: PersonaService) -> None:
        self._db = db
        self._personas = personas

    def create_team(
        self, name: str, description: str, leader_persona_id: str, member_persona_ids: list[str]
    ) -> Team:
        self._validate_roster(leader_persona_id, member_persona_ids)
        team_id = uuid4().hex
        now = _now()
        self._db.execute(
            "insert into teams (id, name, description, leader_persona_id, member_persona_ids_json, "
            "created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?)",
            (team_id, name, description, leader_persona_id,
             json.dumps(member_persona_ids, ensure_ascii=False), now, now),
        )
        return self.get_team(team_id)

    def get_team(self, team_id: str) -> Team:
        row = self._db.fetchone("select * from teams where id = ?", (team_id,))
        if row is None:
            raise KeyError(f"Team not found: {team_id}")
        return _from_row(row)

    def list_teams(self) -> list[Team]:
        return [_from_row(row) for row in self._db.fetchall("select * from teams order by created_at asc")]

    def update_team(
        self,
        team_id: str,
        name: str | None = None,
        description: str | None = None,
        leader_persona_id: str | None = None,
        member_persona_ids: list[str] | None = None,
    ) -> Team:
        current = self.get_team(team_id)
        next_leader = leader_persona_id if leader_persona_id is not None else current.leader_persona_id
        next_members = member_persona_ids if member_persona_ids is not None else current.member_persona_ids
        self._validate_roster(next_leader, next_members)
        self._db.execute(
            "update teams set name = ?, description = ?, leader_persona_id = ?, "
            "member_persona_ids_json = ?, updated_at = ? where id = ?",
            (
                name if name is not None else current.name,
                description if description is not None else current.description,
                next_leader,
                json.dumps(next_members, ensure_ascii=False),
                _now(),
                team_id,
            ),
        )
        return self.get_team(team_id)

    def delete_team(self, team_id: str) -> None:
        self.get_team(team_id)
        self._db.execute("delete from teams where id = ?", (team_id,))

    def _validate_roster(self, leader_persona_id: str, member_persona_ids: list[str]) -> None:
        for persona_id in [leader_persona_id, *member_persona_ids]:
            try:
                self._personas.get_persona(persona_id)
            except KeyError as exc:
                raise ValueError(f"Unknown persona: {persona_id}") from exc


def _from_row(row) -> Team:
    return Team(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        leader_persona_id=row["leader_persona_id"],
        member_persona_ids=list(json.loads(row["member_persona_ids_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_team_directory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_directory.py tests/test_team_directory.py
git commit -m "feat: TeamService(팀 로스터 CRUD)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 팀 기반 실행 생성 + 규칙 스냅샷

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (`TeamRun` 데이터클래스, `_team_run_from_row`, `_team_run_payload` 대상은 API. 여기선 서비스)
- Test: `tests/test_teams.py` (append)

**Interfaces:**
- Consumes: `TeamService.get_team`, `RuleSetService.snapshot_for_team`.
- Produces:
  - `TeamRun` dataclass gains `team_id: str | None`, `rules_snapshot: dict | None`.
  - `TeamRunService.create_team_run_from_team(team_service, rule_set_service, team_id, goal, run_mode, max_workers, rounds_budget=8) -> TeamRun`.

Rationale: 기존 `create_team_run(leader_persona_id, member_persona_ids, ...)`는 내부 빌딩블록/레거시 테스트용으로 유지한다. 새 메서드는 팀 로스터를 읽어 기존 메서드를 호출하고 `team_id`/`rules_snapshot`을 채운다.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_teams.py`:

```python
from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.team_directory import TeamService


def test_create_team_run_from_team_snapshots_roster_and_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    from personal_agent_gateway.db import Database  # already imported at top; keep local clarity
    # reuse same db as make_services by rebuilding services on that db:
    db = Database(tmp_path / "app.db")
    directory = TeamService(db, personas)
    rules = RuleSetService(db)
    rules.seed_defaults()
    lead = personas.create_persona("Lead", "lead", "d", ["plan"], ["scoped"])
    member = personas.create_persona("QA", "qa", "d", ["test"], ["evidence"])
    rules.upsert("team", None, "", [])  # noop guard
    team = directory.create_team("Release Crew", "ships", lead.id, [member.id])
    rules.upsert("team", team.id, "team voice", [{"level": "REQUIRED", "text": "green regression"}])

    run = teams.create_team_run_from_team(
        directory, rules, team_id=team.id, goal="ship pdf",
        run_mode="plan_and_execute", max_workers=2,
    )

    assert run.team_id == team.id
    assert run.rules_snapshot["team"]["personality"] == "team voice"
    assert run.rules_snapshot["global"]["rules"]
    agents = teams.list_agents(run.id)
    assert [a.role for a in agents] == ["leader", "member"]
    assert agents[0].persona_snapshot["name"] == "Lead"


def test_legacy_create_team_run_has_no_team_or_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run("legacy", lead.id, [], "planning_only", 1)
    assert run.team_id is None
    assert run.rules_snapshot is None
```

Note: `make_services` builds its own `TeamRunService` against `tmp_path/"app.db"`. Build `TeamService`/`RuleSetService` against the same `Database(tmp_path/"app.db")` so they share tables. The snippet above does that.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teams.py::test_create_team_run_from_team_snapshots_roster_and_rules -v`
Expected: FAIL (`AttributeError: create_team_run_from_team` / `TeamRun` has no `team_id`).

- [ ] **Step 3: Extend `TeamRun` + row mapping**

In `src/personal_agent_gateway/teams.py`:

Add fields to `TeamRun` dataclass (after `updated_at`):

```python
    team_id: str | None = None
    rules_snapshot: dict | None = None
```

Update `_team_run_from_row` to read them:

```python
        team_id=row["team_id"] if "team_id" in row.keys() else None,
        rules_snapshot=(
            json.loads(row["rules_snapshot_json"])
            if "rules_snapshot_json" in row.keys() and row["rules_snapshot_json"]
            else None
        ),
```

Add these two keyword args to the `TeamRun(...)` construction in `_team_run_from_row`.

- [ ] **Step 4: Persist columns in `create_team_run`**

In `create_team_run`, extend the `insert into team_runs (...)` column list and values to include `team_id` and `rules_snapshot_json` as `None` by default. Change the insert to:

```python
        self._db.execute(
            """
            insert into team_runs (
                id, goal, status, run_mode, leader_agent_id, max_workers,
                rounds_budget, rounds_used, workspace_root, summary, error_message,
                created_at, started_at, finished_at, updated_at, team_id, rules_snapshot_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_run_id, goal, "draft", run_mode, None, max_workers,
                rounds_budget, 0, workspace_root, None, None,
                now, None, None, now, team_id, rules_snapshot_json,
            ),
        )
```

Add optional params to `create_team_run` signature:

```python
    def create_team_run(
        self,
        goal: str,
        leader_persona_id: str,
        member_persona_ids: list[str],
        run_mode: RunMode,
        max_workers: int,
        rounds_budget: int = 8,
        team_id: str | None = None,
        rules_snapshot_json: str | None = None,
    ) -> TeamRun:
```

- [ ] **Step 5: Add `create_team_run_from_team`**

Add method to `TeamRunService` (import `json` already present):

```python
    def create_team_run_from_team(
        self,
        team_service,
        rule_set_service,
        team_id: str,
        goal: str,
        run_mode: RunMode,
        max_workers: int,
        rounds_budget: int = 8,
    ) -> TeamRun:
        team = team_service.get_team(team_id)
        snapshot = rule_set_service.snapshot_for_team(team_id)
        return self.create_team_run(
            goal=goal,
            leader_persona_id=team.leader_persona_id,
            member_persona_ids=list(team.member_persona_ids),
            run_mode=run_mode,
            max_workers=max_workers,
            rounds_budget=rounds_budget,
            team_id=team_id,
            rules_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_teams.py -v`
Expected: PASS (both new tests + existing).

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat: 팀 기반 실행 생성과 규칙 스냅샷 고정

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 런타임 규칙 프롬프트 주입

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Test: `tests/test_team_runtime.py` (append)

**Interfaces:**
- Consumes: `run.rules_snapshot` (dict | None).
- Produces: `_rules_block(snapshot, include_persona_baseline: bool) -> str` module function; PLANNING/WORKER prompts prepend the block when snapshot present.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_team_runtime.py`:

```python
from personal_agent_gateway.team_runtime import _rules_block


def test_rules_block_empty_when_no_snapshot():
    assert _rules_block(None, include_persona_baseline=True) == ""


def test_rules_block_marks_required_and_guideline():
    snapshot = {
        "global": {"personality": "global voice",
                   "rules": [{"level": "REQUIRED", "text": "no destructive writes"}]},
        "team": {"personality": "team voice",
                 "rules": [{"level": "GUIDELINE", "text": "prefer CRF"}]},
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=True)
    assert "global voice" in block
    assert "team voice" in block
    assert "persona voice" in block
    assert "MUST: no destructive writes" in block
    assert "SHOULD: prefer CRF" in block
    assert "MUST: cite paths" in block


def test_rules_block_excludes_persona_baseline_for_leader():
    snapshot = {
        "global": {"personality": "", "rules": []},
        "team": None,
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=False)
    assert "persona voice" not in block
    assert "cite paths" not in block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_team_runtime.py::test_rules_block_marks_required_and_guideline -v`
Expected: FAIL (`ImportError: _rules_block`).

- [ ] **Step 3: Implement `_rules_block` and wire into prompts**

In `src/personal_agent_gateway/team_runtime.py`, add module-level function:

```python
def _rules_block(snapshot: dict | None, include_persona_baseline: bool) -> str:
    if not snapshot:
        return ""
    sections: list[tuple[str, dict | None]] = [
        ("GLOBAL RULES", snapshot.get("global")),
        ("TEAM RULES", snapshot.get("team")),
    ]
    if include_persona_baseline:
        sections.append(("PERSONA BASELINE", snapshot.get("persona_baseline")))
    lines: list[str] = []
    for title, section in sections:
        if not section:
            continue
        personality = (section.get("personality") or "").strip()
        rules = section.get("rules") or []
        if not personality and not rules:
            continue
        lines.append(f"[{title}]")
        if personality:
            lines.append(personality)
        for rule in rules:
            prefix = "MUST" if rule.get("level") == "REQUIRED" else "SHOULD"
            lines.append(f"- {prefix}: {rule.get('text', '')}")
        lines.append("")
    if not lines:
        return ""
    return "TEAM CHARTER (frozen at run start):\n" + "\n".join(lines).strip() + "\n\n"
```

In `_plan`, prepend the block (leader → no persona baseline). Change:

```python
        prompt = _rules_block(run.rules_snapshot, include_persona_baseline=False) + PLANNING_PROMPT.format(
            goal=run.goal,
            persona_snapshot_json=json.dumps(leader_agent.persona_snapshot, ensure_ascii=False),
        )
```

In `_worker_prompt`, prepend the block (worker → include persona baseline):

```python
    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        return _rules_block(run.rules_snapshot, include_persona_baseline=True) + WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )
```

(Leave SYNTHESIS/MEDIATION/ADD_WORK unchanged for this iteration; they operate on already-constrained sessions. Injection at plan+worker covers the run.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_team_runtime.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 실행 시작 시 동결된 규칙을 리더·워커 프롬프트에 주입

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 목록 enrich (리더·멤버·Task 카운트·elapsed)

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (`list_team_runs_enriched`)
- Modify: `src/personal_agent_gateway/api/team_runs.py` (`list_team_runs` 응답)
- Test: `tests/test_teams.py` (append), `tests/test_api_team_runs.py` (append)

**Interfaces:**
- Produces: `TeamRunService.list_team_runs_enriched() -> list[dict]` — 각 항목은 `_team_run_payload` 필드 + `leader_name`, `members` (`[{name, avatar, initials}]`), `task_counts` (dict), `task_done`, `task_total`, `elapsed_seconds`.

- [ ] **Step 1: Write the failing service test**

Append to `tests/test_teams.py`:

```python
def test_list_team_runs_enriched(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [], avatar="a01")
    member = personas.create_persona("Frontend Dev", "fe", "d", [], [], avatar="a05")
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 2)
    t1 = teams.create_task(run.id, "t1", "d")
    teams.create_task(run.id, "t2", "d")
    teams.set_task_status(t1.id, "completed", result="ok")

    enriched = teams.list_team_runs_enriched()
    row = next(r for r in enriched if r["id"] == run.id)
    assert row["leader_name"] == "Lead"
    assert {m["name"] for m in row["members"]} == {"Frontend Dev"}
    assert row["members"][0]["avatar"] == "a05"
    assert row["members"][0]["initials"] == "FD"
    assert row["task_total"] == 2
    assert row["task_done"] == 1
    assert row["task_counts"]["completed"] == 1
    assert isinstance(row["elapsed_seconds"], (int, float))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teams.py::test_list_team_runs_enriched -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement `list_team_runs_enriched`**

In `src/personal_agent_gateway/teams.py`, add helper + method. Add module function near `_now`:

```python
def _initials(name: str) -> str:
    parts = (name or "").strip().split()
    if not parts:
        return "?"
    letters = [word[0] for word in parts[:2]]
    return "".join(letters).upper()


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float:
    if not started_at:
        return 0.0
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
    return max(0.0, (end - start).total_seconds())
```

Add method to `TeamRunService`:

```python
    def list_team_runs_enriched(self) -> list[dict[str, object]]:
        runs = self.list_team_runs()
        result: list[dict[str, object]] = []
        for run in runs:
            agents = self.list_agents(run.id)
            tasks = self.list_tasks(run.id)
            leader = next((a for a in agents if a.role == "leader"), None)
            members = [a for a in agents if a.role != "leader"]
            counts: dict[str, int] = {}
            for task in tasks:
                counts[task.status] = counts.get(task.status, 0) + 1
            result.append(
                {
                    "id": run.id,
                    "goal": run.goal,
                    "status": run.status,
                    "run_mode": run.run_mode,
                    "max_workers": run.max_workers,
                    "team_id": run.team_id,
                    "created_at": run.created_at,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "updated_at": run.updated_at,
                    "leader_name": leader.name if leader else None,
                    "members": [
                        {
                            "name": agent.name,
                            "avatar": agent.persona_snapshot.get("avatar", ""),
                            "initials": _initials(agent.name),
                        }
                        for agent in members
                    ],
                    "task_counts": counts,
                    "task_total": len(tasks),
                    "task_done": counts.get("completed", 0),
                    "elapsed_seconds": _elapsed_seconds(run.started_at, run.finished_at),
                }
            )
        return result
```

- [ ] **Step 4: Run service test to verify pass**

Run: `python -m pytest tests/test_teams.py::test_list_team_runs_enriched -v`
Expected: PASS.

- [ ] **Step 5: Switch API list endpoint to enriched payload**

In `src/personal_agent_gateway/api/team_runs.py`, change `list_team_runs`:

```python
@router.get("")
def list_team_runs(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {"team_runs": request.app.state.team_run_service.list_team_runs_enriched()}
```

- [ ] **Step 6: Write/adjust API test**

Append to `tests/test_api_team_runs.py` a test asserting the list response includes `leader_name`, `members`, `task_counts`, `elapsed_seconds`. Follow the existing fixture/client pattern in that file (inspect the top of the file for the authenticated client fixture and reuse it). Example body:

```python
def test_list_team_runs_returns_enriched_fields(client_and_ids):
    client, run_id = client_and_ids  # adapt to the file's actual fixture
    body = client.get("/api/team-runs").json()
    run = next(r for r in body["team_runs"] if r["id"] == run_id)
    assert "leader_name" in run
    assert "members" in run
    assert "task_counts" in run
    assert "elapsed_seconds" in run
```

If the file has no reusable fixture, mirror the arrangement of an existing test in `tests/test_api_team_runs.py` (create app via `create_app`, authenticate, create a run) — read that file first and copy its exact setup rather than inventing one.

- [ ] **Step 7: Run API tests**

Run: `python -m pytest tests/test_api_team_runs.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/api/team_runs.py tests/test_teams.py tests/test_api_team_runs.py
git commit -m "feat: 팀 실행 목록 enrich(리더·멤버·Task 카운트·elapsed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 워크스페이스 문서 조회 API

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` (documents endpoints + helpers)
- Test: `tests/test_team_documents.py` (Create)

**Interfaces:**
- Produces:
  - `GET /api/team-runs/{id}/documents` → `{"documents": [{"path", "size", "modified_at", "kind", "previewable"}]}`
  - `GET /api/team-runs/{id}/documents/content?path=` → `{"path", "kind", "content", "previewable", "reason"?}` or `400` on traversal.
- `kind`: `md` / `json` / `text` / `code` / `binary` by extension.
- `previewable`: `False` when binary or `size > 1_000_000`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_documents.py`. Read `tests/test_api_team_runs.py` first for the exact authenticated-client + run-creation pattern, then reuse it. Skeleton (adapt fixture to the file's convention):

```python
from pathlib import Path

# Reuse the same app/client/run creation helper style as tests/test_api_team_runs.py.

def test_documents_list_and_content(authed_client_with_run):
    client, run, workspace = authed_client_with_run
    (Path(workspace) / "notes.md").write_text("# Title\nhello", encoding="utf-8")
    (Path(workspace) / "data.json").write_text('{"a":1}', encoding="utf-8")

    listing = client.get(f"/api/team-runs/{run['id']}/documents").json()["documents"]
    paths = {d["path"] for d in listing}
    assert "notes.md" in paths and "data.json" in paths
    md = next(d for d in listing if d["path"] == "notes.md")
    assert md["kind"] == "md" and md["previewable"] is True

    content = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "notes.md"}
    ).json()
    assert content["content"] == "# Title\nhello"
    assert content["kind"] == "md"


def test_documents_content_rejects_traversal(authed_client_with_run):
    client, run, _ = authed_client_with_run
    resp = client.get(
        f"/api/team-runs/{run['id']}/documents/content", params={"path": "../../etc/passwd"}
    )
    assert resp.status_code == 400
```

`authed_client_with_run` must build the app with a known `workspace_root`, authenticate, create a run, and return `(client, run_dict, run.workspace_root)`. Copy the app/auth setup from `tests/test_api_team_runs.py` verbatim and add run-workspace directory creation.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_team_documents.py -v`
Expected: FAIL (404 — endpoints missing).

- [ ] **Step 3: Implement helpers + endpoints**

In `src/personal_agent_gateway/api/team_runs.py`, add imports at top:

```python
from pathlib import Path
```

Add extension maps and helpers near the bottom (before `_team_run_payload`):

```python
_TEXT_EXTS = {".txt", ".log", ".csv", ".yaml", ".yml", ".toml", ".ini", ".env"}
_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh", ".sql"}
_MAX_PREVIEW_BYTES = 1_000_000


def _doc_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
    if suffix in _TEXT_EXTS:
        return "text"
    if suffix in _CODE_EXTS:
        return "code"
    return "binary"


def _resolved_workspace(run) -> Path:
    return Path(run.workspace_root).resolve()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes workspace")
    return candidate
```

Add endpoints (after `list_team_messages`):

```python
@router.get("/{team_run_id}/documents")
def list_team_documents(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    documents: list[dict[str, object]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            kind = _doc_kind(path)
            size = path.stat().st_size
            documents.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": size,
                    "modified_at": _iso_mtime(path),
                    "kind": kind,
                    "previewable": kind != "binary" and size <= _MAX_PREVIEW_BYTES,
                }
            )
    return {"documents": documents}


@router.get("/{team_run_id}/documents/content")
def read_team_document(request: Request, team_run_id: str, path: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    try:
        target = _safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    kind = _doc_kind(target)
    size = target.stat().st_size
    if kind == "binary" or size > _MAX_PREVIEW_BYTES:
        return {"path": path, "kind": kind, "content": None, "previewable": False,
                "reason": "binary or too large"}
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": path, "kind": "binary", "content": None, "previewable": False,
                "reason": "not utf-8 text"}
    return {"path": path, "kind": kind, "content": content, "previewable": True}
```

Add `_iso_mtime` helper:

```python
from datetime import datetime, timezone


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_team_documents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_team_documents.py
git commit -m "feat: 팀 워크스페이스 문서 목록·내용 조회 API(경로 이탈 차단)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 라우터 신설 · 앱 wiring · 팀 기반 실행 생성 API

**Files:**
- Create: `src/personal_agent_gateway/api/teams.py`, `src/personal_agent_gateway/api/rules.py`
- Modify: `src/personal_agent_gateway/app.py` (services, seed, routers, team runtime deps)
- Modify: `src/personal_agent_gateway/api/team_runs.py` (`CreateTeamRunRequest`, create endpoint)
- Test: `tests/test_api_teams.py`, `tests/test_api_rules.py` (Create), `tests/test_api_team_runs.py` (adjust create test)

**Interfaces:**
- Consumes: `app.state.team_directory_service` (TeamService), `app.state.rule_set_service` (RuleSetService).
- Produces routes:
  - `GET/POST /api/teams`, `GET/PUT/DELETE /api/teams/{id}`
  - `GET /api/rules`, `PUT /api/rules/global`, `PUT /api/rules/persona-baseline`, `PUT /api/teams/{id}/rules`
  - `POST /api/team-runs` now `{team_id, goal, run_mode, max_workers}`.

- [ ] **Step 1: Wire services + seed in `app.py`**

In `src/personal_agent_gateway/app.py`, near persona/team_run service creation (~line 416):

```python
    from personal_agent_gateway.team_directory import TeamService
    from personal_agent_gateway.rule_sets import RuleSetService
    persona_service = PersonaService(db)
    team_run_service = TeamRunService(db, persona_service, config.workspace_root)
    team_directory_service = TeamService(db, persona_service)
    rule_set_service = RuleSetService(db)
    rule_set_service.seed_defaults()
```

Register on `app.state` (near line 452):

```python
    app.state.team_run_service = team_run_service
    app.state.team_directory_service = team_directory_service
    app.state.rule_set_service = rule_set_service
```

Register routers (near line 152, after `team_runs_router`):

```python
    from personal_agent_gateway.api.teams import router as teams_router
    from personal_agent_gateway.api.rules import router as rules_router
    app.include_router(teams_router)
    app.include_router(rules_router)
```

(Match the existing import style — top-of-file imports are preferred; if the file imports routers at top, add there instead and only call `include_router` here. Read the import block ~line 30-50 and follow it.)

- [ ] **Step 2: Write failing API tests for teams + rules**

Create `tests/test_api_teams.py` and `tests/test_api_rules.py`. Reuse the authenticated-client setup from `tests/test_api_team_runs.py` (read it first). Core assertions:

`tests/test_api_teams.py`:

```python
def test_team_crud(authed_client_with_personas):
    client, lead_id, member_id = authed_client_with_personas
    created = client.post("/api/teams", json={
        "name": "Release Crew", "description": "ships",
        "leader_persona_id": lead_id, "member_persona_ids": [member_id],
    }).json()["team"]
    assert created["name"] == "Release Crew"

    listed = client.get("/api/teams").json()["teams"]
    assert any(t["id"] == created["id"] for t in listed)

    updated = client.put(f"/api/teams/{created['id']}", json={
        "name": "Release", "member_persona_ids": [],
    }).json()["team"]
    assert updated["name"] == "Release" and updated["member_persona_ids"] == []

    assert client.delete(f"/api/teams/{created['id']}").json()["deleted"] is True


def test_create_team_rejects_unknown_persona(authed_client_with_personas):
    client, lead_id, _ = authed_client_with_personas
    resp = client.post("/api/teams", json={
        "name": "X", "description": "", "leader_persona_id": lead_id,
        "member_persona_ids": ["nope"],
    })
    assert resp.status_code == 400
```

`tests/test_api_rules.py`:

```python
def test_get_and_put_global_rules(authed_client):
    client = authed_client
    body = client.get("/api/rules").json()
    assert "global" in body and "persona_baseline" in body and "teams" in body

    updated = client.put("/api/rules/global", json={
        "personality": "new voice",
        "rules": [{"level": "REQUIRED", "text": "no destructive writes"}],
    }).json()["rule_set"]
    assert updated["personality"] == "new voice"
    assert updated["rules"][0]["level"] == "REQUIRED"


def test_put_rules_rejects_bad_level(authed_client):
    resp = authed_client.put("/api/rules/global", json={
        "personality": "x", "rules": [{"level": "NOPE", "text": "bad"}],
    })
    assert resp.status_code == 400
```

Provide the `authed_client*` fixtures by copying `tests/test_api_team_runs.py` setup (create_app + auth). Create personas via `POST /api/personas` inside `authed_client_with_personas`.

- [ ] **Step 3: Run to verify fail**

Run: `python -m pytest tests/test_api_teams.py tests/test_api_rules.py -v`
Expected: FAIL (routes 404 / modules missing).

- [ ] **Step 4: Implement `api/teams.py`**

Create `src/personal_agent_gateway/api/teams.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.team_directory import Team

router = APIRouter(prefix="/api/teams", tags=["teams"])


class TeamRequest(BaseModel):
    name: str
    description: str = ""
    leader_persona_id: str
    member_persona_ids: list[str] = []


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    leader_persona_id: str | None = None
    member_persona_ids: list[str] | None = None


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_teams(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    service = request.app.state.team_directory_service
    personas = request.app.state.persona_service
    return {"teams": [_team_payload(team, personas) for team in service.list_teams()]}


@router.post("")
def create_team(request: Request, payload: TeamRequest, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        team = service.create_team(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.get("/{team_id}")
def get_team(request: Request, team_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        team = request.app.state.team_directory_service.get_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.put("/{team_id}")
def update_team(request: Request, team_id: str, payload: TeamUpdateRequest, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        team = service.update_team(
            team_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": _team_payload(team, request.app.state.persona_service)}


@router.delete("/{team_id}")
def delete_team(request: Request, team_id: str, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.team_directory_service
    try:
        service.delete_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    request.app.state.rule_set_service.delete_team(team_id)
    return {"deleted": True}


def _persona_summary(personas, persona_id: str) -> dict[str, object]:
    try:
        persona = personas.get_persona(persona_id)
    except KeyError:
        return {"id": persona_id, "name": persona_id, "role": "", "avatar": ""}
    return {"id": persona.id, "name": persona.name, "role": persona.role, "avatar": persona.avatar}


def _team_payload(team: Team, personas) -> dict[str, object]:
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "leader_persona_id": team.leader_persona_id,
        "member_persona_ids": team.member_persona_ids,
        "leader": _persona_summary(personas, team.leader_persona_id),
        "members": [_persona_summary(personas, pid) for pid in team.member_persona_ids],
        "created_at": team.created_at,
        "updated_at": team.updated_at,
    }
```

- [ ] **Step 5: Implement `api/rules.py`**

Create `src/personal_agent_gateway/api/rules.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.rule_sets import RuleSet

router = APIRouter(tags=["rules"])


class RuleItem(BaseModel):
    level: str
    text: str


class RuleSetRequest(BaseModel):
    personality: str = ""
    rules: list[RuleItem] = []


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("/api/rules")
def get_rules(request: Request, _session: None = session_dependency) -> dict[str, object]:
    service = request.app.state.rule_set_service
    return {
        "global": _payload(service.get_global()),
        "persona_baseline": _payload(service.get_persona_baseline()),
        "teams": [_payload(rs) for rs in service.list_team_rule_sets()],
    }


@router.put("/api/rules/global")
def put_global(request: Request, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "global", None, payload)


@router.put("/api/rules/persona-baseline")
def put_persona_baseline(request: Request, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "persona_baseline", None, payload)


@router.put("/api/teams/{team_id}/rules")
def put_team_rules(request: Request, team_id: str, payload: RuleSetRequest, _session: None = session_dependency) -> dict[str, object]:
    return _upsert(request, "team", team_id, payload)


def _upsert(request: Request, scope: str, team_id: str | None, payload: RuleSetRequest) -> dict[str, object]:
    service = request.app.state.rule_set_service
    try:
        rule_set = service.upsert(
            scope, team_id, payload.personality,
            [rule.model_dump() for rule in payload.rules],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rule_set": _payload(rule_set)}


def _payload(rule_set: RuleSet) -> dict[str, object]:
    return {
        "scope": rule_set.scope,
        "team_id": rule_set.team_id,
        "personality": rule_set.personality,
        "rules": rule_set.rules,
        "updated_at": rule_set.updated_at,
    }
```

Note: `PUT /api/teams/{id}/rules` lives in the rules router (no `/api/teams` prefix on this router) so it coexists with the teams router. FastAPI matches by full path; both routers can define paths under `/api/teams/...` without conflict.

- [ ] **Step 6: Switch team-runs create endpoint to team-based**

In `src/personal_agent_gateway/api/team_runs.py`, replace `CreateTeamRunRequest` and `create_team_run`:

```python
class CreateTeamRunRequest(BaseModel):
    team_id: str
    goal: str
    run_mode: Literal["planning_only", "plan_and_execute", "review_only"] = "planning_only"
    max_workers: int = 3


@router.post("")
def create_team_run(
    request: Request, payload: CreateTeamRunRequest, _session: None = session_dependency
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.create_team_run_from_team(
            request.app.state.team_directory_service,
            request.app.state.rule_set_service,
            team_id=payload.team_id,
            goal=payload.goal,
            run_mode=payload.run_mode,
            max_workers=payload.max_workers,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    return {"team_run": _team_run_payload(run)}
```

Add `team_id`/`rules_snapshot` to `_team_run_payload` (so detail view can show them):

```python
        "team_id": run.team_id,
        "rules_snapshot": run.rules_snapshot,
```

- [ ] **Step 7: Adjust existing create test**

In `tests/test_api_team_runs.py`, the existing create-run test posts `leader_persona_id`/`member_persona_ids`. Update it to first create a team (`POST /api/teams`) then `POST /api/team-runs` with `{team_id, goal, run_mode, max_workers}`. Read the current test, then rewrite its request body accordingly; keep the rest of the assertions (status 200, run fields) intact.

- [ ] **Step 8: Run the full API suite**

Run: `python -m pytest tests/test_api_teams.py tests/test_api_rules.py tests/test_api_team_runs.py -v`
Expected: PASS.

- [ ] **Step 9: Run entire backend suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 10: Commit**

```bash
git add src/personal_agent_gateway/api/teams.py src/personal_agent_gateway/api/rules.py src/personal_agent_gateway/app.py src/personal_agent_gateway/api/team_runs.py tests/test_api_teams.py tests/test_api_rules.py tests/test_api_team_runs.py
git commit -m "feat: 팀·규칙 API와 팀 기반 실행 생성 엔드포인트

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review (backend)

- 데이터 모델 → Task 1. RuleSetService → Task 2. TeamService → Task 3. 팀 기반 생성+스냅샷 →
  Task 4. 런타임 주입 → Task 5. 목록 enrich → Task 6. 문서 API → Task 7. 라우터/wiring/생성 API →
  Task 8. 스펙의 백엔드 요구사항을 모두 커버한다.
- 타입 일관성: `create_team_run_from_team(team_service, rule_set_service, ...)`가 Task 8에서
  같은 시그니처로 호출됨. `snapshot_for_team`/`_rules_block`의 dict 형태(`global`/`team`/
  `persona_baseline` → `{personality, rules}`)가 Task 2/4/5에서 일치.
- SQLite NULL 매칭 함정(`team_id is ?`)은 Task 2에서 `_find_row` 분기로 해결.
- 레거시 실행(`team_id`/`rules_snapshot` null)은 런타임 주입에서 빈 블록으로 안전.

## 검증 (백엔드 완료 시)

1. `python -m pytest -q` 전체 통과.
2. 앱 기동 후 `POST /api/teams` → `POST /api/team-runs`(team_id) → 실행이 규칙 스냅샷을 가짐 확인.
3. `GET /api/team-runs`가 enrich 필드 반환.
4. `GET /api/team-runs/{id}/documents` 및 traversal 차단(400) 확인.

프런트엔드는 후속 계획 `2026-07-14-agent-teams-frontend.md`에서 진행한다.
