from pathlib import Path

import pytest

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.teams import TeamRunService


def archive_service(tmp_path: Path) -> tuple[ArchiveService, PersonaService]:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    return ArchiveService(db), PersonaService(db)


def test_user_publish_creates_revision_and_searchable_persona_binding(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona(
        "Researcher",
        "Reference checking",
        "Checks primary sources.",
        [],
        [],
    )

    entry = archive.publish_entry(
        actor_type="user",
        kind="search_method",
        title="Primary-source verification",
        summary="Check implementation claims against primary sources.",
        content_markdown="Prefer official documentation and original papers.",
        tags=["sources", "verification"],
        source_urls=["https://example.com/reference"],
        persona_ids=[persona.id],
    )

    assert entry.current_revision == 1
    assert entry.persona_ids == [persona.id]
    assert [item.id for item in archive.search_entries("official", persona_id=persona.id)] == [
        entry.id
    ]
    assert archive.search_entries("official", persona_id=None) == []

    updated = archive.publish_entry(
        actor_type="user",
        entry_id=entry.id,
        kind=entry.kind,
        title=entry.title,
        summary=entry.summary,
        content_markdown=entry.content_markdown + "\nRecord the checked version.",
        tags=entry.tags,
        source_urls=entry.source_urls,
        persona_ids=[persona.id],
        change_summary="Add version recording",
    )

    assert updated.current_revision == 2
    assert [revision.revision for revision in archive.list_revisions(entry.id)] == [2, 1]


def test_non_user_cannot_publish_canonical_archive_entry(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)

    with pytest.raises(ValueError, match="Only the user"):
        archive.publish_entry(
            actor_type="persona",
            kind="procedure",
            title="Unreviewed procedure",
            summary="",
            content_markdown="Do this automatically.",
            tags=[],
            source_urls=[],
            persona_ids=[],
        )


def test_persona_request_is_a_gap_not_retrievable_knowledge(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Builder", "Implementation", "", [], [])

    request = archive.create_knowledge_request(
        title="Deployment rollback checklist",
        reason="The current workspace has no reusable rollback procedure.",
        suggested_outline=["Preconditions", "Rollback", "Verification"],
        source_hints=["release runbook"],
        requested_by_persona_id=persona.id,
        session_id="session-1",
    )

    assert request.status == "open"
    assert archive.search_entries("rollback", persona_id=persona.id) == []


def test_publishing_from_request_fulfills_it_without_changing_authorship(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Builder", "Implementation", "", [], [])
    request = archive.create_knowledge_request(
        title="Deployment rollback checklist",
        reason="Reusable guidance is missing.",
        suggested_outline=["Rollback", "Verification"],
        source_hints=[],
        requested_by_persona_id=persona.id,
    )

    entry = archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title=request.title,
        summary="A verified rollback sequence.",
        content_markdown="- Roll back\n- Verify",
        tags=["deployment"],
        source_urls=[],
        persona_ids=[persona.id],
        request_id=request.id,
    )

    fulfilled = archive.get_request(request.id)
    assert fulfilled.status == "fulfilled"
    assert fulfilled.fulfilled_by_entry_id == entry.id
    assert entry.created_by == "user"


def test_duplicate_open_request_is_reused(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Builder", "Implementation", "", [], [])

    first = archive.create_knowledge_request(
        title="  Release checklist ",
        reason="Missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=persona.id,
    )
    second = archive.create_knowledge_request(
        title="release CHECKLIST",
        reason="Still missing.",
        suggested_outline=["Steps"],
        source_hints=[],
        requested_by_persona_id=persona.id,
    )

    assert second.id == first.id
    assert len(archive.list_requests()) == 1


def test_team_draft_is_idempotent_and_not_searchable_until_user_publish(
    tmp_path: Path,
) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Researcher", "Sources", "", [], [])

    draft = archive.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-run-1",
        kind="reference",
        title="Provider release reference",
        summary="Checked release behavior.",
        content_markdown="Use the provider's release endpoint.",
        tags=["release"],
        source_urls=["https://example.com/releases"],
        persona_ids=[persona.id],
    )
    duplicate = archive.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-run-1",
        kind="reference",
        title="Ignored duplicate",
        summary="",
        content_markdown="This must not create another revision.",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )

    assert duplicate.id == draft.id
    assert draft.status == "draft"
    assert draft.created_by == "team"
    assert draft.origin_source_type == "hook"
    assert draft.origin_source_id == "hook-run-1"
    assert [entry.id for entry in archive.list_entries(status="draft")] == [draft.id]
    assert archive.search_entries("provider", persona_id=persona.id) == []
    assert [revision.created_by for revision in archive.list_revisions(draft.id)] == [
        "team"
    ]

    published = archive.publish_entry(
        actor_type="user",
        entry_id=draft.id,
        kind=draft.kind,
        title=draft.title,
        summary=draft.summary,
        content_markdown=draft.content_markdown,
        tags=draft.tags,
        source_urls=draft.source_urls,
        persona_ids=draft.persona_ids,
        change_summary="Reviewed and approved",
    )

    assert published.status == "published"
    assert published.current_revision == 2
    assert published.origin_source_type == "hook"
    assert [entry.id for entry in archive.search_entries(
        "provider",
        persona_id=persona.id,
    )] == [draft.id]
    assert [revision.created_by for revision in archive.list_revisions(draft.id)] == [
        "user",
        "team",
    ]


def _documentation_team_run(tmp_path: Path, db: Database, personas: PersonaService):
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Library Lead", "Editor", "", [], [])
    return teams.create_team_run(
        "Prepare reviewed Library drafts",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )


def test_record_draft_failure_reopens_request_and_stores_the_reason(tmp_path: Path) -> None:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    archive = ArchiveService(db)
    personas = PersonaService(db)
    team_run = _documentation_team_run(tmp_path, db, personas)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=["Signals", "Steps"],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.assign_request_team(request.id, team_run.id)

    failed = archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="Team response must contain exactly one Library Draft marker",
        cycle_id="cycle-1",
    )

    assert failed.status == "open"
    assert failed.last_draft_error_code == "draft_contract_violation"
    assert failed.last_draft_error_message == (
        "Team response must contain exactly one Library Draft marker"
    )
    assert failed.last_draft_cycle_id == "cycle-1"
    assert failed.last_draft_failed_at
    assert archive.get_request(request.id).last_draft_error_code == (
        "draft_contract_violation"
    )


