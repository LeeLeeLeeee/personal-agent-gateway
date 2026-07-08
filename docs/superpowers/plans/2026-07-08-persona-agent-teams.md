# Persona-Based Agent Teams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persona-based Agent Teams so a user can create reusable personas, start a Team Run with leader/member agent sessions, watch team tasks/activity, and preserve persona snapshots for reproducible execution.

**Architecture:** Add SQLite-backed `PersonaService` and `TeamRunService` following the existing `JobService` pattern. Add FastAPI routers under `/api/personas` and `/api/team-runs`, publish team lifecycle events through the existing `EventBus`, then extend the static UI with Agent Teams screens that reuse the current console/navigation/activity patterns.

**Tech Stack:** Python 3.11, FastAPI, stdlib `sqlite3`, pytest, existing `EventBus`, existing `CodexModelClient`, vanilla JS static frontend.

---

## Scope

This plan implements the first useful version of the spec:

- Persona CRUD.
- Team Run creation with leader/member persona snapshots.
- Planning-only execution using a leader model response.
- Plan-and-execute worker execution using existing model client boundaries.
- Team Run read APIs for agents/tasks/messages.
- SSE events for team lifecycle.
- Static UI integration for Persona Library, New Team Run, Team Run Detail.

Out of scope:

- ClawTeam dependency.
- tmux.
- automatic git merge.
- production deploy automation.
- per-action approve/deny inside Team Runs.
- worker git worktree isolation.

## File Structure

- `src/personal_agent_gateway/db.py`
  - Add `personas`, `team_runs`, `team_agents`, `team_tasks`, `team_messages` tables.
- `src/personal_agent_gateway/personas.py`
  - New domain service and dataclasses for persona CRUD.
- `src/personal_agent_gateway/teams.py`
  - New domain service and dataclasses for Team Runs, agents, tasks, messages, and status transitions.
- `src/personal_agent_gateway/team_runtime.py`
  - New async coordinator for leader planning and worker execution.
- `src/personal_agent_gateway/api/personas.py`
  - New FastAPI router for persona CRUD.
- `src/personal_agent_gateway/api/team_runs.py`
  - New FastAPI router for Team Run creation, start/cancel, and read model.
- `src/personal_agent_gateway/api/__init__.py`
  - Export new routers.
- `src/personal_agent_gateway/app.py`
  - Attach services and include routers.
- `frontend/src/api/client.js`
  - Add persona/team-run methods to the `api` object (`personas`, `createPersona`, `teamRuns`, `createTeamRun`, `startTeamRun`, `teamRunDetail`).
- `frontend/src/api/client.test.js`
  - Cover the new endpoints (Vitest).
- `frontend/src/components/organisms/Sidebar/index.jsx`
  - Add `Personas` and `Agent Teams` entries to the exported `NAV`.
- `frontend/src/components/containers/GatewayApp/index.jsx`
  - Own persona/team-run state, route the new screens, and extend the existing `/api/events` handler for `team.*` events.
- `frontend/src/components/molecules/PersonaCard/index.jsx`
  - New molecule: single persona summary card.
- `frontend/src/components/molecules/TeamTaskCard/index.jsx`
  - New molecule: single task card for the board.
- `frontend/src/components/organisms/PersonaLibrary/index.jsx`
  - New organism: persona list + seed/create.
- `frontend/src/components/organisms/TeamRunForm/index.jsx`
  - New organism: New Team Run form.
- `frontend/src/components/organisms/TeamRunDetail/index.jsx`
  - New organism: header, agent lanes, task board, team activity.
- `frontend/src/components/references/molecules.md` and `frontend/src/components/references/organisms.md`
  - Register the new atomic-design components.
- `src/personal_agent_gateway/static/**` (app.js/index.html/styles.css)
  - Legacy vanilla frontend. MUST NOT be edited; the live app is the Vite React build served from `frontend_dist/`.
- Tests:
  - `tests/test_personas.py`
  - `tests/test_teams.py`
  - `tests/test_team_runtime.py`
  - `tests/test_api_personas.py`
  - `tests/test_api_team_runs.py`
  - `tests/test_events.py` extension for team events if needed.

---

## Task 1: SQLite Schema for Personas and Team Runs

