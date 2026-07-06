from pathlib import Path

from personal_agent_gateway.db import Database


def test_database_initializes_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    tables = {
        row["name"]
        for row in db.fetchall(
            "select name from sqlite_master where type = 'table'",
        )
    }

    assert "jobs" in tables
    assert "job_events" in tables
    assert "approvals" in tables
    assert "artifacts" in tables
    assert "schedules" in tables


def test_database_uses_row_factory_and_foreign_keys(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    row = db.fetchone("pragma foreign_keys")

    assert row is not None
    assert row["foreign_keys"] == 1
