import json
import shutil
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
    "completed_with_failures",
    "failed",
    "canceled",
    "interrupted",
]
RunMode = Literal["planning_only", "plan_and_execute", "review_only"]
AgentStatus = Literal["pending", "running", "waiting", "completed", "failed", "canceled"]
TaskStatus = Literal["pending", "in_progress", "blocked", "completed", "failed", "canceled"]

_ACTIVE_RUN_STATUSES = {"planning", "running", "summarizing"}
_TERMINAL_RUN_STATUSES = {"completed", "completed_with_failures", "failed", "canceled"}


@dataclass(frozen=True)
class TeamRun:
    id: str
    goal: str
    status: TeamRunStatus
    run_mode: RunMode
    leader_agent_id: str | None
    max_workers: int
    rounds_budget: int
    rounds_used: int
    workspace_root: str
    summary: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    team_id: str | None = None
    rules_snapshot: dict | None = None


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
    reinvocations: int
    upstream_session_id: str | None
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
        rounds_budget: int = 8,
        team_id: str | None = None,
        rules_snapshot_json: str | None = None,
    ) -> TeamRun:
        team_run_id = uuid4().hex
        now = _now()
        workspace_root_path = self._workspace_root / team_run_id
        workspace_root_path.mkdir(parents=True)
        workspace_root = str(workspace_root_path)
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
        leader_agent = self._create_agent(team_run_id, leader_persona_id, "leader")
        for member_persona_id in member_persona_ids:
            self._create_agent(team_run_id, member_persona_id, "member")
        self._db.execute(
            "update team_runs set leader_agent_id = ?, updated_at = ? where id = ?",
            (leader_agent.id, _now(), team_run_id),
        )
        return self.get_team_run(team_run_id)

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

    def get_team_run(self, team_run_id: str) -> TeamRun:
        row = self._db.fetchone("select * from team_runs where id = ?", (team_run_id,))
        if row is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        return _team_run_from_row(row)

    def delete_team_run(self, team_run_id: str) -> None:
        run = self.get_team_run(team_run_id)
        workspace_root = Path(run.workspace_root).resolve()
        expected_workspace_root = (self._workspace_root.resolve() / team_run_id).resolve()
        if workspace_root != expected_workspace_root:
            raise ValueError("Team workspace is outside the configured workspace root")
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        # team_agents / team_tasks / team_messages cascade via foreign keys
        self._db.execute("delete from team_runs where id = ?", (team_run_id,))

    def list_team_runs(self) -> list[TeamRun]:
        return [
            _team_run_from_row(row)
            for row in self._db.fetchall("select * from team_runs order by created_at desc")
        ]

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

    def interrupt_active_runs(self) -> list[TeamRun]:
        run_ids = [
            row["id"]
            for row in self._db.fetchall(
                "select id from team_runs where status in ('planning', 'running', 'summarizing')"
            )
        ]
        return [self.interrupt_run(team_run_id) for team_run_id in run_ids]

    def interrupt_run(self, team_run_id: str, include_canceled: bool = False) -> TeamRun:
        now = _now()
        with self._db.connect() as connection:
            run = connection.execute(
                "select status from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            allowed = set(_ACTIVE_RUN_STATUSES)
            if include_canceled:
                allowed.add("canceled")
            if run["status"] not in allowed:
                return self.get_team_run(team_run_id)

            task_statuses = ("in_progress", "canceled") if include_canceled else ("in_progress",)
            placeholders = ", ".join("?" for _ in task_statuses)
            task_rows = connection.execute(
                f"select id from team_tasks where team_run_id = ? and status in ({placeholders})",
                (team_run_id, *task_statuses),
            ).fetchall()
            requeued_task_ids = [row["id"] for row in task_rows]
            connection.execute(
                f"""
                update team_tasks
                set status = 'pending', result = null, error_message = null,
                    started_at = null, finished_at = null, updated_at = ?
                where team_run_id = ? and status in ({placeholders})
                """,
                (now, team_run_id, *task_statuses),
            )

            agent_statuses = ("running", "canceled") if include_canceled else ("running",)
            agent_placeholders = ", ".join("?" for _ in agent_statuses)
            connection.execute(
                f"""
                update team_agents
                set status = 'pending', current_task_id = null,
                    finished_at = null, updated_at = ?
                where team_run_id = ? and status in ({agent_placeholders})
                """,
                (now, team_run_id, *agent_statuses),
            )
            connection.execute(
                """
                update team_runs
                set status = 'interrupted', error_message = null,
                    finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    "system_interrupted",
                    "Gateway execution stopped. Resume is required.",
                    json.dumps(
                        {
                            "previous_status": run["status"],
                            "requeued_task_ids": requeued_task_ids,
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        return self.get_team_run(team_run_id)

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

    def retry_failed_task(self, team_run_id: str, task_id: str) -> tuple[TeamRun, TeamTask]:
        now = _now()
        with self._db.connect() as connection:
            run = connection.execute(
                "select status from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] not in {"completed_with_failures", "failed"}:
                raise ValueError("Only failed terminal team runs can retry tasks")

            task = connection.execute(
                "select status, error_message from team_tasks where id = ? and team_run_id = ?",
                (task_id, team_run_id),
            ).fetchone()
            if task is None:
                raise KeyError(f"Team task not found: {task_id}")
            if task["status"] != "failed":
                raise ValueError("Only failed tasks can be retried")

            connection.execute(
                """
                update team_tasks
                set status = 'pending', result = null, error_message = null,
                    started_at = null, finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, task_id),
            )
            connection.execute(
                """
                update team_runs
                set status = 'interrupted', summary = null, error_message = null,
                    finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    "system_task_retried",
                    "Failed task queued for retry. Resume is required.",
                    json.dumps(
                        {"task_id": task_id, "previous_error": task["error_message"]},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        return self.get_team_run(team_run_id), self._get_task(task_id)

    def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: str | None = None,
        error_message: str | None = None,
    ) -> TeamTask:
        self._get_task(task_id)
        started_at = _now() if status == "in_progress" else None
        finished_at = _now() if status in ("completed", "failed", "canceled") else None
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

    def backfill_agent_avatars(self) -> int:
        updated = 0
        for row in self._db.fetchall(
            "select id, persona_id, persona_snapshot_json from team_agents"
        ):
            snapshot = json.loads(row["persona_snapshot_json"])
            if "avatar" in snapshot:
                continue
            try:
                persona = self._personas.get_persona(row["persona_id"])
            except KeyError:
                continue
            snapshot["avatar"] = persona.avatar
            self._db.execute(
                "update team_agents set persona_snapshot_json = ?, updated_at = ? where id = ?",
                (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), _now(), row["id"]),
            )
            updated += 1
        return updated

    def _create_agent(self, team_run_id: str, persona_id: str, role: str) -> TeamAgent:
        persona = self._personas.get_persona(persona_id)
        agent_id = uuid4().hex
        now = _now()
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
        "default_options": persona.default_options,
        "avatar": persona.avatar,
    }


def _team_run_from_row(row: object) -> TeamRun:
    return TeamRun(
        id=row["id"],
        goal=row["goal"],
        status=row["status"],
        run_mode=row["run_mode"],
        leader_agent_id=row["leader_agent_id"],
        max_workers=row["max_workers"],
        rounds_budget=row["rounds_budget"],
        rounds_used=row["rounds_used"],
        workspace_root=row["workspace_root"],
        summary=row["summary"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        team_id=row["team_id"] if "team_id" in row.keys() else None,
        rules_snapshot=(
            json.loads(row["rules_snapshot_json"])
            if "rules_snapshot_json" in row.keys() and row["rules_snapshot_json"]
            else None
        ),
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
        reinvocations=row["reinvocations"],
        upstream_session_id=row["upstream_session_id"],
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
