from personal_agent_gateway.db import Database


def _columns(db, table):
    with db.connect() as connection:
        return {row["name"] for row in connection.execute(f"pragma table_info({table})")}


def test_new_tables_and_columns_exist(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()

    teams_cols = _columns(db, "teams")
    assert {"id", "name", "description", "leader_persona_id", "member_persona_ids_json",
            "created_at", "updated_at"} <= teams_cols

    rule_cols = _columns(db, "rule_sets")
    assert {"id", "scope", "team_id", "personality", "rules_json", "updated_at"} <= rule_cols

    run_cols = _columns(db, "team_runs")
    assert {"team_id", "rules_snapshot_json"} <= run_cols

    task_cols = _columns(db, "team_tasks")
    assert {
        "required",
        "acceptance_json",
        "outcome_json",
        "acceptance_result_json",
    } <= task_cols

    cycle_cols = _columns(db, "team_run_cycles")
    assert "execution_metadata_json" in cycle_cols

    with db.connect() as connection:
        cycle_input_cols = {
            row["name"]
            for row in connection.execute(
                "pragma table_info(team_cycle_input_artifacts)"
            )
        }
    assert {
        "cycle_id",
        "artifact_id",
        "relative_path",
        "sha256",
        "size_bytes",
    } <= cycle_input_cols

    with db.connect() as connection:
        dependency_cols = {
            row["name"]
            for row in connection.execute("pragma table_info(team_task_dependencies)")
        }
    assert {"task_id", "depends_on_task_id"} <= dependency_cols

    artifact_cols = _columns(db, "artifacts")
    assert {"retention_class", "expires_at"} <= artifact_cols


def test_migration_adds_columns_to_existing_team_runs(tmp_path):
    db = Database(tmp_path / "app.db")
    with db.connect() as connection:
        connection.execute(
            "create table team_runs (id text primary key, goal text not null, status text not null, "
            "run_mode text not null, leader_agent_id text, max_workers integer not null, "
            "workspace_root text not null, summary text, error_message text, created_at text not null, "
            "started_at text, finished_at text, updated_at text not null)"
        )
    db.initialize()
    run_cols = _columns(db, "team_runs")
    assert "team_id" in run_cols
    assert "rules_snapshot_json" in run_cols
