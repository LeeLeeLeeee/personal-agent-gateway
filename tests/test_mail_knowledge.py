from dataclasses import replace
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService
from personal_agent_gateway.mail_knowledge import (
    MailKnowledgeService,
    MailWorkspaceProjector,
)
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.teams import TeamRunService


def _setup(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Mail lead", "lead", "d", [], [])
    team_run = teams.create_team_run(
        "mailbox",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    hooks = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": object()})
    hook = hooks.create_hook(
        name="mail",
        source_type="email",
        connection={},
        secret="pw",
        filter={},
        target_backend="",
        target_model="",
        target_options={},
        prompt_template="{{subject}}",
        poll_interval_seconds=300,
        target_kind="team_run",
        target_team_run_id=team_run.id,
    )
    runs = HookRunService(db)
    knowledge = MailKnowledgeService(db)
    projector = MailWorkspaceProjector(knowledge)
    return db, teams, team_run, hook, runs, knowledge, projector


def _ingest(tmp_path: Path, dedup_key: str = "mail-1"):
    db, teams, team_run, hook, runs, knowledge, projector = _setup(tmp_path)
    run = runs.create_run(
        hook.id,
        dedup_key,
        "mail",
        {
            "from": "Boss <Boss@Example.com>",
            "subject": "Quarterly <script>alert(1)</script>",
            "date": "Thu, 16 Jul 2026 09:00:00 +0900",
            "body_text": "<script>do not execute</script>",
        },
    )
    assert run is not None
    cycle = teams.create_cycle(team_run.id, "hook", run.id)
    runs.link_cycle(run.id, cycle.id)
    message = knowledge.ingest_hook_run(hook, run, team_run, cycle.id)
    return (
        db,
        teams,
        team_run,
        hook,
        runs,
        knowledge,
        projector,
        run,
        cycle,
        message,
    )


def test_ingest_and_project_mail_and_contact(tmp_path: Path) -> None:
    *_, team_run, _hook, _runs, knowledge, projector, _run, _cycle, message = _ingest(
        tmp_path
    )

    projected = projector.project_safely(message)

    assert projected.projection_status == "projected"
    message_root = Path(team_run.workspace_root) / message.archive_relative_path
    mail_text = (message_root / "MAIL.md").read_text(encoding="utf-8")
    assert "&lt;script&gt;do not execute&lt;/script&gt;" in mail_text
    assert "<script>do not execute</script>" not in mail_text
    assert "Quarterly &lt;script&gt;alert(1)&lt;/script&gt;" in mail_text
    assert "Quarterly <script>alert(1)</script>" not in mail_text
    assert (message_root / "RESULT.md").exists()
    assert (message_root / "META.json").exists()
    contacts = knowledge.list_contacts(team_run.id)
    assert len(contacts) == 1
    assert contacts[0].canonical_address == "boss@example.com"
    assert contacts[0].message_count == 1


def test_ingest_is_idempotent_and_does_not_double_count_contact(tmp_path: Path) -> None:
    (
        _db,
        _teams,
        team_run,
        hook,
        _runs,
        knowledge,
        _projector,
        run,
        cycle,
        first,
    ) = _ingest(tmp_path)

    duplicate = knowledge.ingest_hook_run(hook, run, team_run, cycle.id)

    assert duplicate.id == first.id
    assert knowledge.list_contacts(team_run.id)[0].message_count == 1


def test_projection_preserves_user_notes_and_updates_result(tmp_path: Path) -> None:
    *_, team_run, _hook, _runs, knowledge, projector, _run, cycle, message = _ingest(
        tmp_path
    )
    projector.project_safely(message)
    context = (
        Path(team_run.workspace_root)
        / "CYCLES"
        / cycle.id
        / "MAIL_CONTEXT.md"
    )
    original_context = context.read_text(encoding="utf-8")
    notes = next((Path(team_run.workspace_root) / "MAIL" / "CONTACTS").glob("*/NOTES.md"))
    notes.write_text("# Notes\n\nImportant customer.\n", encoding="utf-8")

    completed = knowledge.complete_cycle(cycle.id, "Reply by Friday")
    assert completed is not None
    projector.project_safely(completed)

    assert "Important customer." in notes.read_text(encoding="utf-8")
    assert context.read_text(encoding="utf-8") == original_context
    result_path = Path(team_run.workspace_root) / message.archive_relative_path / "RESULT.md"
    assert "Reply by Friday" in result_path.read_text(encoding="utf-8")


def test_failed_projection_is_retryable(tmp_path: Path, monkeypatch) -> None:
    *_, knowledge, projector, _run, _cycle, message = _ingest(tmp_path)

    def fail_write(_path, _content):
        raise OSError("locked")

    monkeypatch.setattr("personal_agent_gateway.mail_knowledge._atomic_write", fail_write)
    failed = projector.project_safely(message)
    assert failed.projection_status == "failed"
    assert failed.projection_error == "locked"

    monkeypatch.undo()
    retried = projector.project_pending()

    assert len(retried) == 1
    assert retried[0].projection_status == "projected"


def test_changed_mail_context_is_detected_instead_of_overwritten(tmp_path: Path) -> None:
    (
        _db,
        _teams,
        team_run,
        _hook,
        _runs,
        _knowledge,
        projector,
        _run,
        cycle,
        message,
    ) = _ingest(tmp_path)
    projector.project_safely(message)
    context = (
        Path(team_run.workspace_root)
        / "CYCLES"
        / cycle.id
        / "MAIL_CONTEXT.md"
    )
    context.write_text("tampered\n", encoding="utf-8")

    failed = projector.project_safely(message)

    assert failed.projection_status == "failed"
    assert "Immutable generated file changed" in failed.projection_error
    assert context.read_text(encoding="utf-8") == "tampered\n"


def test_mail_context_path_cannot_escape_team_workspace(tmp_path: Path) -> None:
    (
        _db,
        _teams,
        team_run,
        _hook,
        _runs,
        _knowledge,
        projector,
        _run,
        _cycle,
        message,
    ) = _ingest(tmp_path)
    escaped = replace(message, team_run_cycle_id="../../outside")

    failed = projector.project_safely(escaped)

    assert failed.projection_status == "failed"
    assert "escapes workspace root" in failed.projection_error
    assert not (Path(team_run.workspace_root).parent / "outside").exists()
