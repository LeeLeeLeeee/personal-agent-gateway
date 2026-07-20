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
            pending_approval_ids_json text not null,
            origin text not null default 'chat',
            hook_run_id text
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


def _migration_5_team_decision_requests(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists team_decision_requests (
            id text primary key,
            team_run_id text not null,
            status text not null,
            revision integer not null default 0,
            items_json text not null default '[]',
            answers_json text not null default '{}',
            file_path text not null default 'USER_DECISIONS.md',
            created_at text not null,
            published_at text,
            answered_at text,
            updated_at text not null,
            foreign key (team_run_id) references team_runs(id) on delete cascade
        );

        create unique index if not exists idx_team_decision_requests_active
        on team_decision_requests(team_run_id)
        where status in ('collecting', 'awaiting_user');
        """
    )


def _migration_6_team_run_cycles(connection: sqlite3.Connection) -> None:
    team_run_columns = _columns(connection, "team_runs")
    if "lifecycle_mode" not in team_run_columns:
        connection.execute(
            "alter table team_runs "
            "add column lifecycle_mode text not null default 'standard'"
        )

    connection.executescript(
        """
        create table if not exists team_run_cycles (
            id text primary key,
            team_run_id text not null,
            sequence integer not null,
            source_type text not null,
            source_id text not null,
            status text not null,
            rounds_budget integer not null,
            rounds_used integer not null default 0,
            summary text,
            error_message text,
            created_at text not null,
            started_at text,
            finished_at text,
            updated_at text not null,
            foreign key (team_run_id) references team_runs(id) on delete cascade,
            unique(team_run_id, sequence),
            unique(team_run_id, source_type, source_id)
        );
        """
    )

    for table in ("team_tasks", "team_messages", "team_decision_requests"):
        if "cycle_id" not in _columns(connection, table):
            connection.execute(
                f"alter table {table} add column cycle_id text "
                "references team_run_cycles(id) on delete cascade"
            )

    connection.executescript(
        """
        drop index if exists idx_team_decision_requests_active;

        create unique index if not exists idx_team_decision_requests_active_standard
        on team_decision_requests(team_run_id)
        where cycle_id is null and status in ('collecting', 'awaiting_user');

        create unique index if not exists idx_team_decision_requests_active_cycle
        on team_decision_requests(cycle_id)
        where cycle_id is not null and status in ('collecting', 'awaiting_user');

        create index if not exists idx_team_run_cycles_run_status_sequence
        on team_run_cycles(team_run_id, status, sequence);

        create index if not exists idx_team_tasks_run_cycle_status_created
        on team_tasks(team_run_id, cycle_id, status, created_at);

        create index if not exists idx_team_messages_run_cycle_created
        on team_messages(team_run_id, cycle_id, created_at, id);
        """
    )


def _migration_7_hook_team_run_targets(connection: sqlite3.Connection) -> None:
    hook_columns = _columns(connection, "hooks")
    if "target_kind" not in hook_columns:
        connection.execute(
            "alter table hooks add column target_kind text not null default 'agent'"
        )
    if "target_team_run_id" not in hook_columns:
        connection.execute(
            "alter table hooks add column target_team_run_id text "
            "references team_runs(id) on delete set null"
        )
    if "team_run_cycle_id" not in _columns(connection, "hook_runs"):
        connection.execute(
            "alter table hook_runs add column team_run_cycle_id text "
            "references team_run_cycles(id) on delete set null"
        )
    connection.executescript(
        """
        create unique index if not exists idx_hook_runs_team_run_cycle
        on hook_runs(team_run_cycle_id)
        where team_run_cycle_id is not null;

        create index if not exists idx_hooks_target_team_run
        on hooks(target_team_run_id)
        where target_team_run_id is not null;
        """
    )


def _migration_8_mail_knowledge(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists mail_messages (
            id text primary key,
            mail_team_run_id text not null,
            workspace_root text not null,
            hook_id text,
            hook_run_id text unique,
            team_run_cycle_id text,
            dedup_key text not null,
            sender_raw text not null,
            sender_address text not null,
            sender_name text not null,
            subject text not null,
            sent_at text not null,
            body_text text not null,
            result_text text,
            archive_relative_path text not null,
            projection_status text not null default 'pending',
            projection_error text,
            projected_at text,
            created_at text not null,
            updated_at text not null,
            foreign key (hook_id) references hooks(id) on delete set null,
            foreign key (hook_run_id) references hook_runs(id) on delete set null,
            foreign key (team_run_cycle_id) references team_run_cycles(id) on delete set null,
            unique(mail_team_run_id, dedup_key)
        );

        create table if not exists mail_contacts (
            id text primary key,
            mail_team_run_id text not null,
            canonical_address text not null,
            display_name text not null,
            domain text not null,
            first_seen_at text not null,
            last_seen_at text not null,
            message_count integer not null default 0,
            last_message_id text,
            observations_json text not null default '[]',
            created_at text not null,
            updated_at text not null,
            foreign key (last_message_id) references mail_messages(id) on delete set null,
            unique(mail_team_run_id, canonical_address)
        );

        create index if not exists idx_mail_messages_projection
        on mail_messages(projection_status, created_at);

        create index if not exists idx_mail_messages_cycle
        on mail_messages(team_run_cycle_id);

        create index if not exists idx_mail_contacts_team_seen
        on mail_contacts(mail_team_run_id, last_seen_at desc);
        """
    )


def _migration_9_hook_persona_targets(connection: sqlite3.Connection) -> None:
    hook_columns = _columns(connection, "hooks")
    if "target_persona_id" not in hook_columns:
        connection.execute(
            "alter table hooks add column target_persona_id text "
            "references personas(id) on delete set null"
        )
    if "target_persona_snapshot_json" not in hook_columns:
        connection.execute(
            "alter table hooks add column target_persona_snapshot_json "
            "text not null default '{}'"
        )
    connection.execute(
        "create index if not exists idx_hooks_target_persona "
        "on hooks(target_persona_id) where target_persona_id is not null"
    )


def _migration_10_transcript_origins(connection: sqlite3.Connection) -> None:
    metadata_columns = _columns(connection, "transcript_metadata")
    if not metadata_columns:
        _migration_3_read_path_indexes(connection)
        metadata_columns = _columns(connection, "transcript_metadata")
    if "origin" not in metadata_columns:
        connection.execute(
            "alter table transcript_metadata "
            "add column origin text not null default 'chat'"
        )
    if "hook_run_id" not in metadata_columns:
        connection.execute(
            "alter table transcript_metadata add column hook_run_id text"
        )
    connection.executescript(
        """
        create index if not exists idx_transcript_metadata_origin_updated
        on transcript_metadata(origin, updated_at desc, id desc);

        create index if not exists idx_transcript_metadata_hook_run
        on transcript_metadata(hook_run_id)
        where hook_run_id is not null;
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    (1, "legacy-column-baseline", _migration_1_legacy_columns),
    (2, "operability-foundation", _migration_2_operability_foundation),
    (3, "read-path-indexes", _migration_3_read_path_indexes),
    (4, "team-detail-indexes", _migration_4_team_detail_indexes),
    (5, "team-decision-requests", _migration_5_team_decision_requests),
    (6, "team-run-cycles", _migration_6_team_run_cycles),
    (7, "hook-team-run-targets", _migration_7_hook_team_run_targets),
    (8, "mail-knowledge", _migration_8_mail_knowledge),
    (9, "hook-persona-targets", _migration_9_hook_persona_targets),
    (10, "transcript-origins", _migration_10_transcript_origins),
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
