from dataclasses import replace

import pytest

from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    OperationSpec,
    StaleOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from team_cycle_helpers import make_cycle_services, make_queued_cycle


REQUEST_DIGEST = "a" * 64


def changed_task_plan():
    return {
        "tasks": [
            {
                "title": "Changed task",
                "description": "Changed task description",
                "owner_agent_id": None,
                "required": True,
                "acceptance": {
                    "required_outputs": ["changed.md"],
                    "required_verifications": [],
                },
            }
        ]
    }


def operation_spec(run, cycle, agent, *, key="worker:0", **changes):
    spec = OperationSpec(
        operation_key=f"{cycle.id}:{key}",
        team_run_id=run.id,
        cycle_id=cycle.id,
        task_id=None,
        agent_id=agent.id,
        provider=agent.backend,
        stage="cycle_planning",
        stage_ordinal=0,
        request_digest=REQUEST_DIGEST,
    )
    return replace(spec, **changes)


def test_reserve_is_idempotent_and_rejects_second_open_operation(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)

    first = service.reserve(operation_spec(run, cycle, agent))
    duplicate = service.reserve(operation_spec(run, cycle, agent))

    assert duplicate.id == first.id
    assert duplicate.status == "prepared"
    with pytest.raises(OperationConflict):
        service.reserve(operation_spec(run, cycle, agent, key="other:0"))


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "other-agent"},
        {"provider": "other-provider"},
        {"stage": "cycle_add_work"},
        {"stage_ordinal": 1},
        {"request_digest": "b" * 64},
        {"upstream_session_id": "other-session"},
    ],
)
def test_reserve_rejects_same_key_with_different_immutable_fields(tmp_path, changes):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    spec = operation_spec(
        run,
        cycle,
        agent,
        upstream_session_id="seed-session" if changes.get("upstream_session_id") else None,
    )
    service.reserve(spec)

    with pytest.raises(OperationConflict):
        service.reserve(replace(spec, **changes))


def test_reserve_allows_null_session_seed_after_session_is_learned(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_plan", {"tasks": []}),
        upstream_session_id="learned-session",
    )

    duplicate = service.reserve(operation_spec(run, cycle, agent))

    assert duplicate.id == completed.id
    assert duplicate.upstream_session_id == "learned-session"


def test_lifecycle_uses_version_cas_and_completed_result_is_immutable(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    result = ValidatedOperationResult("task_plan", {"tasks": []})

    completed = service.complete(invoking.id, invoking.version, result)
    same = service.complete(invoking.id, invoking.version, result)

    assert completed.status == "completed"
    assert same.result_digest == completed.result_digest
    with pytest.raises(StaleOperation):
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", changed_task_plan()),
        )


def test_default_constructor_completes_original_task_plan_lifecycle(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_plan", {"tasks": []}),
    )

    assert completed.status == "completed"
    assert completed.result_kind == "task_plan"


def test_stale_completion_rolls_back_without_changing_the_operation(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_plan", {"tasks": []}),
    )

    with pytest.raises(StaleOperation):
        service.complete(
            completed.id,
            completed.version,
            ValidatedOperationResult("task_plan", changed_task_plan()),
        )

    assert service.get(completed.id) == completed


def test_retry_failure_cancellation_and_cycle_queries_use_cas(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    retried = service.prepare_retry(invoking.id, invoking.version, "provider_unavailable")
    next_attempt = service.begin_attempt(retried.id, "consumer-2")
    failed = service.mark_failed(next_attempt.id, next_attempt.version, "invalid_result")
    cancelable = service.reserve(operation_spec(run, cycle, agent, key="cancel:0"))
    canceled = service.mark_canceled(
        cancelable.id,
        cancelable.version,
        expected_status="prepared",
    )

    assert retried.status == "prepared"
    assert retried.reason_code == "provider_unavailable"
    assert next_attempt.attempts == 2
    assert failed.status == "failed"
    assert canceled.status == "canceled"
    assert service.get_by_key(canceled.operation_key) == canceled
    assert service.get_open_for_cycle(cycle.id) is None
    assert service.list_for_cycle(cycle.id) == [failed, canceled]
    with pytest.raises(StaleOperation):
        service.prepare_retry(next_attempt.id, next_attempt.version, "stale")


def test_reserve_rejects_raw_request_content_without_persisting_it(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)

    with pytest.raises(OperationConflict):
        service.reserve(
            operation_spec(
                run,
                cycle,
                agent,
                request_digest="Raw user prompt containing a local token",
            )
        )

    assert db.fetchone("select id from team_model_operations") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"tasks": [], "raw_prompt": "full user prompt"},
        {"tasks": [], "raw_response": "full model response"},
        {"tasks": [], "stderr": "provider stderr"},
        {"tasks": [], "local_token": "secret-token"},
    ],
)
def test_complete_rejects_sensitive_result_fields_without_persisting_them(
    tmp_path,
    payload,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    with pytest.raises(OperationConflict):
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", payload),
        )

    row = db.fetchone(
        "select status, result_kind, result_json from team_model_operations where id = ?",
        (invoking.id,),
    )
    assert row is not None
    assert (row["status"], row["result_kind"], row["result_json"]) == (
        "invoking",
        None,
        None,
    )


def test_complete_rejects_unregistered_result_kind_without_persisting_it(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    with pytest.raises(OperationConflict):
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult(
                "raw_response",
                {"content": "full model response"},
            ),
        )

    assert service.get(invoking.id) == invoking


def test_complete_rejects_result_kind_not_allowed_for_operation_stage(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(
        operation_spec(run, cycle, agent, stage="worker_execution")
    )
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    with pytest.raises(OperationConflict):
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", {"tasks": []}),
        )

    assert service.get(invoking.id) == invoking


def test_domain_registry_can_enable_nonplanning_stage_without_service_changes(
    tmp_path,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(
        db,
        result_validators={
            "worker_execution": {
                "task_outcome": lambda payload: set(payload) == {"status"}
                and payload["status"] == "completed"
            }
        },
    )
    reserved = service.reserve(
        operation_spec(run, cycle, agent, stage="worker_execution")
    )
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_outcome", {"status": "completed"}),
    )

    assert completed.status == "completed"
    assert completed.result_kind == "task_outcome"
