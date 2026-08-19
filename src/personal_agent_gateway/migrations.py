import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


TEAM_CYCLE_POLICY_TABLES_SQL = """
create table if not exists team_run_auto_series (
    id text primary key,
    team_run_id text not null references team_runs(id) on delete cascade,
    series_number integer not null,
    status text not null,
    target_slots integer not null check (target_slots > 0),
    settled_slots integer not null default 0 check (settled_slots >= 0),
    interval_seconds integer not null check (interval_seconds >= 60),
    next_run_at text,
    pause_reason text,
    paused_cycle_id text references team_run_cycles(id) on delete set null,
    created_at text not null,
    started_at text not null,
    completed_at text,
    updated_at text not null,
    unique (team_run_id, series_number)
);

create table if not exists team_cycle_requests (
    id text primary key,
    team_run_id text not null references team_runs(id) on delete cascade,
    auto_series_id text references team_run_auto_series(id) on delete cascade,
    slot_ordinal integer check (slot_ordinal is null or slot_ordinal > 0),
    source_type text not null,
    source_id text not null,
    status text not null,
    instruction text not null,
    previous_cycle_id text references team_run_cycles(id) on delete set null,
    previous_summary_text text,
    retry_of_request_id text references team_cycle_requests(id) on delete set null,
    created_at text not null,
    claimed_at text,
    settled_at text,
    updated_at text not null,
    unique (team_run_id, source_type, source_id)
);
"""

TEAM_CYCLE_POLICY_INDEXES_SQL = """
create unique index if not exists idx_team_auto_series_one_active
on team_run_auto_series(team_run_id)
where status in (
    'running', 'waiting_interval', 'paused_failure',
    'paused_user', 'paused_interrupted'
);

create unique index if not exists idx_team_cycle_requests_one_dispatching
on team_cycle_requests(team_run_id)
where status = 'dispatching';

create index if not exists idx_team_cycle_requests_run_status_created
on team_cycle_requests(team_run_id, status, created_at, id);

create unique index if not exists idx_team_run_cycles_request
on team_run_cycles(request_id)
where request_id is not null;

create unique index if not exists idx_hook_runs_cycle_request
on hook_runs(team_cycle_request_id)
where team_cycle_request_id is not null;
"""


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


def _migration_11_team_cycle_policies(connection: sqlite3.Connection) -> None:
    if "execution_policy" not in _columns(connection, "team_runs"):
        connection.execute("alter table team_runs add column execution_policy text")
    connection.executescript(TEAM_CYCLE_POLICY_TABLES_SQL)
    if "request_id" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column request_id text "
            "references team_cycle_requests(id) on delete set null"
        )
    if "team_cycle_request_id" not in _columns(connection, "hook_runs"):
        connection.execute(
            "alter table hook_runs add column team_cycle_request_id text "
            "references team_cycle_requests(id) on delete set null"
        )
    connection.executescript(TEAM_CYCLE_POLICY_INDEXES_SQL)
    connection.execute(
        "update team_runs set execution_policy = 'triggered' "
        "where lifecycle_mode = 'continuous' and execution_policy is null"
    )


def _migration_12_task_retry_cycles(connection: sqlite3.Connection) -> None:
    if "rules_snapshot_json" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column rules_snapshot_json text"
        )
    if "retry_of_task_id" not in _columns(connection, "team_tasks"):
        connection.execute(
            "alter table team_tasks add column retry_of_task_id text "
            "references team_tasks(id) on delete set null"
        )
    connection.execute(
        "create unique index if not exists idx_team_tasks_one_retry "
        "on team_tasks(retry_of_task_id) where retry_of_task_id is not null"
    )


