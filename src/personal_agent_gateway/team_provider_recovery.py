import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.lmg_client import (
    LMGProtocolMismatch,
    ProviderExecutionCapabilities,
    parse_provider_execution_capabilities,
)
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    TeamModelOperation,
    TeamModelOperationService,
)
from personal_agent_gateway.teams import (
    ProviderRecoveryClaim,
    TeamRunCycle,
    TeamRunService,
)


class ProviderRecoveryRequired(RuntimeError):
    def __init__(self, provider: str, reason_code: str) -> None:
        super().__init__(f"{provider}: {reason_code}")
        self.provider = provider
        self.reason_code = reason_code


class ProviderOperationWaiting(RuntimeError):
    def __init__(self, operation_id: str) -> None:
        super().__init__("provider_operation_waiting")
        self.operation_id = operation_id


class AmbiguousOperationNotReconcilable(ValueError):
    def __init__(self) -> None:
        super().__init__("ambiguous_operation_not_reconcilable")


@dataclass(frozen=True)
class OperationReconcileResult:
    runnable_cycle_ids: tuple[str, ...]
    locally_applicable_cycle_ids: tuple[str, ...]
    interrupted_cycle_ids: tuple[str, ...]


def capability_payload(
    capabilities: ProviderExecutionCapabilities,
) -> dict[str, object]:
    return {
        "resume": capabilities.resume,
        "external_read_only_roots": capabilities.external_read_only_roots,
        "network_modes": list(capabilities.network_modes),
        "sandbox_modes": list(capabilities.sandbox_modes),
        "permission_modes": list(capabilities.permission_modes),
    }


def capabilities_for_cycle(
    cycle: TeamRunCycle,
    provider: str,
) -> ProviderExecutionCapabilities:
    metadata = (
        cycle.execution_metadata
        if isinstance(cycle.execution_metadata, dict)
        else {}
    )
    snapshots = metadata.get("provider_capabilities")
    snapshot = (
        snapshots.get(provider)
        if isinstance(snapshots, dict)
        else None
    )
    try:
        return parse_provider_execution_capabilities(snapshot)
    except LMGProtocolMismatch as exc:
        raise ProviderRecoveryRequired(
            provider,
            _recovery_reason(snapshot),
        ) from exc