**Files:**
- Modify: `src/personal_agent_gateway/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing schema test**

Add to `tests/test_db.py`:

```python
def test_database_initializes_agent_team_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()

    rows = db.fetchall(
        "select name from sqlite_master where type = 'table' and name in (?, ?, ?, ?, ?)",
        ("personas", "team_runs", "team_agents", "team_tasks", "team_messages"),
    )

    assert {row["name"] for row in rows} == {
        "personas",
        "team_runs",
        "team_agents",
        "team_tasks",
        "team_messages",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_db.py::test_database_initializes_agent_team_tables -q
```

Expected: FAIL because the tables do not exist.

- [ ] **Step 3: Add schema**

Append to `SCHEMA_SQL` in `src/personal_agent_gateway/db.py`:

```sql
create table if not exists personas (
    id text primary key,
    name text not null,
    role text not null,
    description text not null,
    responsibilities_json text not null,
    constraints_json text not null,
    default_backend text not null,
    default_model text not null,
    created_at text not null,
    updated_at text not null
);

create table if not exists team_runs (
    id text primary key,
    goal text not null,
    status text not null,
    run_mode text not null,
    leader_agent_id text,
    max_workers integer not null,
    workspace_root text not null,
    summary text,
    error_message text,
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null
);

create table if not exists team_agents (
    id text primary key,
    team_run_id text not null,
    name text not null,
    role text not null,
    persona_id text not null,
    persona_snapshot_json text not null,
    backend text not null,
    model text not null,
    status text not null,
    workspace_path text,
    current_task_id text,
    started_at text,
    finished_at text,
    created_at text not null,
    updated_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (persona_id) references personas(id) on delete restrict
);

create table if not exists team_tasks (
    id text primary key,
    team_run_id text not null,
    title text not null,
    description text not null,
    owner_agent_id text,
    status text not null,
    result text,
    error_message text,
    created_at text not null,
    updated_at text not null,
    started_at text,
    finished_at text,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (owner_agent_id) references team_agents(id) on delete set null
);

create table if not exists team_messages (
    id text primary key,
    team_run_id text not null,
    sender_agent_id text,
    recipient_agent_id text,
    kind text not null,
    content text not null,
    metadata_json text not null,
    created_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (sender_agent_id) references team_agents(id) on delete set null,
    foreign key (recipient_agent_id) references team_agents(id) on delete set null
);
```

- [ ] **Step 4: Run schema test**

Run:

```bash
python -m pytest tests/test_db.py::test_database_initializes_agent_team_tables -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/db.py tests/test_db.py
git commit -m "feat(teams): add persona team schema"
```

---

## Task 2: Persona Service

**Files:**
- Create: `src/personal_agent_gateway/personas.py`
- Test: `tests/test_personas.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_personas.py`:

```python
from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService


def make_service(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    return PersonaService(db)


def test_create_and_list_personas(tmp_path):
    service = make_service(tmp_path)

    persona = service.create_persona(
        name="Frontend Designer",
        role="UI/UX review",
        description="Reviews interface clarity and layout.",
        responsibilities=["Review layout", "Check responsive behavior"],
        constraints=["Do not change backend APIs unless assigned"],
        default_backend="codex",
        default_model="default",
    )

    assert persona.id
    assert persona.name == "Frontend Designer"
    assert persona.responsibilities == ["Review layout", "Check responsive behavior"]
    assert [item.name for item in service.list_personas()] == ["Frontend Designer"]


def test_update_persona_does_not_change_id_or_created_at(tmp_path):
    service = make_service(tmp_path)
    persona = service.create_persona(
        name="QA Tester",
        role="Quality review",
        description="Finds regression risk.",
        responsibilities=["Run tests"],
        constraints=["Report evidence"],
    )

    updated = service.update_persona(
        persona.id,
        name="Strict QA Tester",
        constraints=["Report evidence", "Do not modify product code"],
    )

    assert updated.id == persona.id
    assert updated.created_at == persona.created_at
    assert updated.name == "Strict QA Tester"
    assert updated.constraints == ["Report evidence", "Do not modify product code"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_personas.py -q
```

Expected: FAIL because `personal_agent_gateway.personas` does not exist.

- [ ] **Step 3: Implement service**

Create `src/personal_agent_gateway/personas.py`:

```python
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    description: str
    responsibilities: list[str]
    constraints: list[str]
    default_backend: str
    default_model: str
    created_at: str
    updated_at: str


class PersonaService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_persona(
        self,
        name: str,
        role: str,
        description: str,
        responsibilities: list[str],
        constraints: list[str],
        default_backend: str = "codex",
        default_model: str = "default",
    ) -> Persona:
        persona_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into personas (
                id, name, role, description, responsibilities_json,
                constraints_json, default_backend, default_model, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona_id,
                name,
                role,
                description,
                json.dumps(responsibilities, ensure_ascii=False),
                json.dumps(constraints, ensure_ascii=False),
                default_backend,
                default_model,
                now,
                now,
            ),
        )
        return self.get_persona(persona_id)

    def get_persona(self, persona_id: str) -> Persona:
        row = self._db.fetchone("select * from personas where id = ?", (persona_id,))
        if row is None:
            raise KeyError(f"Persona not found: {persona_id}")
        return _persona_from_row(row)

    def list_personas(self) -> list[Persona]:
        return [
            _persona_from_row(row)
            for row in self._db.fetchall("select * from personas order by created_at asc")
        ]

    def update_persona(
        self,
        persona_id: str,
        name: str | None = None,
        role: str | None = None,
        description: str | None = None,
        responsibilities: list[str] | None = None,
        constraints: list[str] | None = None,
        default_backend: str | None = None,
        default_model: str | None = None,
    ) -> Persona:
        current = self.get_persona(persona_id)
        updated_at = _now()
        self._db.execute(
            """
            update personas
            set name = ?, role = ?, description = ?, responsibilities_json = ?,
                constraints_json = ?, default_backend = ?, default_model = ?, updated_at = ?
            where id = ?
            """,
            (
                name if name is not None else current.name,
                role if role is not None else current.role,
                description if description is not None else current.description,
                json.dumps(responsibilities if responsibilities is not None else current.responsibilities, ensure_ascii=False),
                json.dumps(constraints if constraints is not None else current.constraints, ensure_ascii=False),
                default_backend if default_backend is not None else current.default_backend,
                default_model if default_model is not None else current.default_model,
                updated_at,
                persona_id,
            ),
        )
        return self.get_persona(persona_id)

    def delete_persona(self, persona_id: str) -> None:
        self.get_persona(persona_id)
        self._db.execute("delete from personas where id = ?", (persona_id,))


def _persona_from_row(row) -> Persona:
    return Persona(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        description=row["description"],
        responsibilities=list(json.loads(row["responsibilities_json"])),
        constraints=list(json.loads(row["constraints_json"])),
        default_backend=row["default_backend"],
        default_model=row["default_model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run persona tests**

Run:

```bash
python -m pytest tests/test_personas.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/personas.py tests/test_personas.py
git commit -m "feat(teams): add persona service"
```

---

## Task 3: Persona API

**Files:**
- Create: `src/personal_agent_gateway/api/personas.py`
- Modify: `src/personal_agent_gateway/api/__init__.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_api_personas.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_personas.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-key",
    )


def authenticated_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")
    return client


def test_persona_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/personas")

    assert response.status_code == 401


def test_create_and_list_personas_api(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    create_response = client.post(
        "/api/personas",
        json={
            "name": "Tech Lead",
            "role": "Planning and integration",
            "description": "Splits goals and integrates results.",
            "responsibilities": ["Plan tasks", "Review results"],
            "constraints": ["Keep scope small"],
            "default_backend": "codex",
            "default_model": "default",
        },
    )

    assert create_response.status_code == 200
    persona = create_response.json()["persona"]
    assert persona["name"] == "Tech Lead"

    list_response = client.get("/api/personas")

    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()["personas"]] == ["Tech Lead"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_api_personas.py -q
```

Expected: FAIL because `/api/personas` is not registered.

- [ ] **Step 3: Implement router**

Create `src/personal_agent_gateway/api/personas.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from personal_agent_gateway.personas import Persona


router = APIRouter(prefix="/api/personas", tags=["personas"])


class PersonaRequest(BaseModel):
    name: str
    role: str
    description: str
    responsibilities: list[str] = []
    constraints: list[str] = []
    default_backend: str = "codex"
    default_model: str = "default"


def require_session(session: Annotated[str | None, Cookie(alias="agent_session")] = None) -> None:
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")


session_dependency = Depends(require_session)


@router.get("")
def list_personas(request: Request, _session: None = session_dependency) -> dict[str, list[dict[str, object]]]:
    return {"personas": [_persona_payload(persona) for persona in request.app.state.persona_service.list_personas()]}


@router.post("")
def create_persona(request: Request, payload: PersonaRequest, _session: None = session_dependency) -> dict[str, object]:
    persona = request.app.state.persona_service.create_persona(**payload.model_dump())
    return {"persona": _persona_payload(persona)}


@router.get("/{persona_id}")
def get_persona(request: Request, persona_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        persona = request.app.state.persona_service.get_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"persona": _persona_payload(persona)}


@router.patch("/{persona_id}")
def update_persona(request: Request, persona_id: str, payload: PersonaRequest, _session: None = session_dependency) -> dict[str, object]:
    try:
        persona = request.app.state.persona_service.update_persona(persona_id, **payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"persona": _persona_payload(persona)}


@router.delete("/{persona_id}")
def delete_persona(request: Request, persona_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        request.app.state.persona_service.delete_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    return {"deleted": True}


def _persona_payload(persona: Persona) -> dict[str, object]:
    return {
        "id": persona.id,
        "name": persona.name,
        "role": persona.role,
        "description": persona.description,
        "responsibilities": persona.responsibilities,
        "constraints": persona.constraints,
        "default_backend": persona.default_backend,
        "default_model": persona.default_model,
        "created_at": persona.created_at,
        "updated_at": persona.updated_at,
    }
```

- [ ] **Step 4: Register service and router**

In `src/personal_agent_gateway/api/__init__.py`, export `personas_router`.

In `src/personal_agent_gateway/app.py`:

```python
from personal_agent_gateway.personas import PersonaService
```

Inside `_attach_local_services` after `db.initialize()`:

```python
persona_service = PersonaService(db)
app.state.persona_service = persona_service
```

Include router in `create_app`:

```python
app.include_router(personas_router)
```

- [ ] **Step 5: Run API tests**

Run:

```bash
python -m pytest tests/test_api_personas.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/api/personas.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_personas.py
git commit -m "feat(teams): add persona API"
```

---

## Task 4: Team Run Service and Persona Snapshots

**Files:**
- Create: `src/personal_agent_gateway/teams.py`
- Test: `tests/test_teams.py`

- [ ] **Step 1: Write Team Run tests**

Create `tests/test_teams.py`:

```python
from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.teams import TeamRunService


def make_services(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    return personas, teams


def test_create_team_run_snapshots_personas(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], ["Stay scoped"])
    member = personas.create_persona("QA Tester", "Quality", "Checks risk.", ["Test"], ["Report evidence"])

    run = teams.create_team_run(
        goal="Design Agent Teams",
        leader_persona_id=leader.id,
        member_persona_ids=[member.id],
        run_mode="planning_only",
        max_workers=2,
    )

    agents = teams.list_agents(run.id)
    assert run.status == "draft"
    assert len(agents) == 2
    assert agents[0].persona_snapshot["name"] == "Tech Lead"
    assert agents[1].persona_snapshot["name"] == "QA Tester"

    personas.update_persona(member.id, name="Changed QA")

    unchanged = teams.list_agents(run.id)[1]
    assert unchanged.persona_snapshot["name"] == "QA Tester"


def test_append_team_message(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    message = teams.append_message(run.id, agent.id, None, "note", "Planning started", {"phase": "planning"})

    assert message.content == "Planning started"
    assert teams.list_messages(run.id)[0].metadata == {"phase": "planning"}
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_teams.py -q
```

Expected: FAIL because `personal_agent_gateway.teams` does not exist.

- [ ] **Step 3: Implement `teams.py`**

Create `src/personal_agent_gateway/teams.py` with dataclasses:

```python
TeamRunStatus = Literal["draft", "planning", "running", "summarizing", "completed", "failed", "canceled"]
RunMode = Literal["planning_only", "plan_and_execute", "review_only"]
AgentStatus = Literal["pending", "running", "waiting", "completed", "failed", "canceled"]
TaskStatus = Literal["pending", "in_progress", "blocked", "completed", "failed"]
```

Implement methods:

```python
class TeamRunService:
    def __init__(self, db: Database, personas: PersonaService, workspace_root: Path) -> None: ...
    def create_team_run(self, goal: str, leader_persona_id: str, member_persona_ids: list[str], run_mode: RunMode, max_workers: int) -> TeamRun: ...
    def get_team_run(self, team_run_id: str) -> TeamRun: ...
    def list_team_runs(self) -> list[TeamRun]: ...
    def list_agents(self, team_run_id: str) -> list[TeamAgent]: ...
    def create_task(self, team_run_id: str, title: str, description: str, owner_agent_id: str | None = None) -> TeamTask: ...
    def list_tasks(self, team_run_id: str) -> list[TeamTask]: ...
    def set_task_status(self, task_id: str, status: TaskStatus, result: str | None = None, error_message: str | None = None) -> TeamTask: ...
    def append_message(self, team_run_id: str, sender_agent_id: str | None, recipient_agent_id: str | None, kind: str, content: str, metadata: dict[str, object]) -> TeamMessage: ...
    def list_messages(self, team_run_id: str) -> list[TeamMessage]: ...
    def set_run_status(self, team_run_id: str, status: TeamRunStatus, summary: str | None = None, error_message: str | None = None) -> TeamRun: ...
```

Use the existing `JobService` implementation style:

- ids use `uuid4().hex`
- timestamps use `datetime.now(timezone.utc).isoformat()`
- JSON fields use `json.dumps(..., ensure_ascii=False, sort_keys=True)`
- missing row raises `KeyError`

Persona snapshot shape:

```python
def _persona_snapshot(persona: Persona) -> dict[str, object]:
    return {
        "id": persona.id,
        "name": persona.name,
        "role": persona.role,
        "description": persona.description,
        "responsibilities": persona.responsibilities,
        "constraints": persona.constraints,
        "default_backend": persona.default_backend,
        "default_model": persona.default_model,
    }
```

- [ ] **Step 4: Run Team Run tests**

Run:

```bash
python -m pytest tests/test_teams.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat(teams): add team run service"
```

---

## Task 5: Team Run API

**Files:**
- Create: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `src/personal_agent_gateway/api/__init__.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_api_team_runs.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_team_runs.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-key",
    )


def authenticated_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")
    return client


def create_persona(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/personas",
        json={
            "name": name,
            "role": f"{name} role",
            "description": f"{name} description",
            "responsibilities": ["Do assigned work"],
            "constraints": ["Report evidence"],
        },
    )
    return response.json()["persona"]["id"]


def test_create_team_run_api_snapshots_agents(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Tech Lead")
    member_id = create_persona(client, "QA Tester")

    response = client.post(
        "/api/team-runs",
        json={
            "goal": "Design Agent Teams",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "planning_only",
            "max_workers": 2,
        },
    )

    assert response.status_code == 200
    run = response.json()["team_run"]
    assert run["goal"] == "Design Agent Teams"
    assert run["status"] == "draft"

    agents = client.get(f"/api/team-runs/{run['id']}/agents").json()["agents"]
    assert [agent["name"] for agent in agents] == ["Tech Lead", "QA Tester"]


def test_team_run_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.get("/api/team-runs")

    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/test_api_team_runs.py -q
```

Expected: FAIL because `/api/team-runs` is not registered.

- [ ] **Step 3: Implement router**

Create `src/personal_agent_gateway/api/team_runs.py`.

Request model:

```python
class CreateTeamRunRequest(BaseModel):
    goal: str
    leader_persona_id: str
    member_persona_ids: list[str] = []
    run_mode: Literal["planning_only", "plan_and_execute", "review_only"] = "planning_only"
    max_workers: int = 3
```

Routes:

```python
GET  /api/team-runs
POST /api/team-runs
GET  /api/team-runs/{team_run_id}
POST /api/team-runs/{team_run_id}/start
POST /api/team-runs/{team_run_id}/cancel
GET  /api/team-runs/{team_run_id}/agents
GET  /api/team-runs/{team_run_id}/tasks
GET  /api/team-runs/{team_run_id}/messages
```

Payload helpers should return:

```python
def _team_run_payload(run): ...
def _agent_payload(agent): ...
def _task_payload(task): ...
def _message_payload(message): ...
```

- [ ] **Step 4: Register service and router**

In `app.py`, create `TeamRunService` in `_attach_local_services`:

```python
from personal_agent_gateway.teams import TeamRunService

team_run_service = TeamRunService(db, persona_service, config.workspace_root)
app.state.team_run_service = team_run_service
```

Include `team_runs_router` in `create_app`.

- [ ] **Step 5: Run API tests**

Run:

```bash
python -m pytest tests/test_api_team_runs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/api/__init__.py src/personal_agent_gateway/app.py tests/test_api_team_runs.py
git commit -m "feat(teams): add team run API"
```

---

## Task 6: Leader Planning Runtime

**Files:**
- Create: `src/personal_agent_gateway/team_runtime.py`
- Modify: `src/personal_agent_gateway/api/team_runs.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_team_runtime.py`

- [ ] **Step 1: Write planning runtime test**

Create `tests/test_team_runtime.py`:

```python
from dataclasses import dataclass

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


@dataclass
class FakeModel:
    content: str

    async def complete(self, messages):
        return ModelResponse(content=self.content, tool_calls=[])


@pytest.mark.asyncio
async def test_planning_only_creates_tasks_and_completes_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel(
            '[{"title":"Define schema","description":"Add team tables"},'
            '{"title":"Design UI","description":"Add team screens"}]'
        ),
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id)] == ["Define schema", "Design UI"]
    assert "Planning completed" in teams.list_messages(run.id)[-1].content
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_team_runtime.py -q
```

Expected: FAIL because `team_runtime.py` does not exist.

- [ ] **Step 3: Implement `TeamRuntime`**

Create `src/personal_agent_gateway/team_runtime.py`.

Core interface:

```python
class TeamRuntime:
    def __init__(
        self,
        teams: TeamRunService,
        model_factory: Callable[[TeamAgent], ModelClient],
        event_bus: EventBus | None = None,
    ) -> None: ...

    async def start(self, team_run_id: str) -> TeamRun: ...
```

Planning prompt:

```text
You are the leader agent for a personal-agent-gateway Team Run.
Return ONLY JSON array of task objects.
Each object must have "title" and "description".
Goal: {goal}
Persona snapshot: {persona_snapshot_json}
```

JSON extraction:

```python
def _parse_task_plan(content: str) -> list[dict[str, str]]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    raw = json.loads(stripped)
    if not isinstance(raw, list):
        raise ValueError("Planner response must be a JSON array")
    tasks = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Planner task must be an object")
        title = item.get("title")
        description = item.get("description")
        if not isinstance(title, str) or not isinstance(description, str):
            raise ValueError("Planner task requires title and description")
        tasks.append({"title": title, "description": description})
    return tasks
```

Runtime behavior:

- Set run status to `planning`.
- Set leader status to `running`.
- Call leader model.
- Create tasks.
- Append team message `"Planning completed with {n} tasks."`.
- If run mode is `planning_only`, set run status to `completed`.
- Publish `team.run.started`, `team.task.created`, `team.run.completed` events when `event_bus` exists.

- [ ] **Step 4: Run planning runtime test**

Run:

```bash
python -m pytest tests/test_team_runtime.py -q
```

Expected: PASS.

- [ ] **Step 5: Wire `POST /api/team-runs/{id}/start`**

In `app.py`, attach `app.state.team_runtime`.

Model factory should create `CodexModelClient` for now:

```python
def team_model_factory(agent):
    return CodexModelClient(
        binary=config.codex_binary,
        model=agent.model,
        workspace_root=config.workspace_root,
        sandbox=config.codex_sandbox,
        approval_policy=config.codex_approval_policy,
        timeout_seconds=config.codex_timeout_seconds,
    )
```

In `api/team_runs.py`, `start` awaits `request.app.state.team_runtime.start(team_run_id)`.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/app.py tests/test_team_runtime.py
git commit -m "feat(teams): add leader planning runtime"
```

---

## Task 7: Worker Execution MVP

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Test: `tests/test_team_runtime.py`

- [ ] **Step 1: Add worker execution test**

Append to `tests/test_team_runtime.py`:

```python
@pytest.mark.asyncio
async def test_plan_and_execute_assigns_tasks_to_workers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    worker = personas.create_persona("QA Tester", "Quality", "Checks work.", ["Test"], [])
    run = teams.create_team_run("Build teams", leader.id, [worker.id], "plan_and_execute", 1)

    responses = iter([
        '[{"title":"Verify API","description":"Check team run endpoints"}]',
        "Verified API behavior. No files changed. Evidence: tests passed.",
    ])
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(next(responses)))

    completed = await runtime.start(run.id)

    tasks = teams.list_tasks(run.id)
    messages = teams.list_messages(run.id)
    assert completed.status == "completed"
    assert tasks[0].status == "completed"
    assert "Verified API behavior" in tasks[0].result
    assert any(message.kind == "agent_output" for message in messages)
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_team_runtime.py::test_plan_and_execute_assigns_tasks_to_workers -q
```

Expected: FAIL because worker execution is not implemented.

- [ ] **Step 3: Implement worker execution**

In `TeamRuntime.start` after planning:

- If `run_mode != "plan_and_execute"`, complete after planning.
- Find non-leader agents.
- Assign tasks round-robin to workers.
- For each task:
  - set task status `in_progress`
  - set agent status `running`
  - call worker model with persona prompt and assigned task
  - set task status `completed` with result text
  - append `agent_output` message
  - publish `team.task.updated` and `team.message.created`
- Set run status `summarizing`, then `completed` with summary.

Worker prompt:

```text
You are an agent in a personal-agent-gateway Team Run.
Persona:
{persona_snapshot_json}
Goal: {goal}
Assigned task: {task_title}
Task description: {task_description}
Return concise result, changed files, and verification evidence.
```

MVP can execute tasks sequentially even when `max_workers > 1`. Parallel execution is a later optimization after correctness and attribution are stable.

- [ ] **Step 4: Run team runtime tests**

Run:

```bash
python -m pytest tests/test_team_runtime.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(teams): execute planned team tasks"
```

---

## Task 8: Team SSE Event Coverage

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Test: `tests/test_events.py` or `tests/test_team_runtime.py`

- [ ] **Step 1: Add event publishing test**

Append to `tests/test_team_runtime.py`:

```python
from personal_agent_gateway.events import EventBus


@pytest.mark.asyncio
async def test_team_runtime_publishes_team_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    bus = EventBus()
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel('[{"title":"Define schema","description":"Add tables"}]'),
        event_bus=bus,
    )

    await runtime.start(run.id)

    event_types = [event["type"] for event in bus.recent()]
    assert "team.run.started" in event_types
    assert "team.task.created" in event_types
    assert "team.run.completed" in event_types
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/test_team_runtime.py::test_team_runtime_publishes_team_events -q
```

Expected: FAIL until events are published consistently.

- [ ] **Step 3: Publish events in runtime**

Add helper:

```python
async def _publish(self, event: dict[str, object]) -> None:
    if self._event_bus is not None:
        await self._event_bus.publish(event)
```

Publish:

```python
await self._publish({"type": "team.run.started", "team_run_id": run.id})
await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": task.id, "title": task.title})
await self._publish({"type": "team.task.updated", "team_run_id": run.id, "task_id": task.id, "status": task.status})
await self._publish({"type": "team.message.created", "team_run_id": run.id, "message_id": message.id, "kind": message.kind})
await self._publish({"type": "team.run.completed", "team_run_id": run.id})
```

- [ ] **Step 4: Run event test**

Run:

```bash
python -m pytest tests/test_team_runtime.py::test_team_runtime_publishes_team_events -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(teams): publish team runtime events"
```

---

## Task 9: Frontend API Client and Navigation (Vite React)

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`
- Modify: `frontend/src/components/organisms/Sidebar/index.jsx`

> React conventions (match the existing app): `api` is a single object literal using the `jsonOrNull`/`jsonList` helpers; organisms are pure render + callbacks; the `GatewayApp` container owns all data loading and state; navigation keys live in the exported `NAV`.

- [ ] **Step 1: Write failing API client tests**

Append to `frontend/src/api/client.test.js`, adapting to the file's existing `fetch` mock / `jsonResponse` helpers:

```javascript
  it("supports persona and team-run endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ personas: [{ id: "p1", name: "Tech Lead" }] }))
      .mockResolvedValueOnce(jsonResponse({ persona: { id: "p2", name: "QA Tester" } }))
      .mockResolvedValueOnce(jsonResponse({ team_runs: [{ id: "r1", goal: "Ship" }] }))
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r2", goal: "Design" } }))
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r2", status: "planning" } }));

    await expect(api.personas()).resolves.toEqual([{ id: "p1", name: "Tech Lead" }]);
    await expect(api.createPersona({ name: "QA Tester" })).resolves.toEqual({ id: "p2", name: "QA Tester" });
    await expect(api.teamRuns()).resolves.toEqual([{ id: "r1", goal: "Ship" }]);
    await api.createTeamRun({ goal: "Design", leader_persona_id: "p1" });
    await api.startTeamRun("r2");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/personas");
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/team-runs", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenNthCalledWith(5, "/api/team-runs/r2/start", expect.objectContaining({ method: "POST" }));
  });
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
cd frontend && npm test -- src/api/client.test.js
```

Expected: FAIL because the methods do not exist.

- [ ] **Step 3: Add API methods**

Add to the `api` object in `frontend/src/api/client.js`, reusing the existing `jsonOrNull`/`jsonList` helpers:

```javascript
  async personas() {
    return jsonList(await fetch("/api/personas"), "personas");
  },
  async createPersona(payload) {
    const body = await jsonOrNull(await fetch("/api/personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }));
    return body?.persona || null;
  },
  async teamRuns() {
    return jsonList(await fetch("/api/team-runs"), "team_runs");
  },
  async createTeamRun(payload) {
    const body = await jsonOrNull(await fetch("/api/team-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }));
    return body?.team_run || null;
  },
  async startTeamRun(id) {
    const body = await jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/start`, { method: "POST" }));
    return body?.team_run || null;
  },
  async teamRunDetail(id) {
    const [run, agents, tasks, messages] = await Promise.all([
      jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}`)),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/agents`), "agents"),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/tasks`), "tasks"),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/messages`), "messages")
    ]);
    return { run: run?.team_run || null, agents, tasks, messages };
  },
