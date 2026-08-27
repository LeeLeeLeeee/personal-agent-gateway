import hashlib
import json
from dataclasses import replace

import pytest

from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    failure_shape,
    OperationSpec,
    StaleOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
    _valid_acceptance,
    _valid_contest_verdict,
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


def test_mark_failed_persists_response_session_in_the_same_cas_transition(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    failed = service.mark_failed(
        invoking.id,
        invoking.version,
        "invalid_structured_output",
        upstream_session_id="response-session",
    )

    assert failed.status == "failed"
    assert failed.upstream_session_id == "response-session"


def test_record_invoking_reason_preserves_open_operation_with_cas(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    recorded = service.record_invoking_reason(
        invoking.id,
        invoking.version,
        "provider_not_ready",
    )

    assert recorded.status == "invoking"
    assert recorded.version == invoking.version + 1
    assert recorded.attempts == 1
    assert recorded.reason_code == "provider_not_ready"
    with pytest.raises(StaleOperation):
        service.record_invoking_reason(
            invoking.id,
            invoking.version,
            "provider_not_ready",
        )


def test_valid_acceptance_accepts_both_verification_shapes() -> None:
    assert _valid_acceptance(
        {"required_outputs": ["a.md"], "required_verifications": ["reviewed"]}
    )
    assert _valid_acceptance(
        {
            "required_outputs": ["a.md"],
            "required_verifications": [
                {
                    "name": "marker",
                    "check": {"type": "file_contains", "path": "a.md", "value": "x"},
                }
            ],
        }
    )
    assert not _valid_acceptance(
        {
            "required_outputs": ["a.md"],
            "required_verifications": [
                {"name": "marker", "check": {"type": "shell", "path": "a.md"}}
            ],
        }
    )
    assert not _valid_acceptance({"required_outputs": [], "required_verifications": []})


def test_failure_shape_records_structure_not_content() -> None:
    """The ledger excludes raw model responses, so a diagnostic has to answer
    "how was it broken" without keeping what was said."""
    text = '```json\n{"status": "completed", "surprise": "secret value"}\n```'
    shape = failure_shape(text, frozenset({"status", "summary", "deliverables"}))

    assert shape["length"] == len(text)
    assert shape["fenced"] is True
    assert shape["parsed_json"] is True
    assert sorted(shape["missing_expected_keys"]) == ["deliverables", "summary"]
    # Unexpected key NAMES are model output; only their count is kept.
    assert shape["unexpected_key_count"] == 1
    serialized = json.dumps(shape)
    assert "surprise" not in serialized
    assert "secret value" not in serialized


def test_failure_shape_handles_unparseable_text() -> None:
    shape = failure_shape("I think the answer is probably fine!", frozenset({"status"}))

    assert shape["parsed_json"] is False
    assert shape["fenced"] is False
    assert shape["missing_expected_keys"] == ["status"]
    assert shape["unexpected_key_count"] == 0


def test_mark_failed_records_digest_and_shape_but_not_text(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    text = '{"status": "completed", "leaked": "do not store me"}'

    failed = service.mark_failed(
        invoking.id,
        invoking.version,
        "invalid_structured_output",
        response_text=text,
        expected_keys=frozenset({"status", "summary"}),
    )

    assert failed.failure_digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert failed.failure_shape["missing_expected_keys"] == ["summary"]
    assert failed.failure_shape["unexpected_key_count"] == 1
    with db.connection() as connection:
        row = connection.execute(
            "select * from team_model_operations where id = ?", (failed.id,)
        ).fetchone()
    stored = " ".join(str(value) for value in tuple(row))
    assert "do not store me" not in stored
    assert "leaked" not in stored


def test_mark_failed_without_response_text_stores_nothing(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    reserved = service.reserve(operation_spec(run, cycle, agent))
    invoking = service.begin_attempt(reserved.id, "consumer-1")

    failed = service.mark_failed(
        invoking.id, invoking.version, "provider_unavailable"
    )

    assert failed.failure_digest is None
    assert failed.failure_shape is None


def test_latest_failure_shapes_reports_the_failure_still_blocking_each_task(tmp_path):
    """The detail payload needs one shape per task, not a history.

    A task that failed to parse, recovered, then failed again is described by
    the failure that is still in its way. Keying on task_id in arrival order
    and letting later rows win is what produces that.
    """
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    task = teams.create_task(
        run.id, "Verify guide", "Check it.", cycle_id=cycle.id
    )
    other = teams.create_task(
        run.id, "Draft guide", "Write it.", cycle_id=cycle.id
    )
    service = TeamModelOperationService(db)

    def fail(key, ordinal, task_id, text, keys):
        spec = operation_spec(
            run,
            cycle,
            agent,
            key=key,
            task_id=task_id,
            stage="acceptance_lead",
            stage_ordinal=ordinal,
        )
        reserved = service.reserve(spec)
        started = service.begin_attempt(reserved.id, "consumer")
        service.mark_failed(
            started.id,
            started.version,
            "invalid_structured_output",
            response_text=text,
            expected_keys=frozenset(keys),
        )

    fail("acceptance_lead:1", 1, task.id, "prose only", {"resolution"})
    fail("acceptance_lead:2", 2, task.id, '```json\n{"a": 1}\n```', {"resolution"})
    fail("acceptance_lead:3", 3, other.id, "nope", {"status"})

    shapes = service.latest_failure_shapes(run.id)

    assert set(shapes) == {task.id, other.id}
    assert shapes[task.id]["fenced"] is True
    assert shapes[task.id]["missing_expected_keys"] == ["resolution"]
    assert shapes[other.id]["fenced"] is False


def test_a_verdict_without_a_reason_is_invalid():
    """A verdict with no reason is worthless as a record, which is half of why
    this feature exists -- so it is a parse failure, not a defaulted field."""
    assert not _valid_contest_verdict({"kind": "reject", "reason": ""})
    assert not _valid_contest_verdict({"kind": "reject"})


def test_reject_carries_no_tasks_and_amend_carries_at_least_one():
    task = {
        "title": "Fix §1",
        "description": "Correct the reversed decision.",
        "owner_agent_id": None,
        "required": True,
        "acceptance": {"required_outputs": ["docs/srs.md"], "required_verifications": []},
    }
    assert _valid_contest_verdict({"kind": "reject", "reason": "task 7 covers it"})
    assert not _valid_contest_verdict(
        {"kind": "reject", "reason": "no", "tasks": [task]}
    )
    # The prompt shows every key, so a model will fill them all in. An empty
    # value for a field this kind does not use has to pass, or the repair is
    # spent on nearly every verdict.
    assert _valid_contest_verdict(
        {"kind": "reject", "reason": "no", "tasks": [], "question": None,
         "supersedes": []}
    )
    assert _valid_contest_verdict(
        {"kind": "amend", "reason": "ok", "tasks": [task], "question": ""}
    )
    assert not _valid_contest_verdict(
        {"kind": "amend", "reason": "ok", "tasks": [task],
         "question": "why are you asking?"}
    )
    assert _valid_contest_verdict({"kind": "amend", "reason": "agreed", "tasks": [task]})
    assert not _valid_contest_verdict({"kind": "amend", "reason": "agreed", "tasks": []})


def test_ask_back_needs_a_question():
    assert _valid_contest_verdict(
        {"kind": "ask_back", "reason": "ambiguous", "question": "which one?"}
    )
    assert not _valid_contest_verdict({"kind": "ask_back", "reason": "ambiguous"})


def test_overturning_a_decision_requires_the_work_to_correct_it():
    """If the leader admits an agreed decision is being reversed, correcting the
    document that still states the old decision comes out of the same verdict.
    Run 699c1915 reversed one with nothing but a quiet document edit."""
    task = {
        "title": "Fix §1",
        "description": "Correct the reversed decision.",
        "owner_agent_id": None,
        "required": True,
        "acceptance": {"required_outputs": ["docs/srs.md"], "required_verifications": []},
    }
    supersedes = [{"document_path": "docs/srs.md", "decision": "use a vetted library"}]
    assert not _valid_contest_verdict(
        {"kind": "amend", "reason": "r", "tasks": [], "supersedes": supersedes}
    )
    assert not _valid_contest_verdict(
        {"kind": "reject", "reason": "r", "supersedes": supersedes}
    )
    assert _valid_contest_verdict(
        {"kind": "amend", "reason": "r", "tasks": [task], "supersedes": supersedes}
    )


def test_an_unknown_kind_is_invalid():
    assert not _valid_contest_verdict({"kind": "whatever", "reason": "r"})



def test_a_completed_operation_records_what_the_call_cost(tmp_path):
    """토큰 사용량을 호출 단위로 저장한다.

    가장 잘게 저장하면 런 합계·사이클별·에이전트별을 전부 이것으로 뽑을 수
    있다. 위에서 합쳐 저장하면 "어느 워커가 많이 쓰나" 를 나중에 물을 수 없다.
    """
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    operation = service.reserve(operation_spec(run, cycle, agent))
    service.begin_attempt(operation.id, "run-1")
    invoking = service.get(operation.id)

    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_plan", {"tasks": []}),
        usage={"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 7000},
    )

    assert completed.usage == {
        "input_tokens": 120,
        "output_tokens": 45,
        "cache_read_input_tokens": 7000,
    }


def test_an_operation_without_reported_usage_stays_none(tmp_path):
    """보고하지 않은 호출을 0 으로 적으면 합계가 조용히 낮아진다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)
    operation = service.reserve(operation_spec(run, cycle, agent))
    service.begin_attempt(operation.id, "run-1")
    invoking = service.get(operation.id)

    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("task_plan", {"tasks": []}),
    )

    assert completed.usage is None


def test_run_usage_totals_sum_every_call_and_skip_unreported(tmp_path):
    """런 전체가 얼마나 썼는지는 호출들을 합쳐서 낸다.

    보고하지 않은 호출은 건너뛴다. 0 으로 세면 총합은 같지만 "몇 건이
    보고되지 않았나" 를 잃는다 -- 총합이 실제보다 낮다는 사실 자체가
    안 보이게 된다.
    """
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(db)

    for index, usage in enumerate(
        [
            {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 500},
            {"input_tokens": 50, "output_tokens": 5},
            None,
        ]
    ):
        # 사이클 하나에 열린 호출은 하나뿐이라 회차마다 새 사이클을 쓴다.
        cycle = teams.create_cycle(run.id, "manual", f"m-{index}")
        operation = service.reserve(operation_spec(run, cycle, agent, key=f"w:{index}"))
        service.begin_attempt(operation.id, "run-1")
        invoking = service.get(operation.id)
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", {"tasks": []}),
            usage=usage,
        )

    totals = service.usage_totals(run.id)

    assert totals["input_tokens"] == 150
    assert totals["output_tokens"] == 15
    assert totals["cache_read_input_tokens"] == 500
    assert totals["reported_calls"] == 2
    assert totals["unreported_calls"] == 1


def test_run_usage_totals_are_zero_before_any_call(tmp_path):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    service = TeamModelOperationService(db)

    totals = service.usage_totals(run.id)

    assert totals["input_tokens"] == 0
    assert totals["reported_calls"] == 0


def _usage_fixture(tmp_path):
    """리드와 작업자가 각각 호출을 내고, 한 명은 한 번도 안 불린 런."""
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    lead = teams.get_agent(run.leader_agent_id)
    worker = next(item for item in teams.list_agents(run.id) if item.role != "leader")
    service = TeamModelOperationService(db)

    calls = [
        (lead, {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 500}),
        (lead, {"input_tokens": 40, "output_tokens": 20}),
        (worker, {"input_tokens": 7, "output_tokens": 7}),
        (worker, None),
    ]
    for index, (agent, usage) in enumerate(calls):
        # 사이클 하나에 열린 호출은 하나뿐이라 회차마다 새 사이클을 쓴다.
        cycle = teams.create_cycle(run.id, "manual", f"m-{index}")
        operation = service.reserve(operation_spec(run, cycle, agent, key=f"u:{index}"))
        service.begin_attempt(operation.id, "run-1")
        invoking = service.get(operation.id)
        service.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_plan", {"tasks": []}),
            usage=usage,
        )
    return service, run, {"lead": lead.id, "worker": worker.id}


def test_usage_splits_by_agent_and_the_split_adds_up(tmp_path):
    """총합만 보면 어느 자리가 비싼지 알 수 없다. 리드는 사이클마다 계획과
    합성으로 두 번씩 불리고 작업자는 자기 일감이 있을 때만 불려서, 같은 런
    안에서도 자릿수가 다르다.

    합이 총합과 어긋나면 안 된다. 어긋난 두 숫자를 화면에서 보고 어느 쪽이
    맞는지 가릴 방법이 없다.
    """
    service, run, agents = _usage_fixture(tmp_path)

    by_agent = service.usage_by_agent(run.id)
    totals = service.usage_totals(run.id)

    assert set(by_agent) == {agents["lead"], agents["worker"]}
    assert by_agent[agents["lead"]]["output_tokens"] == 30
    assert by_agent[agents["worker"]]["output_tokens"] == 7
    assert by_agent[agents["worker"]]["unreported_calls"] == 1
    summed = sum(entry["input_tokens"] for entry in by_agent.values())
    assert summed == totals["input_tokens"]


def test_an_agent_that_was_never_called_is_absent_not_zero(tmp_path):
    """호출을 낸 적 없는 팀원은 0 이 아니라 아예 없어야 한다.

    0 을 채워 돌려주면 "안 불렸다" 와 "불렸는데 보고를 안 했다" 가 화면에서
    같아진다. 둘은 다른 상태이고, 뒤쪽은 총합이 실제보다 낮다는 신호다.
    """
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    service = TeamModelOperationService(db)

    assert teams.list_agents(run.id)
    assert service.usage_by_agent(run.id) == {}


def test_the_shape_counts_a_brace_that_was_never_closed():
    """실측: 리드가 같은 판정을 네 번 연속 내보냈고 매번 정확히 닫는 괄호가
    하나 모자랐다. 기록에는 parsed_json: false 만 남아 어떻게 깨졌는지도,
    네 번이 같은 방식으로 깨졌는지도 알 수 없었다."""
    body = '{"resolution":{"kind":"revise_acceptance","acceptance":{"reason":"x"}}'

    shape = failure_shape(body, frozenset({"resolution"}))

    assert shape["parsed_json"] is False
    assert shape["unclosed_braces"] == 1


def test_a_brace_inside_a_string_is_not_counted():
    """모델이 쓰는 이유 문장에 중괄호가 들어갈 수 있다. 그것을 세면 멀쩡한
    응답이 깨진 것으로 보고된다."""
    body = '{"reason":"the shape is {like this}"}'

    assert failure_shape(body, frozenset())["unclosed_braces"] == 0


def test_an_escaped_quote_does_not_flip_the_string_state():
    body = '{"reason":"he said \\"no\\" and left"}'

    assert failure_shape(body, frozenset())["unclosed_braces"] == 0


def test_too_many_closing_braces_are_counted_as_negative():
    assert failure_shape('{"a":1}}', frozenset())["unclosed_braces"] == -1
