import sqlite3
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.migrations import LATEST_SCHEMA_VERSION


def _versions(db: Database) -> list[int]:
    return [
        row["version"]
        for row in db.fetchall("select version from schema_migrations order by version")
    ]


def test_empty_database_reaches_latest_schema_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")

    db.initialize()
    first_versions = _versions(db)
    db.initialize()

    assert first_versions == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert _versions(db) == first_versions
    assert db.schema_version() == LATEST_SCHEMA_VERSION


def test_legacy_database_preserves_rows_while_reaching_latest_schema(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "create table personas ("
        "id text primary key, name text not null, role text not null, "
        "description text not null, responsibilities_json text not null, "
        "constraints_json text not null, default_backend text not null, "
        "default_model text not null, created_at text not null, updated_at text not null)"
    )
    connection.execute(
        "insert into personas values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("p1", "Legacy", "role", "description", "[]", "[]", "codex", "default", "t", "t"),
    )
    connection.commit()
    connection.close()

    db = Database(path)
    db.initialize()

    assert db.schema_version() == LATEST_SCHEMA_VERSION
    assert db.fetchone("select name from personas where id = 'p1'")["name"] == "Legacy"
    assert {row["name"] for row in db.fetchall("pragma table_info(personas)")} >= {
        "avatar",
        "default_options_json",
    }
    assert {row["name"] for row in db.fetchall("pragma table_info(jobs)")} >= {
        "source_job_id",
    }
    assert db.fetchone("select name from sqlite_master where name = 'audit_events'") is not None
    assert db.fetchone(
        "select name from sqlite_master where name = 'transcript_metadata'"
    ) is not None
    assert db.fetchone(
        "select name from sqlite_master where name = 'idx_transcript_metadata_updated'"
    ) is not None
