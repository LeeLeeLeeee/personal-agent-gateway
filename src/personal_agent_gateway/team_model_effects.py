from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.team_collaboration import agent_label
from personal_agent_gateway.team_collaboration_service import UnknownRecipient
from personal_agent_gateway.team_acceptance import (
    AcceptanceResult,
    is_recoverable_acceptance_failure,
    is_worker_declared_outcome,
    rejected_verification_names,
    terminal_rejected_status,
)
from personal_agent_gateway.team_model_operations import (
    CONTEST_VERDICT_KINDS,
    OperationConflict,
    OperationResultValidatorRegistry,
    StaleOperation,
    TeamModelOperation,
    TeamModelOperationService,
)
from personal_agent_gateway.team_plan_negotiation import (
    PlanReview,
    PlanReviewError,
    discarded_task_ids,
    parse_plan_review,
)
from personal_agent_gateway.team_repair_stages import REPAIR_STAGE
from personal_agent_gateway.team_outcomes import (
    TaskOutcome,
    TaskOutcomeError,
    parse_task_outcome,
)
from personal_agent_gateway.team_verification_checks import (
    CHECK_TYPES,
    VerificationCheck,
    verification_check_payload,
)
from personal_agent_gateway.teams import (
    ACCEPTANCE_RECOVERY_CAP,
    TaskAcceptance,
    TeamAgent,
    TeamDecisionRequest,
    TeamMessage,
    TeamRunService,
    TeamTask,
    _acceptance_review_metadata,
    _task_acceptance_json,
    _validate_task_acceptance,
    parse_required_verifications,
)

_LOGGER = logging.getLogger(__name__)


if TYPE_CHECKING:
    from personal_agent_gateway.team_collaboration_service import (
        TeamCollaborationService,
    )
    from personal_agent_gateway.team_runtime import AcceptanceReviewResolution


_PLAN_STAGES = {
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "cycle_contest",
    "cycle_contest_repair",
}
_SYNTHESIS_STAGES = {
    "cycle_synthesis",
    "cycle_synthesis_repair",
}
_PLAN_REVIEW_STAGES = {
    "cycle_plan_review",
    "cycle_plan_review_repair",
}
MediationResolution = Mapping[str, object]


@dataclass(frozen=True)
class WorkerEffectResult:
    task: TeamTask
    agent: TeamAgent
    next_stage: Literal[
        "acceptance_lead",
        "mediation_lead",
        "consult_peer",
        "user_decision",
    ] | None
    message: TeamMessage | None = None
    decision_request: TeamDecisionRequest | None = None


@dataclass(frozen=True)
class MediationEffectResult:
    task: TeamTask
    agent: TeamAgent
    next_stage: Literal["mediation_worker", "user_decision"]
    message: TeamMessage | None = None
    decision_request: TeamDecisionRequest | None = None


@dataclass(frozen=True)
class AcceptanceEffectResult:
    task: TeamTask
    agent: TeamAgent
    next_stage: Literal["acceptance_worker", "user_decision"] | None
    attempt: int
    message: TeamMessage
    decision_request: TeamDecisionRequest | None = None


@dataclass(frozen=True)
class ContestOutcome:
    kind: str
    reason: str
    tasks: list[TeamTask]
    question: str | None
    supersedes: tuple[dict[str, str], ...]


