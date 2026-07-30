import pytest

from personal_agent_gateway.teams import ProviderRecoveryClaim
from team_cycle_helpers import (
    dt,
    make_cycle_services,
    make_queued_cycle,
    make_running_task_in_cycle,
)


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