def _migration_13_space_policies(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists space_policies (
            scope text not null,
            scope_id text not null default '',
            read_mode text not null,
            read_path text,
            write_mode text not null,
            workspace_path text,
            created_at text not null,
            updated_at text not null,
            primary key (scope, scope_id)
        )
        """
    )
    columns = _columns(connection, "team_runs")
    for name in (
        "working_root",
        "artifact_root",
        "worktree_branch",
        "space_policy_snapshot_json",
    ):
        if name not in columns:
            connection.execute(f"alter table team_runs add column {name} text")


def _migration_14_archive_library(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists archive_entries (
            id text primary key,
            kind text not null,
            title text not null,
            summary text not null,
            content_markdown text not null,
            tags_json text not null default '[]',
            source_urls_json text not null default '[]',
            status text not null,
            current_revision integer not null,
            created_by text not null,
            created_at text not null,
            updated_at text not null
        );

        create table if not exists archive_revisions (
            id text primary key,
            entry_id text not null,
            revision integer not null,
            kind text not null,
            title text not null,
            summary text not null,
            content_markdown text not null,
            tags_json text not null,
            source_urls_json text not null,
            change_summary text not null,
            created_by text not null,
            created_at text not null,
            foreign key (entry_id) references archive_entries(id) on delete cascade,
            unique(entry_id, revision)
        );

        create table if not exists archive_bindings (
            entry_id text not null,
            scope text not null,
            scope_id text not null default '',
            created_at text not null,
            primary key (entry_id, scope, scope_id),
            foreign key (entry_id) references archive_entries(id) on delete cascade
        );

        create table if not exists knowledge_requests (
            id text primary key,
            title text not null,
            reason text not null,
            suggested_outline_json text not null default '[]',
            source_hints_json text not null default '[]',
            requested_by_persona_id text,
            session_id text,
            team_run_id text,
            assigned_team_run_id text,
            status text not null,
            fulfilled_by_entry_id text,
            created_at text not null,
            updated_at text not null,
            foreign key (requested_by_persona_id) references personas(id) on delete set null,
            foreign key (team_run_id) references team_runs(id) on delete set null,
            foreign key (assigned_team_run_id) references team_runs(id) on delete set null,
            foreign key (fulfilled_by_entry_id) references archive_entries(id) on delete set null
        );

        create virtual table if not exists archive_entries_fts using fts5(
            entry_id unindexed,
            title,
            summary,
            content_markdown,
            tags,
            tokenize = 'unicode61'
        );

        create index if not exists idx_archive_entries_status_updated
        on archive_entries(status, updated_at desc);

        create index if not exists idx_archive_bindings_scope
        on archive_bindings(scope, scope_id, entry_id);

        create index if not exists idx_archive_revisions_entry_revision
        on archive_revisions(entry_id, revision desc);

        create index if not exists idx_knowledge_requests_status_created
        on knowledge_requests(status, created_at desc);

        create index if not exists idx_knowledge_requests_persona_status
        on knowledge_requests(requested_by_persona_id, status, created_at desc);
        """
    )


def _migration_15_library_team_drafts(connection: sqlite3.Connection) -> None:
    hook_columns = _columns(connection, "hooks")
    if "library_draft_enabled" not in hook_columns:
        connection.execute(
            "alter table hooks add column "
            "library_draft_enabled integer not null default 0"
        )
    request_columns = _columns(connection, "knowledge_requests")
    if "assigned_team_run_id" not in request_columns:
        connection.execute(
            "alter table knowledge_requests add column assigned_team_run_id text"
        )
    connection.executescript(
        """
        create table if not exists archive_draft_origins (
            entry_id text primary key,
            source_type text not null,
            source_id text not null,
            hook_id text,
            hook_run_id text,
            team_run_id text,
            cycle_id text,
            knowledge_request_id text,
            created_at text not null,
            foreign key (entry_id) references archive_entries(id) on delete cascade,
            foreign key (hook_id) references hooks(id) on delete set null,
            foreign key (hook_run_id) references hook_runs(id) on delete set null,
            foreign key (team_run_id) references team_runs(id) on delete set null,
            foreign key (cycle_id) references team_run_cycles(id) on delete set null,
            foreign key (knowledge_request_id)
                references knowledge_requests(id) on delete set null,
            unique(source_type, source_id)
        );

        create index if not exists idx_archive_draft_origins_team_run
        on archive_draft_origins(team_run_id, created_at desc);

        create index if not exists idx_archive_draft_origins_request
        on archive_draft_origins(knowledge_request_id);

        create index if not exists idx_knowledge_requests_assigned_team
        on knowledge_requests(assigned_team_run_id, status, created_at desc);
        """
    )