class TeamProviderRecovery:
    def __init__(
        self,
        teams: TeamRunService,
        registry: AgentRegistry,
        operations: TeamModelOperationService | None = None,
        *,
        session_loader: Callable[[], list[dict[str, object]]] | None = None,
    ) -> None:
        self._teams = teams
        self._registry = registry
        self._operations = operations or TeamModelOperationService(teams._db)
        self._session_loader = session_loader or (lambda: [])

    def freeze_cycle(self, cycle_id: str) -> TeamRunCycle:
        cycle = self._teams.get_cycle(cycle_id)
        providers = sorted(
            {
                agent.backend
                for agent in self._teams.list_agents(cycle.team_run_id)
            }
        )
        snapshots: dict[str, dict[str, object]] = {}
        for provider in providers:
            try:
                descriptor = self._registry.get(provider)
            except ValueError as exc:
                raise ProviderRecoveryRequired(
                    provider,
                    "capabilities_unavailable",
                ) from exc
            capabilities = descriptor.execution_capabilities
            if capabilities is None:
                raise ProviderRecoveryRequired(
                    provider,
                    descriptor.readiness_error or "capabilities_unavailable",
                )
            snapshots[provider] = {
                "ready": descriptor.ready,
                "readiness_error": descriptor.readiness_error,
                "snapshot_status": descriptor.snapshot_status,
                "detected_at": descriptor.detected_at,
                "execution": capability_payload(capabilities),
            }

        return self._teams.set_cycle_provider_capabilities(
            cycle.id,
            snapshots,
        )

    def wait_for_operation(
        self,
        operation_id: str,
        *,
        reason_code: str,
        now: datetime | None = None,
    ) -> ProviderRecoveryClaim:
        timestamp = _timestamp(now)
        current = now or datetime.now(timezone.utc)
        with self._teams._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            if operation.status != "invoking":
                raise OperationConflict(
                    "Provider wait requires an invoking operation"
                )
            _validate_single_open_operation(
                connection,
                operation,
                concurrent_tasks=self._operations._concurrent_tasks,
            )
            source = _validate_operation_source(
                connection,
                operation,
                expected_mode="active",
            )
            metadata = _cycle_metadata(source.cycle["execution_metadata_json"])
            metadata["provider_recovery"] = {
                "operation_id": operation.id,
                "provider": operation.provider,
                "reason_code": reason_code,
                "attempts": operation.attempts,
                "first_failed_at": timestamp,
                "next_retry_at": _timestamp(current + timedelta(seconds=30)),
                "warning_visible_at": _timestamp(
                    current + timedelta(seconds=120)
                ),
            }
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'waiting_for_provider', version = version + 1,
                    reason_code = ?, updated_at = ?
                where id = ? and status = 'invoking' and version = ?
                """,
                (
                    reason_code,
                    timestamp,
                    operation.id,
                    operation.version,
                ),
            )
            _require_one(cursor, "Provider wait operation changed")
            connection.execute(
                """
                update team_runs
                set status = 'waiting_for_provider', finished_at = null,
                    updated_at = ? where id = ?
                """,
                (timestamp, operation.team_run_id),
            )
            connection.execute(
                """
                update team_run_cycles
                set status = 'waiting_for_provider',
                    execution_metadata_json = ?, finished_at = null,
                    updated_at = ? where id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    operation.cycle_id,
                ),
            )
            if operation.task_id is not None:
                connection.execute(
                    """
                    update team_tasks
                    set status = 'waiting_for_provider', finished_at = null,
                        updated_at = ? where id = ?
                    """,
                    (timestamp, operation.task_id),
                )
            connection.execute(
                """
                update team_agents
                set status = 'waiting', finished_at = null, updated_at = ?
                where id = ?
                """,
                (timestamp, operation.agent_id),
            )
        return ProviderRecoveryClaim(
            operation.team_run_id,
            operation.cycle_id,
            operation.task_id,
            operation.id,
        )

    def claim_operation(
        self,
        cycle_id: str,
        *,
        now: datetime | None = None,
    ) -> ProviderRecoveryClaim | None:
        timestamp = _timestamp(now)
        with self._teams._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select * from team_model_operations
                where cycle_id = ? and status = 'waiting_for_provider'
                order by created_at asc, id asc
                """,
                (cycle_id,),
            ).fetchone()
            if row is None:
                return None
            operation = self._operations._get(connection, row["id"])
            _validate_single_open_operation(
                connection,
                operation,
                concurrent_tasks=self._operations._concurrent_tasks,
            )
            source = _validate_operation_source(
                connection,
                operation,
                expected_mode="waiting",
            )
            metadata = _cycle_metadata(source.cycle["execution_metadata_json"])
            recovery = metadata.get("provider_recovery")
            if (
                not isinstance(recovery, dict)
                or recovery.get("operation_id") != operation.id
                or recovery.get("provider") != operation.provider
            ):
                raise ValueError("Invalid provider recovery metadata")
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'prepared', version = version + 1,
                    reason_code = null, updated_at = ?
                where id = ? and status = 'waiting_for_provider'
                  and version = ?
                """,
                (timestamp, operation.id, operation.version),
            )
            if cursor.rowcount != 1:
                return None
            metadata.pop("provider_recovery", None)
            _restore_operation_source(
                connection,
                operation,
                metadata,
                timestamp,
            )
        return ProviderRecoveryClaim(
            operation.team_run_id,
            operation.cycle_id,
            operation.task_id,
            operation.id,
        )

    def recover_due(
        self,
        *,
        now: datetime,
    ) -> list[ProviderRecoveryClaim]:
        claims: list[ProviderRecoveryClaim] = []
        for cycle in self._teams.list_waiting_provider_cycles():
            try:
                recovery = _provider_recovery_metadata(
                    cycle.execution_metadata
                )
                next_retry_at = datetime.fromisoformat(
                    recovery["next_retry_at"]
                )
            except (TypeError, ValueError):
                continue
            if next_retry_at > now:
                continue
            try:
                descriptor = self._registry.get(recovery["provider"])
            except ValueError:
                descriptor = None
            if descriptor is None or not descriptor.ready:
                self._reschedule_waiting_operation(cycle.id, now=now)
                continue
            claim = self.claim_operation(cycle.id, now=now)
            if claim is not None:
                claims.append(claim)
        return claims

    def _reschedule_waiting_operation(
        self,
        cycle_id: str,
        *,
        now: datetime,
    ) -> None:
        timestamp = _timestamp(now)
        next_retry_at = _timestamp(now + timedelta(seconds=30))
        with self._teams._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select id from team_model_operations
                where cycle_id = ? and status = 'waiting_for_provider'
                order by created_at asc, id asc
                """,
                (cycle_id,),
            ).fetchone()
            if row is None:
                return
            operation = self._operations._get(connection, row["id"])
            _validate_single_open_operation(
                connection,
                operation,
                concurrent_tasks=self._operations._concurrent_tasks,
            )
            source = _validate_operation_source(
                connection,
                operation,
                expected_mode="waiting",
            )
            metadata = _cycle_metadata(source.cycle["execution_metadata_json"])
            recovery = _provider_recovery_metadata(metadata)
            if (
                recovery["operation_id"] != operation.id
                or recovery["provider"] != operation.provider
            ):
                return
            recovery["next_retry_at"] = next_retry_at
            cursor = connection.execute(
                """
                update team_run_cycles
                set execution_metadata_json = ?, updated_at = ?
                where id = ? and status = 'waiting_for_provider'
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    cycle_id,
                ),
            )
            _require_one(cursor, "Provider retry schedule changed")

    async def interrupt_ambiguous_operation(
        self,
        operation_id: str,
        *,
        consumer_run_id: str,
        upstream_session_id: str | None,
    ) -> None:
        with self._teams._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation_id)
            if (
                operation.status != "invoking"
                or operation.consumer_run_id != consumer_run_id
            ):
                raise OperationConflict(
                    "Ambiguous interruption does not match the invocation"
                )
            _interrupt_ambiguous(
                connection,
                operation,
                upstream_session_id=upstream_session_id,
                timestamp=_timestamp(),
            )

    def prepare_explicit_resume(
        self,
        team_run_id: str,
        *,
        now: datetime | None = None,
    ) -> ProviderRecoveryClaim:
        operation = _single_ambiguous_for_run(
            self._operations,
            self._teams,
            team_run_id,
        )
        try:
            sessions = self._session_loader()
        except Exception as exc:
            raise AmbiguousOperationNotReconcilable() from exc
        matches = [
            session
            for session in sessions
            if _session_matches(operation, session)
            and (
                operation.upstream_session_id is None
                or session.get("upstream_id")
                == operation.upstream_session_id
            )
        ]
        if len(matches) != 1:
            raise AmbiguousOperationNotReconcilable()
        upstream_session_id = matches[0].get("upstream_id")
        if not isinstance(upstream_session_id, str) or not upstream_session_id:
            raise AmbiguousOperationNotReconcilable()

        timestamp = _timestamp(now)
        with self._teams._db.connection() as connection:
            connection.execute("begin immediate")
            operation = self._operations._get(connection, operation.id)
            if operation.status != "ambiguous":
                raise AmbiguousOperationNotReconcilable()
            _validate_operation_source(
                connection,
                operation,
                expected_mode="interrupted",
            )
            cursor = connection.execute(
                """
                update team_model_operations
                set status = 'prepared', version = version + 1,
                    upstream_session_id = ?, reason_code = null, updated_at = ?
                where id = ? and status = 'ambiguous' and version = ?
                """,
                (
                    upstream_session_id,
                    timestamp,
                    operation.id,
                    operation.version,
                ),
            )
            _require_one(cursor, "Ambiguous operation changed")
            cycle = connection.execute(
                "select execution_metadata_json from team_run_cycles where id = ?",
                (operation.cycle_id,),
            ).fetchone()
            metadata = _cycle_metadata(cycle["execution_metadata_json"])
            metadata.pop("provider_recovery", None)
            _restore_operation_source(
                connection,
                operation,
                metadata,
                timestamp,
            )
        return ProviderRecoveryClaim(
            operation.team_run_id,
            operation.cycle_id,
            operation.task_id,
            operation.id,
        )

    def has_ambiguous_operation(self, team_run_id: str) -> bool:
        row = self._teams._db.fetchone(
            """
            select id from team_model_operations
            where team_run_id = ? and status = 'ambiguous'
            limit 1
            """,
            (team_run_id,),
        )
        return row is not None

    def get_open_operation(self, cycle_id: str) -> TeamModelOperation | None:
        return self._operations.get_open_for_cycle(cycle_id)

    def reconcile_startup(self) -> OperationReconcileResult:
        runnable: list[str] = []
        locally_applicable: list[str] = []
        interrupted: list[str] = []
        rows = self._teams._db.fetchall(
            """
            select id from team_model_operations
            where status in (
                'prepared', 'invoking', 'completed',
                'waiting_for_provider', 'ambiguous'
            )
            order by created_at asc, id asc
            """
        )
        for row in rows:
            with self._teams._db.connection() as connection:
                connection.execute("begin immediate")
                operation = self._operations._get(connection, row["id"])
                if _cancel_for_canceled_source(connection, operation):
                    continue
                if operation.status == "invoking":
                    _interrupt_ambiguous(
                        connection,
                        operation,
                        upstream_session_id=operation.upstream_session_id,
                        timestamp=_timestamp(),
                    )
                    interrupted.append(operation.cycle_id)
                elif operation.status == "prepared":
                    runnable.append(operation.cycle_id)
                elif operation.status == "completed":
                    locally_applicable.append(operation.cycle_id)
        return OperationReconcileResult(
            tuple(runnable),
            tuple(locally_applicable),
            tuple(interrupted),
        )


