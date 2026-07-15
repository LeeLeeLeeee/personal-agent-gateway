from pathlib import Path

from personal_agent_gateway.db import Database


def test_read_path_queries_use_declared_indexes(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    cases = {
        "idx_jobs_status_created": (
            "select * from jobs where status = ? order by created_at desc limit ?",
            ("running", 100),
        ),
        "idx_job_events_job_created": (
            "select * from job_events where job_id = ? "
            "order by created_at desc, id desc limit ?",
            ("job", 200),
        ),
        "idx_artifacts_created": (
            "select * from artifacts order by created_at desc, id desc limit ?",
            (100,),
        ),
        "idx_team_runs_status_created": (
            "select * from team_runs where status = ? order by created_at desc limit ?",
            ("running", 100),
        ),
        "idx_team_tasks_run_status_created": (
            "select * from team_tasks where team_run_id = ? and status = ? "
            "order by created_at",
            ("run", "pending"),
        ),
        "idx_session_activity_events_session_seq": (
            "select * from session_activity_events where session_id = ? "
            "order by event_seq desc limit ?",
            ("session", 200),
        ),
        "idx_team_tasks_run_created": (
            "select * from team_tasks where team_run_id = ? "
            "order by created_at, id limit ?",
            ("run", 200),
        ),
    }

    for index_name, (query, parameters) in cases.items():
        details = " ".join(
            str(row["detail"])
            for row in db.fetchall(f"explain query plan {query}", parameters)
        )
        assert index_name in details