def _migration_16_explicit_no_source_space(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        update space_policies
        set read_mode = 'none', read_path = null
        where read_mode = 'home' and write_mode = 'isolated'
        """
    )


def _migration_17_team_task_acceptance(connection: sqlite3.Connection) -> None:
    task_columns = _columns(connection, "team_tasks")
    if "required" not in task_columns:
        connection.execute(
            "alter table team_tasks add column required integer not null default 1"
        )
    if "acceptance_json" not in task_columns:
        connection.execute(
            "alter table team_tasks add column acceptance_json text not null default '{}'"
        )
    if "outcome_json" not in task_columns:
        connection.execute("alter table team_tasks add column outcome_json text")
    if "acceptance_result_json" not in task_columns:
        connection.execute(
            "alter table team_tasks add column acceptance_result_json text"
        )
    if "execution_metadata_json" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column execution_metadata_json text"
        )


def _migration_18_team_cycle_space_snapshot(connection: sqlite3.Connection) -> None:
    if "space_policy_snapshot_json" not in _columns(connection, "team_run_cycles"):
        connection.execute(
            "alter table team_run_cycles add column space_policy_snapshot_json text"
        )


def _migration_19_team_acceptance_recovery(
    connection: sqlite3.Connection,
) -> None:
    if "acceptance_recovery_attempts" not in _columns(connection, "team_tasks"):
        connection.execute(
            "alter table team_tasks add column "
            "acceptance_recovery_attempts integer not null default 0"
        )


def _migration_20_team_model_operations(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_model_operations (
            id text primary key,
            operation_key text not null unique,
            team_run_id text not null
                references team_runs(id) on delete cascade,
            cycle_id text not null
                references team_run_cycles(id) on delete cascade,
            task_id text references team_tasks(id) on delete cascade,
            agent_id text not null
                references team_agents(id) on delete cascade,
            provider text not null,
            stage text not null,
            stage_ordinal integer not null check (stage_ordinal >= 0),
            status text not null,
            version integer not null default 0 check (version >= 0),
            attempts integer not null default 0 check (attempts >= 0),
            consumer_run_id text,
            upstream_session_id text,
            request_digest text not null,
            result_kind text,
            result_json text,
            result_digest text,
            effect_type text,
            effect_ref_json text,
            reason_code text,
            created_at text not null,
            started_at text,
            completed_at text,
            applied_at text,
            updated_at text not null
        );

        create unique index if not exists
        idx_team_model_operations_one_open_cycle
        on team_model_operations(cycle_id)
        where status in (
            'prepared', 'invoking', 'completed',
            'waiting_for_provider', 'ambiguous'
        );

        create index if not exists idx_team_model_operations_run_cycle
        on team_model_operations(team_run_id, cycle_id, created_at, id);
        """
    )


def _migration_21_knowledge_request_draft_failure(
    connection: sqlite3.Connection,
) -> None:
    columns = _columns(connection, "knowledge_requests")
    for column in (
        "last_draft_error_code",
        "last_draft_error_message",
        "last_draft_failed_at",
        "last_draft_cycle_id",
    ):
        if column not in columns:
            connection.execute(
                f"alter table knowledge_requests add column {column} text"
            )


def _migration_22_team_task_input_artifacts(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_cycle_request_input_artifacts (
            cycle_request_id text not null
                references team_cycle_requests(id) on delete cascade,
            artifact_id text not null references artifacts(id) on delete restrict,
            relative_path text not null,
            sha256 text not null,
            size_bytes integer not null check (size_bytes >= 0),
            created_at text not null,
            primary key (cycle_request_id, artifact_id)
        );

        create table if not exists team_cycle_input_artifacts (
            cycle_id text not null references team_run_cycles(id) on delete cascade,
            artifact_id text not null references artifacts(id) on delete restrict,
            relative_path text not null,
            sha256 text not null,
            size_bytes integer not null check (size_bytes >= 0),
            created_at text not null,
            primary key (cycle_id, artifact_id)
        );

        create index if not exists idx_team_cycle_input_artifacts_cycle
        on team_cycle_input_artifacts(cycle_id);
        """
    )


def _migration_23_team_task_input_bindings(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_task_input_artifacts (
            task_id text not null references team_tasks(id) on delete cascade,
            artifact_id text not null references artifacts(id) on delete restrict,
            relative_path text not null,
            sha256 text not null,
            size_bytes integer not null check (size_bytes >= 0),
            staged_path text not null,
            created_at text not null,
            primary key (task_id, artifact_id)
        );

        create index if not exists idx_team_task_input_artifacts_task
        on team_task_input_artifacts(task_id);
        """
    )


