import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.space_policies import SpacePolicyService
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_directory import TeamService
from personal_agent_gateway.team_verification_checks import VerificationCheck
from personal_agent_gateway.teams import (
    RequiredVerification,
    TaskAcceptance,
    TeamRunService,
    _team_run_display_status,
    parse_required_verifications,
)
from team_cycle_helpers import (
    dt,
    make_cycle_services,
    make_queued_cycle,
    make_running_task_in_cycle,
)


def test_cycle_metadata_owned_writers_preserve_each_other_and_recovery(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    leader = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != leader.id
    )
    recovery = {
        "provider": "codex",
        "reason_code": "provider_not_ready",
        "attempts": 3,
    }
    teams.set_cycle_execution_metadata(
        cycle.id,
        {
            "provider_recovery": recovery,
            "unrelated": {"keep": True},
        },
    )

    teams.set_cycle_provider_capabilities(
        cycle.id,
        {"codex": {"ready": False}},
    )
    teams.set_cycle_agent_execution_metadata(
        cycle.id,
        worker.id,
        {"sandbox_mode": "workspace-write"},
    )
    teams.set_cycle_agent_execution_metadata(
        cycle.id,
        leader.id,
        {"sandbox_mode": "read-only"},
    )
    teams.set_cycle_effective_instruction(
        cycle.id,
        "prepared work",
    )
    updated = teams.set_cycle_provider_capabilities(
        cycle.id,
        {"codex": {"ready": True}},
    )

    assert updated.execution_metadata == {
        "provider_recovery": recovery,
        "provider_capabilities": {"codex": {"ready": True}},
        "agents": {
            worker.id: {"sandbox_mode": "workspace-write"},
            leader.id: {"sandbox_mode": "read-only"},
        },
        "semantic_source": {
            "effective_instruction": "prepared work",
            "output_contract_id": None,
        },
        "unrelated": {"keep": True},
    }
    assert teams.get_cycle_effective_instruction(cycle.id) == "prepared work"


