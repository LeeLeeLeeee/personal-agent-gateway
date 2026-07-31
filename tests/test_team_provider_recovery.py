import hashlib
from types import SimpleNamespace

import pytest

from personal_agent_gateway.team_model_operations import (
    OperationSpec,
    StaleOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from personal_agent_gateway.team_provider_recovery import TeamProviderRecovery
from personal_agent_gateway.teams import ProviderRecoveryClaim
from team_cycle_helpers import (
    dt,
    make_cycle_services,
    make_queued_cycle,
    make_running_task_in_cycle,
)


class SessionLoader:
    def __init__(self, result=None, error=None):
        self.result = [] if result is None else result
        self.error = error

    def __call__(self):
        if self.error is not None:
            raise self.error
        return self.result


def _registry():
    return SimpleNamespace(get=lambda _provider: None)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def make_invoking_operation(tmp_path, stage, *, lead_actor=False, preplanning=False):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    if preplanning:
        cycle = make_queued_cycle(teams, cycles, run)
        task = None
        actor = teams.get_agent(run.leader_agent_id)
        worker = next(
            agent
            for agent in teams.list_agents(run.id)
            if agent.id != run.leader_agent_id
        )
    else:
        cycle, task, worker = make_running_task_in_cycle(
            teams,
            cycles,
            run,
        )
        actor = teams.get_agent(run.leader_agent_id) if lead_actor else worker
        if lead_actor:
            actor = teams.set_agent_status(actor.id, "running")
    operations = TeamModelOperationService(db)
    operation = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:{stage}:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=task.id if task is not None else None,
            agent_id=actor.id,
            provider=actor.backend,
            stage=stage,
            stage_ordinal=0,
            request_digest=_digest(f"{stage}-request"),
        )
    )
    operation = operations.begin_attempt(operation.id, "consumer-1")
    loader = SessionLoader()
    recovery = TeamProviderRecovery(
        teams,
        _registry(),
        operations,
        session_loader=loader,
    )
    return SimpleNamespace(
        db=db,
        teams=teams,
        cycles=cycles,
        run=run,
        cycle=cycle,
        task=task,
        worker=worker,
        actor=actor,
        operations=operations,
        operation=operation,
        recovery=recovery,
        session_loader=loader,
    )


def _session(setup, *, provider=None, consumer_run_id=None, upstream_id="sess-1"):
    return {
        "provider": provider or setup.operation.provider,
        "upstream_id": upstream_id,
        "consumer": "personal-agent-gateway",
        "consumer_session_id": setup.run.id,
        "consumer_run_id": consumer_run_id or setup.operation.consumer_run_id,
    }


def test_claim_lead_waiting_operation_restores_stage_without_pending_worker(
    tmp_path,
):
    setup = make_invoking_operation(
        tmp_path,
        "acceptance_lead",
        lead_actor=True,
    )
    setup.recovery.wait_for_operation(
        setup.operation.id,
        reason_code="provider_unavailable",
        now=dt("2026-07-31T00:00:00+00:00"),
    )

    claim = setup.recovery.claim_operation(
        setup.cycle.id,
        now=dt("2026-07-31T00:00:30+00:00"),
    )
    second = setup.recovery.claim_operation(
        setup.cycle.id,
        now=dt("2026-07-31T00:00:31+00:00"),
    )

    assert claim.operation_id == setup.operation.id
    assert second is None
    assert setup.operations.get(setup.operation.id).status == "prepared"
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    assert setup.teams.get_task(setup.task.id).owner_agent_id == setup.worker.id
    assert setup.teams.get_agent(setup.run.leader_agent_id).status == "running"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "dispatching"


@pytest.mark.parametrize(
    ("stage", "preplanning"),
    [("worker_execution", False), ("cycle_add_work", True)],
)
def test_claim_waiting_operation_restores_worker_and_preplanning_sources(
    tmp_path,
    stage,
    preplanning,
):
    setup = make_invoking_operation(
        tmp_path,
        stage,
        preplanning=preplanning,
    )
    setup.recovery.wait_for_operation(
        setup.operation.id,
        reason_code="provider_unavailable",
        now=dt("2026-07-31T00:00:00+00:00"),
    )

    claim = setup.recovery.claim_operation(
        setup.cycle.id,
        now=dt("2026-07-31T00:00:30+00:00"),
    )

    assert claim.operation_id == setup.operation.id
    assert setup.operations.get(setup.operation.id).status == "prepared"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "dispatching"
    if preplanning:
        assert setup.teams.get_cycle(setup.cycle.id).status == "queued"
        assert setup.teams.get_team_run(setup.run.id).status == "draft"
        assert setup.teams.list_tasks(setup.run.id, setup.cycle.id) == []
    else:
        assert setup.teams.get_task(setup.task.id).status == "in_progress"
        assert setup.teams.get_agent(setup.worker.id).status == "running"