```

- [ ] **Step 4: Add navigation entries**

In `frontend/src/components/organisms/Sidebar/index.jsx`, add to the exported `NAV` array (after `capabilities`, before `artifacts`), keeping them as control-plane execution concepts:

```javascript
  { key: "personas", label: "Personas" },
  { key: "teams", label: "Agent Teams" },
```

The container reads `screen` and renders the matching organism; unimplemented screens keep the existing `<div className="planned">... - PLANNED</div>` placeholder until wired in Task 11.

- [ ] **Step 5: Run frontend tests**

Run:

```bash
cd frontend && npm test -- src/api/client.test.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.js frontend/src/api/client.test.js frontend/src/components/organisms/Sidebar/index.jsx
git commit -m "feat(ui): add agent teams api client and nav"
```

---

## Task 10: Persona Library and New Team Run organisms

**Files:**
- Create: `frontend/src/components/molecules/PersonaCard/index.jsx`
- Create: `frontend/src/components/organisms/PersonaLibrary/index.jsx`
- Create: `frontend/src/components/organisms/PersonaLibrary/PersonaLibrary.test.jsx`
- Create: `frontend/src/components/organisms/TeamRunForm/index.jsx`
- Create: `frontend/src/components/organisms/TeamRunForm/TeamRunForm.test.jsx`
- Modify: `frontend/src/components/references/molecules.md`, `frontend/src/components/references/organisms.md`

> Organisms stay pure (props in, callbacks out). Data loading and the `seed defaults` create calls belong to the `GatewayApp` container (wired in Task 11). Reuse the `Button` atom and existing CSS tokens/classes (`.headline`, `.mono`, `--c-*`); add any new classes to the Vite app stylesheet, never to `static/styles.css`.

- [ ] **Step 1: Write failing component tests**

Create `frontend/src/components/organisms/PersonaLibrary/PersonaLibrary.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaLibrary } from "./index.jsx";

