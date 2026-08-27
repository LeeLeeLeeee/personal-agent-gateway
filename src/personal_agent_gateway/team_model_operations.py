from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.team_plan_negotiation import OBJECTION_KINDS
from personal_agent_gateway.team_verification_checks import CHECK_TYPES

OperationStage = Literal[
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "worker_execution",
    "mediation_lead",
    "mediation_lead_repair",
    # A peer answers instead of the lead when the asker addressed its
    # needs_info to a roster label. Its own stage, not mediation_lead with a
    # different agent: recovery rebuilds a mediation_lead operation with the
    # leader, so reusing that name would make a peer-answered consult
    # unrecoverable the moment it is interrupted.
    "consult_peer",
    "consult_peer_repair",
    "mediation_worker",
    "mediation_worker_repair",
    "acceptance_lead",
    "acceptance_lead_repair",
    "acceptance_worker",
    "acceptance_worker_repair",
    "cycle_synthesis",
    "cycle_synthesis_repair",
    "cycle_contest",
    "cycle_contest_repair",
    # Named cycle_* like every other stage that owns no task: the plan under
    # review belongs to the cycle, not to one assignment. The prefix is load
    # bearing -- team_provider_recovery groups every non-cycle stage as a
    # Worker or Lead stage and both groups validate against a task the
    # reviewer does not have.
    "cycle_plan_review",
    "cycle_plan_review_repair",
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


class OperationResultValidationError(OperationConflict):
    pass


class OperationSessionConflict(OperationConflict):
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
    #: 이 호출이 쓴 토큰. None 은 프로바이더가 보고하지 않았다는 뜻이고
    #: 0 과 다르다 -- 합계를 낼 때 None 을 건너뛰지 않으면 총합이 낮아진다.
    usage: dict[str, int] | None
    failure_digest: str | None
    failure_shape: dict[str, object] | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    applied_at: str | None
    updated_at: str


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)



class _UsageBucket:
    """사용량 행을 모아 한 덩어리로 세는 자리.

    총합과 팀원별 합이 같은 셈을 쓰게 하려고 따로 뒀다. 한쪽만 고치면 둘이
    어긋나는데, 어긋난 두 숫자를 화면에서 보고 어느 쪽이 맞는지 가릴 방법은
    없다.
    """

    def __init__(self) -> None:
        self.totals = {key: 0 for key in _USAGE_KEYS}
        self.reported = 0
        self.unreported = 0

    def add(self, raw: object) -> None:
        if not raw:
            self.unreported += 1
            return
        try:
            usage = json.loads(raw)
        except (TypeError, ValueError):
            self.unreported += 1
            return
        if not isinstance(usage, dict):
            self.unreported += 1
            return
        self.reported += 1
        for key in _USAGE_KEYS:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                self.totals[key] += value

    def result(self) -> dict[str, int]:
        return {
            **self.totals,
            "reported_calls": self.reported,
            "unreported_calls": self.unreported,
        }

