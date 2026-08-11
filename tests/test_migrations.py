import sqlite3
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.migrations import (
    LATEST_SCHEMA_VERSION,
    _migration_6_team_run_cycles,
    _migration_11_team_cycle_policies,
    _migration_16_explicit_no_source_space,
    _migration_17_team_task_acceptance,
    _migration_18_team_cycle_space_snapshot,
    _migration_19_team_acceptance_recovery,
    _migration_20_team_model_operations,
    _migration_21_knowledge_request_draft_failure,
    _migration_29_team_run_workspace_inheritance,
)


def test_migration_20_creates_team_model_operation_ledger_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    connection.executescript(
        """
        create table team_runs (id text primary key);
        create table team_run_cycles (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        create table team_tasks (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        create table team_agents (
            id text primary key,
            team_run_id text not null references team_runs(id)
        );
        """
    )

    _migration_20_team_model_operations(connection)
    _migration_20_team_model_operations(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(team_model_operations)")
    }
    assert {
        "operation_key",
        "stage",
        "status",
        "version",
        "attempts",
        "consumer_run_id",
        "result_json",
        "effect_ref_json",
    } <= columns
    assert any(
        row["name"] == "idx_team_model_operations_one_open_cycle"
        for row in connection.execute(
            "select name from sqlite_master where type = 'index'"
        )
    )


def test_migration_21_adds_knowledge_request_draft_failure_columns_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table knowledge_requests (
            id text primary key,
            status text not null,
            created_at text not null,
            updated_at text not null
        );
        """
    )

    _migration_21_knowledge_request_draft_failure(connection)
    _migration_21_knowledge_request_draft_failure(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(knowledge_requests)")
    }
    assert {
        "last_draft_error_code",
        "last_draft_error_message",
        "last_draft_failed_at",
        "last_draft_cycle_id",
    } <= columns
    assert LATEST_SCHEMA_VERSION == 30


def test_migration_18_adds_nullable_cycle_space_snapshot() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table team_run_cycles (id text primary key)")

    _migration_18_team_cycle_space_snapshot(connection)

    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(team_run_cycles)")
    }
    assert "space_policy_snapshot_json" in columns


def test_migration_19_adds_acceptance_recovery_counter_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table team_tasks (id text primary key)")
    connection.execute("insert into team_tasks values ('task-1')")

    _migration_19_team_acceptance_recovery(connection)
    _migration_19_team_acceptance_recovery(connection)

    row = connection.execute(
        "select acceptance_recovery_attempts from team_tasks where id = 'task-1'"
    ).fetchone()
    assert row["acceptance_recovery_attempts"] == 0


def test_migration_16_only_rewrites_home_isolated_policies() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        create table space_policies (
            scope text not null,
            scope_id text not null,
            read_mode text not null,
            read_path text,
            write_mode text not null,
            workspace_path text,
            created_at text not null,
            updated_at text not null
        )
        """
    )
    rows = [
        ("global", "", "home", "/home/me", "isolated", None, "t", "t"),
        ("persona", "p1", "home", "/home/me", "full_access", "/project", "t", "t"),
        ("team", "t1", "home", "/home/me", "worktree", "/repo", "t", "t"),
    ]
    connection.executemany(
        "insert into space_policies values (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    _migration_16_explicit_no_source_space(connection)

    migrated = connection.execute(
        "select scope, read_mode, read_path from space_policies order by scope"
    ).fetchall()
    assert [tuple(row) for row in migrated] == [
        ("global", "none", None),
        ("persona", "home", "/home/me"),
        ("team", "home", "/home/me"),
    ]


def test_migration_17_preserves_historic_status_and_adds_acceptance_fields() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table team_tasks (
            id text primary key,
            status text not null
        );
        create table team_run_cycles (
            id text primary key,
            status text not null
        );
        insert into team_tasks values ('task-1', 'completed');
        insert into team_run_cycles values ('cycle-1', 'completed');
        """
    )

    _migration_17_team_task_acceptance(connection)
    _migration_17_team_task_acceptance(connection)

    task_columns = {
        row["name"]: row for row in connection.execute("pragma table_info(team_tasks)")
    }
    cycle_columns = {
        row["name"]: row
        for row in connection.execute("pragma table_info(team_run_cycles)")
    }
    task = connection.execute("select * from team_tasks where id = 'task-1'").fetchone()
    cycle = connection.execute(
        "select * from team_run_cycles where id = 'cycle-1'"
    ).fetchone()

    assert task_columns["required"]["notnull"] == 1
    assert task_columns["required"]["dflt_value"] == "1"
    assert task_columns["acceptance_json"]["notnull"] == 1
    assert task_columns["acceptance_json"]["dflt_value"] == "'{}'"
    assert {"outcome_json", "acceptance_result_json"} <= task_columns.keys()
    assert "execution_metadata_json" in cycle_columns
    assert task["required"] == 1
    assert task["acceptance_json"] == "{}"
    assert task["status"] == "completed"
    assert cycle["status"] == "completed"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"pragma table_info({table})")}


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


def test_migration_11_adds_cycle_policy_queue_and_backfills_continuous() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table team_runs (
            id text primary key, goal text not null, status text not null,
            run_mode text not null, lifecycle_mode text not null,
            max_workers integer not null, workspace_root text not null,
            created_at text not null, updated_at text not null
        );
        create table team_run_cycles (
            id text primary key, team_run_id text not null,
            sequence integer not null, source_type text not null,
            source_id text not null, status text not null,
            rounds_budget integer not null, rounds_used integer not null default 0,
            created_at text not null, updated_at text not null
        );
        create table hook_runs (
            id text primary key, hook_id text not null, status text not null
        );
        """
    )
    connection.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, max_workers, workspace_root, "
        "created_at, updated_at) "
        "values ('legacy-standard','g','completed','plan_and_execute','standard',"
        "1,'w','t','t'), "
        "('legacy-continuous','g','draft','plan_and_execute','continuous',"
        "1,'w','t','t')"
    )

    _migration_11_team_cycle_policies(connection)

    rows = connection.execute(
        "select id, execution_policy from team_runs order by id"
    ).fetchall()
    assert [(row["id"], row["execution_policy"]) for row in rows] == [
        ("legacy-continuous", "triggered"),
        ("legacy-standard", None),
    ]
    assert {"team_run_auto_series", "team_cycle_requests"} <= {
        row["name"]
        for row in connection.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    assert "request_id" in _columns(connection, "team_run_cycles")
    assert "team_cycle_request_id" in _columns(connection, "hook_runs")


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


def test_migration_29_adds_team_run_parent_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table team_runs (id text primary key)")

    _migration_29_team_run_workspace_inheritance(connection)
    _migration_29_team_run_workspace_inheritance(connection)

    assert "parent_team_run_id" in {
        row["name"] for row in connection.execute("pragma table_info(team_runs)")
    }


def test_migration_adds_operation_failure_columns(tmp_path: Path) -> None:
    """Diagnostics for a parse failure, kept as structure rather than content --
    the ledger design excludes raw model responses."""
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    with db.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("pragma table_info(team_model_operations)")
        }
    assert {"failure_digest", "failure_shape_json"} <= columns
