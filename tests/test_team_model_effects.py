from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from personal_agent_gateway.team_acceptance import AcceptanceResult
from personal_agent_gateway.team_model_effects import (
    TeamModelEffectService,
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
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


def test_plan_replay_rejects_unrelated_same_cycle_rows(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult(
            "task_plan",
            {"tasks": [valid_task_spec("Research", None)]},
        ),
    )
    services.effects.apply_plan(services.operation.id)
    unrelated = services.teams.create_task(
        services.run.id,
        "Unrelated",
        "Unrelated description",
        cycle_id=services.cycle.id,
        acceptance=TaskAcceptance(("unrelated.md",), ()),
    )
    unrelated_note = services.teams.append_message(
        services.run.id,
        services.actor.id,
        None,
        "plan_note",
        "Planning completed with 1 tasks.",
        {},
        cycle_id=services.cycle.id,
    )
    services.db.execute(
        """
        update team_model_operations set effect_ref_json = ? where id = ?
        """,
        (
            json.dumps(
                {
                    "task_ids": [unrelated.id],
                    "message_id": unrelated_note.id,
                },
                sort_keys=True,
            ),
            services.operation.id,
        ),
    )

    with pytest.raises(OperationConflict):
        services.effects.apply_plan(services.operation.id)


def test_plan_replay_rejects_actor_session_that_no_longer_matches(tmp_path):
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult(
            "task_plan",
            {"tasks": [valid_task_spec("Research", None)]},
        ),
    )
    services.effects.apply_plan(services.operation.id)
    services.db.execute(
        "update team_agents set upstream_session_id = ? where id = ?",
        ("other-session", services.actor.id),
    )

    with pytest.raises(OperationConflict):
        services.effects.apply_plan(services.operation.id)


def test_worker_replay_rejects_unrelated_agent_output(tmp_path):
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
    services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )
    unrelated = services.teams.append_message(
        services.run.id,
        services.worker.id,
        None,
        "agent_output",
        "Unrelated output.",
        {"task_id": services.task.id},
        cycle_id=services.cycle.id,
    )
    effect_ref = services.operations.get(services.operation.id).effect_ref_json
    effect_ref["message_id"] = unrelated.id
    services.db.execute(
        "update team_model_operations set effect_ref_json = ? where id = ?",
        (
            json.dumps(effect_ref, sort_keys=True),
            services.operation.id,
        ),
    )

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            acceptance,
            workspace_changes=changes,
        )


@pytest.mark.parametrize("tampered_state", ["task", "agent", "session"])
def test_worker_replay_rejects_tampered_expected_state(
    tmp_path,
    tampered_state,
):
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
    services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )
    if tampered_state == "task":
        services.db.execute(
            "update team_tasks set status = 'completed' where id = ?",
            (services.task.id,),
        )
    elif tampered_state == "agent":
        services.db.execute(
            "update team_agents set status = 'waiting' where id = ?",
            (services.worker.id,),
        )
    else:
        services.db.execute(
            "update team_agents set upstream_session_id = ? where id = ?",
            ("other-session", services.worker.id),
        )

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            acceptance,
            workspace_changes=changes,
        )


def test_worker_decision_replay_rejects_shared_request_without_its_item(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        decision=user_decision(),
    )
    request = services.teams.defer_run_for_user_decision(
        services.run.id,
        {
            **user_decision(),
            "topic": "unrelated",
            "question": "An unrelated question?",
        },
        stage="planning",
        cycle_id=services.cycle.id,
    )
    changes = {"created": [], "modified": [], "deleted": []}
    services.effects.apply_worker_outcome(
        services.operation.id,
        None,
        workspace_changes=changes,
    )
    services.db.execute(
        """
        update team_decision_requests set items_json = ? where id = ?
        """,
        (
            json.dumps([request.items[0]], sort_keys=True),
            request.id,
        ),
    )

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            None,
            workspace_changes=changes,
        )


def test_worker_decision_replay_rejects_request_outside_applied_state(tmp_path):
    services = make_completed_worker_operation(
        tmp_path,
        decision=user_decision(),
    )
    changes = {"created": [], "modified": [], "deleted": []}
    result = services.effects.apply_worker_outcome(
        services.operation.id,
        None,
        workspace_changes=changes,
    )
    services.db.execute(
        """
        update team_decision_requests
        set status = 'resolved', answers_json = '{"Q-001":"publish"}'
        where id = ?
        """,
        (result.decision_request.id,),
    )

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            None,
            workspace_changes=changes,
        )


@pytest.mark.parametrize("tampered_state", ["run", "cycle", "agent", "session"])
def test_synthesis_replay_rejects_tampered_expected_state(
    tmp_path,
    tampered_state,
):
    services = make_completed_synthesis_operation(tmp_path)
    summary = "The draft is complete."
    services.effects.apply_synthesis(services.operation.id, summary)
    if tampered_state == "run":
        services.db.execute(
            "update team_runs set summary = ? where id = ?",
            ("Other summary.", services.run.id),
        )
    elif tampered_state == "cycle":
        services.db.execute(
            "update team_run_cycles set status = 'running' where id = ?",
            (services.cycle.id,),
        )
    elif tampered_state == "agent":
        services.db.execute(
            "update team_agents set status = 'running' where id = ?",
            (services.leader.id,),
        )
    else:
        services.db.execute(
            "update team_agents set upstream_session_id = ? where id = ?",
            ("other-session", services.leader.id),
        )

    with pytest.raises(OperationConflict):
        services.effects.apply_synthesis(services.operation.id, summary)