def _recovery_reason(snapshot: object) -> str:
    if isinstance(snapshot, dict):
        reason = snapshot.get("readiness_error")
        if isinstance(reason, str) and reason:
            return reason
        return "capabilities_unavailable"
    # No snapshot at all for this provider, which is a different fault and used
    # to be reported as the provider being unavailable. It sent an operator
    # looking at a provider that was live and ready, because the cycle -- not
    # the provider -- is what is missing something. Callers iterate providers
    # in sorted order, so an unfrozen cycle blamed whichever name sorted first.
    return "capabilities_not_frozen"


@dataclass(frozen=True)
class _OperationSource:
    run: object
    cycle: object
    task: object | None
    actor: object
    worker: object | None


_WORKER_STAGES = {
    "worker_execution",
    "mediation_worker",
    "mediation_worker_repair",
    "acceptance_worker",
    "acceptance_worker_repair",
}
_LEAD_STAGES = {
    "mediation_lead",
    "mediation_lead_repair",
    "acceptance_lead",
    "acceptance_lead_repair",
}
# A consult runs in the lead-stage shape -- the asking worker owns and runs
# the task while the answering actor holds no task -- but the actor is a
# fellow worker, so the one check that names the leader cannot apply.
_CONSULT_STAGES = {
    "consult_peer",
    "consult_peer_repair",
}


