from dataclasses import asdict
from types import SimpleNamespace

import pytest

from personal_agent_gateway.team_acceptance import AcceptanceResult
from personal_agent_gateway.team_model_effects import (
    TeamModelEffectService,
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_operations import (
    OperationResultValidationError,
    OperationSpec,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from personal_agent_gateway.team_outcomes import (
    Deliverable,
    TaskOutcome,
    VerificationEvidence,
)
from personal_agent_gateway.teams import TaskAcceptance
from team_cycle_helpers import make_cycle_services, make_queued_cycle


REQUEST_DIGEST = "a" * 64


def valid_task_spec(title, owner_agent_id):
    return {
        "title": title,
        "description": f"{title} description",
        "owner_agent_id": owner_agent_id,
        "required": True,
        "acceptance": {
            "required_outputs": [f"{title.lower()}.md"],
            "required_verifications": [],
        },
    }


def make_completed_operation(tmp_path, *, stage, result):
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    actor = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != actor.id
    )
    operations = TeamModelOperationService(db)
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:{stage}:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=None,
            agent_id=actor.id,
            provider=actor.backend,
            stage=stage,
            stage_ordinal=0,
            request_digest=REQUEST_DIGEST,
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    operation = operations.complete(
        invoking.id,
        invoking.version,
        result,
        upstream_session_id="lead-session",
    )
    return SimpleNamespace(
        db=db,
        teams=teams,
        run=run,
        cycle=cycle,
        actor=actor,
        worker=worker,
        operations=operations,
        operation=operation,
        effects=TeamModelEffectService(db, teams, operations),
    )


def completed_outcome(path):
    return TaskOutcome(
        status="completed",
        summary=f"Created {path}.",
        reason_code=None,
        deliverables=(Deliverable(path=path, kind="document"),),
        verifications=(
            VerificationEvidence(
                name="review",
                status="passed",
                evidence="Reviewed.",
            ),
        ),
    )


def user_decision():
    return {
        "kind": "ask_user",
        "topic": "publication",
        "question": "Should this draft be published?",
        "why_needed": "The publication scope is ambiguous.",
        "options": [
            {
                "id": "publish",
                "label": "Publish",
                "impact": "Makes the draft public.",
            }
        ],
        "recommended_option_id": "publish",
        "blocking_scope": "task",
    }


def make_completed_worker_operation(tmp_path, *, outcome=None, decision=None):
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )
    task = teams.create_task(
        run.id,
        "Draft",
        "Create a draft.",
        owner_agent_id=worker.id,
        cycle_id=cycle.id,
        acceptance=TaskAcceptance(
            required_outputs=("draft.md",),
            required_verifications=("review",),
        ),
    )
    task, worker = teams.start_task(task.id, worker.id)
    operations = TeamModelOperationService(
        db,
        result_validators=team_model_effect_result_validators(),
    )
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:{task.id}:worker_execution:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=task.id,
            agent_id=worker.id,
            provider=worker.backend,
            stage="worker_execution",
            stage_ordinal=0,
            request_digest=REQUEST_DIGEST,
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    if decision is not None:
        result = ValidatedOperationResult("user_decision", decision)
    else:
        result = ValidatedOperationResult("task_outcome", asdict(outcome))
    operation = operations.complete(
        invoking.id,
        invoking.version,
        result,
        upstream_session_id="worker-session",
    )
    return SimpleNamespace(
        db=db,
        teams=teams,
        run=run,
        cycle=cycle,
        task=task,
        worker=worker,
        operations=operations,
        operation=operation,
        effects=TeamModelEffectService(db, teams, operations),
    )


