import sqlite3
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.migrations import (
    LATEST_SCHEMA_VERSION,
    _migration_6_team_run_cycles,
)


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
    assert {row["name"] for row in db.fetchall(
        "pragma table_info(transcript_metadata)"
    )} >= {"origin", "hook_run_id"}
    assert db.fetchone(
        "select name from sqlite_master "
        "where name = 'idx_transcript_metadata_origin_updated'"
    ) is not None
    assert db.fetchone(
        "select name from sqlite_master where name = 'team_decision_requests'"
    ) is not None
    assert db.fetchone(
        "select name from sqlite_master where name = 'team_run_cycles'"
    ) is not None
    assert db.fetchone("select lifecycle_mode from team_runs limit 1") is None
    assert {row["name"] for row in db.fetchall("pragma table_info(hooks)")} >= {
        "target_kind",
        "target_team_run_id",
    }
    assert {row["name"] for row in db.fetchall("pragma table_info(hook_runs)")} >= {
        "team_run_cycle_id"
    }
    assert db.fetchone(
        "select name from sqlite_master where name = 'mail_messages'"
    ) is not None
    assert db.fetchone(
        "select name from sqlite_master where name = 'mail_contacts'"
    ) is not None


def test_team_run_cycle_migration_preserves_existing_team_records() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table team_runs (id text primary key);
        insert into team_runs (id) values ('run-1');

        create table team_tasks (
            id text primary key, team_run_id text not null, status text not null,
            created_at text not null
        );
        insert into team_tasks values ('task-1', 'run-1', 'pending', 't');

        create table team_messages (
            id text primary key, team_run_id text not null, created_at text not null
        );
        insert into team_messages values ('message-1', 'run-1', 't');

        create table team_decision_requests (
            id text primary key, team_run_id text not null, status text not null
        );
        insert into team_decision_requests values ('decision-1', 'run-1', 'resolved');

        create unique index idx_team_decision_requests_active
        on team_decision_requests(team_run_id)
        where status in ('collecting', 'awaiting_user');
        """
    )

    _migration_6_team_run_cycles(connection)

    assert connection.execute(
        "select lifecycle_mode from team_runs where id = 'run-1'"
    ).fetchone()["lifecycle_mode"] == "standard"
    for table, record_id in (
        ("team_tasks", "task-1"),
        ("team_messages", "message-1"),
        ("team_decision_requests", "decision-1"),
    ):
        row = connection.execute(
            f"select cycle_id from {table} where id = ?", (record_id,)
        ).fetchone()
        assert row["cycle_id"] is None
    assert connection.execute(
        "select name from sqlite_master where name = 'team_run_cycles'"
    ).fetchone() is not None


def test_schema_v5_database_reaches_latest_before_cycle_indexes_are_created(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schema-v5.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table schema_migrations (
            version integer primary key, name text not null, applied_at text not null
        );
        insert into schema_migrations values (1, 'v1', 't');
        insert into schema_migrations values (2, 'v2', 't');
        insert into schema_migrations values (3, 'v3', 't');
        insert into schema_migrations values (4, 'v4', 't');
        insert into schema_migrations values (5, 'v5', 't');

        create table team_runs (id text primary key);
        insert into team_runs values ('run-1');
        create table team_tasks (
            id text primary key, team_run_id text not null,
            status text not null, created_at text not null
        );
        create table team_messages (
            id text primary key, team_run_id text not null, created_at text not null
        );
        create table team_decision_requests (
            id text primary key, team_run_id text not null, status text not null
        );
        create table hooks (id text primary key);
        create table hook_runs (id text primary key);
        """
    )
    connection.commit()
    connection.close()

    db = Database(path)
    db.initialize()

    assert db.schema_version() == LATEST_SCHEMA_VERSION
    assert {row["name"] for row in db.fetchall("pragma table_info(team_tasks)")} >= {
        "cycle_id"
    }
    assert db.fetchone(
        "select name from sqlite_master "
        "where name = 'idx_team_tasks_run_cycle_status_created'"
    ) is not None