def _run_with_cycle(tmp_path: Path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    return teams, cycle


def _run_with_cycle_and_agents(tmp_path: Path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    return teams, run, cycle


def test_required_verifications_accept_plain_strings_and_objects() -> None:
    parsed = parse_required_verifications(
        [
            "source-url-verification",
            {
                "name": "marker-format",
                "check": {
                    "type": "file_contains",
                    "path": "draft.md",
                    "value": "<library_draft>",
                },
            },
            {"name": "reviewed", "check": None},
        ]
    )

    assert [item.name for item in parsed] == [
        "source-url-verification",
        "marker-format",
        "reviewed",
    ]
    assert parsed[0].check is None
    assert parsed[1].check == VerificationCheck(
        "file_contains", "draft.md", value="<library_draft>"
    )
    assert parsed[2].check is None


def test_required_verifications_reject_malformed_items() -> None:
    for invalid in (
        "not-a-list",
        [""],
        [{"name": ""}],
        [{"check": {"type": "file_nonempty", "path": "a.md"}}],
        [{"name": "x", "check": {"type": "shell", "path": "a.md"}}],
        [{"name": "x", "extra": 1}],
        ["dup", "dup"],
    ):
        with pytest.raises(ValueError):
            parse_required_verifications(invalid)


def test_acceptance_json_round_trips_both_shapes(tmp_path: Path) -> None:
    teams, run, cycle = _run_with_cycle_and_agents(tmp_path)
    acceptance = TaskAcceptance(
        required_outputs=("draft.md",),
        required_verifications=(
            RequiredVerification("source-url-verification"),
            RequiredVerification(
                "marker-format",
                VerificationCheck("file_contains", "draft.md", value="<library_draft>"),
            ),
        ),
    )

    task = teams.create_task(
        run.id,
        "Write the draft",
        "Write it.",
        cycle_id=cycle.id,
        acceptance=acceptance,
    )

    assert teams.get_task(task.id).acceptance == acceptance


def test_stored_string_verifications_still_load(tmp_path: Path) -> None:
    teams, run, cycle = _run_with_cycle_and_agents(tmp_path)
    task = teams.create_task(
        run.id,
        "Write the draft",
        "Write it.",
        cycle_id=cycle.id,
        acceptance=TaskAcceptance(("draft.md",), (RequiredVerification("legacy"),)),
    )
    with teams._db.connection() as connection:
        connection.execute(
            "update team_tasks set acceptance_json = ? where id = ?",
            (
                '{"required_outputs": ["draft.md"], "required_verifications": ["legacy"]}',
                task.id,
            ),
        )

    loaded = teams.get_task(task.id)

    assert loaded.acceptance.required_verifications == (RequiredVerification("legacy"),)


def test_cycle_stores_and_returns_the_output_contract_id(tmp_path: Path) -> None:
    teams, cycle = _run_with_cycle(tmp_path)

    teams.set_cycle_effective_instruction(
        cycle.id,
        "Prepare the delegated Knowledge Request as a Library review draft.",
        output_contract_id="library_draft",
    )

    assert teams.get_cycle_output_contract_id(cycle.id) == "library_draft"
    assert teams.get_cycle_effective_instruction(cycle.id) == (
        "Prepare the delegated Knowledge Request as a Library review draft."
    )


def test_cycle_without_a_contract_returns_none(tmp_path: Path) -> None:
    teams, cycle = _run_with_cycle(tmp_path)

    teams.set_cycle_effective_instruction(cycle.id, "Do the work.")

    assert teams.get_cycle_output_contract_id(cycle.id) is None


@pytest.mark.parametrize(
    ("run_status", "request_status", "cycle_status", "series_status", "expected"),
    [
        ("canceled", "queued", None, "running", "canceled"),
        ("failed", "queued", "failed", "paused_failure", "active"),
        ("completed", None, "failed", "paused_failure", "needs_attention"),
        ("completed", None, "completed", "waiting_interval", "auto_waiting"),
        ("completed", None, "completed", "auto_completed", "ready"),
    ],
)
def test_team_run_display_status_prioritizes_current_cycle_activity(
    run_status: str,
    request_status: str | None,
    cycle_status: str | None,
    series_status: str | None,
    expected: str,
) -> None:
    request = {"status": request_status} if request_status else None
    cycle = {"status": cycle_status} if cycle_status else None
    series = {"status": series_status} if series_status else None

    assert _team_run_display_status(
        SimpleNamespace(status=run_status), request, cycle, series
    ) == expected


def test_mark_waiting_for_provider_preserves_dispatching_request_and_current_work(
    tmp_path,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    teams.set_cycle_execution_metadata(
        cycle.id,
        {
            "provider_capabilities": {"claude": {"ready": False}},
            "agents": {agent.id: {"sandbox_mode": "workspace-write"}},
        },
    )
    for table, row_id in (
        ("team_runs", run.id),
        ("team_run_cycles", cycle.id),
        ("team_tasks", task.id),
        ("team_agents", agent.id),
    ):
        db.execute(
            f"update {table} set finished_at = ? where id = ?",
            ("2026-07-29T23:59:00+00:00", row_id),
        )

    waiting = teams.mark_waiting_for_provider(
        cycle.id,
        provider="claude",
        reason_code="provider_not_ready",
        attempts=3,
        task_id=task.id,
        agent_id=agent.id,
        now=dt("2026-07-30T00:00:00+00:00"),
    )

    assert waiting.status == "waiting_for_provider"
    assert waiting.finished_at is None
    waiting_run = teams.get_team_run(run.id)
    assert waiting_run.status == "waiting_for_provider"
    assert waiting_run.finished_at is None
    waiting_task = teams.get_task(task.id)
    assert waiting_task.status == "waiting_for_provider"
    assert waiting_task.finished_at is None
    waiting_agent = teams.get_agent(agent.id)
    assert waiting_agent.status == "waiting"
    assert waiting_agent.current_task_id == task.id
    assert waiting_agent.finished_at is None
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    assert teams.list_waiting_provider_cycles() == [waiting]
    assert waiting.execution_metadata["provider_capabilities"] == {
        "claude": {"ready": False}
    }
    assert waiting.execution_metadata["agents"] == {
        agent.id: {"sandbox_mode": "workspace-write"}
    }
    assert waiting.execution_metadata["provider_recovery"] == {
        "provider": "claude",
        "task_id": task.id,
        "agent_id": agent.id,
        "reason_code": "provider_not_ready",
        "attempts": 3,
        "first_failed_at": "2026-07-30T00:00:00+00:00",
        "next_retry_at": "2026-07-30T00:00:30+00:00",
        "warning_visible_at": "2026-07-30T00:02:00+00:00",
    }


def test_mark_waiting_for_provider_accepts_coherent_preplanning_freeze(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    teams.set_cycle_execution_metadata(
        cycle.id,
        {"provider_capabilities": {"claude": {"ready": False}}},
    )

    waiting = teams.mark_waiting_for_provider(
        cycle.id,
        provider="claude",
        reason_code="capabilities_unavailable",
        attempts=3,
        task_id=None,
        agent_id=None,
        now=dt("2026-07-30T00:00:00+00:00"),
    )

    assert waiting.status == "waiting_for_provider"
    assert waiting.finished_at is None
    waiting_run = teams.get_team_run(run.id)
    assert waiting_run.status == "waiting_for_provider"
    assert waiting_run.finished_at is None
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    assert teams.list_tasks(run.id, cycle.id) == []
    assert {agent.status for agent in teams.list_agents(run.id)} == {"pending"}
    assert waiting.execution_metadata["provider_capabilities"] == {
        "claude": {"ready": False}
    }
    assert waiting.execution_metadata["provider_recovery"]["task_id"] is None
    assert waiting.execution_metadata["provider_recovery"]["agent_id"] is None


@pytest.mark.parametrize(
    "invalid_state",
    ["unlinked", "pending_task", "agent_not_pending", "agent_execution_marker"],
)
def test_mark_waiting_for_provider_rejects_nonpristine_preplanning_state(
    tmp_path,
    invalid_state,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = (
        teams.create_cycle(run.id, "manual", "unlinked")
        if invalid_state == "unlinked"
        else make_queued_cycle(teams, cycles, run)
    )
    pending_task_id = None
    leader = teams.get_agent(run.leader_agent_id)
    if invalid_state == "pending_task":
        pending_task = teams.create_task(
            run.id,
            "premature",
            "premature",
            owner_agent_id=None,
            cycle_id=cycle.id,
        )
        pending_task_id = pending_task.id
    elif invalid_state == "agent_not_pending":
        db.execute(
            "update team_agents set status = 'completed' where id = ?",
            (leader.id,),
        )
    elif invalid_state == "agent_execution_marker":
        db.execute(
            """
            update team_agents
            set reinvocations = 1, upstream_session_id = 'prior-session',
                started_at = '2026-07-29T23:59:00+00:00'
            where id = ?
            """,
            (leader.id,),
        )

    with pytest.raises(ValueError, match="provider wait"):
        teams.mark_waiting_for_provider(
            cycle.id,
            provider="claude",
            reason_code="capabilities_unavailable",
            attempts=3,
            task_id=None,
            agent_id=None,
            now=dt("2026-07-30T00:00:00+00:00"),
        )

    assert teams.get_team_run(run.id).status == "draft"
    assert teams.get_cycle(cycle.id).status == "queued"
    assert teams.get_cycle(cycle.id).execution_metadata is None
    if cycle.request_id is not None:
        assert cycles.get_request(cycle.request_id).status == "dispatching"
    if pending_task_id is not None:
        assert teams.get_task(pending_task_id).status == "pending"


@pytest.mark.parametrize(
    "mixed_state",
    ["queued_with_ids", "queued_active_run", "running_draft_run"],
)
def test_mark_waiting_for_provider_rejects_mixed_preplanning_state(
    tmp_path,
    mixed_state,
):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    worker = next(
        agent
        for agent in teams.list_agents(run.id)
        if agent.id != run.leader_agent_id
    )
    task_id = None
    agent_id = None
    if mixed_state == "queued_with_ids":
        task = teams.create_task(
            run.id,
            "not-started",
            "not-started",
            owner_agent_id=worker.id,
            cycle_id=cycle.id,
        )
        task_id = task.id
        agent_id = worker.id
    elif mixed_state == "queued_active_run":
        teams.set_run_status(run.id, "running")
    else:
        teams.set_cycle_status(cycle.id, "running")

    with pytest.raises(ValueError, match="provider wait"):
        teams.mark_waiting_for_provider(
            cycle.id,
            provider="claude",
            reason_code="capabilities_unavailable",
            attempts=3,
            task_id=task_id,
            agent_id=agent_id,
            now=dt("2026-07-30T00:00:00+00:00"),
        )

    expected_run_status = "running" if mixed_state == "queued_active_run" else "draft"
    expected_cycle_status = (
        "running" if mixed_state == "running_draft_run" else "queued"
    )
    assert teams.get_team_run(run.id).status == expected_run_status
    assert teams.get_cycle(cycle.id).status == expected_cycle_status
    assert teams.get_cycle(cycle.id).execution_metadata is None
    assert cycles.get_request(cycle.request_id).status == "dispatching"
    assert teams.get_agent(worker.id).status == "pending"
    if task_id is not None:
        assert teams.get_task(task_id).status == "pending"


def test_mark_waiting_for_provider_rolls_back_mismatched_current_agent(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    leader = teams.get_agent(run.leader_agent_id)

    with pytest.raises(ValueError, match="current task"):
        teams.mark_waiting_for_provider(
            cycle.id,
            provider="claude",
            reason_code="provider_not_ready",
            attempts=3,
            task_id=task.id,
            agent_id=leader.id,
            now=dt("2026-07-30T00:00:00+00:00"),
        )

    assert teams.get_cycle(cycle.id).status == "running"
    assert teams.get_cycle(cycle.id).execution_metadata is None
    assert teams.get_team_run(run.id).status == "running"
    assert teams.get_task(task.id).status == "in_progress"
    assert teams.get_agent(agent.id).status == "running"
    assert teams.get_agent(agent.id).current_task_id == task.id
    assert teams.get_agent(leader.id).status == "pending"
    assert cycles.get_request(cycle.request_id).status == "dispatching"


def test_mark_waiting_for_provider_rolls_back_omitted_current_work(tmp_path):
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)

    with pytest.raises(ValueError, match="current task"):
        teams.mark_waiting_for_provider(
            cycle.id,
            provider="claude",
            reason_code="provider_not_ready",
            attempts=3,
            task_id=None,
            agent_id=None,
            now=dt("2026-07-30T00:00:00+00:00"),
        )

    assert teams.get_cycle(cycle.id).status == "running"
    assert teams.get_cycle(cycle.id).execution_metadata is None
    assert teams.get_team_run(run.id).status == "running"
    assert teams.get_task(task.id).status == "in_progress"
    assert teams.get_agent(agent.id).status == "running"
    assert teams.get_agent(agent.id).current_task_id == task.id
    assert cycles.get_request(cycle.request_id).status == "dispatching"


@pytest.mark.parametrize(
    "stale_source",
    ["run", "cycle", "request_status", "request_source"],
)
def test_mark_waiting_for_provider_rolls_back_stale_source_state(
    tmp_path,
    stale_source,
):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle, task, agent = make_running_task_in_cycle(teams, cycles, run)
    if stale_source == "run":
        db.execute(
            "update team_runs set status = 'canceled' where id = ?",
            (run.id,),
        )
    elif stale_source == "cycle":
        db.execute(
            "update team_run_cycles set status = 'completed' where id = ?",
            (cycle.id,),
        )
    elif stale_source == "request_status":
        db.execute(
            "update team_cycle_requests set status = 'queued' where id = ?",
            (cycle.request_id,),
        )
    else:
        db.execute(
            "update team_cycle_requests set source_id = 'stale' where id = ?",
            (cycle.request_id,),
        )

    with pytest.raises(ValueError, match="provider wait"):
        teams.mark_waiting_for_provider(
            cycle.id,
            provider="claude",
            reason_code="provider_not_ready",
            attempts=3,
            task_id=task.id,
            agent_id=agent.id,
            now=dt("2026-07-30T00:00:00+00:00"),
        )

    expected_run_status = "canceled" if stale_source == "run" else "running"
    expected_cycle_status = "completed" if stale_source == "cycle" else "running"
    expected_request_status = (
        "queued" if stale_source == "request_status" else "dispatching"
    )
    assert teams.get_team_run(run.id).status == expected_run_status
    assert teams.get_cycle(cycle.id).status == expected_cycle_status
    assert teams.get_cycle(cycle.id).execution_metadata is None
    assert teams.get_task(task.id).status == "in_progress"
    assert teams.get_agent(agent.id).status == "running"
    assert teams.get_agent(agent.id).current_task_id == task.id
    assert cycles.get_request(cycle.request_id).status == expected_request_status


def make_services(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    return personas, teams


def make_continuous_team_with_space(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    spaces = SpacePolicyService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "workspace",
        space_policies=spaces,
    )
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    directory = TeamService(db, personas)
    team = directory.create_team("Cycle Team", "cycles", leader.id, [])
    rules = RuleSetService(db)
    rules.seed_defaults()
    run = teams.create_team_run_from_team(
        directory,
        rules,
        team.id,
        "goal",
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    return db, teams, spaces, team, run


def make_policy_services(tmp_path: Path):
    db = Database(tmp_path / "policy.db")
    db.initialize()
    personas = PersonaService(db)
    cycle_service = TeamCycleService(db)
    teams = TeamRunService(
        db,
        personas,
        workspace_root=tmp_path / "policy-workspace",
        cycle_service=cycle_service,
    )
    leader = personas.create_persona("Policy Lead", "lead", "d", [], [])
    worker = personas.create_persona("Policy Worker", "worker", "d", [], [])
    return db, teams, cycle_service, leader.id, worker.id


def test_new_auto_run_is_continuous_and_creates_first_request_atomically(
    tmp_path: Path,
) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)

    run = teams.create_team_run(
        "goal",
        leader_id,
        [worker_id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=3,
        auto_interval_seconds=600,
    )

    assert run.lifecycle_mode == "continuous"
    assert run.execution_policy == "auto"
    series = cycle_service.get_active_series(run.id)
    assert (series.target_slots, series.interval_seconds) == (3, 600)
    assert [
        request.slot_ordinal for request in cycle_service.list_requests(run.id)
    ] == [1]


def test_auto_initialization_failure_rolls_back_team_run_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    workspace_root = tmp_path / "policy-workspace"
    workspace_root.mkdir()
    sentinel = workspace_root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        cycle_service,
        "initialize_auto_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        teams.create_team_run(
            "goal",
            leader_id,
            [worker_id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy="auto",
            auto_repeat_count=2,
            auto_interval_seconds=60,
        )

    assert db.fetchone("select id from team_runs") is None
    assert db.fetchone("select id from team_agents") is None
    assert db.fetchone("select id from team_run_auto_series") is None
    assert db.fetchone("select id from team_cycle_requests") is None
    assert list(workspace_root.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    ("execution_policy", "auto_repeat_count", "auto_interval_seconds", "message"),
    [
        (None, None, None, "requires an execution policy"),
        ("auto", 0, 60, "repeat count must be positive"),
        ("auto", 2, 59, "interval must be at least 60 seconds"),
        ("triggered", 2, None, "does not accept AUTO settings"),
    ],
)
def test_continuous_run_validates_execution_policy_settings(
    tmp_path: Path,
    execution_policy,
    auto_repeat_count,
    auto_interval_seconds,
    message: str,
) -> None:
    _, teams, _, leader_id, worker_id = make_policy_services(tmp_path)

    with pytest.raises(ValueError, match=message):
        teams.create_team_run(
            "goal",
            leader_id,
            [worker_id],
            "plan_and_execute",
            1,
            lifecycle_mode="continuous",
            execution_policy=execution_policy,
            auto_repeat_count=auto_repeat_count,
            auto_interval_seconds=auto_interval_seconds,
        )


def test_create_team_run_from_team_forwards_auto_policy(tmp_path: Path) -> None:
    db, teams, cycle_service, leader_id, worker_id = make_policy_services(tmp_path)
    personas = PersonaService(db)
    directory = TeamService(db, personas)
    rules = RuleSetService(db)
    rules.seed_defaults()
    team = directory.create_team(
        "Policy Team",
        "continuous work",
        leader_id,
        [worker_id],
    )

    run = teams.create_team_run_from_team(
        directory,
        rules,
        team.id,
        "goal",
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="auto",
        auto_repeat_count=2,
        auto_interval_seconds=300,
    )

    assert run.execution_policy == "auto"
    assert cycle_service.get_active_series(run.id).target_slots == 2
    assert [request.slot_ordinal for request in cycle_service.list_requests(run.id)] == [
        1
    ]


def test_create_team_run_snapshots_personas(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], ["Stay scoped"])
    member = personas.create_persona("QA Tester", "Quality", "Checks risk.", ["Test"], ["Report evidence"])

    run = teams.create_team_run(
        goal="Design Agent Teams",
        leader_persona_id=leader.id,
        member_persona_ids=[member.id],
        run_mode="planning_only",
        max_workers=2,
    )

    agents = teams.list_agents(run.id)
    assert run.status == "draft"
    assert Path(run.workspace_root).is_dir()
    assert len(agents) == 2
    assert agents[0].persona_snapshot["name"] == "Tech Lead"
    assert agents[1].persona_snapshot["name"] == "QA Tester"

    personas.update_persona(member.id, name="Changed QA")

    unchanged = teams.list_agents(run.id)[1]
    assert unchanged.persona_snapshot["name"] == "QA Tester"


def test_create_team_run_inherits_terminal_workspace_as_writable_snapshot(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", [], [])
    source = teams.create_team_run(
        "Build SNS studio",
        leader.id,
        [],
        "plan_and_execute",
        1,
    )
    source_root = Path(source.working_root)
    (source_root / "apps" / "web").mkdir(parents=True)
    (source_root / "apps" / "web" / "package.json").write_text(
        '{"name":"sns-studio"}', encoding="utf-8"
    )
    (source_root / ".env").write_text("SECRET=value", encoding="utf-8")
    (source_root / ".env.example").write_text(
        "STUDIO_PUBLISHER_MODE=mock", encoding="utf-8"
    )
    (source_root / ".git").mkdir()
    (source_root / ".git" / "config").write_text("private", encoding="utf-8")
    teams.set_run_status(source.id, "completed")

    inherited = teams.create_team_run(
        "Refactor SNS studio",
        leader.id,
        [],
        "plan_and_execute",
        1,
        parent_team_run_id=source.id,
    )

    inherited_root = Path(inherited.working_root)
    inherited_package = inherited_root / "apps" / "web" / "package.json"
    assert inherited.parent_team_run_id == source.id
    assert inherited_package.read_text(encoding="utf-8") == '{"name":"sns-studio"}'
    assert not (inherited_root / ".env").exists()
    assert (inherited_root / ".env.example").read_text(
        encoding="utf-8"
    ) == "STUDIO_PUBLISHER_MODE=mock"
    assert not (inherited_root / ".git").exists()
    inherited_package.write_text('{"name":"refactored"}', encoding="utf-8")
    assert (source_root / "apps" / "web" / "package.json").read_text(
        encoding="utf-8"
    ) == '{"name":"sns-studio"}'
    manifest = Path(inherited.artifact_root) / "workspace-inheritance.json"
    assert f'"source_team_run_id": "{source.id}"' in manifest.read_text(
        encoding="utf-8"
    )


def test_create_team_run_rejects_active_workspace_inheritance(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", [], [])
    source = teams.create_team_run("Source", leader.id, [], "plan_and_execute", 1)
    teams.set_run_status(source.id, "running")

    with pytest.raises(ValueError, match="must be terminal"):
        teams.create_team_run(
            "Child",
            leader.id,
            [],
            "plan_and_execute",
            1,
            parent_team_run_id=source.id,
        )


def test_list_team_runs_enriched(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [], avatar="a01")
    member = personas.create_persona("Frontend Dev", "fe", "d", [], [], avatar="a05")
    run = teams.create_team_run("goal", lead.id, [member.id], "plan_and_execute", 2)
    t1 = teams.create_task(run.id, "t1", "d")
    teams.create_task(run.id, "t2", "d")
    teams.set_task_status(t1.id, "completed", result="ok")

    enriched = teams.list_team_runs_enriched()
    row = next(r for r in enriched if r["id"] == run.id)
    assert row["leader_name"] == "Lead"
    assert row["leader"] == {"name": "Lead", "avatar": "a01", "initials": "L"}
    assert {m["name"] for m in row["members"]} == {"Frontend Dev"}
    assert row["members"][0]["avatar"] == "a05"
    assert row["members"][0]["initials"] == "FD"
    assert row["task_total"] == 2
    assert row["task_done"] == 1
    assert row["task_counts"]["completed"] == 1
    assert isinstance(row["elapsed_seconds"], (int, float))


def test_task_assignment_updates_task_and_agent_lifecycle_together(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    member = personas.create_persona("Worker", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = teams.list_agents(run.id)[1]
    task = teams.create_task(run.id, "Build API", "d")

    started_task, started_agent = teams.start_task(task.id, worker.id)

    assert started_task.status == "in_progress"
    assert started_task.owner_agent_id == worker.id
    assert started_agent.status == "running"
    assert started_agent.current_task_id == task.id

    finished_task, finished_agent = teams.finish_task(
        task.id, worker.id, "completed", result="done"
    )

    assert finished_task.status == "completed"
    assert finished_task.owner_agent_id == worker.id
    assert finished_task.result == "done"
    assert finished_agent.status == "completed"
    assert finished_agent.current_task_id is None


def test_delete_team_run_removes_isolated_workspace_and_related_records(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    task = teams.create_task(run.id, "temporary task", "test only")
    teams.append_message(run.id, None, None, "note", "temporary", {"task_id": task.id})
    workspace = Path(run.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "result.txt").write_text("temporary", encoding="utf-8")

    teams.delete_team_run(run.id)

    assert not workspace.exists()
    with pytest.raises(KeyError):
        teams.get_team_run(run.id)
    assert teams._db.fetchone("select id from team_tasks where id = ?", (task.id,)) is None
    assert teams._db.fetchone(
        "select id from team_messages where team_run_id = ?", (run.id,)
    ) is None


def test_delete_team_run_allows_missing_workspace(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    shutil.rmtree(run.workspace_root)

    teams.delete_team_run(run.id)

    with pytest.raises(KeyError):
        teams.get_team_run(run.id)


def test_delete_team_run_rejects_workspace_outside_configured_root(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    outside = tmp_path / "outside" / run.id
    outside.mkdir(parents=True)
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    teams._db.execute(
        "update team_runs set workspace_root = ? where id = ?",
        (str(outside), run.id),
    )

    with pytest.raises(ValueError, match="outside the configured workspace root"):
        teams.delete_team_run(run.id)

    assert sentinel.exists()
    assert teams.get_team_run(run.id).id == run.id


def test_delete_team_run_keeps_record_when_workspace_removal_fails(tmp_path, monkeypatch):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("temporary test run", leader.id, [], "planning_only", 1)
    workspace = Path(run.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)

    def fail_removal(_path):
        raise OSError("workspace is locked")

    monkeypatch.setattr("personal_agent_gateway.teams.shutil.rmtree", fail_removal)

    with pytest.raises(OSError, match="workspace is locked"):
        teams.delete_team_run(run.id)

    assert workspace.exists()
    assert teams.get_team_run(run.id).id == run.id


def test_append_team_message(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    message = teams.append_message(run.id, agent.id, None, "note", "Planning started", {"phase": "planning"})

    assert message.content == "Planning started"
    assert teams.list_messages(run.id)[0].metadata == {"phase": "planning"}


def test_new_run_has_default_budget(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    assert run.rounds_budget == 8
    assert run.rounds_used == 0
    assert run.lifecycle_mode == "standard"
    assert run.execution_policy is None


def test_continuous_team_run_cycles_are_ordered_and_idempotent(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        rounds_budget=6,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )

    first = teams.create_cycle(run.id, "hook", "hook-run-1")
    duplicate = teams.create_cycle(run.id, "hook", "hook-run-1")
    second = teams.create_cycle(run.id, "hook", "hook-run-2", rounds_budget=3)

    assert duplicate.id == first.id
    assert [(cycle.sequence, cycle.source_id) for cycle in teams.list_cycles(run.id)] == [
        (1, "hook-run-1"),
        (2, "hook-run-2"),
    ]
    assert first.status == "queued"
    assert first.rounds_budget == 6
    assert second.rounds_budget == 3
    assert teams.increment_cycle_rounds_used(first.id).rounds_used == 1


def test_continuous_cycle_captures_latest_team_space_and_duplicate_keeps_it(tmp_path):
    db, teams, spaces, team, run = make_continuous_team_with_space(tmp_path)
    spaces.upsert(
        "team", team.id,
        read_mode="all", read_path=None,
        write_mode="isolated", workspace_path=None,
    )

    first = teams.create_cycle(run.id, "manual", "source-1")
    spaces.upsert(
        "team", team.id,
        read_mode="none", read_path=None,
        write_mode="isolated", workspace_path=None,
    )
    duplicate = teams.create_cycle(run.id, "manual", "source-1")
    second = teams.create_cycle(run.id, "manual", "source-2")

    assert first.space_policy["read_mode"] == "all"
    assert duplicate.space_policy == first.space_policy
    assert second.space_policy["read_mode"] == "none"
    assert teams.get_team_run(run.id).space_policy["read_mode"] == "none"


def test_retry_cycle_captures_latest_team_space(tmp_path: Path) -> None:
    db, teams, spaces, team, run = make_continuous_team_with_space(tmp_path)
    spaces.upsert(
        "team", team.id,
        read_mode="all", read_path=None,
        write_mode="isolated", workspace_path=None,
    )
    failed = teams.create_task(run.id, "failed", "retry me")
    teams.set_task_status(failed.id, "failed", error_message="timed out")
    teams.set_run_status(run.id, "completed_with_failures", summary="old summary")

    _, _, retry_cycle = teams.retry_failed_task(run.id, failed.id)

    assert retry_cycle.space_policy["read_mode"] == "all"


def test_retry_cycle_resolves_space_after_immediate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, teams, _, _, run = make_continuous_team_with_space(tmp_path)
    failed = teams.create_task(run.id, "failed", "retry me")
    teams.set_task_status(failed.id, "failed", error_message="timed out")
    teams.set_run_status(run.id, "completed_with_failures", summary="old summary")
    resolve_snapshot = teams._space_policy_snapshot_for_cycle

    def resolve_after_lock(run_snapshot):
        observer = db.connect()
        try:
            observer.execute("pragma busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                observer.execute("begin immediate")
        finally:
            observer.rollback()
            observer.close()
        return resolve_snapshot(run_snapshot)

    monkeypatch.setattr(
        teams,
        "_space_policy_snapshot_for_cycle",
        resolve_after_lock,
    )

    _, _, retry_cycle = teams.retry_failed_task(run.id, failed.id)

    assert retry_cycle is not None


def test_continuous_cycle_is_idempotent_by_request(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    db.execute(
        """
        insert into team_cycle_requests (
            id, team_run_id, source_type, source_id, status, instruction,
            created_at, updated_at
        ) values ('request-1', ?, 'manual', 'client-1', 'dispatching', 'go', 't', 't')
        """,
        (run.id,),
    )

    first = teams.create_cycle(
        run.id, "manual", "client-1", request_id="request-1"
    )
    duplicate = teams.create_cycle(
        run.id, "", "", rounds_budget=0, request_id="request-1"
    )

    assert duplicate.id == first.id
    assert first.request_id == "request-1"
    assert teams.get_cycle_for_request("request-1").id == first.id


def test_cycle_request_must_be_dispatching_and_match_source(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    db.execute(
        """
        insert into team_cycle_requests (
            id, team_run_id, source_type, source_id, status, instruction,
            created_at, updated_at
        ) values ('request-1', ?, 'manual', 'client-1', 'queued', 'go', 't', 't')
        """,
        (run.id,),
    )

    with pytest.raises(ValueError, match="dispatching"):
        teams.create_cycle(
            run.id, "manual", "client-1", request_id="request-1"
        )

    db.execute(
        "update team_cycle_requests set status = 'dispatching' where id = 'request-1'"
    )
    with pytest.raises(ValueError, match="source"):
        teams.create_cycle(
            run.id, "hook", "hook-1", request_id="request-1"
        )

    cycle = teams.create_cycle(
        run.id, "manual", "client-1", request_id="request-1"
    )
    assert cycle.request_id == "request-1"


def test_standard_team_run_rejects_explicit_cycles(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)

    with pytest.raises(ValueError, match="continuous"):
        teams.create_cycle(run.id, "hook", "hook-run-1")


def test_task_and_message_keep_cycle_lineage(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "hook", "hook-run-1")

    task = teams.create_task(run.id, "Classify mail", "d", cycle_id=cycle.id)
    message = teams.append_message(
        run.id,
        None,
        None,
        "mail_received",
        "Mail queued.",
        {},
        cycle_id=cycle.id,
    )

    assert task.cycle_id == cycle.id
    assert message.cycle_id == cycle.id


def test_cycle_lineage_rejects_another_team_run_and_cascades(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    first_run = teams.create_team_run(
        "first",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    second_run = teams.create_team_run(
        "second",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(first_run.id, "hook", "hook-run-1")

    with pytest.raises(ValueError, match="different team run"):
        teams.create_task(second_run.id, "wrong", "d", cycle_id=cycle.id)

    teams.delete_team_run(first_run.id)

    with pytest.raises(KeyError):
        teams.get_cycle(cycle.id)


def test_agent_session_and_counters(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 2)
    agent = teams.list_agents(run.id)[0]
    assert agent.reinvocations == 0
    assert agent.upstream_session_id is None

    updated = teams.set_agent_session(agent.id, "thread-123")
    assert updated.upstream_session_id == "thread-123"
    assert teams.increment_agent_reinvocations(agent.id).reinvocations == 1

    assert teams.increment_rounds_used(run.id).rounds_used == 1


def test_completed_with_failures_is_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)
    updated = teams.set_run_status(run.id, "completed_with_failures", summary="1/2")
    assert updated.status == "completed_with_failures"
    assert updated.finished_at is not None


def test_task_outcome_and_acceptance_result_are_persisted(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "role", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    task = teams.create_task(
        run.id,
        "T",
        "D",
        acceptance=TaskAcceptance((), (RequiredVerification("review"),)),
    )

    updated = teams.record_task_outcome(
        task.id,
        {
            "status": "completed",
            "summary": "done",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {"name": "review", "status": "passed", "evidence": "checked"}
            ],
        },
        {
            "accepted": True,
            "status": "completed",
            "reason_code": None,
            "evidence": {"verifications": {"review": {"status": "passed"}}},
        },
    )

    assert updated.outcome is not None
    assert updated.outcome["summary"] == "done"
    assert updated.acceptance_result is not None
    assert updated.acceptance_result["accepted"] is True


def test_acceptance_review_retry_worker_persists_audit_and_counter(tmp_path: Path) -> None:
    personas, teams = make_services(tmp_path)
    lead_persona = personas.create_persona("Lead", "lead", "d", [], [])
    worker_persona = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", lead_persona.id, [worker_persona.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Task",
        "Description",
        acceptance=TaskAcceptance((), (RequiredVerification("source-check"),)),
    )
    teams.start_task(task.id, worker_agent.id)

    updated = teams.record_acceptance_review(
        task.id,
        leader_agent.id,
        worker_agent.id,
        action="retry_worker",
        reason_code="undeclared_deliverable",
        reason="Deliverable was not declared",
        instruction="Declare the deliverable",
        acceptance_after=None,
        rejected_deliverables=("docs/knowledge/d3-review.md",),
        rejected_verifications=(),
    )

    assert updated.status == "in_progress"
    assert updated.acceptance_recovery_attempts == 1
    assert updated.acceptance == TaskAcceptance((), (RequiredVerification("source-check"),))
    review = teams.list_messages(run.id)[-1]
    assert review.kind == "acceptance_review"
    assert review.sender_agent_id == leader_agent.id
    assert review.recipient_agent_id == worker_agent.id
    assert review.metadata["task_id"] == task.id
    assert review.metadata["attempt"] == 1
    assert review.metadata["action"] == "retry_worker"
    assert review.metadata["reason_code"] == "undeclared_deliverable"


def test_acceptance_review_revises_contract_and_ask_user_preserves_counter(
    tmp_path: Path,
) -> None:
    personas, teams = make_services(tmp_path)
    lead_persona = personas.create_persona("Lead", "lead", "d", [], [])
    worker_persona = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", lead_persona.id, [worker_persona.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Task",
        "Description",
        acceptance=TaskAcceptance((), (RequiredVerification("source-check"),)),
    )
    teams.start_task(task.id, worker_agent.id)
    acceptance_after = TaskAcceptance(
        ("docs/knowledge/d3-review.md",),
        (RequiredVerification("source-check"),),
    )

    updated = teams.record_acceptance_review(
        task.id,
        leader_agent.id,
        worker_agent.id,
        action="revise_acceptance",
        reason_code="contract_incomplete",
        reason="Acceptance contract needs an output",
        instruction=None,
        acceptance_after=acceptance_after,
        rejected_deliverables=(),
        rejected_verifications=(),
    )

    assert updated.acceptance_recovery_attempts == 1
    assert updated.acceptance == acceptance_after
    review = teams.list_messages(run.id)[-1]
    assert review.metadata["acceptance_before"] == {
        "required_outputs": [],
        "required_verifications": ["source-check"],
    }
    assert review.metadata["acceptance_after"] == {
        "required_outputs": ["docs/knowledge/d3-review.md"],
        "required_verifications": ["source-check"],
    }

    asked = teams.record_acceptance_review(
        task.id,
        leader_agent.id,
        worker_agent.id,
        action="ask_user",
        reason_code="needs_direction",
        reason="Need user direction",
        instruction=None,
        acceptance_after=None,
        rejected_deliverables=(),
        rejected_verifications=(),
    )

    assert asked.acceptance_recovery_attempts == 1
    assert teams.list_messages(run.id)[-1].metadata["attempt"] == 2


@pytest.mark.parametrize(
    ("acceptance_after", "message"),
    [
        (
            TaskAcceptance((), ()),
            "Acceptance requires an output or verification",
        ),
        (
            TaskAcceptance(("report.md", "report.md"), (RequiredVerification("source-check"),)),
            "Acceptance has duplicate required outputs",
        ),
        (
            TaskAcceptance(
                ("report.md",),
                (RequiredVerification("source-check"), RequiredVerification("source-check")),
            ),
            "Acceptance has duplicate required verifications",
        ),
        (
            TaskAcceptance((" ",), (RequiredVerification("source-check"),)),
            "Acceptance items must not be blank",
        ),
        (
            TaskAcceptance(("report.md",), (RequiredVerification(" "),)),
            "Acceptance items must not be blank",
        ),
        (
            TaskAcceptance(("../outside.md",), (RequiredVerification("source-check"),)),
            "Acceptance output path must be relative and bounded",
        ),
        (
            TaskAcceptance(("/absolute.md",), (RequiredVerification("source-check"),)),
            "Acceptance output path must be relative and bounded",
        ),
        (
            TaskAcceptance((r"C:\absolute.md",), (RequiredVerification("source-check"),)),
            "Acceptance output path must be relative and bounded",
        ),
        (
            TaskAcceptance((r"\outside.md",), (RequiredVerification("source-check"),)),
            "Acceptance output path must be relative and bounded",
        ),
        (
            TaskAcceptance(("C:outside.md",), (RequiredVerification("source-check"),)),
            "Acceptance output path must be relative and bounded",
        ),
    ],
)
def test_acceptance_review_rejects_invalid_revised_contract(
    tmp_path: Path,
    acceptance_after: TaskAcceptance,
    message: str,
) -> None:
    personas, teams = make_services(tmp_path)
    lead_persona = personas.create_persona("Lead", "lead", "d", [], [])
    worker_persona = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", lead_persona.id, [worker_persona.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    original_acceptance = TaskAcceptance((), (RequiredVerification("source-check"),))
    task = teams.create_task(
        run.id,
        "Task",
        "Description",
        acceptance=original_acceptance,
    )
    teams.start_task(task.id, worker_agent.id)

    with pytest.raises(ValueError, match=message):
        teams.record_acceptance_review(
            task.id,
            leader_agent.id,
            worker_agent.id,
            action="revise_acceptance",
            reason_code="contract_incomplete",
            reason="Acceptance contract needs revision",
            instruction=None,
            acceptance_after=acceptance_after,
            rejected_deliverables=(),
            rejected_verifications=(),
        )

    unchanged = teams.list_tasks(run.id)[0]
    assert unchanged.acceptance_recovery_attempts == 0
    assert unchanged.acceptance == original_acceptance
    assert teams.list_messages(run.id) == []


@pytest.mark.parametrize("action", ["ask_user", "fail"])
def test_acceptance_review_non_consuming_action_does_not_update_task_row(
    tmp_path: Path,
    action: str,
) -> None:
    personas, teams = make_services(tmp_path)
    lead_persona = personas.create_persona("Lead", "lead", "d", [], [])
    worker_persona = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal", lead_persona.id, [worker_persona.id], "plan_and_execute", 1
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Task",
        "Description",
        acceptance=TaskAcceptance((), (RequiredVerification("source-check"),)),
    )
    teams.start_task(task.id, worker_agent.id)
    teams._db.execute(
        "update team_tasks set updated_at = ? where id = ?",
        ("2000-01-01T00:00:00+00:00", task.id),
    )
    before = dict(
        teams._db.fetchone("select * from team_tasks where id = ?", (task.id,))
    )

    teams.record_acceptance_review(
        task.id,
        leader_agent.id,
        worker_agent.id,
        action=action,
        reason_code="needs_direction",
        reason="Need user direction",
        instruction=None,
        acceptance_after=None,
        rejected_deliverables=(),
        rejected_verifications=(),
    )

    after = dict(
        teams._db.fetchone("select * from team_tasks where id = ?", (task.id,))
    )
    assert after == before
    assert teams.list_messages(run.id)[-1].metadata["attempt"] == 1


def test_persona_snapshot_includes_avatar(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="person01")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)

    agent = teams.list_agents(run.id)[0]
    assert agent.persona_snapshot["avatar"] == "person01"


def test_persona_snapshot_includes_default_options(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona(
        "L",
        "lead",
        "d",
        [],
        [],
        default_options={"effort": "max", "sandbox": "read-only"},
    )
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)

    assert teams.list_agents(run.id)[0].persona_snapshot["default_options"] == {
        "effort": "max",
        "sandbox": "read-only",
    }


def test_backfill_agent_avatars_populates_missing(tmp_path):
    import json
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="tech03")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    # Simulate a legacy snapshot with no avatar key.
    snapshot = dict(agent.persona_snapshot)
    snapshot.pop("avatar", None)
    db.execute(
        "update team_agents set persona_snapshot_json = ? where id = ?",
        (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), agent.id),
    )
    assert "avatar" not in teams.list_agents(run.id)[0].persona_snapshot

    updated = teams.backfill_agent_avatars()

    assert updated == 1
    assert teams.list_agents(run.id)[0].persona_snapshot["avatar"] == "tech03"
    # Idempotent: a second pass changes nothing.
    assert teams.backfill_agent_avatars() == 0


def test_interrupt_active_run_requeues_only_in_progress_work(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    leader_agent, worker_agent = teams.list_agents(run.id)
    completed = teams.create_task(run.id, "done", "already done", worker_agent.id)
    interrupted = teams.create_task(run.id, "current", "was running", worker_agent.id)
    teams.set_task_status(completed.id, "completed", result="kept result")
    teams.set_task_status(interrupted.id, "in_progress")
    teams.set_agent_session(worker_agent.id, "thread-123")
    teams.set_agent_status(leader_agent.id, "running")
    teams.set_agent_status(worker_agent.id, "running")
    teams._db.execute(  # Simulate the assignment persisted by a running orchestrator.
        "update team_agents set current_task_id = ? where id = ?",
        (interrupted.id, worker_agent.id),
    )
    teams.set_run_status(run.id, "running")

    recovered = teams.interrupt_active_runs()

    assert [item.id for item in recovered] == [run.id]
    updated_run = teams.get_team_run(run.id)
    assert updated_run.status == "interrupted"
    assert updated_run.finished_at is None
    task_by_title = {task.title: task for task in teams.list_tasks(run.id)}
    assert task_by_title["done"].status == "completed"
    assert task_by_title["done"].result == "kept result"
    assert task_by_title["current"].status == "pending"
    assert task_by_title["current"].started_at is None
    updated_worker = teams.get_agent(worker_agent.id)
    assert updated_worker.status == "pending"
    assert updated_worker.current_task_id is None
    assert updated_worker.upstream_session_id == "thread-123"
    assert [message.kind for message in teams.list_messages(run.id)].count("system_interrupted") == 1

    assert teams.interrupt_active_runs() == []
    assert [message.kind for message in teams.list_messages(run.id)].count("system_interrupted") == 1


def test_retry_failed_task_creates_linked_task_and_preserves_original(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    completed = teams.create_task(run.id, "done", "kept")
    failed = teams.create_task(
        run.id,
        "failed",
        "retry me",
        required=False,
        acceptance=TaskAcceptance(
            required_outputs=("outputs/report.md",),
            required_verifications=(RequiredVerification("pytest"),),
        ),
    )
    teams.set_task_status(completed.id, "completed", result="kept result")
    teams.set_task_status(failed.id, "failed", error_message="timed out")
    teams.set_run_status(run.id, "completed_with_failures", summary="old summary")

    updated_run, retry_task, retry_cycle = teams.retry_failed_task(run.id, failed.id)

    assert updated_run.status == "interrupted"
    assert updated_run.summary is None
    assert updated_run.error_message is None
    assert updated_run.finished_at is None
    assert retry_cycle is None
    assert retry_task.id != failed.id
    assert retry_task.retry_of_task_id == failed.id
    assert retry_task.status == "pending"
    assert retry_task.required is False
    assert retry_task.acceptance == TaskAcceptance(
        required_outputs=("outputs/report.md",),
        required_verifications=(RequiredVerification("pytest"),),
    )
    assert retry_task.result is None
    assert retry_task.error_message is None
    assert retry_task.started_at is None
    assert retry_task.finished_at is None
    original = next(task for task in teams.list_tasks(run.id) if task.id == failed.id)
    assert original.status == "failed"
    assert original.error_message == "timed out"
    assert teams.list_tasks(run.id)[0].result == "kept result"
    message = teams.list_messages(run.id)[-1]
    assert message.kind == "system_task_retried"
    assert message.metadata == {
        "original_cycle_id": None,
        "original_task_id": failed.id,
        "retry_cycle_id": None,
        "retry_task_id": retry_task.id,
        "previous_error": "timed out",
    }


def test_task_persists_acceptance_outcome_and_blocked_finish_time(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)

    task = teams.create_task(
        run.id,
        "report",
        "write report",
        required=True,
        acceptance=TaskAcceptance(
            required_outputs=("outputs/report.md",),
            required_verifications=(RequiredVerification("pytest"),),
        ),
    )
    teams._db.execute(
        "update team_tasks set outcome_json = ?, acceptance_result_json = ? where id = ?",
        (
            '{"status":"blocked"}',
            '{"accepted":false}',
            task.id,
        ),
    )

    blocked = teams.set_task_status(task.id, "blocked", error_message="needs input")

    assert blocked.required is True
    assert blocked.acceptance == TaskAcceptance(
        required_outputs=("outputs/report.md",),
        required_verifications=(RequiredVerification("pytest"),),
    )
    assert blocked.outcome == {"status": "blocked"}
    assert blocked.acceptance_result == {"accepted": False}
    assert blocked.finished_at is not None


def test_retry_failed_task_rejects_nonfailed_task_and_nonterminal_run(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "plan_and_execute", 1)
    task = teams.create_task(run.id, "pending", "not failed")

    with pytest.raises(ValueError, match="failed terminal"):
        teams.retry_failed_task(run.id, task.id)

    teams.set_run_status(run.id, "failed", error_message="all failed")
    with pytest.raises(ValueError, match="Only failed tasks"):
        teams.retry_failed_task(run.id, task.id)


def test_decision_request_batches_waiting_tasks_and_projects_workspace_file(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    leader_agent, worker = teams.list_agents(run.id)
    first = teams.create_task(run.id, "Deploy", "choose target")
    second = teams.create_task(run.id, "Notify", "choose audience")
    teams.set_run_status(run.id, "running")
    teams.set_agent_status(leader_agent.id, "running")

    for task in (first, second):
        teams.start_task(task.id, worker.id)
        request = teams.defer_task_for_user_decision(
            task.id,
            worker.id,
            {
                "topic": task.title,
                "question": f"Choose for {task.title}?",
                "why_needed": "The choice changes the result.",
                "options": [
                    {"id": "safe", "label": "Safe", "impact": "Lower risk."},
                    {"id": "fast", "label": "Fast", "impact": "Faster delivery."},
                ],
                "recommended_option_id": "safe",
                "blocking_scope": "task",
            },
        )

    assert request.status == "collecting"
    assert request.revision == 2
    assert [item["id"] for item in request.items] == ["Q-001", "Q-002"]
    assert {task.status for task in teams.list_tasks(run.id)} == {"waiting_for_user"}

    published = teams.publish_decision_request(run.id)

    assert published.status == "awaiting_user"
    assert published.revision == 3
    assert teams.get_team_run(run.id).status == "waiting_for_user"
    assert teams.get_agent(leader_agent.id).status == "running"
    assert teams.get_agent(worker.id).status == "waiting"
    decision_file = Path(run.workspace_root) / "USER_DECISIONS.md"
    content = decision_file.read_text(encoding="utf-8")
    assert "status: awaiting_user" in content
    assert "Q-001" in content and "Q-002" in content
    assert "Choose for Deploy?" in content


def test_answer_decision_request_requeues_only_listed_tasks_and_rejects_stale_submit(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    worker = teams.list_agents(run.id)[1]
    blocked = teams.create_task(run.id, "Deploy", "choose target")
    untouched = teams.create_task(run.id, "Later", "already pending")
    teams.set_run_status(run.id, "running")
    teams.start_task(blocked.id, worker.id)
    teams.defer_task_for_user_decision(
        blocked.id,
        worker.id,
        {
            "topic": "target",
            "question": "Where?",
            "why_needed": "Changes configuration.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
    )
    request = teams.publish_decision_request(run.id)

    updated_run, resolved = teams.answer_decision_request(
        run.id, request.id, request.revision, {"Q-001": "staging"}
    )

    assert updated_run.status == "running"
    assert resolved.status == "resolved"
    assert resolved.answers == {"Q-001": "staging"}
    task_by_id = {task.id: task for task in teams.list_tasks(run.id)}
    assert task_by_id[blocked.id].status == "pending"
    assert task_by_id[untouched.id].status == "pending"
    assert teams.decision_context_for_task(run.id, blocked.id) == "Q: Where?\nA: staging"
    assert "staging" in (Path(run.workspace_root) / "USER_DECISIONS.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no longer awaiting"):
        teams.answer_decision_request(
            run.id, request.id, request.revision, {"Q-001": "production"}
        )


def test_run_level_decision_request_records_stage_and_resolved_context(tmp_path):
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    leader_agent = teams.list_agents(run.id)[0]
    teams.set_run_status(run.id, "planning")
    teams.set_agent_status(leader_agent.id, "running")

    collecting = teams.defer_run_for_user_decision(
        run.id,
        {
            "topic": "scope",
            "question": "Which scope?",
            "why_needed": "Changes the plan.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "run",
        },
        stage="planning",
    )

    assert collecting.status == "collecting"
    assert collecting.items[0]["stage"] == "planning"
    assert collecting.items[0]["blocking_task_ids"] == []

    request = teams.publish_decision_request(run.id)
    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "backend only"},
    )

    assert teams.decision_context_for_run(run.id, stage="planning") == (
        "Q: Which scope?\nA: backend only"
    )
    assert teams.decision_context_for_run(run.id, stage="synthesis") == ""


def test_create_team_run_from_team_snapshots_roster_and_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    from personal_agent_gateway.db import Database  # already imported at top; keep local clarity
    # reuse same db as make_services by rebuilding services on that db:
    db = Database(tmp_path / "app.db")
    directory = TeamService(db, personas)
    rules = RuleSetService(db)
    rules.seed_defaults()
    lead = personas.create_persona("Lead", "lead", "d", ["plan"], ["scoped"])
    member = personas.create_persona("QA", "qa", "d", ["test"], ["evidence"])
    rules.upsert("team", None, "", [])  # noop guard
    team = directory.create_team("Release Crew", "ships", lead.id, [member.id])
    rules.upsert("team", team.id, "team voice", [{"level": "REQUIRED", "text": "green regression"}])

    run = teams.create_team_run_from_team(
        directory, rules, team_id=team.id, goal="ship pdf",
        run_mode="plan_and_execute", max_workers=2,
    )

    assert run.team_id == team.id
    assert run.rules_snapshot["team"]["personality"] == "team voice"
    assert run.rules_snapshot["team"]["name"] == team.name
    assert run.rules_snapshot["global"]["rules"]
    agents = teams.list_agents(run.id)
    assert [a.role for a in agents] == ["leader", "member"]
    assert agents[0].persona_snapshot["name"] == "Lead"


def test_legacy_create_team_run_has_no_team_or_rules(tmp_path):
    (tmp_path / "workspace").mkdir()
    personas, teams = make_services(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run("legacy", lead.id, [], "planning_only", 1)
    assert run.team_id is None
    assert run.rules_snapshot is None


def test_dependency_ready_tasks_wait_for_prerequisite(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    research = teams.create_task(run.id, "Research", "Research", cycle_id=cycle.id)
    draft = teams.create_task(run.id, "Draft", "Draft", cycle_id=cycle.id)

    teams.add_task_dependencies(draft.id, [research.id])

    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [research]
    teams.set_task_status(research.id, "completed")
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == [draft]


def test_failed_prerequisite_skips_transitive_dependents(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    research = teams.create_task(run.id, "Research", "Research", cycle_id=cycle.id)
    draft = teams.create_task(run.id, "Draft", "Draft", cycle_id=cycle.id)
    qa = teams.create_task(run.id, "QA", "QA", cycle_id=cycle.id)
    teams.add_task_dependencies(draft.id, [research.id])
    teams.add_task_dependencies(qa.id, [draft.id])
    teams.set_task_status(research.id, "failed", error_message="source failed")

    skipped = teams.skip_pending_dependency_failures(run.id, cycle.id)

    assert [task.id for task in skipped] == [draft.id, qa.id]
    assert teams.get_task(draft.id).status == "skipped"
    assert teams.get_task(draft.id).error_message == "skipped_by_dependency"
    assert teams.skip_pending_dependency_failures(run.id, cycle.id) == []


def test_failed_prerequisite_skips_dependent_instead_of_running(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    fix = teams.create_task(run.id, "Fix", "Fix", cycle_id=cycle.id)
    qa = teams.create_task(run.id, "Qa", "Qa", cycle_id=cycle.id)
    teams.add_task_dependencies(qa.id, [fix.id])

    assert [t.id for t in teams.list_dependency_ready_tasks(run.id, cycle.id)] == [fix.id]

    teams.set_task_status(fix.id, "failed", error_message="draft-unmodified")
    skipped = teams.skip_pending_dependency_failures(run.id, cycle.id)

    assert [t.id for t in skipped] == [qa.id]
    assert teams.get_task(qa.id).status == "skipped"
    assert teams.get_task(qa.id).error_message == "skipped_by_dependency"
    assert teams.list_dependency_ready_tasks(run.id, cycle.id) == []


@pytest.mark.parametrize(
    "prerequisite_status",
    ["pending", "in_progress", "waiting_for_user", "waiting_for_provider"],
)
def test_nonterminal_prerequisite_does_not_skip_dependent(
    tmp_path,
    prerequisite_status,
) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)
    prerequisite = teams.create_task(run.id, "First", "First", cycle_id=cycle.id)
    dependent = teams.create_task(run.id, "Next", "Next", cycle_id=cycle.id)
    teams.add_task_dependencies(dependent.id, [prerequisite.id])
    teams.set_task_status(prerequisite.id, prerequisite_status)

    assert teams.skip_pending_dependency_failures(run.id, cycle.id) == []
    assert teams.get_task(dependent.id).status == "pending"


def test_create_task_assigns_increasing_plan_ordinals(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, _cycles, run)

    first = teams.create_task(run.id, "First", "First", cycle_id=cycle.id)
    second = teams.create_task(run.id, "Second", "Second", cycle_id=cycle.id)
    other_cycle_task = teams.create_task(run.id, "Loose", "Loose")

    assert first.plan_ordinal == 0
    assert second.plan_ordinal == 1
    assert other_cycle_task.plan_ordinal == 0
    assert [task.title for task in teams.list_tasks(run.id, cycle.id)] == [
        "First",
        "Second",
    ]


def test_run_wide_task_list_stays_chronological_across_cycles(tmp_path) -> None:
    _db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    first_cycle = make_queued_cycle(teams, cycles, run)
    teams.create_task(run.id, "C1 First", "d", cycle_id=first_cycle.id)
    teams.create_task(run.id, "C1 Second", "d", cycle_id=first_cycle.id)
    teams.set_cycle_status(first_cycle.id, "completed")
    cycles.settle_cycle(first_cycle.id)
    request = cycles.enqueue_request(
        run.id, "manual", "second-cycle", "work", previous_cycle_id=None
    )
    claimed = cycles.claim_next(run.id)
    assert claimed is not None and claimed.id == request.id
    second_cycle = teams.create_cycle(
        run.id, claimed.source_type, claimed.source_id, request_id=claimed.id
    )
    teams.create_task(run.id, "C2 First", "d", cycle_id=second_cycle.id)
    teams.create_task(run.id, "C2 Second", "d", cycle_id=second_cycle.id)

    assert [task.title for task in teams.list_tasks(run.id)] == [
        "C1 First",
        "C1 Second",
        "C2 First",
        "C2 Second",
    ]
    # The detail API truncates with tasks[-limit:], so the tail must be the
    # newest tasks rather than the highest ordinals.
    assert [task.title for task in teams.list_tasks(run.id)[-2:]] == [
        "C2 First",
        "C2 Second",
    ]


def test_reset_agents_for_new_cycle_clears_waiting_and_legacy_blocked(
    tmp_path,
) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    leader = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )
    task = teams.create_task(run.id, "Blocked", "Blocked")
    teams.start_task(task.id, worker.id)
    teams.finish_task(task.id, worker.id, "blocked", error_message="need input")
    assert teams.get_agent(worker.id).status == "waiting"
    teams._db.execute(  # Pre-fix rows persisted an illegal 'blocked' agent status.
        "update team_agents set status = 'blocked', finished_at = ? where id = ?",
        ("2026-08-06T00:00:00+00:00", leader.id),
    )

    teams.reset_agents_for_new_cycle(run.id)

    by_id = {agent.id: agent for agent in teams.list_agents(run.id)}
    assert by_id[worker.id].status == "pending"
    assert by_id[worker.id].current_task_id is None
    assert by_id[worker.id].finished_at is None
    assert by_id[leader.id].status == "pending"
    assert by_id[leader.id].finished_at is None


def test_reset_agents_for_new_cycle_only_clears_terminal_agents(tmp_path) -> None:
    _db, teams, _cycles, run = make_cycle_services(tmp_path, "triggered")
    leader = teams.get_agent(run.leader_agent_id)
    worker = next(
        candidate
        for candidate in teams.list_agents(run.id)
        if candidate.id != run.leader_agent_id
    )

    task_one = teams.create_task(run.id, "Task One", "Task One")
    teams.start_task(task_one.id, leader.id)
    teams.set_agent_status(leader.id, "completed")
    teams._db.execute(  # Simulate a stale task assignment carried over from the previous cycle.
        "update team_agents set current_task_id = ? where id = ?",
        (task_one.id, leader.id),
    )
    teams.set_agent_status(worker.id, "running")
    before_reset = teams.get_agent(leader.id)
    assert before_reset.current_task_id == task_one.id
    assert before_reset.finished_at is not None

    teams.reset_agents_for_new_cycle(run.id)
    by_id = {agent.id: agent for agent in teams.list_agents(run.id)}
    assert by_id[leader.id].status == "pending"
    assert by_id[leader.id].current_task_id is None
    assert by_id[leader.id].finished_at is None
    assert by_id[worker.id].status == "running"

    task_two = teams.create_task(run.id, "Task Two", "Task Two")
    teams.start_task(task_two.id, leader.id)
    teams.set_agent_status(leader.id, "failed")
    teams._db.execute(  # Simulate a stale task assignment carried over from the previous cycle.
        "update team_agents set current_task_id = ? where id = ?",
        (task_two.id, leader.id),
    )
    # "waiting" is a terminal parking state for an agent whose task ended
    # blocked, so a new cycle must clear it as well.
    teams.set_agent_status(worker.id, "waiting")
    before_reset = teams.get_agent(leader.id)
    assert before_reset.current_task_id == task_two.id
    assert before_reset.finished_at is not None

    teams.reset_agents_for_new_cycle(run.id)
    by_id = {agent.id: agent for agent in teams.list_agents(run.id)}
    assert by_id[leader.id].status == "pending"
    assert by_id[leader.id].current_task_id is None
    assert by_id[leader.id].finished_at is None
    assert by_id[worker.id].status == "pending"