class TeamModelOperationService:
    def __init__(
        self,
        db: Database,
        *,
        result_validators: OperationResultValidatorRegistry | None = None,
        concurrent_tasks: bool = False,
    ) -> None:
        self._db = db
        # False keeps the pre-concurrency rule exactly: one open operation per
        # cycle, whatever it belongs to. True narrows the refusal to the same
        # assignment, which is the invariant recovery actually needs (never two
        # candidates for one task) -- cycle-level stages still exclude each
        # other, because they speak for the whole cycle.
        self._concurrent_tasks = concurrent_tasks
        self._result_validators = _built_in_result_validators()
        for stage, validators in (result_validators or {}).items():
            stage_validators = self._result_validators.setdefault(stage, {})
            duplicate_kinds = stage_validators.keys() & validators.keys()
            if duplicate_kinds:
                raise ValueError(
                    f"Result validator already registered for {stage}: "
                    f"{', '.join(sorted(duplicate_kinds))}"
                )
            stage_validators.update(validators)

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

            if self._concurrent_tasks:
                open_operation = connection.execute(
                    """
                    select id from team_model_operations
                    where cycle_id = ?
                      and coalesce(task_id, '~cycle~') = ?
                      and status in (?, ?, ?, ?, ?)
                    """,
                    (
                        spec.cycle_id,
                        spec.task_id if spec.task_id is not None else "~cycle~",
                        *_OPEN_STATUSES,
                    ),
                ).fetchone()
                if open_operation is not None:
                    raise OperationConflict(
                        "Task already has an open model operation"
                    )
            else:
                open_operation = connection.execute(
                    """
                    select id from team_model_operations
                    where cycle_id = ? and status in (?, ?, ?, ?, ?)
                    """,
                    (spec.cycle_id, *_OPEN_STATUSES),
                ).fetchone()
                if open_operation is not None:
                    raise OperationConflict(
                        "Cycle already has an open model operation"
                    )

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
        usage: dict[str, int] | None = None,
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
                raise OperationSessionConflict(
                    "upstream session does not match the operation"
                )
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'completed', version = version + 1, result_kind = ?,
                    result_json = ?, result_digest = ?,
                    upstream_session_id = coalesce(upstream_session_id, ?),
                    usage_json = ?,
                    completed_at = ?, updated_at = ?
                where id = ? and status = ? and version = ?
                """,
                (
                    result.kind,
                    serialized,
                    digest,
                    upstream_session_id,
                    json.dumps(usage, sort_keys=True) if usage else None,
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
        upstream_session_id: str | None = None,
        response_text: str | None = None,
        expected_keys: frozenset[str] = frozenset(),
    ) -> TeamModelOperation:
        operation = self._transition(
            operation_id,
            expected_version,
            source_status=expected_status,
            target_status="failed",
            reason_code=reason_code,
            upstream_session_id=upstream_session_id,
        )
        if response_text is None:
            return operation
        # Structure only. The ledger design excludes raw model responses, and a
        # digest plus a shape answers "same breakage every time, and broken how"
        # without keeping what was said.
        digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
        shape = failure_shape(response_text, expected_keys)
        with self._db.connection() as connection:
            connection.execute(
                "update team_model_operations "
                "set failure_digest = ?, failure_shape_json = ? where id = ?",
                (digest, json.dumps(shape, ensure_ascii=False, sort_keys=True), operation_id),
            )
        return replace(operation, failure_digest=digest, failure_shape=shape)

    def record_invoking_reason(
        self,
        operation_id: str,
        expected_version: int,
        reason_code: str,
    ) -> TeamModelOperation:
        return self._transition(
            operation_id,
            expected_version,
            source_status="invoking",
            target_status="invoking",
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

    def usage_totals(self, team_run_id: str) -> dict[str, int]:
        """이 런이 쓴 토큰의 합계.

        호출 단위로 저장한 것을 여기서 합친다.

        보고하지 않은 호출은 세지 않고 개수만 따로 돌려준다. 0 으로 합치면
        총합이 실제보다 낮다는 사실 자체가 화면에서 사라진다.
        """
        bucket = _UsageBucket()
        for row in self._db.fetchall(
            "select usage_json from team_model_operations where team_run_id = ?",
            (team_run_id,),
        ):
            bucket.add(row["usage_json"])
        return bucket.result()

    def usage_by_agent(self, team_run_id: str) -> dict[str, dict[str, int]]:
        """이 런에서 팀원 한 사람이 각각 얼마나 썼는지.

        총합만 보면 어느 자리가 비싼지 알 수 없다. 리드는 사이클마다 계획과
        합성으로 두 번씩 불리고, 작업자는 자기 일감이 있을 때만 불린다 --
        같은 런 안에서도 자릿수가 다르다.

        호출을 낸 적이 없는 팀원은 아예 나오지 않는다. 0 을 채워 돌려주면
        "안 불렸다" 와 "불렸는데 보고를 안 했다" 가 화면에서 같아진다.
        """
        buckets: dict[str, _UsageBucket] = {}
        for row in self._db.fetchall(
            """
            select agent_id, usage_json from team_model_operations
            where team_run_id = ? and agent_id is not null
            """,
            (team_run_id,),
        ):
            buckets.setdefault(row["agent_id"], _UsageBucket()).add(row["usage_json"])
        return {agent_id: bucket.result() for agent_id, bucket in buckets.items()}

    def get(self, operation_id: str) -> TeamModelOperation:
        with self._db.connection() as connection:
            return self._get(connection, operation_id)

    def get_by_key(self, operation_key: str) -> TeamModelOperation | None:
        with self._db.connection() as connection:
            return self._get_by_key(connection, operation_key)

    def get_open_for_cycle(
        self,
        cycle_id: str,
        *,
        task_id: str | None = None,
        scoped_to_task: bool = False,
    ) -> TeamModelOperation | None:
        """The oldest open operation in this cycle, or in one assignment.

        `scoped_to_task` is explicit rather than inferred from `task_id`,
        because a cycle-level stage legitimately has no task and "look for
        cycle-level work only" and "look at everything" are different
        questions. Callers that must see the whole cycle -- recovery walking
        what a crash left behind -- keep the default.
        """
        placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
        with self._db.connection() as connection:
            if scoped_to_task:
                row = connection.execute(
                    f"""
                    select * from team_model_operations
                    where cycle_id = ?
                      and coalesce(task_id, '~cycle~') = ?
                      and status in ({placeholders})
                    order by created_at asc, id asc
                    """,
                    (
                        cycle_id,
                        task_id if task_id is not None else "~cycle~",
                        *_OPEN_STATUSES,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    f"""
                    select * from team_model_operations
                    where cycle_id = ? and status in ({placeholders})
                    order by created_at asc, id asc
                    """,
                    (cycle_id, *_OPEN_STATUSES),
                ).fetchone()
            return _operation_from_row(row) if row is not None else None

    def latest_failure_shapes(self, team_run_id: str) -> dict[str, dict[str, object]]:
        """How the most recent unparseable response per task was malformed.

        Scoped to the run rather than a cycle so one query serves the whole
        detail payload. Only the latest failure per task is kept: a task that
        recovered and failed again later is described by the failure that is
        still blocking it, not by the one it already got past.
        """
        with self._db.connection() as connection:
            rows = connection.execute(
                """
                select task_id, failure_shape_json from team_model_operations
                where team_run_id = ? and task_id is not null
                  and failure_shape_json is not null
                order by created_at asc, id asc
                """,
                (team_run_id,),
            ).fetchall()
        return {
            row["task_id"]: json.loads(row["failure_shape_json"]) for row in rows
        }

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
        upstream_session_id: str | None = None,
    ) -> TeamModelOperation:
        timestamp = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._get(connection, operation_id)
            self._require_status_and_version(operation, source_status, expected_version)
            if (
                operation.upstream_session_id is not None
                and upstream_session_id is not None
                and operation.upstream_session_id != upstream_session_id
            ):
                raise OperationSessionConflict(
                    "upstream session does not match the operation"
                )
            cursor = connection.execute(
                """
                update team_model_operations
                set status = ?, version = version + 1, reason_code = ?,
                    upstream_session_id = coalesce(upstream_session_id, ?), updated_at = ?
                where id = ? and status = ? and version = ?
                """,
                (
                    target_status,
                    reason_code,
                    upstream_session_id,
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


def failure_shape(text: str, expected_keys: frozenset[str]) -> dict[str, object]:
    """Non-content facts about a response that failed to parse.

    Deliberately excludes the text and any key the model invented. Expected key
    names come from the contract, so listing the missing ones records nothing the
    model produced; unexpected key names are model output, so only their count is
    kept. The ledger design excludes raw model responses and this stays inside
    that rule while still answering the first question an investigator asks --
    is it the same breakage every time, and in what way was it broken.
    """
    stripped = text.strip()
    fenced = stripped.startswith("```")
    body = stripped
    if fenced:
        body = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", stripped)
    try:
        parsed: object | None = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    keys = set(parsed) if isinstance(parsed, dict) else set()
    return {
        "length": len(text),
        "fenced": fenced,
        "parsed_json": isinstance(parsed, dict),
        "missing_expected_keys": sorted(expected_keys - keys),
        "unexpected_key_count": len(keys - expected_keys),
        "unclosed_braces": _unclosed_braces(body),
    }


def _unclosed_braces(body: str) -> int:
    """문자열 밖에서 열린 채 남은 중괄호 수. 닫는 쪽이 많으면 음수.

    개수만 센다 -- 이름도 내용도 아니라서 원문을 기록하지 않는 규칙 안에
    있고, "같은 방식으로 깨졌는가" 라는 첫 질문에 답한다. 실측에서 리드가
    네 번 연속 정확히 하나씩 빠뜨렸는데, 이 숫자가 없었다면 그 규칙성을
    보지 못했을 것이다.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in body:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth


