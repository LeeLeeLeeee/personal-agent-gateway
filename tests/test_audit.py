from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.audit import AuditService
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.db import Database


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-key",
    )


def test_audit_is_append_only_and_redacts_sensitive_body_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    audit = AuditService(db)

    event = audit.record(
        event_type="job.failed",
        action="job.run",
        status="failed",
        correlation_id="corr-1",
        command_preview="run --token top-secret",
        metadata={
            "job_id": "job-1",
            "prompt": "private prompt",
            "stdout": "raw output",
            "api_key": "top-secret",
            "safe_count": 2,
        },
        secrets=["top-secret"],
    )

    stored = audit.get(event.id)
    assert stored.correlation_id == "corr-1"
    assert stored.metadata == {"job_id": "job-1", "safe_count": 2}
    assert "top-secret" not in (stored.command_preview or "")
    assert not hasattr(audit, "update")
    assert not hasattr(audit, "delete")


def test_audit_api_requires_session_and_filters_events(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)
    app.state.audit_service.record(
        event_type="security.emergency_stop",
        action="operations.stop",
        status="succeeded",
        severity="critical",
        correlation_id="corr-stop",
        resource_type="gateway",
        resource_id="local",
    )

    assert client.get("/api/audit/events").status_code == 401
    client.cookies.set("agent_session", app.state.auth_session_service.issue().token)
    response = client.get(
        "/api/audit/events",
        params={"severity": "critical", "correlation_id": "corr-stop"},
    )

    assert response.status_code == 200
    assert [item["event_type"] for item in response.json()["events"]] == [
        "security.emergency_stop"
    ]
    assert response.json()["next_cursor"] is None
    assert client.get("/api/audit/events", params={"cursor": "invalid"}).status_code == 400


def test_audit_cursor_pages_are_stable_and_do_not_duplicate(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    audit = AuditService(db)
    events = [
        audit.record(event_type=f"event-{index}", action="test", status="success")
        for index in range(3)
    ]
    for event in events:
        db.execute(
            "update audit_events set occurred_at = ? where id = ?",
            ("2026-07-15T00:00:00+00:00", event.id),
        )

    first, cursor = audit.page(limit=2)
    second, next_cursor = audit.page(limit=2, cursor=cursor)

    assert cursor is not None
    assert next_cursor is None
    assert len(first) == 2
    assert len(second) == 1
    assert {event.id for event in [*first, *second]} == {event.id for event in events}


def test_audit_default_read_window_excludes_events_older_than_retention(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    audit = AuditService(db, retention_days=90)
    old = audit.record(event_type="old", action="test", status="success")
    db.execute(
        "update audit_events set occurred_at = ? where id = ?",
        ("2000-01-01T00:00:00+00:00", old.id),
    )
    recent = audit.record(event_type="recent", action="test", status="success")

    assert [event.id for event in audit.list()] == [recent.id]
    assert {event.id for event in audit.list(since="1990-01-01T00:00:00+00:00")} == {
        old.id,
        recent.id,
    }
