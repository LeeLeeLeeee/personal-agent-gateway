from __future__ import annotations

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
    next_stage: Literal["acceptance_lead", "user_decision"] | None
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
            if operation.status == "applied":
                return self._replay_worker(connection, operation)
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
                    _workspace_changes(workspace_changes),
                    now,
                )
            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type=operation.result_kind or "worker_outcome",
                effect_ref=_worker_effect_ref(result),
                now=now,
            )
            return result

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
        if (
            message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.kind != "synthesis"
            or message.content != summary
        ):
            raise OperationConflict(
                "Applied synthesis message does not match the operation"
            )
        return summary

    def _validate_worker_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> tuple[TeamTask, TeamAgent]:
        if operation.stage != "worker_execution" or operation.task_id is None:
            raise OperationConflict("Operation is not a Worker execution stage")
        if operation.result_kind not in {"task_outcome", "user_decision"}:
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
    ) -> WorkerEffectResult:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != operation.result_kind
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {
                "task_id",
                "agent_id",
                "next_stage",
                "message_id",
                "decision_request_id",
            }
            or effect_ref["task_id"] != operation.task_id
            or effect_ref["agent_id"] != operation.agent_id
            or effect_ref["next_stage"]
            not in {None, "acceptance_lead", "user_decision"}
        ):
            raise OperationConflict("Applied Worker effect reference is invalid")
        task = self._teams._task_from_connection(
            connection,
            str(effect_ref["task_id"]),
        )
        agent = self._teams._agent_from_connection(
            connection,
            str(effect_ref["agent_id"]),
        )
        message_id = effect_ref["message_id"]
        request_id = effect_ref["decision_request_id"]
        if message_id is not None and not isinstance(message_id, str):
            raise OperationConflict("Applied Worker message reference is invalid")
        if request_id is not None and not isinstance(request_id, str):
            raise OperationConflict("Applied Worker decision reference is invalid")
        message = (
            self._teams._message_from_connection(connection, message_id)
            if isinstance(message_id, str)
            else None
        )
        request = (
            self._teams._decision_request_from_connection(connection, request_id)
            if isinstance(request_id, str)
            else None
        )
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or agent.team_run_id != operation.team_run_id
            or (
                message is not None
                and (
                    message.team_run_id != operation.team_run_id
                    or message.cycle_id != operation.cycle_id
                    or message.kind != "agent_output"
                )
            )
            or (
                request is not None
                and (
                    request.team_run_id != operation.team_run_id
                    or request.cycle_id != operation.cycle_id
                )
            )
        ):
            raise OperationConflict("Applied Worker rows do not match the operation")
        if (message is None) == (request is None):
            raise OperationConflict("Applied Worker effect row reference is invalid")
        return WorkerEffectResult(
            task=task,
            agent=agent,
            next_stage=effect_ref["next_stage"],
            message=message,
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
            or not isinstance(effect_ref["message_id"], str)
        ):
            raise OperationConflict("Applied plan effect reference is invalid")
        tasks = [
            self._teams._task_from_connection(connection, task_id)
            for task_id in effect_ref["task_ids"]
        ]
        if any(
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            for task in tasks
        ):
            raise OperationConflict("Applied plan tasks do not match the operation")
        message = connection.execute(
            """
            select team_run_id, cycle_id, kind from team_messages where id = ?
            """,
            (effect_ref["message_id"],),
        ).fetchone()
        if (
            message is None
            or message["team_run_id"] != operation.team_run_id
            or message["cycle_id"] != operation.cycle_id
            or message["kind"] != "plan_note"
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
        changes[field] = list(items)
    return changes


def _worker_effect_ref(result: WorkerEffectResult) -> dict[str, object]:
    return {
        "task_id": result.task.id,
        "agent_id": result.agent.id,
        "next_stage": result.next_stage,
        "message_id": result.message.id if result.message is not None else None,
        "decision_request_id": (
            result.decision_request.id
            if result.decision_request is not None
            else None
        ),
    }


def team_model_effect_result_validators() -> OperationResultValidatorRegistry:
    return {
        "worker_execution": {
            "task_outcome": _valid_task_outcome,
            "user_decision": _valid_user_decision,
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