class TeamModelEffectService:
    def __init__(
        self,
        db: Database,
        teams: TeamRunService,
        operations: TeamModelOperationService,
        collaboration: TeamCollaborationService | None = None,
    ) -> None:
        self._db = db
        self._teams = teams
        self._operations = operations
        # None means the peer-message channel is off, which is what every
        # construction site that predates it keeps getting.
        self._collaboration = collaboration

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
            # Continue the cycle's ordinal sequence instead of restarting at 0:
            # add-work plans land on a cycle that already holds tasks, and
            # TeamRunService.create_task numbers the same way.
            ordinal_base = int(
                connection.execute(
                    """
                    select coalesce(max(plan_ordinal), -1) + 1 from team_tasks
                    where team_run_id = ? and cycle_id is ?
                    """,
                    (operation.team_run_id, operation.cycle_id),
                ).fetchone()[0]
            )
            tasks = [
                self._create_task(connection, operation, spec, now, ordinal_base + index)
                for index, spec in enumerate(specs)
            ]
            self._persist_plan_dependencies(connection, specs, tasks)
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

    def apply_contest_verdict(self, operation_id: str) -> ContestOutcome:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            self._validate_plan_operation(connection, operation)
            if operation.status == "applied":
                return self._replay_contest_verdict(connection, operation)
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )

            payload = _contest_verdict_payload(operation)
            specs = payload.get("tasks") or []
            # Same ordinal continuation as apply_plan: an amend or partial verdict
            # lands on a cycle that may already hold tasks.
            ordinal_base = int(
                connection.execute(
                    """
                    select coalesce(max(plan_ordinal), -1) + 1 from team_tasks
                    where team_run_id = ? and cycle_id is ?
                    """,
                    (operation.team_run_id, operation.cycle_id),
                ).fetchone()[0]
            )
            tasks = [
                self._create_task(connection, operation, spec, now, ordinal_base + index)
                for index, spec in enumerate(specs)
            ]
            self._persist_plan_dependencies(connection, specs, tasks)
            reason = payload["reason"]
            supersedes = tuple(dict(entry) for entry in payload.get("supersedes") or [])
            content = _contest_adjudication_content(payload["kind"], reason, supersedes)
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, null, 'plan_adjudication', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    operation.agent_id,
                    content,
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
                effect_type="contest_verdict",
                effect_ref={
                    "task_ids": [task.id for task in tasks],
                    "message_id": message_id,
                },
                now=now,
            )
            return ContestOutcome(
                kind=payload["kind"],
                reason=reason,
                tasks=tasks,
                question=payload.get("question") or None,
                supersedes=supersedes,
            )

    def apply_worker_outcome(
        self,
        operation_id: str,
        acceptance: AcceptanceResult | None,
        *,
        workspace_changes: Mapping[str, object],
    ) -> WorkerEffectResult:
        now = _now()
        applied_outcome: TaskOutcome | None = None
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
                result = self._replay_worker(
                    connection,
                    operation,
                    input_digest,
                )
                if operation.result_kind == "task_outcome":
                    # The replay path stores the notes too. Storing happens
                    # after this transaction commits (it has to -- see below),
                    # so a process death or a `database is locked` in that gap
                    # leaves the operation applied with the notes unwritten,
                    # and every later entry ends here. Without this the
                    # worker's note is gone with nothing to recover it from and
                    # undelivered_count reports 0.
                    applied_outcome = _task_outcome(operation)
            else:
                if operation.status != "completed":
                    raise StaleOperation(
                        f"Expected operation status completed, got {operation.status}"
                    )
                if task.status != "in_progress":
                    raise OperationConflict("Worker task is not in progress")
                if agent.status != "running" or agent.current_task_id != task.id:
                    raise OperationConflict(
                        "Worker is not running the operation task"
                    )

                _apply_mediation_reinvocation(
                    connection,
                    operation,
                    now,
                )
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
                        raise ValueError(
                            "Acceptance result is required for a task outcome"
                        )
                    result = self._apply_task_outcome(
                        connection,
                        operation,
                        task,
                        agent,
                        acceptance,
                        normalized_changes,
                        now,
                    )
                    applied_outcome = _task_outcome(operation)
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
        # Outside the transaction: append_message opens a second connection, so
        # calling this inside `begin immediate` deadlocks on the write lock, and
        # the failure path then rolls back a task that was already applied.
        self._store_mentions(operation, applied_outcome)
        return result

    def _store_mentions(
        self, operation: TeamModelOperation, outcome: TaskOutcome | None
    ) -> None:
        """Store the worker's notes. A failure must not undo the applied task.

        Collaboration is auxiliary: if a bad label or a failed write voided a
        finished worker task, the ADR's promise that a run which never turned
        this on keeps its lifecycle would not hold.

        Called on the replay path as well, which can store the same note twice.
        That is deliberate and within the ADR's licence -- duplicates are
        allowed, loss is forbidden -- and it is not made idempotent on purpose.
        A note carries no operation identity (`record_mentions` writes only
        `to_label` in the metadata), so the only key available here is
        (cycle, sender, label, text), and one worker can legitimately send the
        same text to the same teammate from two stages of one cycle. Skipping
        on that key would drop a real note to avoid a duplicate, which is the
        trade the ADR refuses.
        """
        if outcome is None:
            return
        if outcome.mention_refusals:
            # A note the parse turned away and a note addressed to nobody are
            # the same fault -- a malformed mention -- so they end the same way:
            # the task stands and the loss is written down. Only the reason_code
            # differs, because "the worker wrote a note we could not accept" and
            # "the worker named someone who is not here" send a reader looking
            # in different places.
            #
            # Above the `_collaboration is None` guard on purpose: writing this
            # down needs the message log, not the channel. TeamRuntime's own
            # default builds the shape where the two disagree -- pass
            # `collaboration` to the runtime and let `model_effects` default --
            # and under it the runtime renders the radio prefix while every
            # refusal here would go unrecorded.
            self._record_degraded_collaboration(
                operation,
                "mention_malformed",
                "mentions were refused as malformed: "
                + ", ".join(outcome.mention_refusals),
            )
        if self._collaboration is None or not outcome.mentions:
            return
        try:
            self._collaboration.record_mentions(
                operation.team_run_id,
                operation.cycle_id,
                operation.agent_id,
                outcome.mentions,
            )
        except Exception as exc:  # noqa: BLE001 - auxiliary work never voids the task
            # A worker naming a recipient that does not exist and a bug in our
            # own code are different facts: reporting both as mention_rejected
            # would send someone reading the run to the model's note when the
            # cause is a TypeError here.
            reason_code = (
                "mention_rejected"
                if isinstance(exc, UnknownRecipient)
                else "mention_store_failed"
            )
            self._record_degraded_collaboration(
                operation,
                reason_code,
                f"mentions were not stored: {type(exc).__name__}: {exc}",
            )

    def _record_degraded_collaboration(
        self,
        operation: TeamModelOperation,
        reason_code: str,
        content: str,
    ) -> None:
        """The one shape every collaboration loss is recorded in."""
        try:
            self._teams.append_message(
                operation.team_run_id,
                None,
                operation.agent_id,
                "collaboration_degraded",
                content,
                {"reason_code": reason_code},
                cycle_id=operation.cycle_id,
            )
        except Exception:  # noqa: BLE001 - recording the degradation cannot fail the run
            # This write can fail for the same reason the note write did (the
            # write lock), and letting it escape would tell the caller that an
            # operation the ledger already marked applied had failed.
            _LOGGER.warning(
                "could not record degraded collaboration for run %s: %s",
                operation.team_run_id,
                content,
                exc_info=True,
            )

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
            # The routing decision lives here, at effect time, so recovery
            # replays it from the ledger instead of re-deciding: a query
            # addressed to a teammate's roster label goes to that teammate as
            # a peer note and the consult stage answers it; anything else --
            # no label, an unknown label, the asker's own label, LEAD, or the
            # peer channel being off -- takes the classic mediation route with
            # the classic message shape, byte for byte.
            peer_id = self._consult_recipient(connection, operation, query)
            if peer_id is not None:
                recipient_id = peer_id
                message_kind = "peer_mention"
                next_stage = "consult_peer"
                metadata = {
                    "operation_id": operation.id,
                    "task_id": task.id,
                    "topic": query["topic"],
                    "to_label": query["to"],
                }
            else:
                recipient_id = run["leader_agent_id"]
                message_kind = "query"
                next_stage = "mediation_lead"
                metadata = {
                    "operation_id": operation.id,
                    "task_id": task.id,
                    "topic": query["topic"],
                }
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    operation.agent_id,
                    recipient_id,
                    message_kind,
                    query["question"],
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            _apply_mediation_reinvocation(
                connection,
                operation,
                now,
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
                    "next_stage": next_stage,
                },
                now=now,
            )
            return WorkerEffectResult(
                task=self._teams._task_from_connection(connection, task.id),
                agent=self._teams._agent_from_connection(connection, agent.id),
                next_stage=next_stage,
                message=self._teams._message_from_connection(connection, message_id),
            )

    def _consult_recipient(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        query: dict[str, object],
    ) -> str | None:
        """The teammate a query's "to" label names, or None for the lead route.

        None is a routing verdict, never an error: a worker that mislabels a
        teammate still deserves an answer, and the lead can give one. Raising
        here would fail the whole worker operation for a one-character label
        typo. Labels resolve by the same ordering labels_for_run uses
        (created_at, id), so the mapping is stable across recovery replays.
        """
        if self._collaboration is None:
            return None
        to = query.get("to")
        if not isinstance(to, str) or not to.strip():
            return None
        rows = connection.execute(
            """
            select id, role from team_agents where team_run_id = ?
            order by created_at asc, id asc
            """,
            (operation.team_run_id,),
        ).fetchall()
        labels: dict[str, str] = {}
        ordinal = 0
        for row in rows:
            if row["role"] == "leader":
                labels[agent_label("leader", None)] = row["id"]
                continue
            ordinal += 1
            labels[agent_label("member", ordinal)] = row["id"]
        recipient = labels.get(to.strip().upper())
        if recipient is None or recipient == operation.agent_id:
            return None
        leader = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        if leader is not None and recipient == leader["leader_agent_id"]:
            return None
        return recipient

    def apply_mediation_lead(
        self,
        operation_id: str,
        resolution: MediationResolution,
    ) -> MediationEffectResult:
        now = _now()
        normalized = _mediation_resolution_payload(resolution)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            task, leader, worker = self._validate_lead_operation(
                connection,
                operation,
                "mediation_lead",
            )
            stored = _result_payload(operation, "mediation_resolution")
            if stored != normalized:
                raise OperationConflict(
                    "Mediation resolution does not match the completed operation"
                )
            if operation.status == "applied":
                return self._replay_mediation_lead(
                    connection,
                    operation,
                    task,
                    leader,
                    worker,
                    normalized,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            cycle = connection.execute(
                "select rounds_used from team_run_cycles where id = ?",
                (operation.cycle_id,),
            ).fetchone()
            if (
                cycle is None
                or int(cycle["rounds_used"]) + 1 != operation.stage_ordinal
            ):
                raise OperationConflict(
                    "Mediation operation does not match the current round"
                )
            if task.status != "in_progress":
                raise OperationConflict("Mediation task is not in progress")
            if worker.status != "running" or worker.current_task_id != task.id:
                raise OperationConflict("Mediation Worker is not running the task")

            query_message = self._query_receipt(connection, operation)
            message_id: str | None = None
            request_id: str | None = None
            decision_item_id: str | None = None
            decision_item_digest: str | None = None
            cursor = connection.execute(
                """
                update team_run_cycles
                set rounds_used = rounds_used + 1, updated_at = ?
                where id = ? and rounds_used = ?
                  and rounds_used < rounds_budget
                """,
                (
                    now,
                    operation.cycle_id,
                    operation.stage_ordinal - 1,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationConflict(
                    "Mediation round changed before effect application"
                )
            if normalized["kind"] == "answer":
                message_id = uuid4().hex
                connection.execute(
                    """
                    insert into team_messages (
                        id, team_run_id, cycle_id, sender_agent_id,
                        recipient_agent_id, kind, content, metadata_json,
                        created_at
                    ) values (?, ?, ?, ?, ?, 'answer', ?, ?, ?)
                    """,
                    (
                        message_id,
                        operation.team_run_id,
                        operation.cycle_id,
                        leader.id,
                        worker.id,
                        normalized["answer"],
                        json.dumps(
                            {
                                "operation_id": operation.id,
                                "task_id": task.id,
                                "round": operation.stage_ordinal,
                                "query_id": query_message.id,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
                next_stage = "mediation_worker"
            else:
                decision = dict(normalized)
                decision["query_message_id"] = query_message.id
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
                    set status = 'waiting_for_user', result = null, error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, task.id),
                )
                connection.execute(
                    """
                    update team_agents
                    set status = 'waiting', current_task_id = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, worker.id),
                )
                request = self._teams._decision_request_from_connection(
                    connection,
                    request_id,
                )
                decision_item_id, decision_item_digest = _task_decision_receipt(
                    connection,
                    operation,
                    request,
                    decision,
                    task.id,
                    query_message_id=query_message.id,
                )
                next_stage = "user_decision"

            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="mediation_lead",
                effect_ref={
                    "task_id": task.id,
                    "worker_agent_id": worker.id,
                    "query_message_id": query_message.id,
                    "answer_message_id": message_id,
                    "decision_request_id": request_id,
                    "decision_item_id": decision_item_id,
                    "decision_item_digest": decision_item_digest,
                    "next_stage": next_stage,
                    "round": operation.stage_ordinal,
                    "resolution_digest": _canonical_digest(normalized),
                },
                now=now,
            )
            return MediationEffectResult(
                task=self._teams._task_from_connection(connection, task.id),
                agent=self._teams._agent_from_connection(connection, worker.id),
                next_stage=next_stage,
                message=(
                    self._teams._message_from_connection(connection, message_id)
                    if message_id is not None
                    else None
                ),
                decision_request=(
                    self._teams._decision_request_from_connection(
                        connection,
                        request_id,
                    )
                    if request_id is not None
                    else None
                ),
            )

    def apply_consult_peer(
        self,
        operation_id: str,
        resolution: MediationResolution,
    ) -> MediationEffectResult:
        """A teammate's answer to a peer-addressed query, onto the ledger.

        Mirrors apply_mediation_lead with two deliberate narrowings: the
        respondent is the fellow worker the query named rather than the lead,
        and the only legal resolution is an answer -- a peer holds no
        authority to escalate to the user, so a peer that cannot answer says
        so in the answer text and the asker escalates to the lead itself.
        Both messages ride the peer_mention ledger: the consult IS the radio
        channel used as a conversation.
        """
        now = _now()
        normalized = _consult_resolution_payload(resolution)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            task, peer, worker = self._validate_consult_operation(
                connection,
                operation,
            )
            stored = _result_payload(operation, "consult_resolution")
            if stored != normalized:
                raise OperationConflict(
                    "Consult resolution does not match the completed operation"
                )
            if operation.status == "applied":
                return self._replay_consult_peer(
                    connection,
                    operation,
                    task,
                    peer,
                    worker,
                    normalized,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            cycle = connection.execute(
                "select rounds_used from team_run_cycles where id = ?",
                (operation.cycle_id,),
            ).fetchone()
            if (
                cycle is None
                or int(cycle["rounds_used"]) + 1 != operation.stage_ordinal
            ):
                raise OperationConflict(
                    "Consult operation does not match the current round"
                )
            if task.status != "in_progress":
                raise OperationConflict("Consult task is not in progress")
            if worker.status != "running" or worker.current_task_id != task.id:
                raise OperationConflict("Consult asker is not running the task")

            query_message = self._consult_query_receipt(connection, operation)
            cursor = connection.execute(
                """
                update team_run_cycles
                set rounds_used = rounds_used + 1, updated_at = ?
                where id = ? and rounds_used = ?
                  and rounds_used < rounds_budget
                """,
                (
                    now,
                    operation.cycle_id,
                    operation.stage_ordinal - 1,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationConflict(
                    "Consult round changed before effect application"
                )
            message_id = uuid4().hex
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id,
                    recipient_agent_id, kind, content, metadata_json,
                    created_at
                ) values (?, ?, ?, ?, ?, 'peer_mention', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    peer.id,
                    worker.id,
                    normalized["answer"],
                    json.dumps(
                        {
                            "operation_id": operation.id,
                            "task_id": task.id,
                            "round": operation.stage_ordinal,
                            "query_id": query_message.id,
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
                effect_type="consult_peer",
                effect_ref={
                    "task_id": task.id,
                    "worker_agent_id": worker.id,
                    "query_message_id": query_message.id,
                    "answer_message_id": message_id,
                    "next_stage": "mediation_worker",
                    "round": operation.stage_ordinal,
                    "resolution_digest": _canonical_digest(normalized),
                },
                now=now,
            )
            return MediationEffectResult(
                task=self._teams._task_from_connection(connection, task.id),
                agent=self._teams._agent_from_connection(connection, worker.id),
                next_stage="mediation_worker",
                message=self._teams._message_from_connection(
                    connection, message_id
                ),
            )

    def _validate_consult_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> tuple[TeamTask, TeamAgent, TeamAgent]:
        if (
            operation.stage
            not in {"consult_peer", REPAIR_STAGE.get("consult_peer")}
            or operation.task_id is None
        ):
            raise OperationConflict("Operation is not a consult_peer stage")
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        if (
            run is None
            or run["leader_agent_id"] == operation.agent_id
            or cycle is None
            or cycle["team_run_id"] != operation.team_run_id
        ):
            raise OperationConflict("Consult operation actor or cycle is invalid")
        task = self._teams._task_from_connection(
            connection,
            operation.task_id,
        )
        peer = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if task.owner_agent_id is None:
            raise OperationConflict("Consult operation task has no Worker owner")
        worker = self._teams._agent_from_connection(
            connection,
            task.owner_agent_id,
        )
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or peer.team_run_id != operation.team_run_id
            or worker.team_run_id != operation.team_run_id
            or peer.id == worker.id
        ):
            raise OperationConflict("Consult operation ownership is invalid")
        return task, peer, worker

    def _consult_query_receipt(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> TeamMessage:
        row = connection.execute(
            """
            select id from team_model_operations
            where team_run_id = ? and cycle_id = ? and task_id = ?
              and status = 'applied' and result_kind = 'worker_query'
            order by applied_at desc, created_at desc, id desc limit 1
            """,
            (
                operation.team_run_id,
                operation.cycle_id,
                operation.task_id,
            ),
        ).fetchone()
        if row is None:
            raise OperationConflict("Consult has no applied Worker query")
        source = self._operations._get(connection, row["id"])
        effect_ref = source.effect_ref_json
        query = _worker_query(source)
        message_id = (
            effect_ref.get("message_id")
            if isinstance(effect_ref, dict)
            else None
        )
        if not isinstance(message_id, str):
            raise OperationConflict("Applied Worker query receipt is invalid")
        message = self._teams._message_from_connection(connection, message_id)
        if (
            source.effect_type != "worker_query"
            or not isinstance(effect_ref, dict)
            or effect_ref.get("task_id") != operation.task_id
            or effect_ref.get("agent_id") != source.agent_id
            or effect_ref.get("next_stage") != "consult_peer"
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != source.agent_id
            or message.recipient_agent_id != operation.agent_id
            or message.kind != "peer_mention"
            or message.content != query["question"]
            or message.metadata
            != {
                "operation_id": source.id,
                "task_id": operation.task_id,
                "topic": query["topic"],
                "to_label": query["to"],
            }
        ):
            raise OperationConflict("Applied consult query rows do not match")
        return message

    def _replay_consult_peer(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        peer: TeamAgent,
        worker: TeamAgent,
        resolution: dict[str, object],
    ) -> MediationEffectResult:
        effect_ref = operation.effect_ref_json
        expected_keys = {
            "task_id",
            "worker_agent_id",
            "query_message_id",
            "answer_message_id",
            "next_stage",
            "round",
            "resolution_digest",
        }
        if (
            operation.effect_type != "consult_peer"
            or not isinstance(effect_ref, dict)
            or set(effect_ref) != expected_keys
            or effect_ref["task_id"] != task.id
            or effect_ref["worker_agent_id"] != worker.id
            or effect_ref["round"] != operation.stage_ordinal
            or effect_ref["next_stage"] != "mediation_worker"
            or effect_ref["resolution_digest"] != _canonical_digest(resolution)
            or not _operation_session_matches(operation, peer)
        ):
            raise OperationConflict("Applied consult receipt is invalid")
        answer_id = effect_ref["answer_message_id"]
        if not isinstance(answer_id, str):
            raise OperationConflict("Applied consult answer receipt is invalid")
        message = self._teams._message_from_connection(connection, answer_id)
        cycle = connection.execute(
            "select rounds_used from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        if (
            cycle is None
            or int(cycle["rounds_used"]) < operation.stage_ordinal
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != peer.id
            or message.recipient_agent_id != worker.id
            or message.kind != "peer_mention"
            or message.content != resolution["answer"]
        ):
            raise OperationConflict("Applied consult answer rows do not match")
        return MediationEffectResult(
            task=task,
            agent=worker,
            next_stage="mediation_worker",
            message=message,
        )

    def apply_acceptance_lead(
        self,
        operation_id: str,
        resolution: AcceptanceReviewResolution,
    ) -> AcceptanceEffectResult:
        now = _now()
        normalized = _acceptance_resolution_payload(resolution)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            task, leader, worker = self._validate_lead_operation(
                connection,
                operation,
                "acceptance_lead",
            )
            stored = _result_payload(operation, "acceptance_review")
            if stored != normalized:
                raise OperationConflict(
                    "Acceptance resolution does not match the completed operation"
                )
            if operation.status == "applied":
                return self._replay_acceptance_lead(
                    connection,
                    operation,
                    task,
                    leader,
                    worker,
                    normalized,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            if (
                task.status != "in_progress"
                or worker.status != "running"
                or worker.current_task_id != task.id
            ):
                raise OperationConflict("Acceptance task is not actively owned")
            if task.acceptance_recovery_attempts + 1 != operation.stage_ordinal:
                raise OperationConflict(
                    "Acceptance operation does not match the current attempt"
                )
            outcome = _persisted_task_outcome(task)
            acceptance = _persisted_acceptance_result(task)
            reason_code = (
                normalized["reason_code"]
                if normalized["kind"] == "fail"
                else acceptance.reason_code
                or outcome.reason_code
                or "task_failed"
            )
            assert isinstance(reason_code, str)
            verification_status = {
                item.name: item.status for item in outcome.verifications
            }
            acceptance_before = json.loads(_task_acceptance_json(task.acceptance))
            acceptance_after = normalized["acceptance"]
            consumes_attempt = normalized["kind"] in {
                "retry_worker",
                "revise_acceptance",
            }
            next_attempt = task.acceptance_recovery_attempts + int(
                consumes_attempt
            )
            if (
                consumes_attempt
                and next_attempt > ACCEPTANCE_RECOVERY_CAP
            ):
                raise OperationConflict("Acceptance recovery limit reached")
            acceptance_json = (
                json.dumps(
                    acceptance_after,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if normalized["kind"] == "revise_acceptance"
                else json.dumps(
                    acceptance_before,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            if consumes_attempt:
                connection.execute(
                    """
                    update team_tasks
                    set acceptance_recovery_attempts = ?, acceptance_json = ?,
                        updated_at = ? where id = ?
                    """,
                    (next_attempt, acceptance_json, now, task.id),
                )

            message_id = uuid4().hex
            message_metadata = _acceptance_review_metadata(
                operation_id=operation.id,
                task_id=task.id,
                attempt=operation.stage_ordinal,
                reason_code=reason_code,
                action=normalized["kind"],
                reason=normalized["reason"],
                instruction=normalized["instruction"],
                acceptance_before=acceptance_before,
                acceptance_after=acceptance_after,
                rejected_deliverables=[
                    item.path for item in outcome.deliverables
                ],
                rejected_verifications=rejected_verification_names(
                    (
                        (required.name, required.check is not None)
                        for required in task.acceptance.required_verifications
                    ),
                    verification_status,
                    acceptance.evidence,
                ),
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id,
                    recipient_agent_id, kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, 'acceptance_review', ?, ?, ?)
                """,
                (
                    message_id,
                    operation.team_run_id,
                    operation.cycle_id,
                    leader.id,
                    worker.id,
                    normalized["instruction"] or normalized["reason"],
                    json.dumps(
                        message_metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )

            request_id: str | None = None
            decision_item_id: str | None = None
            decision_item_digest: str | None = None
            if consumes_attempt:
                next_stage = "acceptance_worker"
            elif normalized["kind"] == "ask_user":
                decision = normalized["decision"]
                assert isinstance(decision, dict)
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
                    set status = 'waiting_for_user', result = null, error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, task.id),
                )
                connection.execute(
                    """
                    update team_agents
                    set status = 'waiting', current_task_id = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, worker.id),
                )
                request = self._teams._decision_request_from_connection(
                    connection,
                    request_id,
                )
                decision_item_id, decision_item_digest = _task_decision_receipt(
                    connection,
                    operation,
                    request,
                    decision,
                    task.id,
                )
                next_stage = "user_decision"
            else:
                connection.execute(
                    """
                    update team_tasks
                    set status = 'failed', result = null, error_message = ?,
                        finished_at = ?, updated_at = ? where id = ?
                    """,
                    (reason_code, now, now, task.id),
                )
                connection.execute(
                    """
                    update team_agents
                    set status = 'failed', current_task_id = null,
                        finished_at = ?, updated_at = ? where id = ?
                    """,
                    (now, now, worker.id),
                )
                next_stage = None

            _promote_actor_session(connection, operation, now)
            _mark_applied(
                connection,
                operation,
                effect_type="acceptance_lead",
                effect_ref={
                    "task_id": task.id,
                    "worker_agent_id": worker.id,
                    "message_id": message_id,
                    "audit_digest": _canonical_digest(
                        {
                            "content": (
                                normalized["instruction"]
                                or normalized["reason"]
                            ),
                            "metadata": message_metadata,
                        }
                    ),
                    "decision_request_id": request_id,
                    "decision_item_id": decision_item_id,
                    "decision_item_digest": decision_item_digest,
                    "next_stage": next_stage,
                    "attempt": operation.stage_ordinal,
                    "resolution_digest": _canonical_digest(normalized),
                },
                now=now,
            )
            return AcceptanceEffectResult(
                task=self._teams._task_from_connection(connection, task.id),
                agent=self._teams._agent_from_connection(connection, worker.id),
                next_stage=next_stage,
                attempt=operation.stage_ordinal,
                message=self._teams._message_from_connection(
                    connection,
                    message_id,
                ),
                decision_request=(
                    self._teams._decision_request_from_connection(
                        connection,
                        request_id,
                    )
                    if request_id is not None
                    else None
                ),
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
            task_rows = _live_cycle_task_rows(
                connection,
                operation.team_run_id,
                operation.cycle_id,
            )
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

    def apply_plan_review(
        self,
        operation_id: str,
        plan_revision_id: str,
    ) -> PlanReview:
        """Record one owner's verdict on a plan revision and close its operation.

        The review row and the applied flag are written in one transaction, the
        way every other stage's effect is: closing the operation first and
        recording the review afterwards left a crash window in which the run
        held a verdict nobody could see, and the review row is the only thing
        the negotiation loop reads back.

        Idempotent on replay: a second call on an already-applied operation
        returns the stored verdict instead of asking the model again, after
        checking that the row the first call wrote is still there and still
        says the same thing.
        """
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            revision = self._validate_plan_review_operation(
                connection,
                operation,
                plan_revision_id,
            )
            review = _plan_review(operation)
            if operation.status == "applied":
                return self._replay_plan_review(
                    connection,
                    operation,
                    plan_revision_id,
                    review,
                )
            if operation.status != "completed":
                raise StaleOperation(
                    f"Expected operation status completed, got {operation.status}"
                )
            if revision["status"] != "awaiting_approval":
                raise OperationConflict(
                    "Plan revision is no longer awaiting approval"
                )
            try:
                connection.execute(
                    """
                    insert into team_plan_approvals (
                        id, plan_revision_id, agent_id, decision,
                        objections_json, created_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        plan_revision_id,
                        operation.agent_id,
                        review.decision,
                        _objections_json(review),
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # The unique index on (plan_revision_id, agent_id) is the
                # source of truth for "once". Re-asking a reviewer that already
                # answered could flip an approval, so it is refused here rather
                # than checked first.
                raise OperationConflict(
                    "Plan review is already recorded for this agent"
                ) from exc
            _mark_applied(
                connection,
                operation,
                effect_type="plan_review",
                effect_ref={
                    "plan_revision_id": plan_revision_id,
                    "agent_id": operation.agent_id,
                },
                now=now,
            )
            return review

    def _validate_plan_review_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        plan_revision_id: str,
    ) -> sqlite3.Row:
        if (
            operation.stage not in _PLAN_REVIEW_STAGES
            or operation.task_id is not None
            or operation.result_kind != "plan_review"
        ):
            raise OperationConflict("Operation is not a plan review stage")
        revision = connection.execute(
            """
            select status, team_run_id, cycle_id from team_plan_revisions
            where id = ?
            """,
            (plan_revision_id,),
        ).fetchone()
        if (
            revision is None
            or revision["team_run_id"] != operation.team_run_id
            or revision["cycle_id"] != operation.cycle_id
        ):
            raise OperationConflict(
                "Plan revision does not belong to the review's cycle"
            )
        agent = self._teams._agent_from_connection(connection, operation.agent_id)
        if agent.team_run_id != operation.team_run_id:
            raise OperationConflict("Plan reviewer does not belong to the run")
        return revision

    def _replay_plan_review(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        plan_revision_id: str,
        review: PlanReview,
    ) -> PlanReview:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "plan_review"
            or not isinstance(effect_ref, dict)
            or set(effect_ref) != {"plan_revision_id", "agent_id"}
            or effect_ref["plan_revision_id"] != plan_revision_id
            or effect_ref["agent_id"] != operation.agent_id
        ):
            raise OperationConflict("Applied plan review effect reference is invalid")
        row = connection.execute(
            """
            select decision, objections_json from team_plan_approvals
            where plan_revision_id = ? and agent_id = ?
            """,
            (plan_revision_id, operation.agent_id),
        ).fetchone()
        if (
            row is None
            or row["decision"] != review.decision
            or _loads_objections(row["objections_json"])
            != [asdict(objection) for objection in review.objections]
        ):
            raise OperationConflict(
                "Applied plan review rows do not match the operation"
            )
        return review

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
            operation.stage not in _SYNTHESIS_STAGES
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
            operation.stage not in _SYNTHESIS_STAGES
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
        _terminal_status(
            _live_cycle_task_rows(
                connection,
                operation.team_run_id,
                operation.cycle_id,
            )
        )

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
        terminal_status = _terminal_status(
            _live_cycle_task_rows(
                connection,
                operation.team_run_id,
                operation.cycle_id,
            )
        )
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
        if (
            operation.stage
            not in {
                "worker_execution",
                "mediation_worker",
                "mediation_worker_repair",
                "acceptance_worker",
                "acceptance_worker_repair",
            }
            or operation.task_id is None
        ):
            raise OperationConflict("Operation is not a Worker execution stage")
        allowed_result_kinds = {
            "task_outcome",
        }
        if operation.stage in {"worker_execution", "mediation_worker"}:
            allowed_result_kinds.add("worker_query")
        if operation.stage == "worker_execution":
            allowed_result_kinds.add("user_decision")
        if operation.result_kind not in allowed_result_kinds:
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

    def _validate_lead_operation(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        stage: Literal["mediation_lead", "acceptance_lead"],
    ) -> tuple[TeamTask, TeamAgent, TeamAgent]:
        # A repair re-emits the same result for the same stage, so its effect is
        # the base stage's effect. Requiring an exact match here rejected the
        # repair after it had already succeeded.
        if (
            operation.stage not in {stage, REPAIR_STAGE.get(stage)}
            or operation.task_id is None
        ):
            raise OperationConflict(f"Operation is not a {stage} stage")
        run = connection.execute(
            "select leader_agent_id from team_runs where id = ?",
            (operation.team_run_id,),
        ).fetchone()
        cycle = connection.execute(
            "select team_run_id from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        if (
            run is None
            or run["leader_agent_id"] != operation.agent_id
            or cycle is None
            or cycle["team_run_id"] != operation.team_run_id
        ):
            raise OperationConflict("Lead operation actor or cycle is invalid")
        task = self._teams._task_from_connection(
            connection,
            operation.task_id,
        )
        leader = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if task.owner_agent_id is None:
            raise OperationConflict("Lead operation task has no Worker owner")
        worker = self._teams._agent_from_connection(
            connection,
            task.owner_agent_id,
        )
        if (
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or leader.team_run_id != operation.team_run_id
            or worker.team_run_id != operation.team_run_id
            or worker.id == leader.id
        ):
            raise OperationConflict("Lead operation ownership is invalid")
        return task, leader, worker

    def _query_receipt(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> TeamMessage:
        row = connection.execute(
            """
            select id from team_model_operations
            where team_run_id = ? and cycle_id = ? and task_id = ?
              and status = 'applied' and result_kind = 'worker_query'
            order by applied_at desc, created_at desc, id desc limit 1
            """,
            (
                operation.team_run_id,
                operation.cycle_id,
                operation.task_id,
            ),
        ).fetchone()
        if row is None:
            raise OperationConflict("Mediation has no applied Worker query")
        source = self._operations._get(connection, row["id"])
        effect_ref = source.effect_ref_json
        query = _worker_query(source)
        message_id = (
            effect_ref.get("message_id")
            if isinstance(effect_ref, dict)
            else None
        )
        if not isinstance(message_id, str):
            raise OperationConflict("Applied Worker query receipt is invalid")
        message = self._teams._message_from_connection(connection, message_id)
        if (
            source.effect_type != "worker_query"
            or not isinstance(effect_ref, dict)
            or effect_ref.get("task_id") != operation.task_id
            or effect_ref.get("agent_id") != source.agent_id
            or effect_ref.get("next_stage") != "mediation_lead"
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != source.agent_id
            or message.recipient_agent_id != operation.agent_id
            or message.kind != "query"
            or message.content != query["question"]
            or message.metadata
            != {
                "operation_id": source.id,
                "task_id": operation.task_id,
                "topic": query["topic"],
            }
        ):
            raise OperationConflict(
                "Applied Worker query rows do not match"
            )
        return message

    def _replay_mediation_lead(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        leader: TeamAgent,
        worker: TeamAgent,
        resolution: dict[str, object],
    ) -> MediationEffectResult:
        effect_ref = operation.effect_ref_json
        expected_keys = {
            "task_id",
            "worker_agent_id",
            "query_message_id",
            "answer_message_id",
            "decision_request_id",
            "decision_item_id",
            "decision_item_digest",
            "next_stage",
            "round",
            "resolution_digest",
        }
        if (
            operation.effect_type != "mediation_lead"
            or not isinstance(effect_ref, dict)
            or set(effect_ref) != expected_keys
            or effect_ref["task_id"] != task.id
            or effect_ref["worker_agent_id"] != worker.id
            or effect_ref["round"] != operation.stage_ordinal
            or effect_ref["resolution_digest"]
            != _canonical_digest(resolution)
            or not _operation_session_matches(operation, leader)
        ):
            raise OperationConflict("Applied mediation receipt is invalid")
        query_id = effect_ref["query_message_id"]
        if not isinstance(query_id, str):
            raise OperationConflict("Applied mediation query receipt is invalid")
        query = self._teams._message_from_connection(connection, query_id)
        if (
            query.team_run_id != operation.team_run_id
            or query.cycle_id != operation.cycle_id
            or query.sender_agent_id != worker.id
            or query.recipient_agent_id != leader.id
            or query.kind != "query"
        ):
            raise OperationConflict("Applied mediation query rows do not match")

        if effect_ref["next_stage"] == "mediation_worker":
            answer_id = effect_ref["answer_message_id"]
            if (
                not isinstance(answer_id, str)
                or effect_ref["decision_request_id"] is not None
                or effect_ref["decision_item_id"] is not None
                or effect_ref["decision_item_digest"] is not None
            ):
                raise OperationConflict(
                    "Applied mediation answer receipt is invalid"
                )
            message = self._teams._message_from_connection(
                connection,
                answer_id,
            )
            cycle = connection.execute(
                "select rounds_used from team_run_cycles where id = ?",
                (operation.cycle_id,),
            ).fetchone()
            if (
                cycle is None
                or cycle["rounds_used"] < operation.stage_ordinal
                or task.status != "in_progress"
                or worker.status != "running"
                or worker.current_task_id != task.id
                or message.sender_agent_id != leader.id
                or message.recipient_agent_id != worker.id
                or message.kind != "answer"
                or message.content != resolution["answer"]
                or message.metadata
                != {
                    "operation_id": operation.id,
                    "task_id": task.id,
                    "round": operation.stage_ordinal,
                    "query_id": query.id,
                }
            ):
                raise OperationConflict("Applied mediation answer rows do not match")
            return MediationEffectResult(
                task=task,
                agent=worker,
                next_stage="mediation_worker",
                message=message,
            )

        if (
            effect_ref["next_stage"] != "user_decision"
            or effect_ref["answer_message_id"] is not None
            or not isinstance(effect_ref["decision_request_id"], str)
            or not isinstance(effect_ref["decision_item_id"], str)
            or not isinstance(effect_ref["decision_item_digest"], str)
            or task.status != "waiting_for_user"
            or worker.status != "waiting"
            or worker.current_task_id is not None
        ):
            raise OperationConflict("Applied mediation decision rows do not match")
        request = self._teams._decision_request_from_connection(
            connection,
            effect_ref["decision_request_id"],
        )
        cycle = connection.execute(
            "select rounds_used from team_run_cycles where id = ?",
            (operation.cycle_id,),
        ).fetchone()
        item = next(
            (
                candidate
                for candidate in request.items
                if candidate.get("id") == effect_ref["decision_item_id"]
            ),
            None,
        )
        decision = dict(resolution)
        decision["query_message_id"] = query.id
        if (
            request.team_run_id != operation.team_run_id
            or request.cycle_id != operation.cycle_id
            or cycle is None
            or cycle["rounds_used"] < operation.stage_ordinal
            or request.status != "collecting"
            or request.answers != {}
            or request.published_at is not None
            or request.answered_at is not None
            or item is None
            or lead_decision_item_digest(
                item,
                task.id,
                query.id,
            )
            != effect_ref["decision_item_digest"]
            or not _decision_item_matches(item, decision, task.id)
            or query.id not in item["query_message_ids"]
            or not _decision_item_references_are_valid(
                connection,
                operation,
                item,
            )
        ):
            raise OperationConflict(
                "Applied mediation decision rows do not match"
            )
        return MediationEffectResult(
            task=task,
            agent=worker,
            next_stage="user_decision",
            decision_request=request,
        )

    def _replay_acceptance_lead(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
        task: TeamTask,
        leader: TeamAgent,
        worker: TeamAgent,
        resolution: dict[str, object],
    ) -> AcceptanceEffectResult:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "acceptance_lead"
            or not isinstance(effect_ref, dict)
            or set(effect_ref)
            != {
                "task_id",
                "worker_agent_id",
                "message_id",
                "audit_digest",
                "decision_request_id",
                "decision_item_id",
                "decision_item_digest",
                "next_stage",
                "attempt",
                "resolution_digest",
            }
            or effect_ref["task_id"] != task.id
            or effect_ref["worker_agent_id"] != worker.id
            or effect_ref["attempt"] != operation.stage_ordinal
            or effect_ref["resolution_digest"]
            != _canonical_digest(resolution)
            or not _operation_session_matches(operation, leader)
            or not isinstance(effect_ref["message_id"], str)
            or not isinstance(effect_ref["audit_digest"], str)
        ):
            raise OperationConflict("Applied acceptance receipt is invalid")
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        if (
            message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != leader.id
            or message.recipient_agent_id != worker.id
            or message.kind != "acceptance_review"
            or message.metadata.get("operation_id") != operation.id
            or message.metadata.get("task_id") != task.id
            or message.metadata.get("attempt") != operation.stage_ordinal
            or message.metadata.get("action") != resolution["kind"]
            or not _acceptance_audit_matches(
                message,
                operation,
                task,
                resolution,
            )
            or _canonical_digest(
                {
                    "content": message.content,
                    "metadata": message.metadata,
                }
            )
            != effect_ref["audit_digest"]
        ):
            raise OperationConflict("Applied acceptance audit rows do not match")
        next_stage = effect_ref["next_stage"]
        request = None
        if next_stage == "acceptance_worker":
            if (
                effect_ref["decision_request_id"] is not None
                or effect_ref["decision_item_id"] is not None
                or effect_ref["decision_item_digest"] is not None
                or task.status != "in_progress"
                or worker.status != "running"
                or worker.current_task_id != task.id
                or task.acceptance_recovery_attempts
                != operation.stage_ordinal
            ):
                raise OperationConflict(
                    "Applied acceptance retry rows do not match"
                )
        elif next_stage == "user_decision":
            request_id = effect_ref["decision_request_id"]
            if (
                not isinstance(request_id, str)
                or not isinstance(effect_ref["decision_item_id"], str)
                or not isinstance(effect_ref["decision_item_digest"], str)
                or task.status != "waiting_for_user"
                or worker.status != "waiting"
                or worker.current_task_id is not None
            ):
                raise OperationConflict(
                    "Applied acceptance decision rows do not match"
                )
            request = self._teams._decision_request_from_connection(
                connection,
                request_id,
            )
            item = next(
                (
                    candidate
                    for candidate in request.items
                    if candidate.get("id")
                    == effect_ref["decision_item_id"]
                ),
                None,
            )
            decision = resolution["decision"]
            if (
                not isinstance(decision, dict)
                or request.team_run_id != operation.team_run_id
                or request.cycle_id != operation.cycle_id
                or request.status != "collecting"
                or request.answers != {}
                or request.published_at is not None
                or request.answered_at is not None
                or item is None
                or lead_decision_item_digest(
                    item,
                    task.id,
                    None,
                )
                != effect_ref["decision_item_digest"]
                or not _decision_item_matches(item, decision, task.id)
                or not _decision_item_references_are_valid(
                    connection,
                    operation,
                    item,
                )
            ):
                raise OperationConflict(
                    "Applied acceptance decision rows do not match"
                )
        elif next_stage is None:
            if (
                effect_ref["decision_request_id"] is not None
                or effect_ref["decision_item_id"] is not None
                or effect_ref["decision_item_digest"] is not None
                or task.status != "failed"
                or worker.status != "failed"
                or worker.current_task_id is not None
            ):
                raise OperationConflict(
                    "Applied acceptance terminal rows do not match"
                )
        else:
            raise OperationConflict("Applied acceptance next stage is invalid")
        return AcceptanceEffectResult(
            task=task,
            agent=worker,
            next_stage=next_stage,
            attempt=operation.stage_ordinal,
            message=message,
            decision_request=request,
        )

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
            or effect_ref["next_stage"] not in {"mediation_lead", "consult_peer"}
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
        if effect_ref["next_stage"] == "consult_peer":
            expected_recipient = self._consult_recipient(
                connection, operation, query
            )
            expected_kind = "peer_mention"
            expected_metadata = {
                "operation_id": operation.id,
                "task_id": task.id,
                "topic": query["topic"],
                "to_label": query["to"],
            }
        else:
            expected_recipient = None if run is None else run["leader_agent_id"]
            expected_kind = "query"
            expected_metadata = {
                "operation_id": operation.id,
                "task_id": task.id,
                "topic": query["topic"],
            }
        if (
            task.status != "in_progress"
            or agent.status != "running"
            or agent.current_task_id != task.id
            or not _operation_session_matches(operation, agent)
            or run is None
            or message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id != expected_recipient
            or message.kind != expected_kind
            or message.content != query["question"]
            or message.metadata != expected_metadata
        ):
            raise OperationConflict("Applied Worker query rows do not match")
        return WorkerEffectResult(
            task=task,
            agent=agent,
            next_stage=effect_ref["next_stage"],
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
        outcome_payload = _stored_outcome(outcome)
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
        elif (
            is_recoverable_acceptance_failure(
                acceptance.reason_code,
                worker_declared=is_worker_declared_outcome(outcome),
            )
            and task.acceptance_recovery_attempts < ACCEPTANCE_RECOVERY_CAP
        ):
            next_stage = "acceptance_lead"
        else:
            terminal_status = terminal_rejected_status(
                acceptance.status,
                worker_declared=is_worker_declared_outcome(outcome),
            )
            connection.execute(
                """
                update team_tasks
                set status = ?, result = null, error_message = ?,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (
                    terminal_status,
                    acceptance.reason_code or outcome.reason_code,
                    now,
                    now,
                    task.id,
                ),
            )
            connection.execute(
                """
                update team_agents
                set status = ?, current_task_id = null,
                    finished_at = ?, updated_at = ? where id = ?
                """,
                (
                    "waiting" if terminal_status == "blocked" else "failed",
                    now,
                    now,
                    agent.id,
                ),
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
            set status = 'waiting_for_user', result = null, error_message = null,
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
            acceptance,
            task.acceptance_recovery_attempts,
            is_worker_declared_outcome(outcome),
        )
        expected_outcome = _json_object(_stored_outcome(outcome))
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
            or task.status != "waiting_for_user"
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
        ordinal: int,
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
            required_verifications=parse_required_verifications(
                acceptance_payload["required_verifications"]
            ),
        )
        _validate_task_acceptance(acceptance)
        input_artifact_ids = spec.get("input_artifact_ids", [])
        if not isinstance(input_artifact_ids, list) or any(
            not isinstance(artifact_id, str) or not artifact_id.strip()
            for artifact_id in input_artifact_ids
        ):
            raise OperationConflict("Task plan input artifacts are invalid")
        if len(set(input_artifact_ids)) != len(input_artifact_ids):
            raise ValueError("Task plan has duplicate input artifact IDs")
        catalog_rows = connection.execute(
            """
            select artifact_id, relative_path, sha256, size_bytes
            from team_cycle_input_artifacts
            where cycle_id = ?
            order by rowid asc
            """,
            (operation.cycle_id,),
        ).fetchall()
        catalog = {row["artifact_id"]: row for row in catalog_rows}
        if not set(input_artifact_ids) <= set(catalog):
            raise ValueError("Planner task has unknown task input artifact")
        task_id = uuid4().hex
        connection.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id,
                status, required, acceptance_json, outcome_json,
                acceptance_result_json, result, error_message, created_at,
                updated_at, started_at, finished_at, plan_ordinal
            ) values (?, ?, ?, ?, ?, ?, 'pending', ?, ?, null, null, null, null,
                      ?, ?, null, null, ?)
            """,
            (
                task_id,
                operation.team_run_id,
                operation.cycle_id,
                spec["title"],
                spec["description"],
                owner_agent_id,
                int(spec["required"]),
                _task_acceptance_json(acceptance),
                now,
                now,
                ordinal,
            ),
        )
        for artifact_id in input_artifact_ids:
            artifact = catalog[artifact_id]
            basename = PurePosixPath(artifact["relative_path"]).name
            if basename in {"", ".", ".."}:
                raise OperationConflict("Task input artifact path is invalid")
            connection.execute(
                """
                insert into team_task_input_artifacts (
                    task_id, artifact_id, relative_path, sha256, size_bytes,
                    staged_path, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    artifact_id,
                    artifact["relative_path"],
                    artifact["sha256"],
                    artifact["size_bytes"],
                    f"inputs/{artifact_id}/{basename}",
                    now,
                ),
            )
        return self._teams._task_from_connection(connection, task_id)

    @staticmethod
    def _persist_plan_dependencies(
        connection: sqlite3.Connection,
        specs: list[dict[str, object]],
        tasks: list[TeamTask],
    ) -> None:
        task_ids_by_plan_id = {
            spec["plan_task_id"]: task.id
            for spec, task in zip(specs, tasks, strict=True)
            if spec.get("plan_task_id") is not None
        }
        for spec, task in zip(specs, tasks, strict=True):
            dependency_ids = spec.get("depends_on_task_ids", [])
            if not isinstance(dependency_ids, list) or not all(
                isinstance(dependency_id, str) for dependency_id in dependency_ids
            ):
                raise OperationConflict("Task plan dependencies are invalid")
            for dependency_id in dependency_ids:
                prerequisite_id = task_ids_by_plan_id.get(dependency_id)
                if prerequisite_id is None:
                    raise OperationConflict("Task plan dependency is missing")
                connection.execute(
                    """
                    insert into team_task_dependencies (task_id, depends_on_task_id)
                    values (?, ?)
                    """,
                    (task.id, prerequisite_id),
                )

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
            or not _task_inputs_match_plan_spec(connection, task.id, spec)
            or not _task_dependencies_match_plan_spec(
                connection, task.id, spec, tasks, specs
            )
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

    def _replay_contest_verdict(
        self,
        connection: sqlite3.Connection,
        operation: TeamModelOperation,
    ) -> ContestOutcome:
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != "contest_verdict"
            or not isinstance(effect_ref, dict)
            or set(effect_ref) != {"task_ids", "message_id"}
            or not isinstance(effect_ref["task_ids"], list)
            or not all(isinstance(item, str) for item in effect_ref["task_ids"])
            or len(set(effect_ref["task_ids"])) != len(effect_ref["task_ids"])
            or not isinstance(effect_ref["message_id"], str)
        ):
            raise OperationConflict("Applied contest verdict effect reference is invalid")
        payload = _contest_verdict_payload(operation)
        specs = payload.get("tasks") or []
        tasks = [
            self._teams._task_from_connection(connection, task_id)
            for task_id in effect_ref["task_ids"]
        ]
        if len(tasks) != len(specs):
            raise OperationConflict("Applied contest verdict task count does not match")
        actor = self._teams._agent_from_connection(
            connection,
            operation.agent_id,
        )
        if any(
            task.team_run_id != operation.team_run_id
            or task.cycle_id != operation.cycle_id
            or not _task_matches_plan_spec(task, spec)
            or not _task_inputs_match_plan_spec(connection, task.id, spec)
            or not _task_dependencies_match_plan_spec(
                connection, task.id, spec, tasks, specs
            )
            for task, spec in zip(tasks, specs, strict=True)
        ) or (
            not _operation_session_matches(operation, actor)
        ):
            raise OperationConflict(
                "Applied contest verdict tasks do not match the operation"
            )
        reason = payload["reason"]
        supersedes = tuple(dict(entry) for entry in payload.get("supersedes") or [])
        content = _contest_adjudication_content(payload["kind"], reason, supersedes)
        message = self._teams._message_from_connection(
            connection,
            effect_ref["message_id"],
        )
        if (
            message.team_run_id != operation.team_run_id
            or message.cycle_id != operation.cycle_id
            or message.sender_agent_id != operation.agent_id
            or message.recipient_agent_id is not None
            or message.kind != "plan_adjudication"
            or message.content != content
            or message.metadata != {"operation_id": operation.id}
        ):
            raise OperationConflict(
                "Applied contest verdict message does not match the operation"
            )
        return ContestOutcome(
            kind=payload["kind"],
            reason=reason,
            tasks=tasks,
            question=payload.get("question") or None,
            supersedes=supersedes,
        )


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


def _contest_verdict_payload(operation: TeamModelOperation) -> dict[str, object]:
    stored = operation.result_json
    if (
        operation.result_kind != "contest_verdict"
        or not isinstance(stored, dict)
        or set(stored) != {"kind", "payload"}
        or stored["kind"] != "contest_verdict"
        or not isinstance(stored["payload"], dict)
    ):
        raise OperationConflict("Completed contest verdict result is invalid")
    payload = stored["payload"]
    if payload.get("kind") not in CONTEST_VERDICT_KINDS:
        raise OperationConflict("Completed contest verdict result is invalid")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise OperationConflict("Completed contest verdict result is invalid")
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
        raise OperationConflict("Completed contest verdict result is invalid")
    supersedes = payload.get("supersedes") or []
    if not isinstance(supersedes, list) or not all(
        isinstance(item, dict) for item in supersedes
    ):
        raise OperationConflict("Completed contest verdict result is invalid")
    return payload


def _contest_adjudication_content(
    kind: str,
    reason: str,
    supersedes: tuple[dict[str, str], ...],
) -> str:
    content = f"Contest verdict: {kind}. {reason}"
    if supersedes:
        overrides = "; ".join(
            f"{entry['document_path']} — {entry['decision']}" for entry in supersedes
        )
        content = f"{content} Supersedes: {overrides}"
    return content


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


def _mediation_resolution_payload(
    resolution: Mapping[str, object],
) -> dict[str, object]:
    normalized = _json_object(dict(resolution))
    if (
        normalized.get("kind") == "answer"
        and set(normalized) == {"kind", "answer"}
        and isinstance(normalized["answer"], str)
        and normalized["answer"].strip()
    ):
        normalized["answer"] = normalized["answer"].strip()
        return normalized
    if _valid_user_decision(normalized):
        return normalized
    raise ValueError("Invalid mediation resolution")


def _acceptance_resolution_payload(
    resolution: AcceptanceReviewResolution,
) -> dict[str, object]:
    kind = getattr(resolution, "kind", None)
    reason = getattr(resolution, "reason", None)
    instruction = getattr(resolution, "instruction", None)
    reason_code = getattr(resolution, "reason_code", None)
    acceptance = getattr(resolution, "acceptance", None)
    decision = getattr(resolution, "decision", None)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Acceptance resolution requires a reason")
    payload = {
        "kind": kind,
        "reason": reason.strip(),
        "instruction": (
            instruction.strip()
            if isinstance(instruction, str) and instruction.strip()
            else None
        ),
        "reason_code": (
            reason_code.strip()
            if isinstance(reason_code, str) and reason_code.strip()
            else None
        ),
        "acceptance": (
            json.loads(_task_acceptance_json(acceptance))
            if isinstance(acceptance, TaskAcceptance)
            else None
        ),
        "decision": (
            _json_object(decision)
            if isinstance(decision, dict)
            else None
        ),
    }
    if kind == "retry_worker":
        if (
            payload["instruction"] is None
            or payload["reason_code"] is not None
            or payload["acceptance"] is not None
            or payload["decision"] is not None
        ):
            raise ValueError("Invalid acceptance retry resolution")
    elif kind == "revise_acceptance":
        if (
            payload["instruction"] is None
            or payload["reason_code"] is not None
            or payload["acceptance"] is None
            or payload["decision"] is not None
        ):
            raise ValueError("Invalid revised acceptance resolution")
        _validate_task_acceptance(acceptance)
    elif kind == "ask_user":
        if (
            payload["instruction"] is not None
            or payload["reason_code"] is not None
            or payload["acceptance"] is not None
            or not isinstance(payload["decision"], dict)
            or not _valid_user_decision(payload["decision"])
        ):
            raise ValueError("Invalid acceptance user decision")
    elif kind == "fail":
        if (
            payload["instruction"] is not None
            or payload["reason_code"] is None
            or payload["acceptance"] is not None
            or payload["decision"] is not None
        ):
            raise ValueError("Invalid terminal acceptance resolution")
    else:
        raise ValueError("Invalid acceptance resolution kind")
    return payload


def _persisted_task_outcome(task: TeamTask):
    if not isinstance(task.outcome, dict):
        raise OperationConflict("Acceptance task has no persisted outcome")
    try:
        return parse_task_outcome(
            json.dumps(task.outcome, ensure_ascii=False, sort_keys=True)
        )
    except TaskOutcomeError as exc:
        raise OperationConflict(
            "Acceptance task outcome is invalid"
        ) from exc


def _persisted_acceptance_result(task: TeamTask) -> AcceptanceResult:
    value = task.acceptance_result
    if not isinstance(value, dict):
        raise OperationConflict(
            "Acceptance task has no persisted acceptance result"
        )
    try:
        acceptance = AcceptanceResult(
            accepted=value["accepted"],
            status=value["status"],
            reason_code=value["reason_code"],
            evidence=value["evidence"],
        )
    except (KeyError, TypeError) as exc:
        raise OperationConflict(
            "Persisted acceptance result is invalid"
        ) from exc
    _validate_acceptance_result(acceptance)
    if acceptance.accepted:
        raise OperationConflict("Acceptance review requires a rejected outcome")
    return acceptance


def _synthesis_summary(operation: TeamModelOperation) -> str:
    payload = _result_payload(operation, "synthesis")
    if not _valid_synthesis(payload):
        raise OperationConflict("Completed synthesis result is invalid")
    return payload["summary"]


def _plan_review(operation: TeamModelOperation) -> PlanReview:
    payload = _result_payload(operation, "plan_review")
    # Re-parsed rather than rebuilt field by field: the stored payload is the
    # reviewer's answer, and parse_plan_review is the only thing that decides
    # what a usable answer is. Every label in it was already checked when the
    # operation completed, so no label set is needed to read it back.
    objections = payload.get("objections")
    if not isinstance(objections, list) or not all(
        isinstance(objection, dict) and isinstance(objection.get("task_ref"), str)
        for objection in objections
    ):
        raise OperationConflict("Completed plan review is invalid")
    try:
        return parse_plan_review(
            json.dumps(payload, ensure_ascii=False),
            frozenset(objection["task_ref"] for objection in objections),
        )
    except PlanReviewError as exc:
        raise OperationConflict("Completed plan review is invalid") from exc


def _objections_json(review: PlanReview) -> str:
    return json.dumps(
        [asdict(objection) for objection in review.objections],
        ensure_ascii=False,
    )


def _loads_objections(stored: str) -> object:
    try:
        return json.loads(stored)
    except json.JSONDecodeError as exc:
        raise OperationConflict("Stored plan review objections are unreadable") from exc


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
    acceptance_recovery_attempts: int,
    worker_declared: bool,
) -> tuple[
    Literal["acceptance_lead"] | None,
    Literal["in_progress", "completed", "failed", "blocked"],
    Literal["running", "completed", "failed", "waiting"],
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
    if (
        is_recoverable_acceptance_failure(
            reason_code, worker_declared=worker_declared
        )
        and acceptance_recovery_attempts < ACCEPTANCE_RECOVERY_CAP
    ):
        return "acceptance_lead", "in_progress", "running"
    if (
        terminal_rejected_status(
            str(acceptance["status"]), worker_declared=worker_declared
        )
        == "blocked"
    ):
        return None, "blocked", "waiting"
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


def _canonical_check_payload(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    check_type = value.get("type")
    if check_type not in CHECK_TYPES:
        return None
    # command_succeeds 만 파일을 가리키지 않는다. path 를 요구하면 이 검사가
    # 조용히 버려지고, 리드가 붙인 판정 근거가 사라진 채 일감이 통과한다.
    if check_type == "command_succeeds":
        command = value.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        return verification_check_payload(
            VerificationCheck(type=check_type, path="", command=command.strip())
        )
    path = value.get("path")
    if not isinstance(path, str):
        return None
    check_value = value.get("value", "")
    pattern = value.get("pattern", "")
    return verification_check_payload(
        VerificationCheck(
            type=check_type,
            path=path.strip(),
            value=check_value if isinstance(check_value, str) else "",
            pattern=pattern if isinstance(pattern, str) else "",
        )
    )


def _canonical_verification_item(item: object) -> object | None:
    if isinstance(item, str):
        name = item.strip()
        return name or None
    if not isinstance(item, dict) or set(item) - {"name", "check"}:
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    check = item.get("check")
    if check is None:
        return name
    payload = _canonical_check_payload(check)
    return None if payload is None else {"name": name, "check": payload}


def _canonical_acceptance(value: object) -> dict[str, object] | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"required_outputs", "required_verifications"}
        or not isinstance(value["required_outputs"], list)
        or not all(isinstance(item, str) for item in value["required_outputs"])
        or not isinstance(value["required_verifications"], list)
    ):
        return None
    verifications: list[object] = []
    for item in value["required_verifications"]:
        canonical_item = _canonical_verification_item(item)
        if canonical_item is None:
            return None
        verifications.append(canonical_item)
    return {
        "required_outputs": value["required_outputs"],
        "required_verifications": verifications,
    }


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
        and _canonical_acceptance(acceptance)
        == json.loads(_task_acceptance_json(task.acceptance))
    )


def _task_inputs_match_plan_spec(
    connection: sqlite3.Connection,
    task_id: str,
    spec: dict[str, object],
) -> bool:
    input_artifact_ids = spec.get("input_artifact_ids", [])
    if not isinstance(input_artifact_ids, list) or not all(
        isinstance(artifact_id, str) for artifact_id in input_artifact_ids
    ):
        return False
    rows = connection.execute(
        """
        select artifact_id from team_task_input_artifacts
        where task_id = ? order by rowid asc
        """,
        (task_id,),
    ).fetchall()
    return [row["artifact_id"] for row in rows] == input_artifact_ids


def _task_dependencies_match_plan_spec(
    connection: sqlite3.Connection,
    task_id: str,
    spec: dict[str, object],
    tasks: list[TeamTask],
    specs: list[dict[str, object]],
) -> bool:
    plan_ids = {
        candidate_spec.get("plan_task_id"): candidate_task.id
        for candidate_task, candidate_spec in zip(tasks, specs, strict=True)
        if candidate_spec.get("plan_task_id") is not None
    }
    dependencies = spec.get("depends_on_task_ids", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(dependency, str) for dependency in dependencies
    ):
        return False
    expected = [plan_ids.get(dependency) for dependency in dependencies]
    if any(dependency is None for dependency in expected):
        return False
    rows = connection.execute(
        """
        select depends_on_task_id from team_task_dependencies
        where task_id = ? order by rowid asc
        """,
        (task_id,),
    ).fetchall()
    return [row["depends_on_task_id"] for row in rows] == expected


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


def _task_decision_receipt(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    request: TeamDecisionRequest,
    decision: dict[str, object],
    task_id: str,
    *,
    query_message_id: str | None = None,
) -> tuple[str, str]:
    matching_items = [
        item
        for item in request.items
        if _decision_item_matches(item, decision, task_id)
        and (
            query_message_id is None
            or query_message_id in item["query_message_ids"]
        )
    ]
    if len(matching_items) != 1:
        raise OperationConflict(
            "Lead decision does not have one exact request item"
        )
    item = matching_items[0]
    if not _decision_item_references_are_valid(
        connection,
        operation,
        item,
    ):
        raise OperationConflict("Lead decision item references are invalid")
    item_id = item.get("id")
    if not isinstance(item_id, str):
        raise OperationConflict("Lead decision item ID is invalid")
    return item_id, lead_decision_item_digest(
        item,
        task_id,
        query_message_id,
    )


def lead_decision_item_digest(
    item: dict[str, object],
    task_id: str,
    query_message_id: str | None,
) -> str:
    return _canonical_digest(
        {
            "id": item.get("id"),
            "stage": item.get("stage"),
            "topic": item.get("topic"),
            "question": item.get("question"),
            "why_needed": item.get("why_needed"),
            "options": item.get("options"),
            "recommended_option_id": item.get(
                "recommended_option_id"
            ),
            "blocking_scope": item.get("blocking_scope"),
            "task_id": task_id,
            "query_message_id": query_message_id,
        }
    )


def _acceptance_before_verification_name(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if (
        isinstance(item, dict)
        and set(item) == {"name", "check"}
        and isinstance(item.get("name"), str)
        and item["name"].strip()
        and (item.get("check") is None or isinstance(item.get("check"), dict))
    ):
        return item["name"]
    return None


def _acceptance_audit_matches(
    message: TeamMessage,
    operation: TeamModelOperation,
    task: TeamTask,
    resolution: dict[str, object],
) -> bool:
    acceptance_before = message.metadata.get("acceptance_before")
    if (
        not isinstance(acceptance_before, dict)
        or set(acceptance_before)
        != {"required_outputs", "required_verifications"}
        or not isinstance(acceptance_before["required_outputs"], list)
        or not all(
            isinstance(item, str) for item in acceptance_before["required_outputs"]
        )
        or not isinstance(
            acceptance_before["required_verifications"],
            list,
        )
        or any(
            _acceptance_before_verification_name(item) is None
            for item in acceptance_before["required_verifications"]
        )
    ):
        return False
    outcome = _persisted_task_outcome(task)
    acceptance = _persisted_acceptance_result(task)
    reason_code = (
        resolution["reason_code"]
        if resolution["kind"] == "fail"
        else acceptance.reason_code
        or outcome.reason_code
        or "task_failed"
    )
    verification_status = {
        item.name: item.status for item in outcome.verifications
    }
    verification_pairs = [
        (
            _acceptance_before_verification_name(item),
            isinstance(item, dict) and item.get("check") is not None,
        )
        for item in acceptance_before["required_verifications"]
    ]
    expected_metadata = _acceptance_review_metadata(
        operation_id=operation.id,
        task_id=task.id,
        attempt=operation.stage_ordinal,
        reason_code=str(reason_code),
        action=str(resolution["kind"]),
        reason=str(resolution["reason"]),
        instruction=(
            resolution["instruction"]
            if isinstance(resolution["instruction"], str)
            else None
        ),
        acceptance_before=acceptance_before,
        acceptance_after=(
            resolution["acceptance"]
            if isinstance(resolution["acceptance"], dict)
            else None
        ),
        rejected_deliverables=[item.path for item in outcome.deliverables],
        rejected_verifications=rejected_verification_names(
            verification_pairs,
            verification_status,
            acceptance.evidence,
        ),
    )
    current_acceptance = json.loads(_task_acceptance_json(task.acceptance))
    expected_current = (
        _canonical_acceptance(resolution["acceptance"])
        if resolution["kind"] == "revise_acceptance"
        else acceptance_before
    )
    return (
        message.content
        == (resolution["instruction"] or resolution["reason"])
        and message.metadata == expected_metadata
        and current_acceptance == expected_current
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
            or task["status"] != "waiting_for_user"
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


def _stored_outcome(outcome: TaskOutcome) -> dict[str, object]:
    """The outcome as it is stored and compared, minus the note fields.

    The notes are stored as messages instead, and a refused note is stored as a
    degradation message; putting either field in the stored payload would make
    the replay comparison disagree for every operation applied before this
    upgrade, where the stored JSON has no such key.
    """
    return {
        key: value
        for key, value in asdict(outcome).items()
        if key not in {"mentions", "mention_refusals"}
    }


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
        "mediation_lead": {
            "mediation_resolution": _valid_mediation_resolution,
        },
        "mediation_lead_repair": {
            "mediation_resolution": _valid_mediation_resolution,
        },
        "consult_peer": {
            "consult_resolution": _valid_consult_resolution,
        },
        "consult_peer_repair": {
            "consult_resolution": _valid_consult_resolution,
        },
        "mediation_worker": {
            "task_outcome": _valid_task_outcome,
            "worker_query": _valid_worker_query,
        },
        "mediation_worker_repair": {
            "task_outcome": _valid_task_outcome,
            "worker_query": _valid_worker_query,
        },
        "acceptance_lead": {
            "acceptance_review": _valid_acceptance_resolution,
        },
        "acceptance_lead_repair": {
            "acceptance_review": _valid_acceptance_resolution,
        },
        "acceptance_worker": {
            "task_outcome": _valid_task_outcome,
        },
        "acceptance_worker_repair": {
            "task_outcome": _valid_task_outcome,
        },
        "cycle_synthesis": {
            "synthesis": _valid_synthesis,
            "user_decision": _valid_user_decision,
        },
        "cycle_synthesis_repair": {
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
    # "to" is optional: absent means the lead answers, present names the
    # roster label of the teammate the asker wants.
    if set(payload) not in ({"topic", "question"}, {"topic", "question", "to"}):
        return False
    if not all(
        isinstance(payload[field], str) and bool(payload[field].strip())
        for field in ("topic", "question")
    ):
        return False
    if "to" in payload and (
        not isinstance(payload["to"], str) or not payload["to"].strip()
    ):
        return False
    return True


def _consult_resolution_payload(
    resolution: Mapping[str, object],
) -> dict[str, object]:
    """Answer-only: a peer cannot escalate to the user."""
    normalized = _json_object(dict(resolution))
    if (
        normalized.get("kind") == "answer"
        and set(normalized) == {"kind", "answer"}
        and isinstance(normalized["answer"], str)
        and normalized["answer"].strip()
    ):
        normalized["answer"] = normalized["answer"].strip()
        return normalized
    raise OperationConflict("Consult resolution must be an answer")


def _valid_consult_resolution(payload: dict[str, object]) -> bool:
    try:
        _consult_resolution_payload(payload)
    except (OperationConflict, TypeError, ValueError):
        return False
    return True


def _valid_mediation_resolution(payload: dict[str, object]) -> bool:
    try:
        _mediation_resolution_payload(payload)
    except (TypeError, ValueError):
        return False
    return True


def _valid_acceptance_resolution(payload: dict[str, object]) -> bool:
    kind = payload.get("kind")
    if set(payload) != {
        "kind",
        "reason",
        "instruction",
        "reason_code",
        "acceptance",
        "decision",
    }:
        return False
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        return False
    instruction = payload["instruction"]
    reason_code = payload["reason_code"]
    acceptance = payload["acceptance"]
    decision = payload["decision"]
    if kind == "retry_worker":
        return (
            isinstance(instruction, str)
            and bool(instruction.strip())
            and reason_code is None
            and acceptance is None
            and decision is None
        )
    if kind == "revise_acceptance":
        try:
            _validate_task_acceptance(
                TaskAcceptance(
                    required_outputs=tuple(acceptance["required_outputs"]),
                    required_verifications=parse_required_verifications(
                        acceptance["required_verifications"]
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            return False
        return (
            isinstance(instruction, str)
            and bool(instruction.strip())
            and reason_code is None
            and isinstance(acceptance, dict)
            and set(acceptance)
            == {"required_outputs", "required_verifications"}
            and decision is None
        )
    if kind == "ask_user":
        return (
            instruction is None
            and reason_code is None
            and acceptance is None
            and isinstance(decision, dict)
            and _valid_user_decision(decision)
        )
    if kind == "fail":
        return (
            instruction is None
            and isinstance(reason_code, str)
            and bool(reason_code.strip())
            and acceptance is None
            and decision is None
        )
    return False


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
    optional = {"contract_payload", "coverage_gaps", "team_note", "next_cycle"}
    if set(payload) - optional != {"summary"}:
        return False
    if not isinstance(payload["summary"], str) or not payload["summary"].strip():
        return False
    if "contract_payload" in payload:
        contract_payload = payload["contract_payload"]
        if not isinstance(contract_payload, str) or not contract_payload.strip():
            return False
    if "coverage_gaps" in payload:
        gaps = payload["coverage_gaps"]
        if not isinstance(gaps, list) or not all(
            isinstance(gap, dict) for gap in gaps
        ):
            return False
    if "team_note" in payload:
        note = payload["team_note"]
        if not isinstance(note, dict):
            return False
        if not all(
            isinstance(note.get(field), str) and note.get(field, "").strip()
            for field in ("title", "content_markdown")
        ):
            return False
    if "next_cycle" in payload:
        instruction = payload["next_cycle"]
        if not isinstance(instruction, str) or not instruction.strip():
            return False
    return True


def _live_cycle_task_rows(
    connection: sqlite3.Connection,
    team_run_id: str,
    cycle_id: str | None,
) -> list[sqlite3.Row]:
    """The cycle's task rows with every discarded plan proposal dropped.

    A superseded or abandoned plan revision leaves its tasks behind as
    canceled rows, and a canceled *required* task reads as terminal `blocked`.
    So a negotiation that worked -- a plan every owner approved, every task of
    which completed -- reported the cycle as blocked, because the plan nobody
    agreed to was still being counted. team_runtime drops the same rows before
    it decides whether to synthesize (``_live_plan_tasks``); this is the same
    rule applied where the terminal status is actually written.

    A run without plan negotiation has no revision row at all, so nothing is
    ever dropped and its status is derived from exactly the rows it was
    before -- pinned by
    test_synthesis_without_negotiation_still_counts_canceled_tasks.

    Both apply_synthesis and _replay_synthesis derive the status from here, so
    the applied effect_ref and the live rows keep agreeing on resume.
    """
    rows = connection.execute(
        """
        select id, status, required from team_tasks
        where team_run_id = ? and cycle_id = ?
        order by created_at asc, id asc
        """,
        (team_run_id, cycle_id),
    ).fetchall()
    discarded = _discarded_plan_task_ids(connection, team_run_id, cycle_id)
    if not discarded:
        return rows
    live = [row for row in rows if row["id"] not in discarded]
    # A cycle whose every task belongs to a discarded revision has no plan
    # left to judge, and _terminal_status refuses an empty cycle. Negotiation
    # settles such a cycle before synthesis, so this is unreachable in
    # practice; deriving from the unscoped rows keeps it a status rather than
    # a raise if it ever is reached.
    return live or rows


def _discarded_plan_task_ids(
    connection: sqlite3.Connection,
    team_run_id: str,
    cycle_id: str | None,
) -> frozenset[str]:
    """Read this cycle's revisions and ask the shared rule which tasks died.

    The rule itself lives in team_plan_negotiation so the runtime, this service
    and the read model cannot drift apart; this function only supplies the rows.
    """
    revisions: list[tuple[str, list[str]]] = []
    for row in connection.execute(
        """
        select status, task_ids_json from team_plan_revisions
        where team_run_id = ? and cycle_id = ?
        """,
        (team_run_id, cycle_id),
    ).fetchall():
        try:
            task_ids = json.loads(row["task_ids_json"])
        except json.JSONDecodeError as exc:
            raise OperationConflict("Plan revision task ids are unreadable") from exc
        if not isinstance(task_ids, list):
            raise OperationConflict("Plan revision task ids are unreadable")
        revisions.append((row["status"], [str(task_id) for task_id in task_ids]))
    return discarded_task_ids(revisions)


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


def _apply_mediation_reinvocation(
    connection: sqlite3.Connection,
    operation: TeamModelOperation,
    now: str,
) -> None:
    if operation.stage != "mediation_worker":
        return
    cursor = connection.execute(
        """
        update team_agents
        set reinvocations = reinvocations + 1, updated_at = ?
        where id = ? and team_run_id = ?
        """,
        (
            now,
            operation.agent_id,
            operation.team_run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise OperationConflict(
            "Mediation Worker changed before effect application"
        )


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