def _result_serialization(
    validators: OperationResultValidatorRegistry,
    stage: OperationStage,
    result: ValidatedOperationResult,
) -> tuple[str, str]:
    validator = validators.get(stage, {}).get(result.kind)
    if validator is None or not validator(result.payload):
        raise OperationResultValidationError(
            "Operation result is not safe to persist"
        )
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


def _built_in_result_validators() -> dict[
    OperationStage,
    dict[str, OperationResultValidator],
]:
    return {
        "cycle_planning": {"task_plan": _valid_task_plan},
        "cycle_planning_repair": {"task_plan": _valid_task_plan},
        "cycle_add_work": {"task_plan": _valid_task_plan},
        "cycle_contest": {"contest_verdict": _valid_contest_verdict},
        "cycle_contest_repair": {"contest_verdict": _valid_contest_verdict},
        "cycle_plan_review": {"plan_review": _valid_plan_review},
        "cycle_plan_review_repair": {"plan_review": _valid_plan_review},
    }


def _valid_plan_review(payload: dict[str, object]) -> bool:
    """Re-check the reviewer's verdict on the way into the ledger.

    parse_plan_review already refused anything the leader cannot replan from,
    but the persisted row is what a restart replays, so the shape is checked
    again here rather than trusted from the caller.
    """
    if set(payload) != {"decision", "objections"}:
        return False
    decision = payload["decision"]
    # isinstance first: a JSON list or dict is unhashable and `in` on a set
    # raises TypeError instead of returning False.
    if not isinstance(decision, str) or decision not in {"approve", "object"}:
        return False
    objections = payload["objections"]
    if not isinstance(objections, list):
        return False
    for objection in objections:
        if not isinstance(objection, dict) or set(objection) != {
            "kind",
            "task_ref",
            "detail",
        }:
            return False
        kind = objection["kind"]
        if not isinstance(kind, str) or kind not in OBJECTION_KINDS:
            return False
        if not all(
            isinstance(objection[field], str) and objection[field].strip()
            for field in ("task_ref", "detail")
        ):
            return False
    return bool(objections) == (decision == "object")