def make_completed_synthesis_operation(tmp_path):
    db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    teams.set_cycle_status(cycle.id, "running")
    teams.set_run_status(run.id, "running")
    leader = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != leader.id
    )
    task = teams.create_task(
        run.id,
        "Draft",
        "Create a draft.",
        owner_agent_id=worker.id,
        cycle_id=cycle.id,
    )
    teams.start_task(task.id, worker.id)
    teams.finish_task(task.id, worker.id, "completed", result="Drafted.")
    teams.set_run_status(run.id, "summarizing")
    teams.set_agent_status(leader.id, "running")
    operations = TeamModelOperationService(
        db,
        result_validators=team_model_effect_result_validators(),
    )
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:cycle_synthesis:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=None,
            agent_id=leader.id,
            provider=leader.backend,
            stage="cycle_synthesis",
            stage_ordinal=0,
            request_digest=REQUEST_DIGEST,
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    operation = operations.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult(
            "synthesis",
            {"summary": "The draft is complete."},
        ),
        upstream_session_id="lead-session",
    )
    return SimpleNamespace(
        db=db,
        teams=teams,
        run=run,
        cycle=cycle,
        leader=leader,
        operations=operations,
        operation=operation,
        effects=TeamModelEffectService(db, teams, operations),
    )


def test_apply_plan_and_operation_are_atomic_and_idempotent(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult(
            "task_plan",
            {"tasks": [valid_task_spec("Research", None)]},
        ),
    )

    first = services.effects.apply_plan(services.operation.id)
    second = services.effects.apply_plan(services.operation.id)

    assert [task.id for task in second] == [task.id for task in first]
    assert len(services.teams.list_tasks(services.run.id, services.cycle.id)) == 1
    plan_notes = [
        message
        for message in services.teams.list_messages(services.run.id)
        if message.kind == "plan_note"
    ]
    assert len(plan_notes) == 1
    assert services.teams.get_agent(services.actor.id).upstream_session_id == (
        "lead-session"
    )
    applied = services.operations.get(services.operation.id)
    assert applied.status == "applied"
    assert applied.effect_type == "task_plan"


def test_apply_plan_rolls_back_all_effects_for_unknown_owner(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult(
            "task_plan",
            {"tasks": [valid_task_spec("Research", "missing-agent")]},
        ),
    )

    with pytest.raises(ValueError, match="owner"):
        services.effects.apply_plan(services.operation.id)

    assert services.teams.list_tasks(services.run.id, services.cycle.id) == []
    assert services.operations.get(services.operation.id).status == "completed"
    assert all(
        message.kind != "plan_note"
        for message in services.teams.list_messages(services.run.id)
    )
    assert services.teams.get_agent(services.actor.id).upstream_session_id is None


def test_worker_result_apply_is_atomic_and_does_not_finish_recoverable_rejection(
    tmp_path,
):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )
    rejected = AcceptanceResult(
        accepted=False,
        status="failed",
        reason_code="undeclared_deliverable",
        evidence={},
    )

    result = services.effects.apply_worker_outcome(
        services.operation.id,
        rejected,
        workspace_changes={
            "created": ["draft.md"],
            "modified": [],
            "deleted": [],
        },
    )

    task = services.teams.get_task(services.task.id)
    assert result.next_stage == "acceptance_lead"
    assert task.status == "in_progress"
    assert task.outcome is not None
    assert task.acceptance_result is not None
    assert len(
        [
            message
            for message in services.teams.list_messages(services.run.id)
            if message.kind == "agent_output"
        ]
    ) == 1
    worker = services.teams.get_agent(services.worker.id)
    assert worker.status == "running"
    assert worker.upstream_session_id == "worker-session"
    assert services.operations.get(services.operation.id).status == "applied"


def test_accepted_worker_outcome_finishes_task_and_worker_atomically(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )

    result = services.effects.apply_worker_outcome(
        services.operation.id,
        AcceptanceResult(
            accepted=True,
            status="completed",
            reason_code=None,
            evidence={"deliverables": ["draft.md"]},
        ),
        workspace_changes={
            "created": ["draft.md"],
            "modified": [],
            "deleted": [],
        },
    )

    assert result.next_stage is None
    task = services.teams.get_task(services.task.id)
    worker = services.teams.get_agent(services.worker.id)
    assert task.status == "completed"
    assert task.result == "Created draft.md."
    assert task.finished_at is not None
    assert worker.status == "completed"
    assert worker.current_task_id is None
    assert worker.finished_at is not None
    assert services.operations.get(services.operation.id).status == "applied"