@pytest.mark.asyncio
async def test_ambiguous_operation_without_one_strict_session_stays_interrupted(
    tmp_path,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")

    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id=None,
    )

    assert setup.operations.get(setup.operation.id).status == "ambiguous"
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"
    assert setup.teams.get_cycle(setup.cycle.id).status == "interrupted"
    assert setup.session_loader.result == []


@pytest.mark.parametrize(
    "sessions",
    [
        [],
        ["duplicate"],
        ["provider_mismatch"],
        ["consumer_run_mismatch"],
    ],
)
@pytest.mark.asyncio
async def test_explicit_resume_requires_exactly_one_strict_session(
    tmp_path,
    sessions,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id=None,
    )
    if sessions == ["duplicate"]:
        setup.session_loader.result = [
            _session(setup, upstream_id="sess-1"),
            _session(setup, upstream_id="sess-2"),
        ]
    elif sessions == ["provider_mismatch"]:
        setup.session_loader.result = [_session(setup, provider="claude")]
    elif sessions == ["consumer_run_mismatch"]:
        setup.session_loader.result = [
            _session(setup, consumer_run_id="consumer-2")
        ]

    with pytest.raises(
        ValueError,
        match="ambiguous_operation_not_reconcilable",
    ):
        setup.recovery.prepare_explicit_resume(setup.run.id)

    assert setup.operations.get(setup.operation.id).status == "ambiguous"
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"
    assert setup.teams.get_cycle(setup.cycle.id).status == "interrupted"


@pytest.mark.asyncio
async def test_explicit_resume_records_strict_session_and_restores_source(
    tmp_path,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id=None,
    )
    setup.session_loader.result = [_session(setup)]

    claim = setup.recovery.prepare_explicit_resume(setup.run.id)

    operation = setup.operations.get(setup.operation.id)
    assert claim.operation_id == operation.id
    assert operation.status == "prepared"
    assert operation.upstream_session_id == "sess-1"
    assert setup.teams.get_team_run(setup.run.id).status == "running"
    assert setup.teams.get_cycle(setup.cycle.id).status == "running"
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    assert setup.teams.get_agent(setup.worker.id).status == "running"
    assert setup.teams.get_agent(setup.worker.id).upstream_session_id is None


@pytest.mark.asyncio
async def test_explicit_resume_loader_failure_keeps_ambiguous_state(tmp_path):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id=None,
    )
    setup.session_loader.error = RuntimeError("LMG unavailable")

    with pytest.raises(
        ValueError,
        match="ambiguous_operation_not_reconcilable",
    ):
        setup.recovery.prepare_explicit_resume(setup.run.id)

    assert setup.operations.get(setup.operation.id).status == "ambiguous"
    assert setup.teams.get_team_run(setup.run.id).status == "interrupted"