describe("PersonaLibrary", () => {
  it("renders personas and triggers seeding of defaults", async () => {
    const onSeedDefaults = vi.fn();
    render(
      <PersonaLibrary
        personas={[{ id: "p1", name: "Tech Lead", role: "Planning", responsibilities: ["Plan"] }]}
        onSeedDefaults={onSeedDefaults}
      />
    );

    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /seed defaults/i }));
    expect(onSeedDefaults).toHaveBeenCalled();
  });
});
```

Create `frontend/src/components/organisms/TeamRunForm/TeamRunForm.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamRunForm } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead" },
  { id: "p2", name: "QA Tester" }
];

describe("TeamRunForm", () => {
  it("submits an assembled team-run payload", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText(/goal/i), "Design Agent Teams");
    await userEvent.selectOptions(screen.getByLabelText(/leader/i), "p1");
    await userEvent.click(screen.getByRole("button", { name: /create team run/i }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      goal: "Design Agent Teams",
      leader_persona_id: "p1",
      run_mode: "planning_only"
    }));
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd frontend && npm test -- src/components/organisms/PersonaLibrary src/components/organisms/TeamRunForm
```

Expected: FAIL because the components do not exist.

- [ ] **Step 3: Implement `PersonaCard` molecule**

Create `frontend/src/components/molecules/PersonaCard/index.jsx`:

```jsx
export function PersonaCard({ persona }) {
  return (
    <article className="persona-card">
      <div className="headline" style={{ fontSize: 13 }}>{persona.name}</div>
      <div className="mono" style={{ fontSize: 10, color: "var(--c-grey)" }}>{persona.role}</div>
      <p style={{ fontSize: 12 }}>{persona.description || ""}</p>
      <div className="chip-row">
        {(persona.responsibilities || []).slice(0, 3).map((item) => (
          <span className="chip" key={item}>{item}</span>
        ))}
      </div>
    </article>
  );
}
```

- [ ] **Step 4: Implement `PersonaLibrary` organism**

Create `frontend/src/components/organisms/PersonaLibrary/index.jsx`. It renders a grid of `PersonaCard` and a `Seed defaults` button that calls `onSeedDefaults` (the container seeds Tech Lead, Frontend Designer, Backend Engineer, QA Tester, Release Manager via `api.createPersona`). No `fetch` inside the organism.

- [ ] **Step 5: Implement `TeamRunForm` organism**

Create `frontend/src/components/organisms/TeamRunForm/index.jsx` with local form state:

- goal `<textarea>` (label `Goal`),
- leader `<select>` (label `Leader`) populated from `personas`,
- member persona checkboxes,
- run-mode `<select>`: `planning_only` / `plan_and_execute` / `review_only`,
- max-workers number input (default 3),
- `Create Team Run` button → `onSubmit({ goal, leader_persona_id, member_persona_ids, run_mode, max_workers })`.

- [ ] **Step 6: Register components in references catalogs**

Add `PersonaCard` to `frontend/src/components/references/molecules.md` and `PersonaLibrary`/`TeamRunForm` to `frontend/src/components/references/organisms.md`, using the existing `Path / Props / Use when / Don't use when` entry format.

- [ ] **Step 7: Run tests**

Run:

```bash
cd frontend && npm test -- src/components/organisms/PersonaLibrary src/components/organisms/TeamRunForm
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/molecules/PersonaCard frontend/src/components/organisms/PersonaLibrary frontend/src/components/organisms/TeamRunForm frontend/src/components/references
git commit -m "feat(ui): add persona library and team run form"
```

---

## Task 11: Team Run Detail organism and container wiring

**Files:**
- Create: `frontend/src/components/molecules/TeamTaskCard/index.jsx`
- Create: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Create: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `frontend/src/components/references/molecules.md`, `frontend/src/components/references/organisms.md`

- [ ] **Step 1: Write failing `TeamRunDetail` test**

Create `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`:

```javascript
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TeamRunDetail } from "./index.jsx";

describe("TeamRunDetail", () => {
  it("renders header, agent lanes, task board, and activity", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [{ id: "a1", name: "Tech Lead", role: "Planning", status: "running", current_task_id: null }],
          tasks: [{ id: "t1", title: "Define schema", description: "tables", status: "in_progress" }],
          messages: [{ id: "m1", kind: "note", content: "Planning started", created_at: "2026-07-08T00:00:00Z" }]
        }}
      />
    );

    expect(screen.getByText("Design")).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(screen.getByText("Define schema")).toBeInTheDocument();
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd frontend && npm test -- src/components/organisms/TeamRunDetail
```

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement `TeamTaskCard` molecule and `TeamRunDetail` organism**

`TeamTaskCard`: title, description, optional `<pre>` result. `TeamRunDetail` composes: header (goal/status/run mode/elapsed), agent lanes (name/role/status via the `StatusBadge` atom / current task), a task board with columns, and a team activity list.

```jsx
const TEAM_TASK_COLUMNS = ["pending", "in_progress", "blocked", "completed", "failed"];
```

The organism takes a single `detail` prop (`{ run, agents, tasks, messages }`) and renders `"No team run selected."` when `detail?.run` is absent. No `fetch` inside the organism.

- [ ] **Step 4: Wire into the `GatewayApp` container**

In `frontend/src/components/containers/GatewayApp/index.jsx`:

- Add state: `personas`, `teamRuns`, `selectedTeamRunId`, `teamRunDetail`.
- Load `api.personas()` / `api.teamRuns()` when entering the `personas`/`teams` screens (or in `loadApp`).
- Route screens: for `screen === "personas"` render `<PersonaLibrary personas={personas} onSeedDefaults={...} />`; for `screen === "teams"` render the team-run list + `<TeamRunForm .../>` and, when one is selected, `<TeamRunDetail detail={teamRunDetail} />`. These replace the PLANNED placeholder branch for those keys.
- Add `handleSeedDefaults`, `handleCreateTeamRun` (then `startTeamRun` + `teamRunDetail`), `handleSelectTeamRun` handlers that call the container's `api` methods and set state.
- **Reuse the existing `/api/events` EventSource** (do not open a second one): in the existing `source.onmessage`, after parsing, if `parsed.type?.startsWith("team.")` and `parsed.team_run_id === selectedTeamRunId`, refresh `teamRunDetail` via `api.teamRunDetail(selectedTeamRunId)`.

- [ ] **Step 5: Register components and run the full frontend suite**

Add `TeamTaskCard`/`TeamRunDetail` to the references catalogs, then run:

```bash
cd frontend && npm test && npm run build
```

Expected: Vitest passes and the Vite build succeeds (refreshing `frontend_dist/`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/molecules/TeamTaskCard frontend/src/components/organisms/TeamRunDetail frontend/src/components/containers/GatewayApp frontend/src/components/references
git commit -m "feat(ui): add team run detail and wire agent teams screens"
```

---

## Task 12: End-to-End Verification

**Files:**
- No new files required unless fixing defects.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
python -m pytest tests/test_personas.py tests/test_teams.py tests/test_team_runtime.py tests/test_api_personas.py tests/test_api_team_runs.py -q
```

Expected: all pass.

- [ ] **Step 2: Run full backend tests**

Run:

```bash
python -m pytest -q
```

Expected: all pass.

- [ ] **Step 3: Run ruff**

Run:

```bash
python -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 4: Run frontend tests and build**

Run:

```bash
cd frontend && npm test && npm run build
```

Expected: Vitest passes and the Vite build succeeds (refreshing `frontend_dist/`, which is what the app serves at `/`).

- [ ] **Step 5: Manual browser verification**

Run local server:

```bash
python -m uvicorn personal_agent_gateway.app:create_app --factory --host 127.0.0.1 --port 8787
```

Verify:

- OTP login still works.
- Agent Teams nav appears in the current app shell.
- Seed default personas.
- Create a planning-only Team Run.
- Start it.
- Team Run Detail shows agent lanes, tasks, and team activity.
- SSE updates activity without full page reload.

## Self-Review

- Spec coverage:
  - Persona CRUD: Tasks 1-3.
  - Persona snapshot at Team Run creation: Task 4.
  - Team Run API/read model: Task 5.
  - Leader planning: Task 6.
  - Worker execution MVP: Task 7.
  - SSE activity: Task 8.
  - Existing UI/UX integration: Tasks 9-11.
  - approve/deny exclusion: runtime tasks do not add approval routes.
  - Full Access attribution: Team messages/events include agent/task attribution; checkpoint/diff remains separate per security operating model.
- Placeholder scan:
  - No TBD/TODO steps. Later phases explicitly out of scope.
- Type consistency:
  - `PersonaService`, `TeamRunService`, `TeamRuntime`, `/api/personas`, `/api/team-runs`, `team_run_id`, `persona_snapshot` are used consistently.