def _validate_single_open_operation(
    connection,
    operation,
    *,
    concurrent_tasks: bool = False,
) -> None:
    """Exactly one open operation for whatever this one speaks for.

    Sequentially that is the cycle. Under concurrency it is the assignment:
    siblings are legitimately open at the same time, and counting them would
    refuse every provider wait the moment a second worker exists.

    A known narrowing of concurrency, deliberately not papered over here:
    parking for a provider moves the run and the cycle to
    waiting_for_provider, which is cycle-wide state. A sibling that is still
    running then fails its own active-source validation and its task fails.
    That is a loud failure rather than a wrong answer, and it is why
    concurrency stays opt-in.
    """
    if concurrent_tasks:
        row = connection.execute(
            """
            select count(*) as total from team_model_operations
            where cycle_id = ?
              and coalesce(task_id, '~cycle~') = ?
              and status in (
                  'prepared', 'invoking', 'completed',
                  'waiting_for_provider', 'ambiguous'
              )
            """,
            (
                operation.cycle_id,
                operation.task_id if operation.task_id is not None else "~cycle~",
            ),
        ).fetchone()
        if row is None or row["total"] != 1:
            raise OperationConflict("Task does not have one open operation")
        return
    row = connection.execute(
        """
        select count(*) as total from team_model_operations
        where cycle_id = ? and status in (
            'prepared', 'invoking', 'completed',
            'waiting_for_provider', 'ambiguous'
        )
        """,
        (operation.cycle_id,),
    ).fetchone()
    if row is None or row["total"] != 1:
        raise OperationConflict("Cycle does not have one open operation")