def test_nonrecoverable_worker_rejection_finishes_failed_atomically(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )

    result = services.effects.apply_worker_outcome(
        services.operation.id,
        AcceptanceResult(
            accepted=False,
            status="failed",
            reason_code="input_snapshot_modified",
            evidence={},
        ),
        workspace_changes={"created": [], "modified": [], "deleted": []},
    )

    assert result.next_stage is None
    task = services.teams.get_task(services.task.id)
    worker = services.teams.get_agent(services.worker.id)
    assert task.status == "failed"
    assert task.error_message == "input_snapshot_modified"
    assert worker.status == "failed"
    assert worker.current_task_id is None
    assert services.operations.get(services.operation.id).status == "applied"


def test_worker_user_decision_is_atomic_and_duplicate_apply_is_idempotent(
    tmp_path,
):
    services = make_completed_worker_operation(
        tmp_path,
        decision=user_decision(),
    )

    first = services.effects.apply_worker_outcome(
        services.operation.id,
        None,
        workspace_changes={"created": [], "modified": [], "deleted": []},
    )
    second = services.effects.apply_worker_outcome(
        services.operation.id,
        None,
        workspace_changes={"created": [], "modified": [], "deleted": []},
    )

    assert second.decision_request.id == first.decision_request.id
    assert second.next_stage == "user_decision"
    assert len(services.teams.list_decision_requests(services.run.id)) == 1
    task = services.teams.get_task(services.task.id)
    worker = services.teams.get_agent(services.worker.id)
    assert task.status == "blocked"
    assert worker.status == "waiting"
    assert worker.current_task_id is None
    assert services.operations.get(services.operation.id).status == "applied"


def test_duplicate_worker_outcome_does_not_append_another_message(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )
    acceptance = AcceptanceResult(
        accepted=False,
        status="failed",
        reason_code="undeclared_deliverable",
        evidence={},
    )
    changes = {"created": ["draft.md"], "modified": [], "deleted": []}

    first = services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )
    second = services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )

    assert second.task.id == first.task.id
    assert second.message.id == first.message.id
    assert len(
        [
            message
            for message in services.teams.list_messages(services.run.id)
            if message.kind == "agent_output"
        ]
    ) == 1


def test_worker_result_validator_rejects_unknown_fields(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        outcome=completed_outcome("draft.md"),
    )
    operations = TeamModelOperationService(
        services.db,
        result_validators=team_model_effect_result_validators(),
    )
    services.db.execute(
        "delete from team_model_operations where id = ?",
        (services.operation.id,),
    )
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{services.cycle.id}:{services.task.id}:worker_execution:1",
            team_run_id=services.run.id,
            cycle_id=services.cycle.id,
            task_id=services.task.id,
            agent_id=services.worker.id,
            provider=services.worker.backend,
            stage="worker_execution",
            stage_ordinal=1,
            request_digest=REQUEST_DIGEST,
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-2")
    payload = asdict(completed_outcome("draft.md"))
    payload["raw_response"] = "must not persist"

    with pytest.raises(OperationResultValidationError):
        operations.complete(
            invoking.id,
            invoking.version,
            ValidatedOperationResult("task_outcome", payload),
        )


def test_synthesis_and_operation_are_atomic_and_idempotent(tmp_path):
    services = make_completed_synthesis_operation(tmp_path)

    first = services.effects.apply_synthesis(
        services.operation.id,
        "The draft is complete.",
    )
    second = services.effects.apply_synthesis(
        services.operation.id,
        "The draft is complete.",
    )

    assert second == first == "The draft is complete."
    assert services.teams.get_team_run(services.run.id).status == "completed"
    cycle = services.teams.get_cycle(services.cycle.id)
    assert cycle.status == "completed"
    assert cycle.summary == "The draft is complete."
    assert services.teams.get_agent(services.leader.id).status == "completed"
    assert services.teams.get_agent(
        services.leader.id
    ).upstream_session_id == "lead-session"
    assert len(
        [
            message
            for message in services.teams.list_messages(services.run.id)
            if message.kind == "synthesis"
        ]
    ) == 1
    operation = services.operations.get(services.operation.id)
    assert operation.status == "applied"
    assert operation.effect_type == "synthesis"
