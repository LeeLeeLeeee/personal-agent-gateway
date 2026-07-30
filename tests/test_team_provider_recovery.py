from personal_agent_gateway.teams import ProviderRecoveryClaim
from team_cycle_helpers import dt, make_cycle_services, make_running_task_in_cycle


def test_claim_provider_recovery_resumes_same_cycle_once(tmp_path):
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
