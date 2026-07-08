import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import Persona, PersonaService


TeamRunStatus = Literal[
    "draft",
    "planning",
    "running",
    "summarizing",
    "completed",
    "failed",
    "canceled",
]
RunMode = Literal["planning_only", "plan_and_execute", "review_only"]
AgentStatus = Literal["pending", "running", "waiting", "completed", "failed", "canceled"]
TaskStatus = Literal["pending", "in_progress", "blocked", "completed", "failed"]

_ACTIVE_RUN_STATUSES = {"planning", "running"}
_TERMINAL_RUN_STATUSES = {"completed", "failed", "canceled"}


@dataclass(frozen=True)
class TeamRun:
    id: str
    goal: str
    status: TeamRunStatus
    run_mode: RunMode
    leader_agent_id: str | None
    max_workers: int
    workspace_root: str
    summary: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


@dataclass(frozen=True)
class TeamAgent:
    id: str
    team_run_id: str
    name: str
    role: str
    persona_id: str
    persona_snapshot: dict[str, object]
    backend: str
    model: str
    status: AgentStatus
    workspace_path: str | None
    current_task_id: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class TeamTask:
    id: str
    team_run_id: str
    title: str
    description: str
    owner_agent_id: str | None
    status: TaskStatus
    result: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class TeamMessage:
    id: str
    team_run_id: str
    sender_agent_id: str | None
    recipient_agent_id: str | None
    kind: str
    content: str
    metadata: dict[str, object]
    created_at: str


