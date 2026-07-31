from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database

OperationStage = Literal[
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "worker_execution",
    "mediation_lead",
    "mediation_worker",
    "acceptance_lead",
    "acceptance_worker",
    "cycle_synthesis",
]

OperationStatus = Literal[
    "prepared",
    "invoking",
    "completed",
    "applied",
    "waiting_for_provider",
    "ambiguous",
    "failed",
    "canceled",
]
OperationResultValidator = Callable[[dict[str, object]], bool]
OperationResultValidatorRegistry = Mapping[
    OperationStage,
    Mapping[str, OperationResultValidator],
]

_OPEN_STATUSES = {
    "prepared",
    "invoking",
    "completed",
    "waiting_for_provider",
    "ambiguous",
}
_CANCELABLE_STATUSES = _OPEN_STATUSES


class OperationConflict(RuntimeError):
    pass


class StaleOperation(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidatedOperationResult:
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class OperationSpec:
    operation_key: str
    team_run_id: str
    cycle_id: str
    task_id: str | None
    agent_id: str
    provider: str
    stage: OperationStage
    stage_ordinal: int
    request_digest: str
    upstream_session_id: str | None = None


@dataclass(frozen=True)
class TeamModelOperation:
    id: str
    operation_key: str
    team_run_id: str
    cycle_id: str
    task_id: str | None
    agent_id: str
    provider: str
    stage: OperationStage
    stage_ordinal: int
    status: OperationStatus
    version: int
    attempts: int
    consumer_run_id: str | None
    upstream_session_id: str | None
    request_digest: str
    result_kind: str | None
    result_json: dict[str, object] | None
    result_digest: str | None
    effect_type: str | None
    effect_ref_json: dict[str, object] | None
    reason_code: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    applied_at: str | None
    updated_at: str


class TeamModelOperationService:
    def __init__(
        self,
        db: Database,
        *,
        result_validators: OperationResultValidatorRegistry | None = None,
    ) -> None:
        self._db = db
        self._result_validators = {
            stage: dict(validators)
            for stage, validators in (result_validators or {}).items()
        }

    def reserve(self, spec: OperationSpec) -> TeamModelOperation:
        timestamp = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            _validate_request_digest(spec.request_digest)
            self._validate_spec_ownership(connection, spec)
            existing = self._get_by_key(connection, spec.operation_key)
            if existing is not None:
                self._validate_existing_spec(existing, spec)
                return existing

            open_operation = connection.execute(
                """
                select id from team_model_operations
                where cycle_id = ? and status in (?, ?, ?, ?, ?)
                """,
                (spec.cycle_id, *_OPEN_STATUSES),
            ).fetchone()
            if open_operation is not None:
                raise OperationConflict("Cycle already has an open model operation")

            operation_id = uuid4().hex
            connection.execute(
                """
                insert into team_model_operations (
                    id, operation_key, team_run_id, cycle_id, task_id, agent_id,
                    provider, stage, stage_ordinal, status, version, attempts,
                    consumer_run_id, upstream_session_id, request_digest, result_kind,
                    result_json, result_digest, effect_type, effect_ref_json, reason_code,
                    created_at, started_at, completed_at, applied_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', 0, 0, null, ?, ?,
                          null, null, null, null, null, null, ?, null, null, null, ?)
                """,
                (
                    operation_id,
                    spec.operation_key,
                    spec.team_run_id,
                    spec.cycle_id,
                    spec.task_id,
                    spec.agent_id,
                    spec.provider,
                    spec.stage,
                    spec.stage_ordinal,
                    spec.upstream_session_id,
                    spec.request_digest,
                    timestamp,
                    timestamp,
                ),
            )
            return self._get(connection, operation_id)

    def begin_attempt(
        self,
        operation_id: str,
        consumer_run_id: str,
    ) -> TeamModelOperation:
        if not consumer_run_id:
            raise OperationConflict("consumer_run_id is required")
        timestamp = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._get(connection, operation_id)
            self._require_status(operation, "prepared")
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'invoking', version = version + 1, attempts = attempts + 1,
                    consumer_run_id = ?, reason_code = null,
                    started_at = coalesce(started_at, ?), updated_at = ?
                where id = ? and status = ? and version = ?
                """,
                (
                    consumer_run_id,
                    timestamp,
                    timestamp,
                    operation_id,
                    "prepared",
                    operation.version,
                ),
            )
            self._require_one_updated(cursor)
            return self._get(connection, operation_id)

    def complete(
        self,
        operation_id: str,
        expected_version: int,
        result: ValidatedOperationResult,
        *,
        upstream_session_id: str | None = None,
    ) -> TeamModelOperation:
        timestamp = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._get(connection, operation_id)
            if operation.status == "completed":
                if operation.version != expected_version + 1:
                    raise StaleOperation("Completed operation version does not match")
                serialized, digest = _result_serialization(
                    self._result_validators,
                    operation.stage,
                    result,
                )
                if (
                    operation.result_digest == digest
                    and _matches_session(operation.upstream_session_id, upstream_session_id)
                ):
                    return operation
                raise StaleOperation("Completed operation does not match this result")
            self._require_status_and_version(operation, "invoking", expected_version)
            serialized, digest = _result_serialization(
                self._result_validators,
                operation.stage,
                result,
            )
            if (
                operation.upstream_session_id is not None
                and upstream_session_id is not None
                and operation.upstream_session_id != upstream_session_id
            ):
                raise OperationConflict("upstream session does not match the operation")
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'completed', version = version + 1, result_kind = ?,
                    result_json = ?, result_digest = ?,
                    upstream_session_id = coalesce(upstream_session_id, ?),
                    completed_at = ?, updated_at = ?
                where id = ? and status = ? and version = ?
                """,
                (
                    result.kind,
                    serialized,
                    digest,
                    upstream_session_id,
                    timestamp,
                    timestamp,
                    operation_id,
                    "invoking",
                    expected_version,
                ),
            )
            self._require_one_updated(cursor)
            return self._get(connection, operation_id)

    def prepare_retry(
        self,
        operation_id: str,
        expected_version: int,
        reason_code: str,
    ) -> TeamModelOperation:
        return self._transition(
            operation_id,
            expected_version,
            source_status="invoking",
            target_status="prepared",
            reason_code=reason_code,
        )

    def mark_failed(
        self,
        operation_id: str,
        expected_version: int,
        reason_code: str,
        *,
        expected_status: OperationStatus = "invoking",
    ) -> TeamModelOperation:
        return self._transition(
            operation_id,
            expected_version,
            source_status=expected_status,
            target_status="failed",
            reason_code=reason_code,
        )

    def mark_canceled(
        self,
        operation_id: str,
        expected_version: int,
        *,
        expected_status: OperationStatus,
        reason_code: str | None = None,
    ) -> TeamModelOperation:
        if expected_status not in _CANCELABLE_STATUSES:
            raise OperationConflict("Operation status cannot be canceled")
        return self._transition(
            operation_id,
            expected_version,
            source_status=expected_status,
            target_status="canceled",
            reason_code=reason_code,
        )

    def get(self, operation_id: str) -> TeamModelOperation:
        with self._db.connection() as connection:
            return self._get(connection, operation_id)

    def get_by_key(self, operation_key: str) -> TeamModelOperation | None:
        with self._db.connection() as connection:
            return self._get_by_key(connection, operation_key)

    def get_open_for_cycle(self, cycle_id: str) -> TeamModelOperation | None:
        placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
        with self._db.connection() as connection:
            row = connection.execute(
                f"""
                select * from team_model_operations
                where cycle_id = ? and status in ({placeholders})
                order by created_at asc, id asc
                """,
                (cycle_id, *_OPEN_STATUSES),
            ).fetchone()
            return _operation_from_row(row) if row is not None else None

    def list_for_cycle(self, cycle_id: str) -> list[TeamModelOperation]:
        with self._db.connection() as connection:
            rows = connection.execute(
                """
                select * from team_model_operations
                where cycle_id = ?
                order by created_at asc, id asc
                """,
                (cycle_id,),
            ).fetchall()
            return [_operation_from_row(row) for row in rows]

    def _transition(
        self,
        operation_id: str,
        expected_version: int,
        *,
        source_status: OperationStatus,
        target_status: OperationStatus,
        reason_code: str | None,
    ) -> TeamModelOperation:
        timestamp = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._get(connection, operation_id)
            self._require_status_and_version(operation, source_status, expected_version)
            cursor = connection.execute(
                """
                update team_model_operations
                set status = ?, version = version + 1, reason_code = ?, updated_at = ?
                where id = ? and status = ? and version = ?
                """,
                (
                    target_status,
                    reason_code,
                    timestamp,
                    operation_id,
                    source_status,
                    expected_version,
                ),
            )
            self._require_one_updated(cursor)
            return self._get(connection, operation_id)

    def _validate_spec_ownership(
        self,
        connection: sqlite3.Connection,
        spec: OperationSpec,
    ) -> None:
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (spec.cycle_id,),
        ).fetchone()
        if cycle is None or cycle["team_run_id"] != spec.team_run_id:
            raise OperationConflict("Cycle does not belong to the team run")
        agent = connection.execute(
            "select team_run_id, backend from team_agents where id = ?",
            (spec.agent_id,),
        ).fetchone()
        if agent is None or agent["team_run_id"] != spec.team_run_id:
            raise OperationConflict("Agent does not belong to the team run")
        if agent["backend"] != spec.provider:
            raise OperationConflict("Provider does not match the agent backend")
        if spec.task_id is None:
            return
        task = connection.execute(
            "select team_run_id, cycle_id from team_tasks where id = ?",
            (spec.task_id,),
        ).fetchone()
        if (
            task is None
            or task["team_run_id"] != spec.team_run_id
            or task["cycle_id"] != spec.cycle_id
        ):
            raise OperationConflict("Task does not belong to the operation cycle")

    def _validate_existing_spec(
        self,
        operation: TeamModelOperation,
        spec: OperationSpec,
    ) -> None:
        immutable_values = (
            (operation.team_run_id, spec.team_run_id),
            (operation.cycle_id, spec.cycle_id),
            (operation.task_id, spec.task_id),
            (operation.agent_id, spec.agent_id),
            (operation.provider, spec.provider),
            (operation.stage, spec.stage),
            (operation.stage_ordinal, spec.stage_ordinal),
            (operation.request_digest, spec.request_digest),
        )
        if any(existing != incoming for existing, incoming in immutable_values):
            raise OperationConflict("Operation key is already bound to another request")
        if (
            spec.upstream_session_id is not None
            and spec.upstream_session_id != operation.upstream_session_id
        ):
            raise OperationConflict("Operation key is bound to another upstream session")

    def _get(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> TeamModelOperation:
        row = connection.execute(
            "select * from team_model_operations where id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Team model operation not found: {operation_id}")
        return _operation_from_row(row)

    def _get_by_key(
        self,
        connection: sqlite3.Connection,
        operation_key: str,
    ) -> TeamModelOperation | None:
        row = connection.execute(
            "select * from team_model_operations where operation_key = ?",
            (operation_key,),
        ).fetchone()
        return _operation_from_row(row) if row is not None else None

    @staticmethod
    def _require_status(operation: TeamModelOperation, status: OperationStatus) -> None:
        if operation.status != status:
            raise StaleOperation(
                f"Expected operation status {status}, got {operation.status}"
            )

    def _require_status_and_version(
        self,
        operation: TeamModelOperation,
        status: OperationStatus,
        version: int,
    ) -> None:
        self._require_status(operation, status)
        if operation.version != version:
            raise StaleOperation(
                f"Expected operation version {version}, got {operation.version}"
            )

    @staticmethod
    def _require_one_updated(cursor: sqlite3.Cursor) -> None:
        if cursor.rowcount != 1:
            raise StaleOperation("Operation changed before the transition completed")


def _result_serialization(
    validators: OperationResultValidatorRegistry,
    stage: OperationStage,
    result: ValidatedOperationResult,
) -> tuple[str, str]:
    validator = validators.get(stage, {}).get(result.kind)
    if validator is None or not validator(result.payload):
        raise OperationConflict("Operation result is not safe to persist")
    serialized = json.dumps(
        {"kind": result.kind, "payload": result.payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return serialized, digest


def _validate_request_digest(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise OperationConflict("request_digest must be a lowercase SHA-256 digest")


def _matches_session(
    existing_session_id: str | None,
    submitted_session_id: str | None,
) -> bool:
    return submitted_session_id is None or submitted_session_id == existing_session_id


def _operation_from_row(row: sqlite3.Row) -> TeamModelOperation:
    return TeamModelOperation(
        id=str(row["id"]),
        operation_key=str(row["operation_key"]),
        team_run_id=str(row["team_run_id"]),
        cycle_id=str(row["cycle_id"]),
        task_id=row["task_id"],
        agent_id=str(row["agent_id"]),
        provider=str(row["provider"]),
        stage=row["stage"],
        stage_ordinal=int(row["stage_ordinal"]),
        status=row["status"],
        version=int(row["version"]),
        attempts=int(row["attempts"]),
        consumer_run_id=row["consumer_run_id"],
        upstream_session_id=row["upstream_session_id"],
        request_digest=str(row["request_digest"]),
        result_kind=row["result_kind"],
        result_json=(json.loads(row["result_json"]) if row["result_json"] else None),
        result_digest=row["result_digest"],
        effect_type=row["effect_type"],
        effect_ref_json=(
            json.loads(row["effect_ref_json"]) if row["effect_ref_json"] else None
        ),
        reason_code=row["reason_code"],
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        applied_at=row["applied_at"],
        updated_at=str(row["updated_at"]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
