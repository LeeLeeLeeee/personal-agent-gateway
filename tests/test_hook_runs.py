from datetime import datetime, timezone
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRunService


def _service(tmp_path: Path) -> HookRunService:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    db.execute(
        "insert into hooks (id, name, source_type, connection_ref, filter_json, "
        "target_backend, target_model, target_options_json, prompt_template, "
        "poll_interval_seconds, enabled, created_at, updated_at) "
        "values ('h1','n','email','c','{}','codex','default','{}','t',300,1,'t','t')"
    )
    return HookRunService(db)


def test_create_run_returns_queued_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "email:1:2", "메일: hi — a@b", {"subject": "hi"})
    assert run is not None
    assert run.status == "queued"
    assert run.hook_id == "h1"
    assert run.dedup_key == "email:1:2"
    assert run.trigger_payload == {"subject": "hi"}


def test_create_run_dedup_returns_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.create_run("h1", "email:1:2", "s", {})
    second = service.create_run("h1", "email:1:2", "s", {})
    assert first is not None
    assert second is None
    assert len(service.list_runs("h1")) == 1


def test_status_transitions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    succeeded = service.mark_succeeded(run.id, "done")
    assert succeeded.status == "succeeded"
    assert succeeded.result_text == "done"
    assert succeeded.started_at is not None
    assert succeeded.finished_at is not None


def test_mark_failed_records_message(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    failed = service.mark_failed(run.id, "boom")
    assert failed.status == "failed"
    assert failed.error_message == "boom"


def test_recover_interrupted_marks_running_as_failed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service.create_run("h1", "k", "s", {})
    assert run is not None
    service.mark_running(run.id)
    service.recover_interrupted_runs()
    assert service.get_run(run.id).status == "failed"


def test_list_queued_runs_excludes_other_statuses_and_orders_by_created_at(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = service.create_run("h1", "k1", "s", {}, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert first is not None
    running = service.create_run(
        "h1", "k2", "s", {}, now=datetime(2024, 1, 2, tzinfo=timezone.utc)
    )
    assert running is not None
    service.mark_running(running.id)
    second = service.create_run("h1", "k3", "s", {}, now=datetime(2024, 1, 3, tzinfo=timezone.utc))
    assert second is not None

    queued = service.list_queued_runs()

    assert [run.id for run in queued] == [first.id, second.id]
    assert all(run.status == "queued" for run in queued)


def test_hook_run_links_exactly_one_team_run_cycle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    db = service._db
    db.execute(
        "insert into team_runs (id, goal, status, run_mode, lifecycle_mode, max_workers, "
        "workspace_root, created_at, updated_at) "
        "values ('team-run','mailbox','draft','plan_and_execute','continuous',1,'w','t','t')"
    )
    db.execute(
        "insert into team_run_cycles (id, team_run_id, sequence, source_type, source_id, "
        "status, rounds_budget, created_at, updated_at) "
        "values ('cycle-1','team-run',1,'hook','source-1','queued',8,'t','t')"
    )
    run = service.create_run("h1", "k", "s", {})
    assert run is not None

    linked = service.link_cycle(run.id, "cycle-1")
    duplicate = service.link_cycle(run.id, "cycle-1")

    assert linked.team_run_cycle_id == "cycle-1"
    assert duplicate.team_run_cycle_id == "cycle-1"
