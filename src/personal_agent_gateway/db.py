import sqlite3
from pathlib import Path
from typing import Sequence


SCHEMA_SQL = """
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
    foreign key (owner_agent_id) references team_agents(id) on delete set null
);

create table if not exists team_messages (
    id text primary key,
    team_run_id text not null,
    sender_agent_id text,
    recipient_agent_id text,
    kind text not null,
    content text not null,
    metadata_json text not null,
    created_at text not null,
    foreign key (team_run_id) references team_runs(id) on delete cascade,
    foreign key (sender_agent_id) references team_agents(id) on delete set null,
    foreign key (recipient_agent_id) references team_agents(id) on delete set null
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

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            _migrate(connection)

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, parameters)

    def fetchone(
        self,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(sql, parameters).fetchone()

    def fetchall(
        self,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, parameters).fetchall())


def _migrate(connection: sqlite3.Connection) -> None:
    """Additive column migrations for databases created before a column existed.

    ``create table if not exists`` never alters an existing table, so columns
    added to SCHEMA_SQL after a table already shipped must be backfilled here.
    """
    persona_columns = {
        row["name"] for row in connection.execute("pragma table_info(personas)")
    }
    if "avatar" not in persona_columns:
        connection.execute(
            "alter table personas add column avatar text not null default ''"
        )
    if "default_options_json" not in persona_columns:
        connection.execute(
            "alter table personas add column default_options_json text not null default '{}'"
        )
    team_run_columns = {
        row["name"] for row in connection.execute("pragma table_info(team_runs)")
    }
    if "rounds_budget" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_budget integer not null default 8"
        )
    if "rounds_used" not in team_run_columns:
        connection.execute(
            "alter table team_runs add column rounds_used integer not null default 0"
        )
    team_agent_columns = {
        row["name"] for row in connection.execute("pragma table_info(team_agents)")
    }
    if "reinvocations" not in team_agent_columns:
        connection.execute(
            "alter table team_agents add column reinvocations integer not null default 0"
        )
    if "upstream_session_id" not in team_agent_columns:
        connection.execute(
            "alter table team_agents add column upstream_session_id text"
        )
    team_run_columns_v2 = {
        row["name"] for row in connection.execute("pragma table_info(team_runs)")
    }
    if "team_id" not in team_run_columns_v2:
        connection.execute("alter table team_runs add column team_id text")
    if "rules_snapshot_json" not in team_run_columns_v2:
        connection.execute("alter table team_runs add column rules_snapshot_json text")
