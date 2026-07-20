import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor
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
    "waiting_for_user",
]
RunMode = Literal["planning_only", "plan_and_execute", "review_only"]
LifecycleMode = Literal["standard", "continuous"]
CycleStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "interrupted",
    "completed",
    "completed_with_failures",
    "failed",
    "canceled",
]
AgentStatus = Literal["pending", "running", "waiting", "completed", "failed", "canceled"]
TaskStatus = Literal["pending", "in_progress", "blocked", "completed", "failed", "canceled"]
DecisionRequestStatus = Literal["collecting", "awaiting_user", "resolved", "canceled"]

_ACTIVE_RUN_STATUSES = {"planning", "running", "summarizing"}
_TERMINAL_RUN_STATUSES = {"completed", "completed_with_failures", "failed", "canceled"}


@dataclass(frozen=True)
class TeamRun:
    id: str
    goal: str
    status: TeamRunStatus
    run_mode: RunMode
    lifecycle_mode: LifecycleMode
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
class TeamRunCycle:
    id: str
    team_run_id: str
    sequence: int
    source_type: str
    source_id: str
    status: CycleStatus
    rounds_budget: int
    rounds_used: int
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
    cycle_id: str | None = None


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
    cycle_id: str | None = None


@dataclass(frozen=True)
class TeamDecisionRequest:
    id: str
    team_run_id: str
    status: DecisionRequestStatus
    revision: int
    items: list[dict[str, object]]
    answers: dict[str, str]
    file_path: str
    created_at: str
    published_at: str | None
    answered_at: str | None
    updated_at: str
    cycle_id: str | None = None


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
        lifecycle_mode: LifecycleMode = "standard",
    ) -> TeamRun:
        team_run_id = uuid4().hex
        now = _now()
        workspace_root_path = self._workspace_root / team_run_id
        workspace_root_path.mkdir(parents=True)
        workspace_root = str(workspace_root_path)
        self._db.execute(
            """
            insert into team_runs (
                id, goal, status, run_mode, lifecycle_mode, leader_agent_id, max_workers,
                rounds_budget, rounds_used, workspace_root, summary, error_message,
                created_at, started_at, finished_at, updated_at, team_id, rules_snapshot_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_run_id, goal, "draft", run_mode, lifecycle_mode, None, max_workers,
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
        lifecycle_mode: LifecycleMode = "standard",
    ) -> TeamRun:
        team = team_service.get_team(team_id)
        snapshot = rule_set_service.snapshot_for_team(team_id)
        if snapshot.get("team") is not None:
            snapshot["team"]["name"] = team.name
        return self.create_team_run(
            goal=goal,
            leader_persona_id=team.leader_persona_id,
            member_persona_ids=list(team.member_persona_ids),
            run_mode=run_mode,
            max_workers=max_workers,
            rounds_budget=rounds_budget,
            team_id=team_id,
            rules_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            lifecycle_mode=lifecycle_mode,
        )

    def get_team_run(self, team_run_id: str) -> TeamRun:
        row = self._db.fetchone("select * from team_runs where id = ?", (team_run_id,))
        if row is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        return _team_run_from_row(row)

    def create_cycle(
        self,
        team_run_id: str,
        source_type: str,
        source_id: str,
        rounds_budget: int | None = None,
    ) -> TeamRunCycle:
        run = self.get_team_run(team_run_id)
        if run.lifecycle_mode != "continuous":
            raise ValueError("Cycles require a continuous team run")
        normalized_source_type = source_type.strip()
        normalized_source_id = source_id.strip()
        if not normalized_source_type or not normalized_source_id:
            raise ValueError("Cycle source type and source id are required")
        normalized_budget = run.rounds_budget if rounds_budget is None else rounds_budget
        if normalized_budget < 1:
            raise ValueError("Cycle rounds budget must be positive")

        with self._db.connection() as connection:
            connection.execute("begin immediate")
            existing = connection.execute(
                """
                select * from team_run_cycles
                where team_run_id = ? and source_type = ? and source_id = ?
                """,
                (team_run_id, normalized_source_type, normalized_source_id),
            ).fetchone()
            if existing is not None:
                return _team_run_cycle_from_row(existing)
            row = connection.execute(
                "select coalesce(max(sequence), 0) + 1 as next from team_run_cycles "
                "where team_run_id = ?",
                (team_run_id,),
            ).fetchone()
            sequence = int(row["next"])
            cycle_id = uuid4().hex
            now = _now()
            connection.execute(
                """
                insert into team_run_cycles (
                    id, team_run_id, sequence, source_type, source_id, status,
                    rounds_budget, rounds_used, summary, error_message,
                    created_at, started_at, finished_at, updated_at
                ) values (?, ?, ?, ?, ?, 'queued', ?, 0, null, null, ?, null, null, ?)
                """,
                (
                    cycle_id,
                    team_run_id,
                    sequence,
                    normalized_source_type,
                    normalized_source_id,
                    normalized_budget,
                    now,
                    now,
                ),
            )
        return self.get_cycle(cycle_id)

    def get_cycle(self, cycle_id: str) -> TeamRunCycle:
        row = self._db.fetchone(
            "select * from team_run_cycles where id = ?", (cycle_id,)
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        return _team_run_cycle_from_row(row)

    def list_cycles(self, team_run_id: str) -> list[TeamRunCycle]:
        self.get_team_run(team_run_id)
        return [
            _team_run_cycle_from_row(row)
            for row in self._db.fetchall(
                "select * from team_run_cycles where team_run_id = ? order by sequence asc",
                (team_run_id,),
            )
        ]

    def list_source_cycles(self, source_type: str) -> list[TeamRunCycle]:
        return [
            _team_run_cycle_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_run_cycles
                where source_type = ?
                order by created_at asc, id asc
                """,
                (source_type,),
            )
        ]

    def get_cycle_for_source(
        self,
        source_type: str,
        source_id: str,
    ) -> TeamRunCycle | None:
        row = self._db.fetchone(
            """
            select * from team_run_cycles
            where source_type = ? and source_id = ?
            order by created_at asc, id asc limit 1
            """,
            (source_type, source_id),
        )
        return _team_run_cycle_from_row(row) if row is not None else None

    def increment_cycle_rounds_used(self, cycle_id: str) -> TeamRunCycle:
        self.get_cycle(cycle_id)
        self._db.execute(
            "update team_run_cycles set rounds_used = rounds_used + 1, updated_at = ? "
            "where id = ?",
            (_now(), cycle_id),
        )
        return self.get_cycle(cycle_id)

    def set_cycle_status(
        self,
        cycle_id: str,
        status: CycleStatus,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> TeamRunCycle:
        self.get_cycle(cycle_id)
        started_at = _now() if status == "running" else None
        finished_at = (
            _now()
            if status in {"completed", "completed_with_failures", "failed", "canceled"}
            else None
        )
        self._db.execute(
            """
            update team_run_cycles
            set status = ?, summary = ?, error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at), updated_at = ?
            where id = ?
            """,
            (
                status,
                summary,
                error_message,
                started_at,
                finished_at,
                _now(),
                cycle_id,
            ),
        )
        return self.get_cycle(cycle_id)

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
        return self._enrich_runs(self.list_team_runs())

    def page_team_runs_enriched(
        self, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[dict[str, object]], str | None]:
        clauses: list[str] = []
        parameters: list[object] = []
        if cursor:
            created_at, team_run_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(team_run_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(created_at < ? or (created_at = ? and id < ?))")
            parameters.extend((created_at, created_at, team_run_id))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        normalized_limit = max(1, min(limit, 200))
        rows = self._db.fetchall(
            f"select * from team_runs {where} "
            "order by created_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        runs = [_team_run_from_row(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return self._enrich_runs(runs), next_cursor

    def _enrich_runs(self, runs: list[TeamRun]) -> list[dict[str, object]]:
        if not runs:
            return []
        run_ids = [run.id for run in runs]
        placeholders = ", ".join("?" for _ in run_ids)
        agent_rows = self._db.fetchall(
            f"select * from team_agents where team_run_id in ({placeholders}) "
            "order by created_at asc, id asc",
            run_ids,
        )
        count_rows = self._db.fetchall(
            f"select team_run_id, status, count(*) as total from team_tasks "
            f"where team_run_id in ({placeholders}) group by team_run_id, status",
            run_ids,
        )
        agents_by_run: dict[str, list[TeamAgent]] = {run_id: [] for run_id in run_ids}
        for row in agent_rows:
            agent = _team_agent_from_row(row)
            agents_by_run[agent.team_run_id].append(agent)
        counts_by_run: dict[str, dict[str, int]] = {run_id: {} for run_id in run_ids}
        for row in count_rows:
            counts_by_run[row["team_run_id"]][row["status"]] = int(row["total"])

        result: list[dict[str, object]] = []
        for run in runs:
            agents = agents_by_run[run.id]
            leader = next((a for a in agents if a.role == "leader"), None)
            members = [a for a in agents if a.role != "leader"]
            counts = counts_by_run[run.id]
            result.append(
                {
                    "id": run.id,
                    "goal": run.goal,
                    "status": run.status,
                    "run_mode": run.run_mode,
                    "lifecycle_mode": run.lifecycle_mode,
                    "max_workers": 1,
                    "configured_max_workers": run.max_workers,
                    "execution_mode": "sequential",
                    "team_id": run.team_id,
                    "created_at": run.created_at,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "updated_at": run.updated_at,
                    "leader_name": leader.name if leader else None,
                    "leader": (
                        {
                            "name": leader.name,
                            "avatar": leader.persona_snapshot.get("avatar", ""),
                            "initials": _initials(leader.name),
                        }
                        if leader else None
                    ),
                    "members": [
                        {
                            "name": agent.name,
                            "avatar": agent.persona_snapshot.get("avatar", ""),
                            "initials": _initials(agent.name),
                        }
                        for agent in members
                    ],
                    "task_counts": counts,
                    "task_total": sum(counts.values()),
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
        with self._db.connection() as connection:
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
            cycle_statuses = ("running", "canceled") if include_canceled else ("running",)
            cycle_placeholders = ", ".join("?" for _ in cycle_statuses)
            active_cycle = connection.execute(
                f"""
                select id from team_run_cycles
                where team_run_id = ? and status in ({cycle_placeholders})
                order by sequence asc limit 1
                """,
                (team_run_id, *cycle_statuses),
            ).fetchone()
            cycle_id = active_cycle["id"] if active_cycle is not None else None
            if cycle_id is not None:
                connection.execute(
                    """
                    update team_run_cycles
                    set status = 'interrupted', error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, cycle_id),
                )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    cycle_id,
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
                "select * from team_agents where team_run_id = ? order by created_at asc, id asc",
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
                current_task_id = case
                    when ? in ('completed', 'failed', 'canceled') then null
                    else current_task_id
                end,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, status, started_at, finished_at, _now(), agent_id),
        )
        return self._get_agent(agent_id)

    def get_agent(self, agent_id: str) -> TeamAgent:
        return self._get_agent(agent_id)

    def get_task(self, task_id: str) -> TeamTask:
        return self._get_task(task_id)

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

    def reset_agent_reinvocations(self, team_run_id: str) -> None:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_agents set reinvocations = 0, updated_at = ? "
            "where team_run_id = ?",
            (_now(), team_run_id),
        )

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
        cycle_id: str | None = None,
    ) -> TeamTask:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        task_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id, status,
                result, error_message, created_at, updated_at, started_at, finished_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                team_run_id,
                cycle_id,
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

    def list_tasks(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> list[TeamTask]:
        self.get_team_run(team_run_id)
        where = "team_run_id = ?"
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            where += " and cycle_id = ?"
            parameters += (cycle_id,)
        return [
            _team_task_from_row(row)
            for row in self._db.fetchall(
                f"select * from team_tasks where {where} order by created_at asc, id asc",
                parameters,
            )
        ]

    def retry_failed_task(self, team_run_id: str, task_id: str) -> tuple[TeamRun, TeamTask]:
        now = _now()
        with self._db.connection() as connection:
            run = connection.execute(
                "select status from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] not in {"completed_with_failures", "failed"}:
                raise ValueError("Only failed terminal team runs can retry tasks")

            task = connection.execute(
                "select cycle_id, status, error_message from team_tasks "
                "where id = ? and team_run_id = ?",
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
            if task["cycle_id"] is not None:
                connection.execute(
                    """
                    update team_run_cycles
                    set status = 'interrupted', summary = null, error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, task["cycle_id"]),
                )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    task["cycle_id"],
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

    def start_task(self, task_id: str, agent_id: str) -> tuple[TeamTask, TeamAgent]:
        now = _now()
        with self._db.connection() as connection:
            task = connection.execute(
                "select team_run_id from team_tasks where id = ?", (task_id,)
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            connection.execute(
                """
                update team_tasks
                set owner_agent_id = ?, status = 'in_progress', result = null,
                    error_message = null, started_at = ?, finished_at = null, updated_at = ?
                where id = ?
                """,
                (agent_id, now, now, task_id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'running', current_task_id = ?,
                    started_at = coalesce(started_at, ?), finished_at = null, updated_at = ?
                where id = ?
                """,
                (task_id, now, now, agent_id),
            )
        return self._get_task(task_id), self._get_agent(agent_id)

    def finish_task(
        self,
        task_id: str,
        agent_id: str,
        status: Literal["completed", "failed", "canceled"],
        result: str | None = None,
        error_message: str | None = None,
    ) -> tuple[TeamTask, TeamAgent]:
        now = _now()
        with self._db.connection() as connection:
            task = connection.execute(
                "select team_run_id, owner_agent_id from team_tasks where id = ?", (task_id,)
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            connection.execute(
                """
                update team_tasks
                set status = ?, result = ?, error_message = ?, finished_at = ?, updated_at = ?
                where id = ?
                """,
                (status, result, error_message, now, now, task_id),
            )
            connection.execute(
                """
                update team_agents
                set status = ?, current_task_id = null, finished_at = ?, updated_at = ?
                where id = ?
                """,
                (status, now, now, agent_id),
            )
        return self._get_task(task_id), self._get_agent(agent_id)

    def get_active_decision_request(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamDecisionRequest | None:
        self.get_team_run(team_run_id)
        cycle_clause = ""
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            cycle_clause = "and cycle_id = ?"
            parameters += (cycle_id,)
        row = self._db.fetchone(
            f"""
            select * from team_decision_requests
            where team_run_id = ? {cycle_clause}
              and status in ('collecting', 'awaiting_user')
            order by created_at desc limit 1
            """,
            parameters,
        )
        return _team_decision_request_from_row(row) if row is not None else None

    def list_decision_requests(self, team_run_id: str) -> list[TeamDecisionRequest]:
        self.get_team_run(team_run_id)
        return [
            _team_decision_request_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_decision_requests
                where team_run_id = ? order by created_at asc, id asc
                """,
                (team_run_id,),
            )
        ]

    def defer_task_for_user_decision(
        self,
        task_id: str,
        agent_id: str,
        decision: dict[str, object],
    ) -> TeamDecisionRequest:
        now = _now()
        with self._db.connection() as connection:
            task = connection.execute(
                "select team_run_id, cycle_id, status from team_tasks where id = ?",
                (task_id,),
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            if task["status"] != "in_progress":
                raise ValueError("Only in-progress tasks can wait for a user decision")

            team_run_id = task["team_run_id"]
            cycle_id = task["cycle_id"]
            request_id = self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                decision,
                now,
                blocking_task_id=task_id,
                stage="task",
            )
            connection.execute(
                """
                update team_tasks
                set status = 'blocked', result = null, error_message = null,
                    finished_at = null, updated_at = ? where id = ?
                """,
                (now, task_id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'waiting', current_task_id = null, finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, agent_id),
            )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return request

    def defer_run_for_user_decision(
        self,
        team_run_id: str,
        decision: dict[str, object],
        *,
        stage: Literal["planning", "synthesis"],
        cycle_id: str | None = None,
    ) -> TeamDecisionRequest:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        now = _now()
        with self._db.connection() as connection:
            request_id = self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                decision,
                now,
                blocking_task_id=None,
                stage=stage,
            )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return request

    def _append_decision_item(
        self,
        connection,
        team_run_id: str,
        cycle_id: str | None,
        decision: dict[str, object],
        now: str,
        *,
        blocking_task_id: str | None,
        stage: Literal["task", "planning", "synthesis"],
    ) -> str:
        cycle_clause = "cycle_id is null" if cycle_id is None else "cycle_id = ?"
        parameters: tuple[object, ...] = (
            (team_run_id,) if cycle_id is None else (team_run_id, cycle_id)
        )
        row = connection.execute(
            f"""
            select * from team_decision_requests
            where team_run_id = ? and {cycle_clause}
              and status in ('collecting', 'awaiting_user')
            order by created_at desc limit 1
            """,
            parameters,
        ).fetchone()
        if row is not None and row["status"] != "collecting":
            raise ValueError("Decision request is already awaiting user input")

        if row is None:
            request_id = uuid4().hex
            items: list[dict[str, object]] = []
            revision = 0
            connection.execute(
                """
                insert into team_decision_requests (
                    id, team_run_id, cycle_id, status, revision, items_json, answers_json,
                    file_path, created_at, published_at, answered_at, updated_at
                ) values (?, ?, ?, 'collecting', 0, '[]', '{}', 'USER_DECISIONS.md', ?, null, null, ?)
                """,
                (request_id, team_run_id, cycle_id, now, now),
            )
        else:
            request_id = row["id"]
            items = json.loads(row["items_json"])
            revision = row["revision"]

        topic = str(decision.get("topic") or "").strip()
        question = str(decision.get("question") or "").strip()
        if not question:
            raise ValueError("User decision requires a question")
        duplicate = next(
            (
                item
                for item in items
                if item.get("topic") == topic and item.get("question") == question
            ),
            None,
        )
        query_message_id = decision.get("query_message_id")
        if duplicate is not None:
            blocking_ids = list(duplicate.get("blocking_task_ids") or [])
            if blocking_task_id is not None and blocking_task_id not in blocking_ids:
                blocking_ids.append(blocking_task_id)
            duplicate["blocking_task_ids"] = blocking_ids
            query_ids = list(duplicate.get("query_message_ids") or [])
            if isinstance(query_message_id, str) and query_message_id not in query_ids:
                query_ids.append(query_message_id)
            duplicate["query_message_ids"] = query_ids
        else:
            items.append(
                {
                    "id": f"Q-{len(items) + 1:03d}",
                    "stage": stage,
                    "topic": topic,
                    "question": question,
                    "why_needed": str(decision.get("why_needed") or "").strip(),
                    "options": list(decision.get("options") or []),
                    "recommended_option_id": decision.get("recommended_option_id"),
                    "blocking_scope": (
                        "run"
                        if blocking_task_id is None or decision.get("blocking_scope") == "run"
                        else "task"
                    ),
                    "blocking_task_ids": (
                        [blocking_task_id] if blocking_task_id is not None else []
                    ),
                    "query_message_ids": (
                        [query_message_id] if isinstance(query_message_id, str) else []
                    ),
                }
            )

        connection.execute(
            """
            update team_decision_requests
            set items_json = ?, revision = ?, updated_at = ? where id = ?
            """,
            (
                json.dumps(items, ensure_ascii=False, sort_keys=True),
                revision + 1,
                now,
                request_id,
            ),
        )
        return request_id

    def publish_decision_request(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamDecisionRequest:
        now = _now()
        cycle_clause = ""
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            cycle_clause = "and cycle_id = ?"
            parameters += (cycle_id,)
        with self._db.connection() as connection:
            row = connection.execute(
                f"""
                select * from team_decision_requests
                where team_run_id = ? {cycle_clause} and status = 'collecting'
                order by created_at desc limit 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                raise ValueError("No collecting decision request")
            items = json.loads(row["items_json"])
            if not items:
                raise ValueError("Cannot publish an empty decision request")
            connection.execute(
                """
                update team_decision_requests
                set status = 'awaiting_user', revision = revision + 1,
                    published_at = ?, updated_at = ? where id = ?
                """,
                (now, now, row["id"]),
            )
            connection.execute(
                """
                update team_runs
                set status = 'waiting_for_user', error_message = null,
                    finished_at = null, updated_at = ? where id = ?
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'waiting', current_task_id = null, finished_at = null, updated_at = ?
                where team_run_id = ? and status = 'running'
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, 'user_decision_requested', ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    row["cycle_id"],
                    f"User input requested for {len(items)} decision(s).",
                    json.dumps(
                        {"request_id": row["id"], "question_count": len(items)},
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        request = self._get_decision_request(row["id"])
        self._project_decisions_safely(team_run_id)
        return request

    def answer_decision_request(
        self,
        team_run_id: str,
        request_id: str,
        revision: int,
        answers: dict[str, str],
    ) -> tuple[TeamRun, TeamDecisionRequest]:
        now = _now()
        with self._db.connection() as connection:
            run = connection.execute(
                "select status, leader_agent_id from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            row = connection.execute(
                "select * from team_decision_requests where id = ? and team_run_id = ?",
                (request_id, team_run_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Decision request not found: {request_id}")
            if run["status"] != "waiting_for_user" or row["status"] != "awaiting_user":
                raise ValueError("Decision request is no longer awaiting user input")
            if row["revision"] != revision:
                raise ValueError("Decision request revision is stale")
            items = json.loads(row["items_json"])
            required_ids = {item["id"] for item in items}
            normalized = {
                key: value.strip()
                for key, value in answers.items()
                if key in required_ids and isinstance(value, str) and value.strip()
            }
            if set(normalized) != required_ids:
                raise ValueError("Every open decision requires an answer")
            blocking_task_ids = {
                task_id
                for item in items
                for task_id in item.get("blocking_task_ids", [])
                if isinstance(task_id, str)
            }
            connection.execute(
                """
                update team_decision_requests
                set status = 'resolved', revision = revision + 1, answers_json = ?,
                    answered_at = ?, updated_at = ? where id = ?
                """,
                (
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    request_id,
                ),
            )
            if blocking_task_ids:
                placeholders = ", ".join("?" for _ in blocking_task_ids)
                connection.execute(
                    f"""
                    update team_tasks
                    set status = 'pending', result = null, error_message = null,
                        started_at = null, finished_at = null, updated_at = ?
                    where team_run_id = ? and status = 'blocked'
                      and id in ({placeholders})
                    """,
                    (now, team_run_id, *sorted(blocking_task_ids)),
                )
            connection.execute(
                """
                update team_agents
                set status = 'pending', current_task_id = null, finished_at = null, updated_at = ?
                where team_run_id = ? and status = 'waiting'
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                update team_runs
                set status = 'running', summary = null, error_message = null,
                    finished_at = null, updated_at = ? where id = ?
                """,
                (now, team_run_id),
            )
            if row["cycle_id"] is not None:
                connection.execute(
                    """
                    update team_run_cycles
                    set status = 'interrupted', error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, row["cycle_id"]),
                )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, 'user_decision_answer', ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    row["cycle_id"],
                    f"User answered {len(normalized)} decision(s).",
                    json.dumps(
                        {"request_id": request_id, "question_count": len(normalized)},
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            for item in items:
                answer = normalized[str(item["id"])]
                for query_id in item.get("query_message_ids", []):
                    query = connection.execute(
                        """
                        select sender_agent_id from team_messages
                        where id = ? and team_run_id = ? and kind = 'query'
                        """,
                        (query_id, team_run_id),
                    ).fetchone()
                    if query is None:
                        continue
                    connection.execute(
                        """
                        insert into team_messages (
                            id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                            kind, content, metadata_json, created_at
                        ) values (?, ?, ?, ?, ?, 'answer', ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            team_run_id,
                            row["cycle_id"],
                            run["leader_agent_id"],
                            query["sender_agent_id"],
                            answer,
                            json.dumps(
                                {
                                    "query_id": query_id,
                                    "request_id": request_id,
                                    "source": "user_decision",
                                },
                                sort_keys=True,
                            ),
                            now,
                        ),
                    )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return self.get_team_run(team_run_id), request

    def decision_context_for_task(self, team_run_id: str, task_id: str) -> str:
        lines: list[str] = []
        for request in self.list_decision_requests(team_run_id):
            if request.status != "resolved":
                continue
            for item in request.items:
                if task_id not in item.get("blocking_task_ids", []):
                    continue
                answer = request.answers.get(str(item.get("id")))
                if answer:
                    lines.append(f"Q: {item.get('question', '')}\nA: {answer}")
        return "\n\n".join(lines)

    def decision_context_for_run(
        self,
        team_run_id: str,
        *,
        stage: Literal["planning", "synthesis"],
        cycle_id: str | None = None,
    ) -> str:
        lines: list[str] = []
        for request in self.list_decision_requests(team_run_id):
            if request.status != "resolved" or request.cycle_id != cycle_id:
                continue
            for item in request.items:
                if item.get("stage") != stage:
                    continue
                answer = request.answers.get(str(item.get("id")))
                if answer:
                    lines.append(f"Q: {item.get('question', '')}\nA: {answer}")
        return "\n\n".join(lines)

    def cancel_waiting_decision(self, team_run_id: str) -> TeamRun:
        now = _now()
        with self._db.connection() as connection:
            run = connection.execute(
                "select status from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] != "waiting_for_user":
                raise ValueError("Team run is not waiting for user input")
            connection.execute(
                """
                update team_run_cycles
                set status = 'canceled', finished_at = ?, updated_at = ?
                where id in (
                    select cycle_id from team_decision_requests
                    where team_run_id = ? and status = 'awaiting_user'
                      and cycle_id is not null
                )
                """,
                (now, now, team_run_id),
            )
            connection.execute(
                """
                update team_decision_requests
                set status = 'canceled', revision = revision + 1, updated_at = ?
                where team_run_id = ? and status = 'awaiting_user'
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                update team_tasks set status = 'canceled', finished_at = ?, updated_at = ?
                where team_run_id = ? and status = 'blocked'
                """,
                (now, now, team_run_id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'canceled', current_task_id = null, finished_at = ?, updated_at = ?
                where team_run_id = ? and status = 'waiting'
                """,
                (now, now, team_run_id),
            )
            connection.execute(
                """
                update team_runs set status = 'canceled', finished_at = ?, updated_at = ?
                where id = ?
                """,
                (now, now, team_run_id),
            )
        self._project_decisions_safely(team_run_id)
        return self.get_team_run(team_run_id)

    def append_message(
        self,
        team_run_id: str,
        sender_agent_id: str | None,
        recipient_agent_id: str | None,
        kind: str,
        content: str,
        metadata: dict[str, object],
        cycle_id: str | None = None,
    ) -> TeamMessage:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        message_id = uuid4().hex
        self._db.execute(
            """
            insert into team_messages (
                id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id, kind,
                content, metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                team_run_id,
                cycle_id,
                sender_agent_id,
                recipient_agent_id,
                kind,
                content,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        return self._get_message(message_id)

    def _require_cycle_for_run(self, team_run_id: str, cycle_id: str) -> TeamRunCycle:
        cycle = self.get_cycle(cycle_id)
        if cycle.team_run_id != team_run_id:
            raise ValueError("Cycle belongs to a different team run")
        return cycle

    def list_messages(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> list[TeamMessage]:
        self.get_team_run(team_run_id)
        where = "team_run_id = ?"
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            where += " and cycle_id = ?"
            parameters += (cycle_id,)
        return [
            _team_message_from_row(row)
            for row in self._db.fetchall(
                f"select * from team_messages where {where} order by created_at asc, id asc",
                parameters,
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

    def _get_decision_request(self, request_id: str) -> TeamDecisionRequest:
        row = self._db.fetchone(
            "select * from team_decision_requests where id = ?", (request_id,)
        )
        if row is None:
            raise KeyError(f"Decision request not found: {request_id}")
        return _team_decision_request_from_row(row)

    def _project_decisions_safely(self, team_run_id: str) -> None:
        try:
            self._project_decisions(team_run_id)
        except OSError as exc:
            self.append_message(
                team_run_id,
                None,
                None,
                "document_projection_error",
                "Could not update USER_DECISIONS.md.",
                {"error_type": type(exc).__name__},
            )

    def _project_decisions(self, team_run_id: str) -> None:
        run = self.get_team_run(team_run_id)
        requests = self.list_decision_requests(team_run_id)
        if not requests:
            return
        current = next(
            (
                request
                for request in reversed(requests)
                if request.status in {"collecting", "awaiting_user"}
            ),
            requests[-1],
        )
        lines = [
            "---",
            "schema: gateway.team-decisions/v1",
            f"team_run_id: {team_run_id}",
            f"active_request_id: {current.id}",
            f"revision: {current.revision}",
            f"status: {current.status}",
            f"generated_at: {_now()}",
            "---",
            "",
            "# User decisions",
            "",
            "Team Run 화면의 INPUT NEEDED에서 답변하세요. 이 파일은 자동 생성됩니다.",
            "",
        ]
        for request in reversed(requests):
            heading = "Active request" if request.status in {"collecting", "awaiting_user"} else "History"
            lines.extend([f"## {heading} — {request.id}", ""])
            for item in request.items:
                item_id = str(item.get("id") or "")
                answer = request.answers.get(item_id)
                lines.extend(
                    [
                        f"### {item_id} — {item.get('topic') or 'Decision'}",
                        "",
                        f"- Status: {'answered' if answer else 'open'}",
                        f"- Stage: {item.get('stage') or 'task'}",
                        f"- Blocks: {', '.join(item.get('blocking_task_ids') or []) or '-'}",
                        f"- Why now: {item.get('why_needed') or '-'}",
                        f"- Question: {item.get('question') or '-'}",
                    ]
                )
                recommended = item.get("recommended_option_id")
                if recommended:
                    lines.append(f"- Recommended: {recommended}")
                options = item.get("options") or []
                if options:
                    lines.extend(["", "#### Options", ""])
                    for option in options:
                        lines.append(
                            f"- `{option.get('id', '')}` — {option.get('label', '')}: "
                            f"{option.get('impact', '')}"
                        )
                lines.extend(["", "#### Answer", "", answer or "Pending", ""])
        target = Path(run.workspace_root) / current.file_path
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)

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
        lifecycle_mode=(
            row["lifecycle_mode"] if "lifecycle_mode" in row.keys() else "standard"
        ),
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


def _team_run_cycle_from_row(row: object) -> TeamRunCycle:
    return TeamRunCycle(
        id=row["id"],
        team_run_id=row["team_run_id"],
        sequence=row["sequence"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        status=row["status"],
        rounds_budget=row["rounds_budget"],
        rounds_used=row["rounds_used"],
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
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
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
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
    )


def _team_decision_request_from_row(row: object) -> TeamDecisionRequest:
    return TeamDecisionRequest(
        id=row["id"],
        team_run_id=row["team_run_id"],
        status=row["status"],
        revision=row["revision"],
        items=json.loads(row["items_json"]),
        answers=json.loads(row["answers_json"]),
        file_path=row["file_path"],
        created_at=row["created_at"],
        published_at=row["published_at"],
        answered_at=row["answered_at"],
        updated_at=row["updated_at"],
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
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
