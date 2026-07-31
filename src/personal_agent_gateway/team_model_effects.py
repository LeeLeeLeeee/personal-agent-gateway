from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.team_acceptance import (
    AcceptanceResult,
    is_recoverable_acceptance_failure,
)
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    OperationResultValidatorRegistry,
    StaleOperation,
    TeamModelOperation,
    TeamModelOperationService,
)
from personal_agent_gateway.team_outcomes import TaskOutcomeError, parse_task_outcome
from personal_agent_gateway.teams import (
    TaskAcceptance,
    TeamAgent,
    TeamDecisionRequest,
    TeamMessage,
    TeamRunService,
    TeamTask,
    _validate_task_acceptance,
)


_PLAN_STAGES = {
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
}


@dataclass(frozen=True)
class WorkerEffectResult:
    task: TeamTask
    agent: TeamAgent
    next_stage: Literal[
        "acceptance_lead",
        "mediation_lead",
        "user_decision",
    ] | None
    message: TeamMessage | None = None
    decision_request: TeamDecisionRequest | None = None


class TeamModelEffectService:
    def __init__(
        self,
        db: Database,
        teams: TeamRunService,
        operations: TeamModelOperationService,
    ) -> None:
        self._db = db
        self._teams = teams
        self._operations = operations

    def apply_plan(self, operation_id: str) -> list[TeamTask]:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            self._validate_plan_operation(connection, operation)
            if operation.status == "applied":
                return self._replay_plan(connection, operation)
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )

            specs = _plan_specs(operation)
            tasks = [
                self._create_task(connection, operation, spec, now)
                for spec in specs
            ]
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, null, 'plan_note', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    operation.agent_id,
                    f"Planning completed with {len(tasks)} tasks.",
                    json.dumps(
                        {"operation_id": operation.id},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="task_plan",
                effect_ref={
                    "task_ids": [task.id for task in tasks],
                    "message_id": message_id,
                },
                now=now,
            )
            return tasks

    def apply_worker_outcome(
        self,
        operation_id: str,
        acceptance: AcceptanceResult | None,
        *,
        workspace_changes: Mapping[str, object],
    ) -> WorkerEffectResult:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            task, agent = self._validate_worker_operation(connection, operation)
            normalized_changes = _workspace_changes(workspace_changes)
            input_digest = _worker_input_digest(
                operation,
                acceptance,
                normalized_changes,
            )
            if operation.status == "applied":
                return self._replay_worker(
                    connection,
                    operation,
                    input_digest,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            if task.status != "in_progress":
                raise OperationConflict("Worker task is not in progress")
            if agent.status != "running" or agent.current_task_id != task.id:
                raise OperationConflict("Worker is not running the operation task")

            if operation.result_kind == "user_decision":
                result = self._apply_worker_decision(
                    connection,
                    operation,
                    task,
                    agent,
                    now,
                )
            else:
                if acceptance is None:
                    raise ValueError("Acceptance result is required for a task outcome")
                result = self._apply_task_outcome(
                    connection,
                    operation,
                    task,
                    agent,
                    acceptance,
                    normalized_changes,
                    now,
                )
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type=operation.result_kind or "worker_outcome",
                effect_ref=_worker_effect_ref(
                    connection,
                    operation,
                    result,
                    input_digest,
                ),
                now=now,
            )
            return result

    def apply_worker_query(self, operation_id: str) -> WorkerEffectResult:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            task, agent = self._validate_worker_operation(connection, operation)
            query = _worker_query(operation)
            if operation.status == "applied":
                return self._replay_worker_query(
                    connection,
                    operation,
                    task,
                    agent,
                    query,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            if task.status != "in_progress":
                raise OperationConflict("Worker task is not in progress")
            if agent.status != "running" or agent.current_task_id != task.id:
                raise OperationConflict("Worker is not running the operation task")

            run = connection.execute(
                "select leader_agent_id from team_runs where id = ?",
                (operation.team_run_id,),
            ).fetchone()
            if run is None:
                raise OperationConflict("Worker query run does not exist")
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, 'query', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    operation.agent_id,
                    run["leader_agent_id"],
                    query["question"],
                    json.dumps(
                        {
                            "operation_id": operation.id,
                            "task_id": task.id,
                            "topic": query["topic"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="worker_query",
                effect_ref={
                    "task_id": task.id,
                    "agent_id": agent.id,
                    "message_id": message_id,
                    "next_stage": "mediation_lead",
                },
                now=now,
            )
            return WorkerEffectResult(
                task=self._teams._task_from_connection(connection, task.id),
                agent=self._teams._agent_from_connection(connection, agent.id),
                next_stage="mediation_lead",
                message=self._teams._message_from_connection(connection, message_id),
            )

    def apply_synthesis(
        self,
        operation_id: str,
        summary: str,
    ) -> str:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            self._validate_synthesis_operation(connection, operation)
            stored_summary = _synthesis_summary(operation)
            if summary != stored_summary:
                raise OperationConflict(
                    "Synthesis summary does not match the completed operation"
                )
            if operation.status == "applied":
                return self._replay_synthesis(
                    connection,
                    operation,
                    stored_summary,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )

            run = connection.execute(
                "select status from team_runs where id = ?",
                (operation.team_run_id,),
            ).fetchone()
            cycle = connection.execute(
                "select status from team_run_cycles where id = ?",
                (operation.cycle_id,),
            ).fetchone()
            if (
                run is None
                or run["status"] != "summarizing"
                or cycle is None
                or cycle["status"] != "running"
            ):
                raise OperationConflict("Team state is not ready for synthesis")
            task_rows = connection.execute(
                """
                select status, required from team_tasks
                where team_run_id = ? and cycle_id = ?
                order by created_at asc, id asc
                """,
                (operation.team_run_id, operation.cycle_id),
            ).fetchall()
            terminal_status = _terminal_status(task_rows)
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, null, 'synthesis', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    operation.agent_id,
                    stored_summary,
                    json.dumps(
                        {"operation_id": operation.id},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            connection.execute(
                """
                update team_runs
                set status = ?, summary = ?, error_message = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (
                    terminal_status,
                    stored_summary,
                    now,
                    now,
                    operation.team_run_id,
                ),
            )
            connection.execute(
                """
                update team_run_cycles
                set status = ?, summary = ?, error_message = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (
                    terminal_status,
                    stored_summary,
                    now,
                    now,
                    operation.cycle_id,
                ),
            )
            connection.execute(
                """
                update team_agents
                set status = 'completed', current_task_id = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (now, now, operation.agent_id),
            )
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="synthesis",
                effect_ref={
                    "run_id": operation.team_run_id,
                    "cycle_id": operation.cycle_id,
                    "agent_id": operation.agent_id,
                    "message_id": message_id,
                    "status": terminal_status,
                },
                now=now,
            )
            return stored_summary

    def apply_synthesis_decision(
        self,
        operation_id: str,
    ) -> TeamDecisionRequest:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            self._validate_synthesis_decision_operation(connection, operation)
            decision = _user_decision(operation)
            if operation.status == "applied":
                return self._replay_synthesis_decision(
                    connection,
                    operation,
                    decision,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            self._validate_synthesis_ready(connection, operation)
            request_id = self._teams._append_decision_item(
                connection,
                operation.team_run_id,
                operation.cycle_id,
                decision,
                now,
                blocking_task_id=None,
                stage="synthesis",
            )
            request = self._teams._decision_request_from_connection(
                connection,
                request_id,
            )
            matching_items = [
                item
                for item in request.items
                if _run_decision_item_matches(item, decision)
            ]
            if len(matching_items) != 1:
                raise OperationConflict(
                    "Synthesis decision does not have one exact request item"
                )
            item = matching_items[0]
            item_id = item.get("id")
            if not isinstance(item_id, str):
                raise OperationConflict("Synthesis decision item ID is invalid")
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="user_decision",
                effect_ref={
                    "run_id": operation.team_run_id,
                    "cycle_id": operation.cycle_id,
                    "agent_id": operation.agent_id,
                    "decision_request_id": request.id,
                    "decision_item_id": item_id,
                    "decision_item_digest": _canonical_digest(item),
                },
                now=now,
            )
            return request

    def _validate_synthesis_decision_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> None:
        if (
            operation.stage != "cycle_synthesis"
            or operation.task_id is not None
            or operation.result_kind != "user_decision"
        ):
            raise OperationConflict("Operation is not a synthesis decision stage")
        self._validate_synthesis_actor(connection, operation)

    def _validate_synthesis_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> None:
        if (
            operation.stage != "cycle_synthesis"
            or operation.task_id is not None
            or operation.result_kind != "synthesis"
        ):
            raise OperationConflict("Operation is not a synthesis stage")
        self._validate_synthesis_actor(connection, operation)

    def _validate_synthesis_actor(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> None:
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        agent = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if run is None or run["leader_agent_id"] != operation.agent_id:
            raise OperationConflict("Synthesis actor is not the team lead")
        if cycle is None or cycle["team_run_id"] != operation.team_run_id:
            raise OperationConflict("Synthesis cycle does not belong to the run")
        if agent.team_run_id != operation.team_run_id:
            raise OperationConflict("Synthesis actor does not belong to the run")

    def _validate_synthesis_ready(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> None:
        run = connection.execute(
            "select status from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            "select status from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        if (
            run is None
            or run["status"] != "summarizing"
            or cycle is None
            or cycle["status"] != "running"
        ):
            raise OperationConflict("Team state is not ready for synthesis")
        task_rows = connection.execute(
            """
            select status, required from team_tasks
            where team_run_id = ? and cycle_id = ?
            order by created_at asc, id asc
            """,
            (operation.team_run_id, operation.cycle_id),
        ).fetchall()
        _terminal_status(task_rows)

    def _replay_synthesis_decision(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        decision: dict[str, object],
    ) -> TeamDecisionRequest:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "user_decision"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {
                "run_id",
                "cycle_id",
                "agent_id",
                "decision_request_id",
                "decision_item_id",
                "decision_item_digest",
            }
            or effect_ref["run_id"] != operation.team_run_id
            or effect_ref["cycle_id"] != operation.cycle_id
            or effect_ref["agent_id"] != operation.agent_id
            or not isinstance(effect_ref["decision_request_id"], str)
            or not isinstance(effect_ref["decision_item_id"], str)
            or not isinstance(effect_ref["decision_item_digest"], str)
        ):
            raise OperationConflict(
                "Applied synthesis decision reference is invalid"
            )
        self._validate_synthesis_ready(connection, operation)
        request = self._teams._decision_request_from_connection(
            connection,
            effect_ref["decision_request_id"],
        )
        agent = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        item = next(
            (
                candidate
                for candidate in request.items
                if candidate.get("id") == effect_ref["decision_item_id"]
            ),
            None,
        )
        if (
            request.team_run_id != operation.team_run_id
            or request.cycle_id != operation.cycle_id
            or request.status != "collecting"
            or request.answers != {}
            or request.published_at is not None
            or request.answered_at is not None
            or agent.status != "running"
            or agent.current_task_id is not None
            or not _operation_session_matches(operation, agent)
            or item is None
            or _canonical_digest(item) != effect_ref["decision_item_digest"]
            or not _run_decision_item_matches(item, decision)
        ):
            raise OperationConflict(
                "Applied synthesis decision rows do not match"
            )
        return request

    def _replay_synthesis(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        summary: str,
    ) -> str:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "synthesis"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {"run_id", "cycle_id", "agent_id", "message_id", "status"}
            or effect_ref["run_id"] != operation.team_run_id
            or effect_ref["cycle_id"] != operation.cycle_id
            or effect_ref["agent_id"] != operation.agent_id
            or not isinstance(effect_ref["message_id"], str)
            or effect_ref["status"]
            not in {
                "completed",
                "completed_with_failures",
                "blocked",
                "failed",
            }
        ):
            raise OperationConflict("Applied synthesis effect reference is invalid")
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        run = connection.execute(
            "select status, summary, finished_at from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            """
            select status, summary, finished_at from team_run_cycles where id = ?
            """,
            (operation.cycle_id,),
        ).fetchone()
        agent = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        task_rows = connection.execute(
            """
            select status, required from team_tasks
            where team_run_id = ? and cycle_id = ?
            order by created_at asc, id asc
            """,
            (operation.team_run_id, operation.cycle_id),
        ).fetchall()
        terminal_status = _terminal_status(task_rows)
        if (
            message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id is not None
            or message.kind != "synthesis"
            or message.content != summary
            or message.metadata != {"operation_id": operation.id}
            or effect_ref["status"] != terminal_status
            or run is None
            or run["status"] != effect_ref["status"]
            or run["summary"] != summary
            or run["finished_at"] is None
            or cycle is None
            or cycle["status"] != effect_ref["status"]
            or cycle["summary"] != summary
            or cycle["finished_at"] is None
            or agent.status != "completed"
            or agent.current_task_id is not None
            or agent.finished_at is None
            or not _operation_session_matches(operation, agent)
        ):
            raise OperationConflict(
                "Applied synthesis rows do not match the operation"
            )
        return summary

    def _validate_worker_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> tuple[TeamTask, TeamAgent]:
        if operation.stage != "worker_execution" or operation.task_id is None:
            raise OperationConflict("Operation is not a Worker execution stage")
        if operation.result_kind not in {
            "task_outcome",
            "user_decision",
            "worker_query",
        }:
            raise OperationConflict("Completed Worker result kind is invalid")
        task = self._teams._task_from_connection(connection, operation.task_id)
        agent = self._teams._agent_from_connection(connection, operation.agent_id)
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or task.owner_agent_id != operation.agent_id
        ):
            raise OperationConflict("Worker task ownership does not match the operation")
        if (
            agent.team_run_id != operation.team_run_id
            or run is None
            or run["leader_agent_id"] == operation.agent_id
        ):
            raise OperationConflict("Worker actor does not match the operation")
        if cycle is None or cycle["team_run_id"] != operation.team_run_id:
            raise OperationConflict("Worker cycle does not belong to the team run")
        return task, agent

    def _replay_worker_query(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        agent: TeamAgent,
        query: dict[str, object],
    ) -> WorkerEffectResult:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "worker_query"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {"task_id", "agent_id", "message_id", "next_stage"}
            or effect_ref["task_id"] != operation.task_id
            or effect_ref["agent_id"] != operation.agent_id
            or effect_ref["next_stage"] != "mediation_lead"
            or not isinstance(effect_ref["message_id"], str)
        ):
            raise OperationConflict("Applied Worker query reference is invalid")
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        if (
            task.status != "in_progress"
            or agent.status != "running"
            or agent.current_task_id != task.id
            or not _operation_session_matches(operation, agent)
            or run is None
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id != run["leader_agent_id"]
            or message.kind != "query"
            or message.content != query["question"]
            or message.metadata
            != {
                "operation_id": operation.id,
                "task_id": task.id,
                "topic": query["topic"],
            }
        ):
            raise OperationConflict("Applied Worker query rows do not match")
        return WorkerEffectResult(
            task=task,
            agent=agent,
            next_stage="mediation_lead",
            message=message,
        )

    def _apply_task_outcome(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        agent: TeamAgent,
        acceptance: AcceptanceResult,
        workspace_changes: dict[str, list[str]],
        now: str,
    ) -> WorkerEffectResult:
        outcome = _task_outcome(operation)
        _validate_acceptance_result(acceptance)
        outcome_payload = asdict(outcome)
        acceptance_payload = asdict(acceptance)
        connection.execute(
            """
            update team_tasks
            set outcome_json = ?, acceptance_result_json = ?, updated_at = ?
            where id = ?
            """,
            (
                json.dumps(outcome_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(acceptance_payload, ensure_ascii=False, sort_keys=True),
                now,
                task.id,
            ),
        )
        message_id = uuid4().hex
        connection.execute(
            """
            insert into team_messages (
                id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                kind, content, metadata_json, created_at
            ) values (?, ?, ?, ?, null, 'agent_output', ?, ?, ?)
            """,
            (
                message_id,
                operation.team_run_id,
                operation.cycle_id,
                operation.agent_id,
                outcome.summary,
                json.dumps(
                    {
                        "operation_id": operation.id,
                        "task_id": task.id,
                        "outcome_status": outcome.status,
                        "reason_code": outcome.reason_code,
                        **workspace_changes,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                now,
            ),
        )

        next_stage: Literal["acceptance_lead"] | None = None
        if acceptance.accepted:
            connection.execute(
                """
                update team_tasks
                set status = 'completed', result = ?, error_message = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (outcome.summary, now, now, task.id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'completed', current_task_id = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (now, now, agent.id),
            )
        elif is_recoverable_acceptance_failure(acceptance.reason_code):
            next_stage = "acceptance_lead"
        else:
            connection.execute(
                """
                update team_tasks
                set status = 'failed', result = null, error_message = ?,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (acceptance.reason_code or outcome.reason_code, now, now, task.id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'failed', current_task_id = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (now, now, agent.id),
            )
        return WorkerEffectResult(
            task=self._teams._task_from_connection(connection, task.id),
            agent=self._teams._agent_from_connection(connection, agent.id),
            next_stage=next_stage,
            message=self._teams._message_from_connection(connection, message_id),
        )

    def _apply_worker_decision(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        agent: TeamAgent,
        now: str,
    ) -> WorkerEffectResult:
        decision = _user_decision(operation)
        request_id = self._teams._append_decision_item(
            connection,
            operation.team_run_id,
            operation.cycle_id,
            decision,
            now,
            blocking_task_id=task.id,
            stage="task",
        )
        connection.execute(
            """
            update team_tasks
            set status = 'blocked', result = null, error_message = null,
                finished_at = null, updated_at = ? where id = ?
            """,
            (now, task.id),
        )
        connection.execute(
            """
            update team_agents
            set status = 'waiting', current_task_id = null, finished_at = null,
                updated_at = ? where id = ?
            """,
            (now, agent.id),
        )
        return WorkerEffectResult(
            task=self._teams._task_from_connection(connection, task.id),
            agent=self._teams._agent_from_connection(connection, agent.id),
            next_stage="user_decision",
            decision_request=self._teams._decision_request_from_connection(
                connection,
                request_id,
            ),
        )

    def _replay_worker(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        input_digest: str,
    ) -> WorkerEffectResult:
        effect_ref = operation.effect_ref_json
        if operation.result_kind == "task_outcome":
            return self._replay_task_outcome(
                connection,
                operation,
                effect_ref,
                input_digest,
            )
        if operation.result_kind == "user_decision":
            return self._replay_worker_decision(
                connection,
                operation,
                effect_ref,
                input_digest,
            )
        raise OperationConflict("Applied Worker result kind is invalid")

    def _replay_task_outcome(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        effect_ref: dict[str, object] | None,
        input_digest: str,
    ) -> WorkerEffectResult:
        if (
            operation.effect_type != "task_outcome"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {
                "task_id",
                "agent_id",
                "next_stage",
                "message_id",
                "input_digest",
            }
            or effect_ref["task_id"] != operation.task_id
            or effect_ref["agent_id"] != operation.agent_id
            or effect_ref["next_stage"] not in {None, "acceptance_lead"}
            or not isinstance(effect_ref["message_id"], str)
            or effect_ref["input_digest"] != input_digest
        ):
            raise OperationConflict("Applied task outcome reference is invalid")
        task = self._teams._task_from_connection(
            connection,
            str(effect_ref["task_id"]),
        )
        agent = self._teams._agent_from_connection(
            connection,
            str(effect_ref["agent_id"]),
        )
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        outcome = _task_outcome(operation)
        acceptance = task.acceptance_result
        expected_next_stage, task_status, agent_status = _expected_worker_state(
            acceptance
        )
        expected_outcome = _json_object(asdict(outcome))
        expected_message_metadata = {
            "operation_id": operation.id,
            "task_id": task.id,
            "outcome_status": outcome.status,
            "reason_code": outcome.reason_code,
        }
        persisted_input_digest = _persisted_worker_input_digest(
            acceptance,
            message.metadata,
        )
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or task.owner_agent_id != operation.agent_id
            or task.outcome != expected_outcome
            or task.status != task_status
            or agent.team_run_id != operation.team_run_id
            or agent.status != agent_status
            or not _operation_session_matches(operation, agent)
            or effect_ref["next_stage"] != expected_next_stage
            or not _worker_terminal_fields_match(
                task,
                agent,
                outcome.summary,
                acceptance,
                expected_next_stage,
            )
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id is not None
            or message.kind != "agent_output"
            or message.content != outcome.summary
            or any(
                message.metadata.get(key) != value
                for key, value in expected_message_metadata.items()
            )
            or persisted_input_digest != effect_ref["input_digest"]
        ):
            raise OperationConflict("Applied task outcome rows do not match")
        return WorkerEffectResult(
            task=task,
            agent=agent,
            next_stage=effect_ref["next_stage"],
            message=message,
        )

    def _replay_worker_decision(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        effect_ref: dict[str, object] | None,
        input_digest: str,
    ) -> WorkerEffectResult:
        if (
            operation.effect_type != "user_decision"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {
                "task_id",
                "agent_id",
                "next_stage",
                "decision_request_id",
                "decision_item_id",
                "decision_item_digest",
                "input_digest",
            }
            or effect_ref["task_id"] != operation.task_id
            or effect_ref["agent_id"] != operation.agent_id
            or effect_ref["next_stage"] != "user_decision"
            or not isinstance(effect_ref["decision_request_id"], str)
            or not isinstance(effect_ref["decision_item_id"], str)
            or not isinstance(effect_ref["decision_item_digest"], str)
            or effect_ref["input_digest"] != input_digest
        ):
            raise OperationConflict("Applied Worker decision reference is invalid")
        task = self._teams._task_from_connection(
            connection,
            str(effect_ref["task_id"]),
        )
        agent = self._teams._agent_from_connection(
            connection,
            str(effect_ref["agent_id"]),
        )
        request = self._teams._decision_request_from_connection(
            connection,
            effect_ref["decision_request_id"],
        )
        item = next(
            (
                candidate
                for candidate in request.items
                if candidate.get("id") == effect_ref["decision_item_id"]
            ),
            None,
        )
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or task.owner_agent_id != operation.agent_id
            or task.status != "blocked"
            or agent.team_run_id != operation.team_run_id
            or agent.status != "waiting"
            or agent.current_task_id is not None
            or not _operation_session_matches(operation, agent)
            or request.team_run_id != operation.team_run_id
            or request.cycle_id != operation.cycle_id
            or request.status != "collecting"
            or request.answers != {}
            or request.published_at is not None
            or request.answered_at is not None
            or item is None
            or _canonical_digest(item) != effect_ref["decision_item_digest"]
            or not _decision_item_matches(
                item,
                _user_decision(operation),
                task.id,
            )
            or not _decision_item_references_are_valid(
                connection,
                operation,
                item,
            )
        ):
            raise OperationConflict("Applied Worker decision rows do not match")
        return WorkerEffectResult(
            task=task,
            agent=agent,
            next_stage="user_decision",
            decision_request=request,
        )

    def _validate_plan_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> None:
        if operation.stage not in _PLAN_STAGES:
            raise OperationConflict("Operation is not a planning stage")
        if operation.task_id is not None:
            raise OperationConflict("Planning operation cannot own a task")
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        actor = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if run is None or run["leader_agent_id"] != operation.agent_id:
            raise OperationConflict("Planning actor is not the team lead")
        if cycle is None or cycle["team_run_id"] != operation.team_run_id:
            raise OperationConflict("Planning cycle does not belong to the team run")
        if actor.team_run_id != operation.team_run_id:
            raise OperationConflict("Planning actor does not belong to the team run")

    def _create_task(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        spec: dict[str, object],
        now: str,
    ) -> TeamTask:
        owner_agent_id = spec["owner_agent_id"]
        if owner_agent_id is not None:
            owner = connection.execute(
                "select team_run_id from team_agents where id = ?",
                (owner_agent_id,),
            ).fetchone()
            if owner is None or owner["team_run_id"] != operation.team_run_id:
                raise ValueError("Task owner does not belong to the team run")

        acceptance_payload = spec["acceptance"]
        if not isinstance(acceptance_payload, dict):
            raise OperationConflict("Task plan acceptance is invalid")
        acceptance = TaskAcceptance(
            required_outputs=tuple(acceptance_payload["required_outputs"]),
            required_verifications=tuple(
                acceptance_payload["required_verifications"]
            ),
        )
        _validate_task_acceptance(acceptance)
        task_id = uuid4().hex
        connection.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id,
                status, required, acceptance_json, outcome_json,
                acceptance_result_json, result, error_message, created_at,
                updated_at, started_at, finished_at
            ) values (?, ?, ?, ?, ?, ?, 'pending', ?, ?, null, null, null, null,
                      ?, ?, null, null)
            """,
            (
                task_id,
                operation.team_run_id,
                operation.cycle_id,
                spec["title"],
                spec["description"],
                owner_agent_id,
                int(spec["required"]),
                json.dumps(
                    {
                        "required_outputs": list(acceptance.required_outputs),
                        "required_verifications": list(
                            acceptance.required_verifications
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                now,
                now,
            ),
        )
        return self._teams._task_from_connection(connection, task_id)

    def _replay_plan(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> list[TeamTask]:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "task_plan"
            or not isinstance(effect_ref, dict)
            or set(effect_ref) != {"task_ids", "message_id"}
            or not isinstance(effect_ref["task_ids"], list)
            or not all(isinstance(item, str) for item in effect_ref["task_ids"])
            or len(set(effect_ref["task_ids"])) != len(effect_ref["task_ids"])
            or not isinstance(effect_ref["message_id"], str)
        ):
            raise OperationConflict("Applied plan effect reference is invalid")
        tasks = [
            self._teams._task_from_connection(connection, task_id)
            for task_id in effect_ref["task_ids"]
        ]
        specs = _plan_specs(operation)
        if len(tasks) != len(specs):
            raise OperationConflict("Applied plan task count does not match")
        actor = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if any(
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or not _task_matches_plan_spec(task, spec)
            for task, spec in zip(tasks, specs, strict=True)
        ) or (
            not _operation_session_matches(operation, actor)
        ):
            raise OperationConflict("Applied plan tasks do not match the operation")
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        if (
            message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id is not None
            or message.kind != "plan_note"
            or message.content != f"Planning completed with {len(specs)} tasks."
            or message.metadata != {"operation_id": operation.id}
        ):
            raise OperationConflict("Applied plan message does not match the operation")
        return tasks


def _plan_specs(operation: TeamModelOperation) -> list[dict[str, object]]:
    stored = operation.result_json
    if (
        operation.result_kind != "task_plan"
        or not isinstance(stored, dict)
        or set(stored) != {"kind", "payload"}
        or stored["kind"] != "task_plan"
        or not isinstance(stored["payload"], dict)
        or set(stored["payload"]) != {"tasks"}
        or not isinstance(stored["payload"]["tasks"], list)
        or not all(isinstance(item, dict) for item in stored["payload"]["tasks"])
    ):
        raise OperationConflict("Completed planning result is invalid")
    return stored["payload"]["tasks"]


def _task_outcome(operation: TeamModelOperation):
    payload = _result_payload(operation, "task_outcome")
    try:
        return parse_task_outcome(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
    except TaskOutcomeError as exc:
        raise OperationConflict("Completed Worker outcome is invalid") from exc


def _user_decision(operation: TeamModelOperation) -> dict[str, object]:
    payload = _result_payload(operation, "user_decision")
    if not _valid_user_decision(payload):
        raise OperationConflict("Completed user decision is invalid")
    return payload


def _worker_query(operation: TeamModelOperation) -> dict[str, object]:
    payload = _result_payload(operation, "worker_query")
    if not _valid_worker_query(payload):
        raise OperationConflict("Completed Worker query is invalid")
    return payload


def _synthesis_summary(operation: TeamModelOperation) -> str:
    payload = _result_payload(operation, "synthesis")
    if not _valid_synthesis(payload):
        raise OperationConflict("Completed synthesis result is invalid")
    return payload["summary"]


def _result_payload(
    operation: TeamModelOperation,
    kind: str,
) -> dict[str, object]:
    stored = operation.result_json
    if (
        operation.result_kind != kind
        or not isinstance(stored, dict)
        or set(stored) != {"kind", "payload"}
        or stored["kind"] != kind
        or not isinstance(stored["payload"], dict)
    ):
        raise OperationConflict("Completed operation result is invalid")
    return stored["payload"]


def _validate_acceptance_result(acceptance: AcceptanceResult) -> None:
    if acceptance.accepted:
        if acceptance.status != "completed" or acceptance.reason_code is not None:
            raise ValueError("Accepted result must be completed without a reason")
        return
    if acceptance.status == "completed" or not acceptance.reason_code:
        raise ValueError("Rejected result requires a non-completed status and reason")


def _workspace_changes(value: Mapping[str, object]) -> dict[str, list[str]]:
    if set(value) != {"created", "modified", "deleted"}:
        raise ValueError("Workspace changes require exact fields")
    changes: dict[str, list[str]] = {}
    for field in ("created", "modified", "deleted"):
        items = value[field]
        if not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            raise ValueError("Workspace change paths must be strings")
        changes[field] = sorted(items)
    return changes


def _worker_effect_ref(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    result: WorkerEffectResult,
    input_digest: str,
) -> dict[str, object]:
    if operation.result_kind == "task_outcome" and result.message is not None:
        return {
            "task_id": result.task.id,
            "agent_id": result.agent.id,
            "next_stage": result.next_stage,
            "message_id": result.message.id,
            "input_digest": input_digest,
        }
    if (
        operation.result_kind == "user_decision"
        and result.decision_request is not None
    ):
        decision = _user_decision(operation)
        matching_items = [
            item
            for item in result.decision_request.items
            if _decision_item_matches(item, decision, result.task.id)
        ]
        if len(matching_items) != 1:
            raise OperationConflict(
                "Worker decision does not have one exact request item"
            )
        item = matching_items[0]
        if not _decision_item_references_are_valid(
            connection,
            operation,
            item,
        ):
            raise OperationConflict("Worker decision item references are invalid")
        item_id = item.get("id")
        if not isinstance(item_id, str):
            raise OperationConflict("Worker decision item ID is invalid")
        return {
            "task_id": result.task.id,
            "agent_id": result.agent.id,
            "next_stage": "user_decision",
            "decision_request_id": result.decision_request.id,
            "decision_item_id": item_id,
            "decision_item_digest": _canonical_digest(item),
            "input_digest": input_digest,
        }
    raise OperationConflict("Worker effect does not match its result kind")


def _worker_input_digest(
    operation: TeamModelOperation,
    acceptance: AcceptanceResult | None,
    workspace_changes: dict[str, list[str]],
) -> str:
    if operation.result_kind == "task_outcome":
        if acceptance is None:
            raise ValueError("Acceptance result is required for a task outcome")
        _validate_acceptance_result(acceptance)
        acceptance_payload: dict[str, object] | None = _json_object(
            asdict(acceptance)
        )
    elif operation.result_kind == "user_decision":
        if acceptance is not None:
            raise ValueError("User decision cannot have an acceptance result")
        acceptance_payload = None
    else:
        raise OperationConflict("Worker result kind is invalid")
    return _canonical_digest(
        {
            "acceptance": acceptance_payload,
            "workspace_changes": workspace_changes,
        }
    )


def _persisted_worker_input_digest(
    acceptance: dict[str, object],
    message_metadata: dict[str, object],
) -> str:
    if set(message_metadata) != {
        "operation_id",
        "task_id",
        "outcome_status",
        "reason_code",
        "created",
        "modified",
        "deleted",
    }:
        raise OperationConflict("Applied Worker message metadata is invalid")
    try:
        changes = _workspace_changes(
            {
                field: message_metadata[field]
                for field in ("created", "modified", "deleted")
            }
        )
    except ValueError as exc:
        raise OperationConflict(
            "Applied Worker workspace changes are invalid"
        ) from exc
    return _canonical_digest(
        {
            "acceptance": acceptance,
            "workspace_changes": changes,
        }
    )


def _expected_worker_state(
    acceptance: dict[str, object] | None,
) -> tuple[
    Literal["acceptance_lead"] | None,
    Literal["in_progress", "completed", "failed"],
    Literal["running", "completed", "failed"],
]:
    if (
        not isinstance(acceptance, dict)
        or set(acceptance) != {"accepted", "status", "reason_code", "evidence"}
        or not isinstance(acceptance["accepted"], bool)
        or not isinstance(acceptance["evidence"], dict)
    ):
        raise OperationConflict("Applied acceptance result is invalid")
    if acceptance["accepted"]:
        if (
            acceptance["status"] != "completed"
            or acceptance["reason_code"] is not None
        ):
            raise OperationConflict("Applied accepted result is invalid")
        return None, "completed", "completed"
    reason_code = acceptance["reason_code"]
    if not isinstance(reason_code, str) or not reason_code:
        raise OperationConflict("Applied rejected result is invalid")
    if is_recoverable_acceptance_failure(reason_code):
        return "acceptance_lead", "in_progress", "running"
    return None, "failed", "failed"


def _worker_terminal_fields_match(
    task: TeamTask,
    agent: TeamAgent,
    summary: str,
    acceptance: dict[str, object],
    next_stage: Literal["acceptance_lead"] | None,
) -> bool:
    if next_stage == "acceptance_lead":
        return (
            task.result is None
            and task.error_message is None
            and task.finished_at is None
            and agent.current_task_id == task.id
            and agent.finished_at is None
        )
    if acceptance["accepted"]:
        return (
            task.result == summary
            and task.error_message is None
            and task.finished_at is not None
            and agent.current_task_id is None
            and agent.finished_at is not None
        )
    return (
        task.result is None
        and task.error_message == acceptance["reason_code"]
        and task.finished_at is not None
        and agent.current_task_id is None
        and agent.finished_at is not None
    )


def _task_matches_plan_spec(
    task: TeamTask,
    spec: dict[str, object],
) -> bool:
    acceptance = spec.get("acceptance")
    return (
        isinstance(acceptance, dict)
        and task.title == spec.get("title")
        and task.description == spec.get("description")
        and task.owner_agent_id == spec.get("owner_agent_id")
        and task.required is spec.get("required")
        and list(task.acceptance.required_outputs)
        == acceptance.get("required_outputs")
        and list(task.acceptance.required_verifications)
        == acceptance.get("required_verifications")
    )


def _decision_item_matches(
    item: object,
    decision: dict[str, object],
    task_id: str,
) -> bool:
    return (
        isinstance(item, dict)
        and set(item)
        == {
            "id",
            "stage",
            "topic",
            "question",
            "why_needed",
            "options",
            "recommended_option_id",
            "blocking_scope",
            "blocking_task_ids",
            "query_message_ids",
        }
        and isinstance(item["id"], str)
        and item["stage"] == "task"
        and item["topic"] == decision["topic"]
        and item["question"] == decision["question"]
        and item["why_needed"] == decision["why_needed"]
        and item["options"] == decision["options"]
        and item["recommended_option_id"]
        == decision["recommended_option_id"]
        and item["blocking_scope"] == decision["blocking_scope"]
        and isinstance(item["blocking_task_ids"], list)
        and all(
            isinstance(value, str) for value in item["blocking_task_ids"]
        )
        and len(set(item["blocking_task_ids"]))
        == len(item["blocking_task_ids"])
        and task_id in item["blocking_task_ids"]
        and isinstance(item["query_message_ids"], list)
        and all(
            isinstance(value, str) for value in item["query_message_ids"]
        )
        and len(set(item["query_message_ids"]))
        == len(item["query_message_ids"])
    )


def _run_decision_item_matches(
    item: object,
    decision: dict[str, object],
) -> bool:
    return (
        isinstance(item, dict)
        and set(item)
        == {
            "id",
            "stage",
            "topic",
            "question",
            "why_needed",
            "options",
            "recommended_option_id",
            "blocking_scope",
            "blocking_task_ids",
            "query_message_ids",
        }
        and isinstance(item["id"], str)
        and item["stage"] in {"planning", "synthesis"}
        and item["topic"] == decision["topic"]
        and item["question"] == decision["question"]
        and item["why_needed"] == decision["why_needed"]
        and item["options"] == decision["options"]
        and item["recommended_option_id"] == decision["recommended_option_id"]
        and item["blocking_scope"] == "run"
        and item["blocking_task_ids"] == []
        and item["query_message_ids"] == []
    )


def _decision_item_references_are_valid(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    item: dict[str, object],
) -> bool:
    blocking_task_ids = item["blocking_task_ids"]
    query_message_ids = item["query_message_ids"]
    for task_id in blocking_task_ids:
        task = connection.execute(
            """
            select team_run_id, cycle_id, status from team_tasks where id = ?
            """,
            (task_id,),
        ).fetchone()
        if (
            task is None
            or task["team_run_id"] != operation.team_run_id
            or task["cycle_id"] != operation.cycle_id
            or task["status"] != "blocked"
        ):
            return False
    for message_id in query_message_ids:
        message = connection.execute(
            """
            select team_run_id, cycle_id, kind, metadata_json
            from team_messages where id = ?
            """,
            (message_id,),
        ).fetchone()
        if (
            message is None
            or message["team_run_id"] != operation.team_run_id
            or message["cycle_id"] != operation.cycle_id
            or message["kind"] != "query"
        ):
            return False
        try:
            metadata = json.loads(message["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        if (
            not isinstance(metadata, dict)
            or metadata.get("task_id") not in blocking_task_ids
        ):
            return False
    return True


def _canonical_digest(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_object(value: object) -> dict[str, object]:
    normalized = json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    if not isinstance(normalized, dict):
        raise OperationConflict("Expected a JSON object")
    return normalized


def team_model_effect_result_validators() -> OperationResultValidatorRegistry:
    return {
        "worker_execution": {
            "task_outcome": _valid_task_outcome,
            "user_decision": _valid_user_decision,
            "worker_query": _valid_worker_query,
        },
        "cycle_synthesis": {
            "synthesis": _valid_synthesis,
            "user_decision": _valid_user_decision,
        },
    }


def _valid_task_outcome(payload: dict[str, object]) -> bool:
    try:
        parse_task_outcome(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except (TaskOutcomeError, TypeError, ValueError):
        return False
    return True


def _valid_worker_query(payload: dict[str, object]) -> bool:
    return (
        set(payload) == {"topic", "question"}
        and all(
            isinstance(payload[field], str) and bool(payload[field].strip())
            for field in ("topic", "question")
        )
    )


def _valid_user_decision(payload: dict[str, object]) -> bool:
    if set(payload) != {
        "kind",
        "topic",
        "question",
        "why_needed",
        "options",
        "recommended_option_id",
        "blocking_scope",
    }:
        return False
    if payload["kind"] != "ask_user":
        return False
    if any(
        not isinstance(payload[field], str) or not payload[field].strip()
        for field in ("topic", "question", "why_needed")
    ):
        return False
    options = payload["options"]
    if not isinstance(options, list) or not all(_valid_decision_option(x) for x in options):
        return False
    option_ids = [option["id"] for option in options]
    if len(set(option_ids)) != len(option_ids):
        return False
    recommended = payload["recommended_option_id"]
    return (
        (recommended is None or recommended in option_ids)
        and payload["blocking_scope"] in {"task", "run"}
    )


def _valid_decision_option(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"id", "label", "impact"}
        and all(
            isinstance(value[field], str) and bool(value[field].strip())
            for field in ("id", "label", "impact")
        )
    )


def _valid_synthesis(payload: dict[str, object]) -> bool:
    return (
        set(payload) == {"summary"}
        and isinstance(payload["summary"], str)
        and bool(payload["summary"].strip())
    )


def _terminal_status(rows: list[sqlite3.Row]) -> str:
    if not rows:
        raise OperationConflict("Synthesis requires at least one task")
    if any(
        row["status"] not in {"blocked", "completed", "failed", "canceled"}
        for row in rows
    ):
        raise OperationConflict("Synthesis requires terminal tasks")
    required = [row for row in rows if bool(row["required"])]
    optional = [row for row in rows if not bool(row["required"])]
    if any(row["status"] == "failed" for row in required):
        return "failed"
    if any(row["status"] in {"blocked", "canceled"} for row in required):
        return "blocked"
    if all(row["status"] == "completed" for row in required):
        if any(row["status"] in {"blocked", "failed", "canceled"} for row in optional):
            return "completed_with_failures"
        return "completed"
    if not required and any(
        row["status"] in {"blocked", "failed", "canceled"} for row in optional
    ):
        return "completed_with_failures"
    return "blocked"


def _promote_actor_session(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    now: str,
) -> None:
    if operation.upstream_session_id is None:
        return
    cursor = connection.execute(
        """
        update team_agents set upstream_session_id = ?, updated_at = ?
        where id = ? and team_run_id = ?
        """,
        (
            operation.upstream_session_id,
            now,
            operation.agent_id,
            operation.team_run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise OperationConflict("Operation actor changed before effect application")


def _operation_session_matches(
    operation: TeamModelOperation,
    agent: TeamAgent,
) -> bool:
    return (
        operation.upstream_session_id is None
        or agent.upstream_session_id == operation.upstream_session_id
    )


def _mark_applied(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    *,
    effect_type: str,
    effect_ref: dict[str, object],
    now: str,
) -> None:
    cursor = connection.execute(
        """
        update team_model_operations
        set status = 'applied', version = version + 1, effect_type = ?,
            effect_ref_json = ?, applied_at = ?, updated_at = ?
        where id = ? and status = 'completed' and version = ?
        """,
        (
            effect_type,
            json.dumps(
                effect_ref,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            now,
            now,
            operation.id,
            operation.version,
        ),
    )
    if cursor.rowcount != 1:
        raise StaleOperation("Operation changed before effect application")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