class TeamRunService:
    def __init__(self, db: Database, personas: PersonaService, workspace_root: Path) -> None:
        self._db = db
        self._personas = personas
        self._workspace_root = workspace_root

    def create_team_run(
        self,
        goal: str,
        leader_persona_id: str,
        member_persona_ids: list[str],
        run_mode: RunMode,
        max_workers: int,
    ) -> TeamRun:
        team_run_id = uuid4().hex
        now = _now()
        workspace_root = str(self._workspace_root / team_run_id)
        self._db.execute(
            """
            insert into team_runs (
                id, goal, status, run_mode, leader_agent_id, max_workers,
                workspace_root, summary, error_message, created_at, started_at,
                finished_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_run_id,
                goal,
                "draft",
                run_mode,
                None,
                max_workers,
                workspace_root,
                None,
                None,
                now,
                None,
                None,
                now,
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

    def get_team_run(self, team_run_id: str) -> TeamRun:
        row = self._db.fetchone("select * from team_runs where id = ?", (team_run_id,))
        if row is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        return _team_run_from_row(row)

    def list_team_runs(self) -> list[TeamRun]:
        return [
            _team_run_from_row(row)
            for row in self._db.fetchall("select * from team_runs order by created_at desc")
        ]

    def list_agents(self, team_run_id: str) -> list[TeamAgent]:
        self.get_team_run(team_run_id)
        return [
            _team_agent_from_row(row)
            for row in self._db.fetchall(
                "select * from team_agents where team_run_id = ? order by created_at asc, rowid asc",
                (team_run_id,),
            )
        ]

    def set_agent_status(self, agent_id: str, status: AgentStatus) -> TeamAgent:
        self._get_agent(agent_id)
        started_at = _now() if status == "running" else None
        finished_at = _now() if status in ("completed", "failed", "canceled") else None
        self._db.execute(
            """
            update team_agents
            set status = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, started_at, finished_at, _now(), agent_id),
        )
        return self._get_agent(agent_id)

    def create_task(
        self,
        team_run_id: str,
        title: str,
        description: str,
        owner_agent_id: str | None = None,
    ) -> TeamTask:
        self.get_team_run(team_run_id)
        task_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into team_tasks (
                id, team_run_id, title, description, owner_agent_id, status,
                result, error_message, created_at, updated_at, started_at, finished_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                team_run_id,
                title,
                description,
                owner_agent_id,
                "pending",
                None,
                None,
                now,
                now,
                None,
                None,
            ),
        )
        return self._get_task(task_id)

    def list_tasks(self, team_run_id: str) -> list[TeamTask]:
        self.get_team_run(team_run_id)
        return [
            _team_task_from_row(row)
            for row in self._db.fetchall(
                "select * from team_tasks where team_run_id = ? order by created_at asc, rowid asc",
                (team_run_id,),
            )
        ]

    def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: str | None = None,
        error_message: str | None = None,
    ) -> TeamTask:
        self._get_task(task_id)
        started_at = _now() if status == "in_progress" else None
        finished_at = _now() if status in ("completed", "failed") else None
        self._db.execute(
            """
            update team_tasks
            set status = ?,
                result = ?,
                error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, result, error_message, started_at, finished_at, _now(), task_id),
        )
        return self._get_task(task_id)

    def append_message(
        self,
        team_run_id: str,
        sender_agent_id: str | None,
        recipient_agent_id: str | None,
        kind: str,
        content: str,
        metadata: dict[str, object],
    ) -> TeamMessage:
        self.get_team_run(team_run_id)
        message_id = uuid4().hex
        self._db.execute(
            """
            insert into team_messages (
                id, team_run_id, sender_agent_id, recipient_agent_id, kind,
                content, metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                team_run_id,
                sender_agent_id,
                recipient_agent_id,
                kind,
                content,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        return self._get_message(message_id)

    def list_messages(self, team_run_id: str) -> list[TeamMessage]:
        self.get_team_run(team_run_id)
        return [
            _team_message_from_row(row)
            for row in self._db.fetchall(
                "select * from team_messages where team_run_id = ? order by created_at asc, rowid asc",
                (team_run_id,),
            )
        ]

    def set_run_status(
        self,
        team_run_id: str,
        status: TeamRunStatus,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> TeamRun:
        self.get_team_run(team_run_id)
        started_at = _now() if status in _ACTIVE_RUN_STATUSES else None
        finished_at = _now() if status in _TERMINAL_RUN_STATUSES else None
        self._db.execute(
            """
            update team_runs
            set status = ?,
                summary = ?,
                error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, summary, error_message, started_at, finished_at, _now(), team_run_id),
        )
        return self.get_team_run(team_run_id)

    def _create_agent(self, team_run_id: str, persona_id: str, role: str) -> TeamAgent:
        persona = self._personas.get_persona(persona_id)
        agent_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into team_agents (
                id, team_run_id, name, role, persona_id, persona_snapshot_json,
                backend, model, status, workspace_path, current_task_id,
                started_at, finished_at, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                team_run_id,
                persona.name,
                role,
                persona.id,
                json.dumps(_persona_snapshot(persona), ensure_ascii=False, sort_keys=True),
                persona.default_backend,
                persona.default_model,
                "pending",
                None,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        return self._get_agent(agent_id)

    def _get_agent(self, agent_id: str) -> TeamAgent:
        row = self._db.fetchone("select * from team_agents where id = ?", (agent_id,))
        if row is None:
            raise KeyError(f"Team agent not found: {agent_id}")
        return _team_agent_from_row(row)

    def _get_task(self, task_id: str) -> TeamTask:
        row = self._db.fetchone("select * from team_tasks where id = ?", (task_id,))
        if row is None:
            raise KeyError(f"Team task not found: {task_id}")
        return _team_task_from_row(row)

    def _get_message(self, message_id: str) -> TeamMessage:
        row = self._db.fetchone("select * from team_messages where id = ?", (message_id,))
        if row is None:
            raise KeyError(f"Team message not found: {message_id}")
        return _team_message_from_row(row)


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


def _team_run_from_row(row: object) -> TeamRun:
    return TeamRun(
        id=row["id"],
        goal=row["goal"],
        status=row["status"],
        run_mode=row["run_mode"],
        leader_agent_id=row["leader_agent_id"],
        max_workers=row["max_workers"],
        workspace_root=row["workspace_root"],
        summary=row["summary"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


def _team_agent_from_row(row: object) -> TeamAgent:
    return TeamAgent(
        id=row["id"],
        team_run_id=row["team_run_id"],
        name=row["name"],
        role=row["role"],
        persona_id=row["persona_id"],
        persona_snapshot=json.loads(row["persona_snapshot_json"]),
        backend=row["backend"],
        model=row["model"],
        status=row["status"],
        workspace_path=row["workspace_path"],
        current_task_id=row["current_task_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _team_task_from_row(row: object) -> TeamTask:
    return TeamTask(
        id=row["id"],
        team_run_id=row["team_run_id"],
        title=row["title"],
        description=row["description"],
        owner_agent_id=row["owner_agent_id"],
        status=row["status"],
        result=row["result"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _team_message_from_row(row: object) -> TeamMessage:
    return TeamMessage(
        id=row["id"],
        team_run_id=row["team_run_id"],
        sender_agent_id=row["sender_agent_id"],
        recipient_agent_id=row["recipient_agent_id"],
        kind=row["kind"],
        content=row["content"],
        metadata=json.loads(row["metadata_json"]),
        created_at=row["created_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