def test_record_draft_failure_truncates_the_message(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )

    failed = archive.record_draft_failure(
        request.id,
        error_code="draft_invalid_payload",
        message="x" * 900,
        cycle_id=None,
    )

    assert failed.last_draft_error_message == "x" * 500
    assert failed.last_draft_cycle_id is None


def test_fulfilled_request_cannot_record_a_draft_failure(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title="Rollback checklist",
        summary="Verified rollback steps.",
        content_markdown="Stop traffic, then roll back.",
        tags=[],
        source_urls=[],
        persona_ids=[],
        request_id=request.id,
    )

    with pytest.raises(ValueError):
        archive.record_draft_failure(
            request.id,
            error_code="draft_save_failed",
            message="too late",
            cycle_id=None,
        )


def test_record_draft_failure_is_idempotent_for_the_same_code_and_cycle(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    archive = ArchiveService(db)
    personas = PersonaService(db)
    team_run = _documentation_team_run(tmp_path, db, personas)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.assign_request_team(request.id, team_run.id)

    first = archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )
    archive.update_request_status(request.id, "deferred")

    second = archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker, reported again on restart",
        cycle_id="cycle-1",
    )

    assert second.status == "deferred"
    assert second.last_draft_failed_at == first.last_draft_failed_at
    assert second.last_draft_error_message == first.last_draft_error_message
    assert archive.get_request(request.id).status == "deferred"


def test_record_draft_failure_records_again_for_a_new_cycle_or_code(
    tmp_path: Path,
) -> None:
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )

    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )
    archive.update_request_status(request.id, "deferred")

    reopened_for_new_cycle = archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker on retry",
        cycle_id="cycle-2",
    )

    assert reopened_for_new_cycle.status == "open"
    assert reopened_for_new_cycle.last_draft_cycle_id == "cycle-2"
    assert reopened_for_new_cycle.last_draft_error_message == "no marker on retry"

    archive.update_request_status(request.id, "deferred")

    reopened_for_new_code = archive.record_draft_failure(
        request.id,
        error_code="draft_save_failed",
        message="save failed",
        cycle_id="cycle-2",
    )

    assert reopened_for_new_code.status == "open"
    assert reopened_for_new_code.last_draft_error_code == "draft_save_failed"