def _validate_operation_source(
    connection,
    operation: TeamModelOperation,
    *,
    expected_mode: str,
) -> _OperationSource:
    run = connection.execute(
        "select * from team_runs where id = ?",
        (operation.team_run_id,),
    ).fetchone()
    cycle = connection.execute(
        "select * from team_run_cycles where id = ?",
        (operation.cycle_id,),
    ).fetchone()
    actor = connection.execute(
        "select * from team_agents where id = ?",
        (operation.agent_id,),
    ).fetchone()
    task = (
        connection.execute(
            "select * from team_tasks where id = ?",
            (operation.task_id,),
        ).fetchone()
        if operation.task_id is not None
        else None
    )
    worker = None
    if task is not None and task["owner_agent_id"] is not None:
        worker = connection.execute(
            "select * from team_agents where id = ?",
            (task["owner_agent_id"],),
        ).fetchone()
    if (
        run is None
        or cycle is None
        or actor is None
        or cycle["team_run_id"] != operation.team_run_id
        or actor["team_run_id"] != operation.team_run_id
        or actor["backend"] != operation.provider
        or (operation.task_id is not None and task is None)
        or (
            task is not None
            and (
                task["team_run_id"] != operation.team_run_id
                or task["cycle_id"] != operation.cycle_id
            )
        )
    ):
        raise OperationConflict("Operation source ownership is invalid")
    request = connection.execute(
        "select status from team_cycle_requests where id = ?",
        (cycle["request_id"],),
    ).fetchone()
    if request is None or request["status"] != "dispatching":
        raise OperationConflict("Operation cycle request is not dispatching")
    if expected_mode == "waiting":
        if (
            run["status"] != "waiting_for_provider"
            or cycle["status"] != "waiting_for_provider"
            or actor["status"] != "waiting"
            or (
                task is not None
                and task["status"] != "waiting_for_provider"
            )
        ):
            raise OperationConflict("Provider waiting source state is invalid")
        if (
            operation.stage in _WORKER_STAGES
            and (
                task is None
                or actor["current_task_id"] != task["id"]
            )
        ):
            raise OperationConflict("Waiting Worker source state is invalid")
        if (
            operation.stage in _LEAD_STAGES | _CONSULT_STAGES
            and (
                task is None
                or worker is None
                or actor["current_task_id"] is not None
                or worker["status"] != "running"
                or worker["current_task_id"] != task["id"]
            )
        ):
            raise OperationConflict("Waiting Lead source state is invalid")
    elif expected_mode == "interrupted":
        if (
            run["status"] != "interrupted"
            or cycle["status"] != "interrupted"
            or actor["status"] != "pending"
            or actor["current_task_id"] is not None
            or (
                task is not None
                and task["status"] != "pending"
            )
            or (
                worker is not None
                and (
                    worker["status"] != "pending"
                    or worker["current_task_id"] is not None
                )
            )
        ):
            raise OperationConflict("Ambiguous source state is invalid")
    elif expected_mode == "active":
        _validate_active_source(operation, run, cycle, task, actor, worker)
    return _OperationSource(run, cycle, task, actor, worker)