@pytest.mark.asyncio
async def test_recorded_ambiguous_session_still_requires_strict_identity(
    tmp_path,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    await setup.recovery.interrupt_ambiguous_operation(
        setup.operation.id,
        consumer_run_id="consumer-1",
        upstream_session_id="recorded-session",
    )
    setup.session_loader.result = [
        _session(setup, upstream_id="different-session")
    ]

    with pytest.raises(
        ValueError,
        match="ambiguous_operation_not_reconcilable",
    ):
        setup.recovery.prepare_explicit_resume(setup.run.id)

    operation = setup.operations.get(setup.operation.id)
    assert operation.status == "ambiguous"
    assert operation.upstream_session_id == "recorded-session"


def test_startup_reconciliation_separates_runnable_and_ambiguous(tmp_path):
    invoking = make_invoking_operation(tmp_path / "invoking", "worker_execution")
    prepared = make_invoking_operation(
        tmp_path / "prepared",
        "cycle_add_work",
        preplanning=True,
    )
    prepared.operations.prepare_retry(
        prepared.operation.id,
        prepared.operation.version,
        "provider_unavailable",
    )

    invoking_result = invoking.recovery.reconcile_startup()
    prepared_result = prepared.recovery.reconcile_startup()

    assert invoking.operations.get(invoking.operation.id).status == "ambiguous"
    assert invoking.teams.get_team_run(invoking.run.id).status == "interrupted"
    assert invoking.cycle.id in invoking_result.interrupted_cycle_ids
    assert prepared.operations.get(prepared.operation.id).status == "prepared"
    assert prepared.cycle.id in prepared_result.runnable_cycle_ids


def test_cancel_waiting_operation_settles_complete_continuous_lineage(tmp_path):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    setup.recovery.wait_for_operation(
        setup.operation.id,
        reason_code="provider_unavailable",
        now=dt("2026-07-31T00:00:00+00:00"),
    )

    setup.cycles.cancel_run(
        setup.run.id,
        reason="user",
        now=dt("2026-07-31T00:00:10+00:00"),
    )

    assert setup.operations.get(setup.operation.id).status == "canceled"
    assert setup.teams.get_team_run(setup.run.id).status == "canceled"
    assert setup.teams.get_cycle(setup.cycle.id).status == "canceled"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "canceled"
    assert setup.teams.get_task(setup.task.id).status == "canceled"
    assert setup.teams.get_agent(setup.worker.id).status == "canceled"


def test_cancel_invoking_operation_rejects_late_completion_and_startup_replay(
    tmp_path,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")

    setup.cycles.cancel_run(
        setup.run.id,
        reason="user",
        now=dt("2026-07-31T00:00:10+00:00"),
    )
    result = setup.recovery.reconcile_startup()

    with pytest.raises(StaleOperation, match="Expected operation status invoking"):
        setup.operations.complete(
            setup.operation.id,
            setup.operation.version,
            ValidatedOperationResult("task_outcome", {"status": "completed"}),
        )
    assert result == type(result)((), (), ())
    assert setup.operations.get(setup.operation.id).status == "canceled"
    assert setup.teams.get_team_run(setup.run.id).status == "canceled"
    assert setup.teams.get_cycle(setup.cycle.id).status == "canceled"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "canceled"
    assert setup.teams.get_task(setup.task.id).status == "canceled"
    assert setup.teams.get_agent(setup.worker.id).status == "canceled"


def test_startup_reconciliation_cancels_lingering_operation_for_canceled_source(
    tmp_path,
):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    setup.db.execute(
        "update team_runs set status = 'canceled' where id = ?",
        (setup.run.id,),
    )
    setup.db.execute(
        "update team_run_cycles set status = 'canceled' where id = ?",
        (setup.cycle.id,),
    )
    setup.db.execute(
        "update team_cycle_requests set status = 'canceled' where id = ?",
        (setup.cycle.request_id,),
    )

    result = setup.recovery.reconcile_startup()

    assert result == type(result)((), (), ())
    assert setup.operations.get(setup.operation.id).status == "canceled"
    assert setup.teams.get_team_run(setup.run.id).status == "canceled"
    assert setup.teams.get_cycle(setup.cycle.id).status == "canceled"
    assert setup.cycles.get_request(setup.cycle.request_id).status == "canceled"


def test_generic_startup_interrupt_skips_operation_backed_run(tmp_path):
    setup = make_invoking_operation(tmp_path, "worker_execution")
    setup.operations.prepare_retry(
        setup.operation.id,
        setup.operation.version,
        "provider_unavailable",
    )

    interrupted = setup.teams.interrupt_active_runs()

    assert interrupted == []
    assert setup.teams.get_team_run(setup.run.id).status == "running"
    assert setup.teams.get_cycle(setup.cycle.id).status == "running"
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    assert setup.teams.get_agent(setup.worker.id).status == "running"


def make_waiting_provider_state(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    teams.set_cycle_execution_metadata(
        cycle.id,
        {
            "provider_capabilities": {"codex": {"ready": True}},
            "agents": {agent.id: {"permission_mode": "default"}},
        },
    )
    teams.mark_waiting_for_provider(
        cycle.id,
        provider="codex",
        reason_code="provider_unavailable",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )
    return db, teams, cycles, run, cycle, task, agent


def make_preplanning_waiting_state(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    teams.set_cycle_execution_metadata(
        cycle.id,
        {"provider_capabilities": {"codex": {"ready": True}}},
    )
    teams.mark_waiting_for_provider(
        cycle.id,
        provider="codex",
        reason_code="capabilities_unavailable",
        attempts=3,
        task_id=None,
        agent_id=None,
        now=dt("2026-07-30T00:00:00+00:00"),
    )
    return db, teams, cycles, run, cycle


def test_claim_provider_recovery_resumes_same_cycle_once(tmp_path):
    db, teams, cycles, run, cycle, task, agent = make_waiting_provider_state(
        tmp_path
    )
    db.execute(
        """
        update team_tasks
        set result = 'stale', error_message = 'stale',
            finished_at = '2026-07-30T00:00:15+00:00'
        where id = ?
        """,
        (task.id,),
    )

    first = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:30+00:00"),
    )
    second = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:31+00:00"),
    )

    assert first == ProviderRecoveryClaim(run.id, cycle.id, task.id)
    assert second is None
    recovered_cycle = teams.get_cycle(cycle.id)
    assert recovered_cycle.status == "running"
    assert recovered_cycle.updated_at == "2026-07-30T00:00:30+00:00"
    recovered_run = teams.get_team_run(run.id)
    assert recovered_run.status == "running"
    assert recovered_run.updated_at == "2026-07-30T00:00:30+00:00"
    recovered_task = teams.get_task(task.id)
    assert recovered_task.status == "pending"
    assert recovered_task.result is None
    assert recovered_task.error_message is None
    assert recovered_task.started_at is None
    assert recovered_task.finished_at is None
    assert recovered_task.updated_at == "2026-07-30T00:00:30+00:00"
    recovered_agent = teams.get_agent(agent.id)
    assert recovered_agent.status == "pending"
    assert recovered_agent.current_task_id is None
    assert recovered_agent.updated_at == "2026-07-30T00:00:30+00:00"
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    assert recovered_cycle.execution_metadata == {
        "provider_capabilities": {"codex": {"ready": True}},
        "agents": {agent.id: {"permission_mode": "default"}},
    }


def test_claim_provider_recovery_resumes_preplanning_freeze_without_task(tmp_path):
    _db, teams, cycles, run, cycle = make_preplanning_waiting_state(
        tmp_path
    )

    first = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:30+00:00"),
    )
    second = teams.claim_provider_recovery(
        cycle.id,
        now=dt("2026-07-30T00:00:31+00:00"),
    )

    assert first == ProviderRecoveryClaim(run.id, cycle.id, None)
    assert second is None
    recovered_cycle = teams.get_cycle(cycle.id)
    assert recovered_cycle.status == "running"
    assert recovered_cycle.execution_metadata == {
        "provider_capabilities": {"codex": {"ready": True}}
    }
    assert teams.get_team_run(run.id).status == "running"
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    assert teams.list_tasks(run.id, cycle.id) == []
    assert {agent.status for agent in teams.list_agents(run.id)} == {"pending"}


@pytest.mark.parametrize(
    "invalid_state",
    ["unlinked", "pending_task", "agent_execution_marker"],
)
def test_claim_provider_recovery_rolls_back_nonpristine_preplanning_state(
    tmp_path,
    invalid_state,
):
    db, teams, cycles, run, cycle = make_preplanning_waiting_state(tmp_path)
    request_id = cycle.request_id
    if invalid_state == "unlinked":
        db.execute(
            "update team_run_cycles set request_id = null where id = ?",
            (cycle.id,),
        )
    elif invalid_state == "pending_task":
        teams.create_task(
            run.id,
            "premature",
            "premature",
            owner_agent_id=None,
            cycle_id=cycle.id,
        )
    else:
        db.execute(
            """
            update team_agents
            set reinvocations = 1, upstream_session_id = 'prior-session'
            where id = ?
            """,
            (run.leader_agent_id,),
        )
    before_cycle = teams.get_cycle(cycle.id)
    before_run = teams.get_team_run(run.id)
    before_tasks = teams.list_tasks(run.id, cycle.id)
    before_agents = teams.list_agents(run.id)

    with pytest.raises(ValueError, match="provider recovery related state"):
        teams.claim_provider_recovery(
            cycle.id,
            now=dt("2026-07-30T00:00:30+00:00"),
        )

    assert teams.get_cycle(cycle.id) == before_cycle
    assert teams.get_team_run(run.id) == before_run
    assert teams.list_tasks(run.id, cycle.id) == before_tasks
    assert teams.list_agents(run.id) == before_agents
    assert cycles.get_request(request_id).status == "dispatching"


@pytest.mark.parametrize("malformed_state", ["missing", "wrong_type"])
def test_claim_provider_recovery_rolls_back_malformed_metadata(
    tmp_path,
    malformed_state,
):
    _db, teams, cycles, run, cycle, task, agent = make_waiting_provider_state(
        tmp_path
    )
    metadata = {
        "provider_capabilities": {"codex": {"ready": True}},
        "agents": {agent.id: {"permission_mode": "default"}},
    }
    if malformed_state == "wrong_type":
        metadata["provider_recovery"] = "invalid"
    teams.set_cycle_execution_metadata(cycle.id, metadata)
    before_cycle = teams.get_cycle(cycle.id)
    before_run = teams.get_team_run(run.id)
    before_task = teams.get_task(task.id)
    before_agent = teams.get_agent(agent.id)

    with pytest.raises(ValueError, match="provider recovery metadata"):
        teams.claim_provider_recovery(
            cycle.id,
            now=dt("2026-07-30T00:00:30+00:00"),
        )

    assert teams.get_cycle(cycle.id) == before_cycle
    assert teams.get_team_run(run.id) == before_run
    assert teams.get_task(task.id) == before_task
    assert teams.get_agent(agent.id) == before_agent
    assert cycles.get_request(cycle.request_id).status == "dispatching"


@pytest.mark.parametrize("missing_row", ["task", "agent"])
def test_claim_provider_recovery_rolls_back_missing_related_state(
    tmp_path,
    missing_row,
):
    db, teams, cycles, run, cycle, task, agent = make_waiting_provider_state(
        tmp_path
    )
    if missing_row == "task":
        db.execute("delete from team_tasks where id = ?", (task.id,))
    else:
        db.execute("delete from team_agents where id = ?", (agent.id,))
    before_cycle = teams.get_cycle(cycle.id)
    before_run = teams.get_team_run(run.id)

    with pytest.raises(ValueError, match="provider recovery related state"):
        teams.claim_provider_recovery(
            cycle.id,
            now=dt("2026-07-30T00:00:30+00:00"),
        )

    assert teams.get_cycle(cycle.id) == before_cycle
    assert teams.get_team_run(run.id) == before_run
    if missing_row == "task":
        assert teams.get_agent(agent.id).status == "waiting"
        assert teams.get_agent(agent.id).current_task_id == task.id
    else:
        assert teams.get_task(task.id).status == "waiting_for_provider"
        assert teams.get_task(task.id).owner_agent_id is None
    assert cycles.get_request(cycle.request_id).status == "dispatching"


def test_claim_provider_recovery_rolls_back_omitted_related_ids(tmp_path):
    _db, teams, cycles, run, cycle, task, agent = make_waiting_provider_state(
        tmp_path
    )
    metadata = teams.get_cycle(cycle.id).execution_metadata
    metadata["provider_recovery"]["task_id"] = None
    metadata["provider_recovery"]["agent_id"] = None
    teams.set_cycle_execution_metadata(cycle.id, metadata)
    before_cycle = teams.get_cycle(cycle.id)
    before_run = teams.get_team_run(run.id)
    before_task = teams.get_task(task.id)
    before_agent = teams.get_agent(agent.id)

    with pytest.raises(ValueError, match="provider recovery related state"):
        teams.claim_provider_recovery(
            cycle.id,
            now=dt("2026-07-30T00:00:30+00:00"),
        )

    assert teams.get_cycle(cycle.id) == before_cycle
    assert teams.get_team_run(run.id) == before_run
    assert teams.get_task(task.id) == before_task
    assert teams.get_agent(agent.id) == before_agent
    assert cycles.get_request(cycle.request_id).status == "dispatching"
