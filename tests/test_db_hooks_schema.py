from pathlib import Path

from personal_agent_gateway.db import Database


def _table_columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.fetchall(f"pragma table_info({table})")}


def test_initialize_creates_hook_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()

    assert _table_columns(db, "hooks") == {
        "id", "name", "source_type", "connection_ref", "filter_json",
        "target_backend", "target_model", "target_options_json",
        "target_kind", "target_persona_id", "target_persona_snapshot_json",
        "target_team_run_id",
        "prompt_template", "poll_interval_seconds", "enabled",
        "cursor_json", "last_polled_at", "last_error",
        "created_at", "updated_at",
    }
    assert _table_columns(db, "hook_runs") == {
        "id", "hook_id", "dedup_key", "trigger_summary", "trigger_payload_json",
        "status", "result_text", "error_message",
        "team_run_cycle_id",
        "created_at", "started_at", "finished_at",
    }


def test_hook_runs_dedup_key_is_unique_per_hook(tmp_path: Path) -> None:
    import sqlite3

    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    db.execute(
        "insert into hooks (id, name, source_type, connection_ref, filter_json, "
        "target_backend, target_model, target_options_json, prompt_template, "
        "poll_interval_seconds, enabled, created_at, updated_at) "
        "values ('h1','n','email','c','{}','codex','default','{}','t',300,1,'t','t')"
    )
    db.execute(
        "insert into hook_runs (id, hook_id, dedup_key, trigger_summary, "
        "trigger_payload_json, status, created_at) "
        "values ('r1','h1','k','s','{}','queued','t')"
    )
    try:
        db.execute(
            "insert into hook_runs (id, hook_id, dedup_key, trigger_summary, "
            "trigger_payload_json, status, created_at) "
            "values ('r2','h1','k','s','{}','queued','t')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised is True