def _valid_task_plan(payload: dict[str, object]) -> bool:
    if set(payload) != {"tasks"}:
        return False
    tasks = payload["tasks"]
    return isinstance(tasks, list) and all(_valid_task_spec(task) for task in tasks)


CONTEST_VERDICT_KINDS = {"amend", "partial", "reject", "ask_back"}


def _valid_contest_verdict(payload: dict[str, object]) -> bool:
    if set(payload) - {"kind", "reason", "tasks", "question", "supersedes"}:
        return False
    kind = payload.get("kind")
    if kind not in CONTEST_VERDICT_KINDS:
        return False
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list) or not all(
        _valid_task_spec(task) for task in tasks
    ):
        return False
    if kind in {"amend", "partial"} and not tasks:
        return False
    if kind in {"reject", "ask_back"} and tasks:
        return False
    question = payload.get("question")
    if kind == "ask_back":
        if not isinstance(question, str) or not question.strip():
            return False
    # A model shown the full object in the prompt will send every key, so a
    # present-but-empty question on an amend is the normal case, not a defect.
    # Rejecting it would burn the repair on almost every verdict. A question with
    # actual content on a kind that has no question to ask is still wrong.
    elif question not in (None, ""):
        return False
    supersedes = payload.get("supersedes") or []
    if not isinstance(supersedes, list):
        return False
    for entry in supersedes:
        if not isinstance(entry, dict) or set(entry) != {
            "document_path",
            "decision",
        }:
            return False
        if not all(
            isinstance(entry[key], str) and entry[key].strip() for key in entry
        ):
            return False
    # Admitting a reversal without producing the work to correct the document
    # is exactly the FSRS episode this rule exists to prevent.
    if supersedes and not tasks:
        return False
    return True


