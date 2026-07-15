import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"pragma table_info({table})")}


def _migration_1_legacy_columns(connection: sqlite3.Connection) -> None:
    persona_columns = _columns(connection, "personas")
    if "avatar" not in persona_columns:
        connection.execute("alter table personas add column avatar text not null default ''")
    if "default_options_json" not in persona_columns:
        connection.execute(
            "alter table personas add column default_options_json text not null default '{}'"
        )

    team_run_columns = _columns(connection, "team_runs")
    if "rounds_budget" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_budget integer not null default 8"
        )
    if "rounds_used" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_used integer not null default 0"
        )
    if "team_id" not in team_run_columns:
        connection.execute("alter table team_runs add column team_id text")
    if "rules_snapshot_json" not in team_run_columns:
        connection.execute("alter table team_runs add column rules_snapshot_json text")

    team_agent_columns = _columns(connection, "team_agents")
    if "reinvocations" not in team_agent_columns:
        connection.execute(
            "alter table team_agents add column reinvocations integer not null default 0"
        )
    if "upstream_session_id" not in team_agent_columns:
        connection.execute("alter table team_agents add column upstream_session_id text")


def _migration_2_operability_foundation(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists runtime_settings (
            key text primary key,
            value text not null,
            updated_at text not null
        );

        create table if not exists audit_events (
            id text primary key,
            occurred_at text not null,
            event_type text not null,
            severity text not null,
            actor_type text not null,
            actor_id text,
            session_id text,
            team_run_id text,
            team_task_id text,
            job_id text,
            artifact_id text,
            correlation_id text,
            action text not null,
            resource_type text,
            resource_id text,
            status text not null,
            command_preview text,
            metadata_json text not null,
            redaction_version integer not null
        );

        create index if not exists idx_audit_events_occurred_at
        on audit_events(occurred_at desc);
        create index if not exists idx_audit_events_correlation
        on audit_events(correlation_id);
        create index if not exists idx_jobs_status_created
        on jobs(status, created_at desc);
        create index if not exists idx_jobs_schedule_created
        on jobs(source_schedule_id, created_at desc);
        create index if not exists idx_schedules_enabled_next
        on schedules(enabled, next_run_at);
        create index if not exists idx_team_runs_status_created
        on team_runs(status, created_at desc);
        """
    )
    job_columns = _columns(connection, "jobs")
    if "source_job_id" not in job_columns:
        connection.execute("alter table jobs add column source_job_id text")
    connection.execute(
        "create index if not exists idx_jobs_source_job on jobs(source_job_id)"
    )


def _migration_3_read_path_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists transcript_metadata (
            id text primary key,
            title text not null,
            created_at text not null,
            updated_at text not null,
            message_count integer not null,
            status text not null,
            agent_id text not null,
            model text not null,
            options_json text not null,
            editable integer not null,
            pending_approval_ids_json text not null
        );

        create index if not exists idx_transcript_metadata_updated
        on transcript_metadata(updated_at desc, id desc);
        create index if not exists idx_job_events_job_created
        on job_events(job_id, created_at desc, id desc);
        create index if not exists idx_artifacts_created
        on artifacts(created_at desc, id desc);
        create index if not exists idx_team_tasks_run_status_created
        on team_tasks(team_run_id, status, created_at);
        create index if not exists idx_team_messages_run_created
        on team_messages(team_run_id, created_at, id);
        """
    )


def _migration_4_team_detail_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create index if not exists idx_team_agents_run_created
        on team_agents(team_run_id, created_at, id);
        create index if not exists idx_team_tasks_run_created
        on team_tasks(team_run_id, created_at, id);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    (1, "legacy-column-baseline", _migration_1_legacy_columns),
    (2, "operability-foundation", _migration_2_operability_foundation),
    (3, "read-path-indexes", _migration_3_read_path_indexes),
    (4, "team-detail-indexes", _migration_4_team_detail_indexes),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def run_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists schema_migrations (
            version integer primary key,
            name text not null,
            applied_at text not null
        )
        """
    )
    applied = {
        row["version"]
        for row in connection.execute("select version from schema_migrations")
    }
    for version, name, apply in MIGRATIONS:
        if version in applied:
            continue
        apply(connection)
        connection.execute(
            "insert into schema_migrations (version, name, applied_at) values (?, ?, ?)",
            (version, name, datetime.now(timezone.utc).isoformat()),
        )
    connection.execute(f"pragma user_version = {LATEST_SCHEMA_VERSION}")