def _validate_active_source(operation, run, cycle, task, actor, worker) -> None:
    if operation.stage == "cycle_add_work" or (
        operation.stage == "cycle_planning_repair"
        and operation.stage_ordinal == 2
    ):
        if (
            operation.task_id is not None
            or cycle["status"] != "queued"
            or run["status"] == "canceled"
        ):
            raise OperationConflict("Add-work operation source is invalid")
        return
    if operation.stage in {"cycle_planning", "cycle_planning_repair"}:
        valid = (
            operation.task_id is None
            and run["status"] == "planning"
            and cycle["status"] == "running"
            and actor["status"] == "running"
        )
    elif operation.stage in _WORKER_STAGES:
        valid = (
            task is not None
            and task["owner_agent_id"] == operation.agent_id
            and task["status"] == "in_progress"
            and actor["status"] == "running"
            and actor["current_task_id"] == task["id"]
            and run["status"] == "running"
            and cycle["status"] == "running"
        )
    elif operation.stage in _LEAD_STAGES:
        valid = (
            task is not None
            and worker is not None
            and task["status"] == "in_progress"
            and worker["status"] == "running"
            and worker["current_task_id"] == task["id"]
            and actor["id"] == run["leader_agent_id"]
            and actor["status"] == "running"
            and run["status"] == "running"
            and cycle["status"] == "running"
        )
    elif operation.stage in _CONSULT_STAGES:
        # Lead-stage shape with a different respondent: the answering actor is
        # a fellow worker, never the task owner (a consult with yourself is
        # refused at record time) and never the leader (that is mediation).
        valid = (
            task is not None
            and worker is not None
            and task["status"] == "in_progress"
            and worker["status"] == "running"
            and worker["current_task_id"] == task["id"]
            and actor["id"] != run["leader_agent_id"]
            and actor["id"] != task["owner_agent_id"]
            and run["status"] == "running"
            and cycle["status"] == "running"
        )
    elif operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}:
        valid = (
            operation.task_id is None
            and run["status"] == "summarizing"
            and cycle["status"] == "running"
            and actor["status"] == "running"
        )
    elif operation.stage in {"cycle_plan_review", "cycle_plan_review_repair"}:
        # A plan review in flight is the second state that matches no other
        # stage: negotiation runs after the plan is applied but before the run
        # is promoted, so the run is still "planning" while the cycle runs, the
        # actor is a *worker* that has not been given work yet and so is still
        # "pending", and there is no task because the review is about the whole
        # revision -- pinned by
        # test_a_parked_plan_review_restores_the_state_it_was_parked_from.
        # Without this branch the fallback below refused the source,
        # wait_for_operation raised OperationConflict instead of parking, and a
        # provider blip during a worker's review failed the cycle.
        valid = (
            operation.task_id is None
            and run["status"] == "planning"
            and cycle["status"] == "running"
            and actor["status"] == "pending"
            and actor["current_task_id"] is None
        )
    elif operation.stage in {"cycle_contest", "cycle_contest_repair"}:
        # A contest in flight looks like nothing else: the run and cycle are
        # both "running", there is no task, and the leader is still "pending"
        # because adjudicate_contest never calls set_agent_status -- pinned by
        # test_a_parked_contest_restores_the_state_it_was_parked_from. Without
        # this branch the fallback below refused the source, wait_for_operation
        # raised OperationConflict instead of parking, and a provider blip
        # during the leader's ruling failed the cycle and lost the objection.
        valid = (
            operation.task_id is None
            and run["status"] == "running"
            and cycle["status"] == "running"
            and actor["status"] == "pending"
            and actor["current_task_id"] is None
        )
    else:
        valid = False
    if not valid:
        raise OperationConflict("Operation active source state is invalid")