def test_clear_and_redelegation_remove_the_recorded_failure(tmp_path: Path) -> None:
    db = Database(tmp_path / "gateway.db")
    db.initialize()
    archive = ArchiveService(db)
    personas = PersonaService(db)
    team_run = _documentation_team_run(tmp_path, db, personas)
    request = archive.create_knowledge_request(
        title="Rollback checklist",
        reason="Reusable rollback guidance is missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )

    cleared = archive.clear_draft_failure(request.id)

    assert cleared.last_draft_error_code is None
    assert cleared.last_draft_error_message is None
    assert cleared.last_draft_failed_at is None
    assert cleared.last_draft_cycle_id is None

    archive.record_draft_failure(
        request.id,
        error_code="draft_contract_violation",
        message="no marker",
        cycle_id="cycle-1",
    )
    reassigned = archive.assign_request_team(request.id, team_run.id)

    assert reassigned.status == "in_progress"
    assert reassigned.last_draft_error_code is None
    assert reassigned.last_draft_failed_at is None


def test_delete_entry_removes_published_document_and_its_traces(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Researcher", "Reference checking", "Checks sources.", [], [])
    entry = archive.publish_entry(
        actor_type="user",
        kind="reference",
        title="Rollback checklist",
        summary="A verified rollback sequence.",
        content_markdown="- Roll back\n- Verify",
        tags=["deployment"],
        source_urls=[],
        persona_ids=[persona.id],
    )

    status = archive.delete_entry(entry.id)

    assert status == "published"
    assert archive.list_entries(status="published") == []
    assert archive.list_entries(query="rollback", status="published") == []
    with pytest.raises(KeyError):
        archive.get_entry(entry.id)


def test_delete_entry_reopens_the_request_the_published_document_fulfilled(tmp_path: Path) -> None:
    """The fulfilled link lives on knowledge_requests.fulfilled_by_entry_id, not on
    archive_draft_origins — the pre-existing delete path never touched it, so a deleted
    document used to leave a fulfilled request with no supporting document."""
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Researcher", "Reference checking", "Checks sources.", [], [])
    request = archive.create_knowledge_request(
        title="Deployment rollback checklist",
        reason="Reusable guidance is missing.",
        suggested_outline=["Rollback", "Verification"],
        source_hints=[],
        requested_by_persona_id=persona.id,
    )
    entry = archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title=request.title,
        summary="A verified rollback sequence.",
        content_markdown="- Roll back\n- Verify",
        tags=[],
        source_urls=[],
        persona_ids=[persona.id],
        request_id=request.id,
    )
    assert archive.get_request(request.id).status == "fulfilled"

    archive.delete_entry(entry.id)

    reopened = archive.get_request(request.id)
    assert reopened.status == "open"
    assert reopened.fulfilled_by_entry_id is None
    assert reopened.assigned_team_run_id is None


def test_delete_entry_still_reopens_an_in_progress_draft_origin(tmp_path: Path) -> None:
    """Regression: the draft-origin path must keep working exactly as before."""
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback guidance",
        reason="Missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    draft = archive.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-1",
        kind="reference",
        title="Draft",
        summary="",
        content_markdown="# Draft",
        tags=[],
        source_urls=[],
        persona_ids=[],
        origin_request_id=request.id,
    )

    status = archive.delete_entry(draft.id)

    assert status == "draft"
    assert archive.get_request(request.id).status == "open"


def test_delete_entry_removes_an_archived_document(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    entry = archive.publish_entry(
        actor_type="user",
        kind="reference",
        title="Old guidance",
        summary="Superseded.",
        content_markdown="# Old",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )
    archive.archive_entry(entry.id)

    status = archive.delete_entry(entry.id)

    assert status == "archived"
    with pytest.raises(KeyError):
        archive.get_entry(entry.id)


def test_delete_entry_rejects_an_unknown_id(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)

    with pytest.raises(KeyError):
        archive.delete_entry("nope")
