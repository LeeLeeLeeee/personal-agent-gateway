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


def test_database_initializes_agent_team_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()

    rows = db.fetchall(
        "select name from sqlite_master where type = 'table' and name in (?, ?, ?, ?, ?)",
        ("personas", "team_runs", "team_agents", "team_tasks", "team_messages"),
    )

    assert {row["name"] for row in rows} == {
        "personas",
        "team_runs",
        "team_agents",
        "team_tasks",
        "team_messages",
    }


def _columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.fetchall(f"pragma table_info({table})")}


def test_personas_table_has_avatar_column(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()

    assert "avatar" in _columns(db, "personas")
    assert "default_options_json" in _columns(db, "personas")


def test_initialize_migrates_avatar_onto_legacy_personas(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "create table personas ("
        "id text primary key, name text not null, role text not null, "
        "description text not null, responsibilities_json text not null, "
        "constraints_json text not null, default_backend text not null, "
        "default_model text not null, created_at text not null, updated_at text not null)"
    )
    conn.execute(
        "insert into personas values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("p1", "Legacy", "role", "description", "[]", "[]", "codex", "default", "t", "t"),
    )
    conn.commit()
    conn.close()

    db = Database(path)
    assert "avatar" not in _columns(db, "personas")

    db.initialize()

    assert "avatar" in _columns(db, "personas")
    assert "default_options_json" in _columns(db, "personas")
    row = db.fetchone("select default_options_json from personas where id = 'p1'")
    assert row is not None
    assert row["default_options_json"] == "{}"