def _valid_task_spec(value: object) -> bool:
    required_fields = {
        "title",
        "description",
        "owner_agent_id",
        "required",
        "acceptance",
    }
    if (
        not isinstance(value, dict)
        or not required_fields <= set(value)
        or set(value)
        - (required_fields | {"input_artifact_ids", "plan_task_id", "depends_on_task_ids"})
    ):
        return False
    owner_agent_id = value["owner_agent_id"]
    return (
        _nonempty_text(value["title"])
        and _nonempty_text(value["description"])
        and (owner_agent_id is None or _nonempty_text(owner_agent_id))
        and isinstance(value["required"], bool)
        and _valid_acceptance(value["acceptance"])
        and (
            "input_artifact_ids" not in value
            or _valid_string_list(value["input_artifact_ids"])
            or value["input_artifact_ids"] == []
        )
        and (
            "plan_task_id" not in value
            or value["plan_task_id"] is None
            or _nonempty_text(value["plan_task_id"])
        )
        and (
            "depends_on_task_ids" not in value
            or _valid_string_list(value["depends_on_task_ids"])
            or value["depends_on_task_ids"] == []
        )
    )


# This validator only gates the shape stored in the operation ledger.
# team_model_operations is a leaf module that must not import the domain modules
# above it (e.g. teams), so the check vocabulary is restated below for this
# validator only rather than reused from parse_required_verifications.
def _valid_acceptance(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "required_outputs",
        "required_verifications",
    }:
        return False
    outputs = value["required_outputs"]
    verifications = value["required_verifications"]
    return (
        _valid_string_list(outputs)
        and isinstance(verifications, list)
        and all(_valid_required_verification(item) for item in verifications)
        and bool(outputs or verifications)
    )


def _valid_required_verification(value: object) -> bool:
    if _nonempty_text(value):
        return True
    if not isinstance(value, dict) or set(value) - {"name", "check"}:
        return False
    if not _nonempty_text(value.get("name")):
        return False
    check = value.get("check")
    return check is None or _valid_verification_check(check)


def _valid_verification_check(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    check_type = value.get("type")
    if check_type not in CHECK_TYPES:
        return False
    # 파일을 가리키지 않는 유일한 검사. 이 분기가 없으면 리드가 낸 응답이
    # 통째로 형식 오류가 되어 수리 요청으로 되돌아간다.
    if check_type == "command_succeeds":
        return set(value) == {"type", "command"} and _nonempty_text(value.get("command"))
    if not _nonempty_text(value.get("path")):
        return False
    expected = {"type", "path"}
    if check_type == "file_contains":
        expected.add("value")
    if check_type == "file_matches":
        expected.add("pattern")
    if set(value) != expected:
        return False
    detail_key = "value" if check_type == "file_contains" else "pattern"
    if detail_key in expected:
        return _nonempty_text(value.get(detail_key))
    return True


def _valid_string_list(value: object) -> bool:
    return isinstance(value, list) and all(_nonempty_text(item) for item in value)


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
        usage=(
            json.loads(row["usage_json"])
            if "usage_json" in row.keys() and row["usage_json"]
            else None
        ),
        failure_digest=row["failure_digest"],
        failure_shape=(
            json.loads(row["failure_shape_json"])
            if row["failure_shape_json"]
            else None
        ),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        applied_at=row["applied_at"],
        updated_at=str(row["updated_at"]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
