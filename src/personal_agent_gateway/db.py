import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Sequence

from personal_agent_gateway.migrations import run_migrations


SCHEMA_SQL = """
create table if not exists auth_sessions (
    id text primary key,
    token_hash text not null unique,
    created_at text not null,
    last_seen_at text not null,
    absolute_expires_at text not null,
    idle_expires_at text not null,
    revoked_at text
);

create index if not exists idx_auth_sessions_token_hash
on auth_sessions(token_hash);

create table if not exists jobs (
    id text primary key,
    capability_id text not null,
    source text not null,
    source_session_id text,
    source_schedule_id text,
    title text not null,
    status text not null,
    input_json text not null,
    command_preview text,
    approval_id text,
    started_at text,
    finished_at text,
    created_at text not null,
    updated_at text not null,
    error_message text
);

create table if not exists job_events (
    id text primary key,
    job_id text not null,
    kind text not null,
    payload_json text not null,
    created_at text not null,
    foreign key (job_id) references jobs(id) on delete cascade
);

create table if not exists approvals (
    id text primary key,
    job_id text not null,
    risk_level text not null,
    command_preview text not null,
    status text not null,
    created_at text not null,
    decided_at text,
    foreign key (job_id) references jobs(id) on delete cascade
);

create table if not exists artifacts (
    id text primary key,
    type text not null,
    title text not null,
    file_path text not null,
    relative_path text not null,
    mime_type text not null,
    size_bytes integer not null,
    thumbnail_path text,
    source_job_id text,
    source_session_id text,
    created_at text not null,
    tags_json text not null,
    metadata_json text not null,
    foreign key (source_job_id) references jobs(id) on delete set null
);

create table if not exists schedules (
    id text primary key,
    name text not null,
    capability_id text not null,
    cron_expression text not null,
    timezone text not null,
    input_template_json text not null,
    enabled integer not null,
    last_run_job_id text,
    last_run_at text,
    next_run_at text not null,
    created_at text not null,
    updated_at text not null,
    foreign key (last_run_job_id) references jobs(id) on delete set null
);

create table if not exists personas (
    id text primary key,
    name text not null,
    role text not null,
    description text not null,
    responsibilities_json text not null,
    constraints_json text not null,
    default_backend text not null,
    default_model text not null,
    default_options_json text not null default '{}',
    avatar text not null default '',
    created_at text not null,
    updated_at text not null
);

create table if not exists team_runs (
    id text primary key,
    goal text not null,
    status text not null,
    run_mode text not null,
    lifecycle_mode text not null default 'standard',
    leader_agent_id text,
    max_workers integer not null,
    rounds_budget integer not null default 8,
    rounds_used integer not null default 0,
    workspace_root text not null,
    summary text,
    error_message text,
    team_id text,
    rules_snapshot_json text,
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null
);

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

create table if not exists team_agents (
    id text primary key,
    team_run_id text not null,
    name text not null,
    role text not null,
    persona_id text not null,
    persona_snapshot_json text not null,
    backend text not null,
    model text not null,
    status text not null,
    workspace_path text,
    current_task_id text,
    reinvocations integer not null default 0,
    upstream_session_id text,
    started_at text,
    finished_at text,
    created_at text not null,
    updated_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (persona_id) references personas(id) on delete restrict
);

create table if not exists team_tasks (
    id text primary key,
    team_run_id text not null,
    cycle_id text,
    title text not null,
    description text not null,
    owner_agent_id text,
    status text not null,
    result text,
    error_message text,
    created_at text not null,
    updated_at text not null,
    started_at text,
    finished_at text,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (cycle_id) references team_run_cycles(id) on delete cascade,
    foreign key (owner_agent_id) references team_agents(id) on delete set null
);

create table if not exists team_messages (
    id text primary key,
    team_run_id text not null,
    cycle_id text,
    sender_agent_id text,
    recipient_agent_id text,
    kind text not null,
    content text not null,
    metadata_json text not null,
    created_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (cycle_id) references team_run_cycles(id) on delete cascade,
    foreign key (sender_agent_id) references team_agents(id) on delete set null,
    foreign key (recipient_agent_id) references team_agents(id) on delete set null
);

create table if not exists team_decision_requests (
    id text primary key,
    team_run_id text not null,
    cycle_id text,
    status text not null,
    revision integer not null default 0,
    items_json text not null default '[]',
    answers_json text not null default '{}',
    file_path text not null default 'USER_DECISIONS.md',
    created_at text not null,
    published_at text,
    answered_at text,
    updated_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (cycle_id) references team_run_cycles(id) on delete cascade
);

create table if not exists session_activity_events (
    id integer primary key autoincrement,
    session_id text not null,
    event_seq integer not null,
    event_type text not null,
    source text not null,
    payload_json text not null,
    transcript_event_id text,
    created_at text not null,
    unique(session_id, event_seq)
);

create index if not exists idx_session_activity_events_session_seq
on session_activity_events(session_id, event_seq);

create table if not exists teams (
    id text primary key,
    name text not null,
    description text not null default '',
    leader_persona_id text not null,
    member_persona_ids_json text not null default '[]',
    created_at text not null,
    updated_at text not null
);

create table if not exists rule_sets (
    id text primary key,
    scope text not null,
    team_id text,
    personality text not null default '',
    rules_json text not null default '[]',
    updated_at text not null,
    unique(scope, team_id)
);

create table if not exists hooks (
    id text primary key,
    name text not null,
    source_type text not null,
    connection_ref text not null,
    filter_json text not null default '{}',
    target_backend text not null,
    target_model text not null,
    target_options_json text not null default '{}',
    target_kind text not null default 'agent',
    target_team_run_id text,
    prompt_template text not null,
    poll_interval_seconds integer not null default 300,
    enabled integer not null,
    cursor_json text,
    last_polled_at text,
    last_error text,
    created_at text not null,
    updated_at text not null,
    foreign key (target_team_run_id) references team_runs(id) on delete set null
);

create table if not exists hook_runs (
    id text primary key,
    hook_id text not null,
    dedup_key text not null,
    trigger_summary text not null,
    trigger_payload_json text not null,
    status text not null,
    result_text text,
    error_message text,
    team_run_cycle_id text unique,
    created_at text not null,
    started_at text,
    finished_at text,
    foreign key (hook_id) references hooks(id) on delete cascade,
    foreign key (team_run_cycle_id) references team_run_cycles(id) on delete set null,
    unique(hook_id, dedup_key)
);

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


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma journal_mode = wal")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA_SQL)
            run_migrations(connection)

    def schema_version(self) -> int:
        row = self.fetchone("select max(version) as version from schema_migrations")
        return int(row["version"] or 0) if row is not None else 0

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        with self.connection() as connection:
            connection.execute(sql, parameters)

    def fetchone(
        self,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(sql, parameters).fetchone()

    def fetchall(
        self,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute(sql, parameters).fetchall())