@pytest.mark.parametrize("changed_input", ["acceptance", "workspace_changes"])
def test_worker_outcome_replay_rejects_changed_apply_inputs(
    tmp_path,
    changed_input,
):
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
    services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )
    duplicate_acceptance = acceptance
    duplicate_changes = changes
    if changed_input == "acceptance":
        duplicate_acceptance = AcceptanceResult(
            accepted=False,
            status="failed",
            reason_code="undeclared_deliverable",
            evidence={"remaining_undeclared_paths": ["draft.md"]},
        )
    else:
        duplicate_changes = {
            "created": [],
            "modified": ["draft.md"],
            "deleted": [],
        }

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            duplicate_acceptance,
            workspace_changes=duplicate_changes,
        )


@pytest.mark.parametrize("changed_input", ["acceptance", "workspace_changes"])
def test_worker_decision_replay_rejects_changed_apply_inputs(
    tmp_path,
    changed_input,
):
    services = make_completed_worker_operation(
        tmp_path,
        decision=user_decision(),
    )
    changes = {"created": [], "modified": [], "deleted": []}
    services.effects.apply_worker_outcome(
        services.operation.id,
        None,
        workspace_changes=changes,
    )
    duplicate_acceptance = None
    duplicate_changes = changes
    if changed_input == "acceptance":
        duplicate_acceptance = AcceptanceResult(
            accepted=False,
            status="failed",
            reason_code="undeclared_deliverable",
            evidence={},
        )
    else:
        duplicate_changes = {
            "created": ["unexpected.md"],
            "modified": [],
            "deleted": [],
        }

    with pytest.raises((OperationConflict, ValueError)):
        services.effects.apply_worker_outcome(
            services.operation.id,
            duplicate_acceptance,
            workspace_changes=duplicate_changes,
        )


@pytest.mark.parametrize(
    "acceptance",
    [
        {
            "required_outputs": ["draft.md", "draft.md"],
            "required_verifications": [],
        },
        {
            "required_outputs": ["draft.md"],
            "required_verifications": ["review", "review"],
        },
    ],
)
def test_apply_plan_rolls_back_duplicate_acceptance_requirements(
    tmp_path,
    acceptance,
):
    spec = valid_task_spec("Research", None)
    spec["acceptance"] = acceptance
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult("task_plan", {"tasks": [spec]}),
    )

    with pytest.raises(ValueError, match="duplicate"):
        services.effects.apply_plan(services.operation.id)

    assert services.teams.list_tasks(services.run.id, services.cycle.id) == []
    assert services.operations.get(services.operation.id).status == "completed"
    assert all(
        message.kind != "plan_note"
        for message in services.teams.list_messages(services.run.id)
    )
    assert services.teams.get_agent(services.actor.id).upstream_session_id is None


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.md",
        "C:\\outside.md",
        "/absolute.md",
    ],
)
def test_apply_plan_rolls_back_unsafe_acceptance_output(
    tmp_path,
    unsafe_path,
):
    spec = valid_task_spec("Research", None)
    spec["acceptance"] = {
        "required_outputs": [unsafe_path],
        "required_verifications": [],
    }
    services = make_completed_operation(
        tmp_path,
        stage="cycle_planning",
        result=ValidatedOperationResult("task_plan", {"tasks": [spec]}),
    )

    with pytest.raises(ValueError, match="relative and bounded"):
        services.effects.apply_plan(services.operation.id)

    assert services.teams.list_tasks(services.run.id, services.cycle.id) == []
    assert services.operations.get(services.operation.id).status == "completed"
    assert all(
        message.kind != "plan_note"
        for message in services.teams.list_messages(services.run.id)
    )
    assert services.teams.get_agent(services.actor.id).upstream_session_id is None


@pytest.mark.parametrize("tampered_row", ["acceptance", "workspace_metadata"])
def test_worker_replay_rejects_rows_that_no_longer_match_input_digest(
    tmp_path,
    tampered_row,
):
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
    result = services.effects.apply_worker_outcome(
        services.operation.id,
        acceptance,
        workspace_changes=changes,
    )
    if tampered_row == "acceptance":
        services.db.execute(
            """
            update team_tasks set acceptance_result_json = ? where id = ?
            """,
            (
                json.dumps(
                    {
                        **asdict(acceptance),
                        "evidence": {"tampered": True},
                    },
                    sort_keys=True,
                ),
                services.task.id,
            ),
        )
    else:
        metadata = result.message.metadata
        metadata["created"] = []
        metadata["modified"] = ["draft.md"]
        services.db.execute(
            "update team_messages set metadata_json = ? where id = ?",
            (json.dumps(metadata, sort_keys=True), result.message.id),
        )

    with pytest.raises(OperationConflict):
        services.effects.apply_worker_outcome(
            services.operation.id,
            acceptance,
            workspace_changes=changes,
        )
