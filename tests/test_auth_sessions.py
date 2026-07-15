from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_agent_gateway.auth_sessions import AuthSessionService
from personal_agent_gateway.db import Database


def make_service(tmp_path: Path) -> tuple[Database, AuthSessionService]:
    database = Database(tmp_path / "app.sqlite")
    database.initialize()
    return database, AuthSessionService(
        database,
        absolute_ttl_seconds=3600,
        idle_ttl_seconds=600,
    )


def test_issue_stores_only_a_token_hash_and_validates_principal(tmp_path: Path) -> None:
    database, service = make_service(tmp_path)
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)

    issued = service.issue(now=now)
    row = database.fetchone("select * from auth_sessions where id = ?", (issued.principal.id,))

    assert row is not None
    assert row["token_hash"] != issued.token
    assert issued.token not in tuple(str(value) for value in row)
    assert service.validate(issued.token, now=now) == issued.principal


def test_validate_slides_idle_expiry_without_extending_absolute_expiry(tmp_path: Path) -> None:
    _database, service = make_service(tmp_path)
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    issued = service.issue(now=now)

    refreshed = service.validate(issued.token, now=now + timedelta(minutes=5))

    assert refreshed is not None
    assert refreshed.last_seen_at == now + timedelta(minutes=5)
    assert refreshed.idle_expires_at == now + timedelta(minutes=15)
    assert refreshed.absolute_expires_at == now + timedelta(hours=1)
    assert service.validate(issued.token, now=now + timedelta(minutes=16)) is None


def test_validate_rejects_absolute_expiry_even_after_recent_use(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite")
    database.initialize()
    service = AuthSessionService(
        database,
        absolute_ttl_seconds=3600,
        idle_ttl_seconds=7200,
    )
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    issued = service.issue(now=now)

    assert service.validate(issued.token, now=now + timedelta(minutes=59)) is not None
    assert service.validate(issued.token, now=now + timedelta(minutes=61)) is None


def test_revoke_and_revoke_all_invalidate_existing_tokens(tmp_path: Path) -> None:
    _database, service = make_service(tmp_path)
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    first = service.issue(now=now)
    second = service.issue(now=now)

    assert service.revoke(first.token, now=now + timedelta(seconds=1)) is True
    assert service.validate(first.token, now=now + timedelta(seconds=2)) is None
    assert service.validate(second.token, now=now + timedelta(seconds=2)) is not None

    assert service.revoke_all(now=now + timedelta(seconds=3)) == 1
    assert service.validate(second.token, now=now + timedelta(seconds=4)) is None