def _restore_operation_source(
    connection,
    operation: TeamModelOperation,
    metadata: dict[str, object],
    timestamp: str,
) -> None:
    preplanning = operation.stage == "cycle_add_work" or (
        operation.stage == "cycle_planning_repair"
        and operation.stage_ordinal == 2
    )
    if preplanning:
        run_status = "draft"
        cycle_status = "queued"
        actor_status = "pending"
    elif operation.stage in {"cycle_planning", "cycle_planning_repair"}:
        run_status = "planning"
        cycle_status = "running"
        actor_status = "running"
    elif operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}:
        run_status = "summarizing"
        cycle_status = "running"
        actor_status = "running"
    elif operation.stage in {"cycle_plan_review", "cycle_plan_review_repair"}:
        # The state _validate_active_source accepts for a plan review, restored
        # exactly: the generic fallback would promote the run to "running" and
        # the reviewing worker to "running", neither of which a review ever had,
        # and the validator would then refuse the source this just wrote.
        run_status = "planning"
        cycle_status = "running"
        actor_status = "pending"
    elif operation.stage in {"cycle_contest", "cycle_contest_repair"}:
        # The state _validate_active_source accepts for a contest, restored
        # exactly: the generic fallback would put the leader back as "running",
        # which is a status a contest never had, and the validator would then
        # refuse the very source this just wrote.
        run_status = "running"
        cycle_status = "running"
        actor_status = "pending"
    else:
        run_status = "running"
        cycle_status = "running"
        actor_status = "running"
    connection.execute(
        """
        update team_runs set status = ?, error_message = null,
            finished_at = null, updated_at = ? where id = ?
        """,
        (run_status, timestamp, operation.team_run_id),
    )
    connection.execute(
        """
        update team_run_cycles set status = ?, error_message = null,
            execution_metadata_json = ?, finished_at = null, updated_at = ?
        where id = ?
        """,
        (
            cycle_status,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            timestamp,
            operation.cycle_id,
        ),
    )
    if operation.task_id is not None:
        connection.execute(
            """
            update team_tasks set status = 'in_progress',
                error_message = null, finished_at = null, updated_at = ?
            where id = ?
            """,
            (timestamp, operation.task_id),
        )
    if (
        operation.stage in _LEAD_STAGES | _CONSULT_STAGES
        and operation.task_id is not None
    ):
        task = connection.execute(
            "select owner_agent_id from team_tasks where id = ?",
            (operation.task_id,),
        ).fetchone()
        connection.execute(
            """
            update team_agents set status = 'running',
                current_task_id = ?, finished_at = null, updated_at = ?
            where id = ?
            """,
            (operation.task_id, timestamp, task["owner_agent_id"]),
        )
        connection.execute(
            """
            update team_agents set status = 'running',
                current_task_id = null, finished_at = null, updated_at = ?
            where id = ?
            """,
            (timestamp, operation.agent_id),
        )
    else:
        connection.execute(
            """
            update team_agents set status = ?, current_task_id = ?,
                finished_at = null, updated_at = ? where id = ?
            """,
            (
                actor_status,
                (
                    operation.task_id
                    if operation.stage in _WORKER_STAGES
                    else None
                ),
                timestamp,
                operation.agent_id,
            ),
        )