def _migration_24_team_task_dependencies(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_task_dependencies (
            task_id text not null references team_tasks(id) on delete cascade,
            depends_on_task_id text not null
                references team_tasks(id) on delete cascade,
            primary key (task_id, depends_on_task_id),
            check (task_id <> depends_on_task_id)
        );

        create index if not exists idx_team_task_dependencies_prerequisite
        on team_task_dependencies(depends_on_task_id);
        """
    )


def _migration_25_artifact_retention_cleanup(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        alter table artifacts add column retention_class text not null default 'durable';
        alter table artifacts add column expires_at text;
        create index if not exists idx_artifacts_retention_expiry
        on artifacts(retention_class, expires_at);
        """
    )


def _migration_26_artifact_browser_origins(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists chat_turns (
            id text primary key,
            session_id text not null,
            user_event_id text,
            prompt_excerpt text not null,
            status text not null,
            created_at text not null,
            finished_at text
        );
        create index if not exists idx_chat_turns_session_created
        on chat_turns(session_id, created_at desc, id desc);
        """
    )
    job_columns = _columns(connection, "jobs")
    if "source_chat_turn_id" not in job_columns:
        connection.execute("alter table jobs add column source_chat_turn_id text")
    artifact_columns = _columns(connection, "artifacts")
    for column in (
        "origin_kind text not null default 'legacy'",
        "artifact_role text not null default 'attachment'",
        "source_chat_turn_id text",
        "source_team_task_id text",
        "source_team_run_id text",
        "source_cycle_id text",
        "origin_group_label_snapshot text not null default ''",
        "origin_item_label_snapshot text not null default ''",
    ):
        name = column.split(" ", 1)[0]
        if name not in artifact_columns:
            connection.execute(f"alter table artifacts add column {column}")
    connection.executescript(
        """
        create index if not exists idx_jobs_source_chat_turn
        on jobs(source_chat_turn_id);
        create index if not exists idx_artifacts_origin_created
        on artifacts(origin_kind, created_at desc, id desc);
        create index if not exists idx_artifacts_source_team_task
        on artifacts(source_team_task_id);
        create index if not exists idx_artifacts_source_team_run
        on artifacts(source_team_run_id);
        create index if not exists idx_artifacts_source_chat_turn
        on artifacts(source_chat_turn_id);
        """
    )


def _migration_27_backfill_artifact_origins(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        select id, metadata_json, source_job_id, source_session_id, origin_kind,
               source_team_task_id, source_team_run_id, source_cycle_id
        from artifacts
        """
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        run_id = metadata.get("team_run_id") if isinstance(metadata.get("team_run_id"), str) else None
        task_id = metadata.get("task_id") if isinstance(metadata.get("task_id"), str) else None
        cycle_id = metadata.get("cycle_id") if isinstance(metadata.get("cycle_id"), str) else None
        if run_id or task_id:
            origin_kind = "team_task_output"
        elif row["source_job_id"]:
            origin_kind = "job_output"
        elif row["source_session_id"]:
            origin_kind = "chat_upload"
        else:
            origin_kind = row["origin_kind"]
        connection.execute(
            """
            update artifacts
            set origin_kind = ?,
                source_team_task_id = coalesce(source_team_task_id, ?),
                source_team_run_id = coalesce(source_team_run_id, ?),
                source_cycle_id = coalesce(source_cycle_id, ?)
            where id = ?
            """,
            (origin_kind, task_id, run_id, cycle_id, row["id"]),
        )


def _migration_28_team_task_plan_ordinal(
    connection: sqlite3.Connection,
) -> None:
    if "plan_ordinal" not in _columns(connection, "team_tasks"):
        connection.execute(
            "alter table team_tasks add column plan_ordinal integer not null default 0"
        )
    connection.execute(
        """
        update team_tasks
        set plan_ordinal = (
            select count(*)
            from team_tasks earlier
            where earlier.team_run_id = team_tasks.team_run_id
              and earlier.cycle_id is team_tasks.cycle_id
              and earlier.rowid < team_tasks.rowid
        )
        """
    )


def _migration_29_team_run_workspace_inheritance(
    connection: sqlite3.Connection,
) -> None:
    if "parent_team_run_id" not in _columns(connection, "team_runs"):
        connection.execute(
            "alter table team_runs add column parent_team_run_id text "
            "references team_runs(id) on delete set null"
        )
    connection.execute(
        "create index if not exists idx_team_runs_parent on team_runs(parent_team_run_id)"
    )


def _migration_30_operation_failure_shape(
    connection: sqlite3.Connection,
) -> None:
    existing = _columns(connection, "team_model_operations")
    if "failure_digest" not in existing:
        connection.execute(
            "alter table team_model_operations add column failure_digest text"
        )
    if "failure_shape_json" not in existing:
        connection.execute(
            "alter table team_model_operations add column failure_shape_json text"
        )


def _migration_31_team_plan_negotiation(
    connection: sqlite3.Connection,
) -> None:
    if "plan_negotiation_enabled" not in _columns(connection, "team_runs"):
        connection.execute(
            "alter table team_runs add column plan_negotiation_enabled"
            " integer not null default 0"
        )
    connection.executescript(
        """
        create table if not exists team_plan_revisions (
            id text primary key,
            team_run_id text not null,
            cycle_id text,
            revision integer not null,
            status text not null,
            task_ids_json text not null default '[]',
            required_approver_agent_ids_json text not null default '[]',
            created_at text not null,
            decided_at text,
            foreign key (team_run_id) references team_runs(id) on delete cascade
        );

        create unique index if not exists idx_team_plan_revisions_number
        on team_plan_revisions(team_run_id, cycle_id, revision);

        create table if not exists team_plan_approvals (
            id text primary key,
            plan_revision_id text not null,
            agent_id text not null,
            decision text not null,
            objections_json text not null default '[]',
            created_at text not null,
            foreign key (plan_revision_id)
                references team_plan_revisions(id) on delete cascade
        );

        create unique index if not exists idx_team_plan_approvals_one_per_agent
        on team_plan_approvals(plan_revision_id, agent_id);
        """
    )


def _migration_32_team_collaboration_deliveries(
    connection: sqlite3.Connection,
) -> None:
    connection.executescript(
        """
        create table if not exists team_collaboration_deliveries (
            id text primary key,
            team_run_id text not null,
            agent_id text not null,
            operation_key text not null unique,
            status text not null,
            created_at text not null,
            settled_at text
        );
        create index if not exists idx_collab_delivery_agent
        on team_collaboration_deliveries(team_run_id, agent_id, status);

        create table if not exists team_collaboration_delivery_items (
            delivery_id text not null,
            message_id text not null,
            primary key (delivery_id, message_id)
        );
        create index if not exists idx_collab_delivery_items_message
        on team_collaboration_delivery_items(message_id);
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
    (11, "team-cycle-policies", _migration_11_team_cycle_policies),
    (12, "task-retry-cycles", _migration_12_task_retry_cycles),
    (13, "space-policies", _migration_13_space_policies),
    (14, "archive-library", _migration_14_archive_library),
    (15, "library-team-drafts", _migration_15_library_team_drafts),
    (16, "explicit-no-source-space", _migration_16_explicit_no_source_space),
    (17, "team-task-acceptance", _migration_17_team_task_acceptance),
    (18, "team-cycle-space-snapshot", _migration_18_team_cycle_space_snapshot),
    (19, "team-acceptance-recovery", _migration_19_team_acceptance_recovery),
    (20, "team-model-operations", _migration_20_team_model_operations),
    (21, "knowledge-request-draft-failure", _migration_21_knowledge_request_draft_failure),
    (22, "team-task-input-artifacts", _migration_22_team_task_input_artifacts),
    (23, "team-task-input-bindings", _migration_23_team_task_input_bindings),
    (24, "team-task-dependencies", _migration_24_team_task_dependencies),
    (25, "artifact-retention-cleanup", _migration_25_artifact_retention_cleanup),
    (26, "artifact-browser-origins", _migration_26_artifact_browser_origins),
    (27, "backfill-artifact-origins", _migration_27_backfill_artifact_origins),
    (28, "team-task-plan-ordinal", _migration_28_team_task_plan_ordinal),
    (29, "team-run-workspace-inheritance", _migration_29_team_run_workspace_inheritance),
    (30, "operation-failure-shape", _migration_30_operation_failure_shape),
    (31, "team-plan-negotiation", _migration_31_team_plan_negotiation),
    (32, "team-collaboration-deliveries", _migration_32_team_collaboration_deliveries),
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