def _interrupt_ambiguous(
    connection,
    operation: TeamModelOperation,
    *,
    upstream_session_id: str | None,
    timestamp: str,
) -> None:
    cursor = connection.execute(
        """
        update team_model_operations
        set status = 'ambiguous', version = version + 1,
            upstream_session_id = coalesce(upstream_session_id, ?),
            reason_code = 'ambiguous_remote_result', updated_at = ?
        where id = ? and status = 'invoking' and version = ?
        """,
        (
            upstream_session_id,
            timestamp,
            operation.id,
            operation.version,
        ),
    )
    _require_one(cursor, "Ambiguous operation changed")
    connection.execute(
        """
        update team_tasks set status = 'pending', result = null,
            error_message = null, started_at = null, finished_at = null,
            updated_at = ?
        where team_run_id = ? and status in (
            'in_progress', 'waiting_for_provider'
        )
        """,
        (timestamp, operation.team_run_id),
    )
    connection.execute(
        """
        update team_agents set status = 'pending', current_task_id = null,
            finished_at = null, updated_at = ?
        where team_run_id = ? and status in ('running', 'waiting')
        """,
        (timestamp, operation.team_run_id),
    )
    connection.execute(
        """
        update team_runs set status = 'interrupted', error_message = null,
            finished_at = null, updated_at = ? where id = ?
        """,
        (timestamp, operation.team_run_id),
    )
    connection.execute(
        """
        update team_run_cycles set status = 'interrupted',
            error_message = null, finished_at = null, updated_at = ?
        where id = ?
        """,
        (timestamp, operation.cycle_id),
    )


def _cancel_for_canceled_source(
    connection,
    operation: TeamModelOperation,
) -> bool:
    source = connection.execute(
        """
        select run.status as run_status, cycle.status as cycle_status,
               request.status as request_status
        from team_runs run
        join team_run_cycles cycle
          on cycle.id = ? and cycle.team_run_id = run.id
        left join team_cycle_requests request on request.id = cycle.request_id
        where run.id = ?
        """,
        (operation.cycle_id, operation.team_run_id),
    ).fetchone()
    if source is None or "canceled" not in {
        source["run_status"],
        source["cycle_status"],
        source["request_status"],
    }:
        return False
    cursor = connection.execute(
        """
        update team_model_operations
        set status = 'canceled', version = version + 1,
            reason_code = 'source_canceled', updated_at = ?
        where id = ? and status = ? and version = ?
        """,
        (
            _timestamp(),
            operation.id,
            operation.status,
            operation.version,
        ),
    )
    _require_one(cursor, "Canceled source operation changed")
    return True


def _single_ambiguous_for_run(operations, teams, team_run_id):
    rows = teams._db.fetchall(
        """
        select id from team_model_operations
        where team_run_id = ? and status = 'ambiguous'
        order by created_at asc, id asc
        """,
        (team_run_id,),
    )
    if len(rows) != 1:
        raise AmbiguousOperationNotReconcilable()
    return operations.get(rows[0]["id"])


def _session_matches(
    operation: TeamModelOperation,
    session: object,
) -> bool:
    return (
        isinstance(session, dict)
        and session.get("provider") == operation.provider
        and session.get("consumer") == "personal-agent-gateway"
        and session.get("consumer_session_id") == operation.team_run_id
        and session.get("consumer_run_id") == operation.consumer_run_id
        and isinstance(session.get("upstream_id"), str)
        and bool(session["upstream_id"])
    )


def _cycle_metadata(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(value) if value else {}
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cycle execution metadata") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Invalid cycle execution metadata")
    return parsed


def _provider_recovery_metadata(
    metadata: dict[str, object],
) -> dict[str, object]:
    recovery = metadata.get("provider_recovery")
    if not isinstance(recovery, dict):
        raise ValueError("Invalid provider recovery metadata")
    if not all(
        isinstance(recovery.get(field), str) and recovery[field]
        for field in ("operation_id", "provider", "next_retry_at")
    ):
        raise ValueError("Invalid provider recovery metadata")
    return recovery


def _timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _require_one(cursor, message: str) -> None:
    if cursor.rowcount != 1:
        raise OperationConflict(message)
