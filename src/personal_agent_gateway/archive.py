import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database

ArchiveKind = Literal[
    "procedure",
    "search_method",
    "implementation_pattern",
    "reference",
    "checklist",
]

_KINDS = {
    "procedure",
    "search_method",
    "implementation_pattern",
    "reference",
    "checklist",
}
_REQUEST_STATUSES = {"open", "in_progress", "deferred", "dismissed", "fulfilled"}
_USER_REQUEST_STATUSES = _REQUEST_STATUSES - {"fulfilled"}
_ACTIVE_REQUEST_STATUSES = {"open", "in_progress", "deferred"}
_DRAFT_SOURCE_TYPES = {"hook", "knowledge_request", "team_note"}
#: A team note is rewritten in full every cycle, so it has to stay small enough
#: that rewriting it is cheap and that the lead has to choose what to keep. An
#: uncapped note only grows: nothing is ever dropped because nothing forces a
#: choice, and the note stops being knowledge and becomes a log.
TEAM_NOTE_MAX_CHARS = 4_000
#: 한 글자짜리 토큰은 어느 문서에나 있어 자리만 차지한다.
_MIN_TERM_LENGTH = 2
#: FTS 질의가 무한정 길어지지 않게 막는 상한. 자르는 것이 목적이 아니라
#: 병적으로 긴 일감 설명 하나가 질의를 망가뜨리는 것을 막는 것이다.
_MAX_QUERY_TERMS = 32
#: 단어 하나가 겹치는 것은 우연이다 -- "점심 메뉴를 고르고 영수증을 정리한다"
#: 가 `정리한다` 하나로 d3 규약 문서를 끌어왔고, bm25 점수는 그것을 실제
#: 일감보다 높게 매겼다(-0.409 대 -0.499). 문서가 몇 개 없을 때 bm25 는 신호가
#: 되지 못하므로, 서로 다른 단어가 몇 개나 같은 문서에 있는지로 본다.
#:
#: 2 다. 더 올리고 싶은 유혹이 있었고 실제로 4 로 뒀다가 되돌렸다 -- 긴 규약
#: 문서 두 개에 맞춰 고른 값이었는데, 85 자짜리 문서는 서로 다른 단어 넷을
#: 담을 자리가 없어서 주제가 정확히 맞아도 걸러졌다. 이 값은 문서 길이에
#: 기대면 안 된다.
#: 질의가 그보다 짧으면 그 길이를 쓴다 -- 아니면 짧은 질의는 아무것도 못 찾는다.
_MIN_TERM_OVERLAP = 2
_REQUEST_PATTERN = re.compile(
    r"<knowledge_request>\s*(\{.*?\})\s*</knowledge_request>",
    re.DOTALL,
)
_LIBRARY_DRAFT_OPEN = "<library_draft>"
_LIBRARY_DRAFT_CLOSE = "</library_draft>"
_DRAFT_ERROR_MESSAGE_LIMIT = 500


@dataclass(frozen=True)
class ArchiveEntry:
    id: str
    kind: str
    title: str
    summary: str
    content_markdown: str
    tags: list[str]
    source_urls: list[str]
    status: str
    current_revision: int
    created_by: str
    persona_ids: list[str]
    created_at: str
    updated_at: str
    origin_source_type: str | None
    origin_source_id: str | None
    origin_hook_id: str | None
    origin_hook_run_id: str | None
    origin_team_run_id: str | None
    origin_cycle_id: str | None
    origin_request_id: str | None


@dataclass(frozen=True)
class ArchiveRevision:
    id: str
    entry_id: str
    revision: int
    kind: str
    title: str
    summary: str
    content_markdown: str
    tags: list[str]
    source_urls: list[str]
    change_summary: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class KnowledgeRequest:
    id: str
    title: str
    reason: str
    suggested_outline: list[str]
    source_hints: list[str]
    requested_by_persona_id: str | None
    requested_by_persona_name: str | None
    session_id: str | None
    team_run_id: str | None
    assigned_team_run_id: str | None
    status: str
    fulfilled_by_entry_id: str | None
    last_draft_error_code: str | None
    last_draft_error_message: str | None
    last_draft_failed_at: str | None
    last_draft_cycle_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LibraryDraftPayload:
    kind: str
    title: str
    summary: str
    content_markdown: str
    tags: list[str]
    source_urls: list[str]
    persona_ids: list[str]


def library_draft_output_contract() -> str:
    return (
        "LIBRARY DRAFT OUTPUT CONTRACT:\n"
        "- Research and synthesize a reusable document. Prefer primary sources and record source URLs.\n"
        "- This output becomes a private review draft. It is not published until the user reviews it.\n"
        "- End the final response with exactly one JSON marker in this shape:\n"
        '<library_draft>{"kind":"procedure|search_method|'
        'implementation_pattern|reference|checklist",'
        '"title":"document title","summary":"short summary",'
        '"content_markdown":"complete Markdown document",'
        '"tags":["tag"],"source_urls":["https://source.example"],'
        '"persona_ids":[]}</library_draft>\n'
        "- Use only known persona IDs in persona_ids; use [] for a shared Library draft.\n"
        "- Do not write anything after the closing </library_draft> marker."
    )


def parse_library_draft_response(
    content: str,
) -> tuple[str, LibraryDraftPayload]:
    if (
        content.count(_LIBRARY_DRAFT_OPEN) != 1
        or content.count(_LIBRARY_DRAFT_CLOSE) != 1
    ):
        raise ValueError("Team response must contain exactly one Library Draft marker")
    start = content.index(_LIBRARY_DRAFT_OPEN)
    payload_start = start + len(_LIBRARY_DRAFT_OPEN)
    end = content.index(_LIBRARY_DRAFT_CLOSE, payload_start)
    trailing = content[end + len(_LIBRARY_DRAFT_CLOSE) :]
    if trailing.strip():
        raise ValueError("Library Draft marker must be the final response content")
    raw_payload = content[payload_start:end].strip()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Library Draft marker must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(  # noqa: TRY004
            "Library Draft marker must contain a JSON object"
        )
    kind = _draft_string(payload, "kind")
    title = _draft_string(payload, "title")
    summary = _draft_string(payload, "summary", allow_empty=True)
    content_markdown = _draft_string(payload, "content_markdown")
    tags = _draft_string_list(payload, "tags")
    source_urls = _draft_string_list(payload, "source_urls")
    persona_ids = _draft_string_list(payload, "persona_ids")
    normalized = _validated_entry(
        kind,
        title,
        summary,
        content_markdown,
        tags,
        source_urls,
        persona_ids,
    )
    clean_response = content[:start].strip()
    return (
        clean_response,
        LibraryDraftPayload(
            kind=str(normalized["kind"]),
            title=str(normalized["title"]),
            summary=str(normalized["summary"]),
            content_markdown=str(normalized["content_markdown"]),
            tags=[str(value) for value in normalized["tags"]],
            source_urls=[str(value) for value in normalized["source_urls"]],
            persona_ids=[str(value) for value in normalized["persona_ids"]],
        ),
    )


class ArchiveService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def publish_entry(
        self,
        *,
        actor_type: str,
        kind: str,
        title: str,
        summary: str,
        content_markdown: str,
        tags: list[str],
        source_urls: list[str],
        persona_ids: list[str],
        entry_id: str | None = None,
        change_summary: str = "",
        request_id: str | None = None,
    ) -> ArchiveEntry:
        if actor_type != "user":
            raise ValueError("Only the user can publish canonical Archive entries")
        normalized = _validated_entry(
            kind,
            title,
            summary,
            content_markdown,
            tags,
            source_urls,
            persona_ids,
        )
        now = _now()
        resolved_id = entry_id or uuid4().hex
        with self._db.connection() as connection:
            self._validate_personas(connection, normalized["persona_ids"])
            row = connection.execute(
                "select * from archive_entries where id = ?",
                (resolved_id,),
            ).fetchone()
            if entry_id is not None and row is None:
                raise KeyError(f"Archive entry not found: {entry_id}")
            revision = int(row["current_revision"]) + 1 if row is not None else 1
            if row is None:
                connection.execute(
                    """
                    insert into archive_entries (
                        id, kind, title, summary, content_markdown, tags_json,
                        source_urls_json, status, current_revision, created_by,
                        created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 'published', ?, 'user', ?, ?)
                    """,
                    (
                        resolved_id,
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        revision,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    update archive_entries
                    set kind = ?, title = ?, summary = ?, content_markdown = ?,
                        tags_json = ?, source_urls_json = ?, status = 'published',
                        current_revision = ?, updated_at = ?
                    where id = ?
                    """,
                    (
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        revision,
                        now,
                        resolved_id,
                    ),
                )
            connection.execute(
                """
                insert into archive_revisions (
                    id, entry_id, revision, kind, title, summary, content_markdown,
                    tags_json, source_urls_json, change_summary, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?)
                """,
                (
                    uuid4().hex,
                    resolved_id,
                    revision,
                    normalized["kind"],
                    normalized["title"],
                    normalized["summary"],
                    normalized["content_markdown"],
                    _json(normalized["tags"]),
                    _json(normalized["source_urls"]),
                    change_summary.strip(),
                    now,
                ),
            )
            self._replace_bindings(connection, resolved_id, normalized["persona_ids"], now)
            self._index_entry(connection, resolved_id, normalized)
            if request_id is not None:
                self._fulfill_request(connection, request_id, resolved_id, now)
        return self.get_entry(resolved_id)

    def save_draft(
        self,
        *,
        actor_type: str,
        origin_source_type: str,
        origin_source_id: str,
        kind: str,
        title: str,
        summary: str,
        content_markdown: str,
        tags: list[str],
        source_urls: list[str],
        persona_ids: list[str],
        origin_hook_id: str | None = None,
        origin_hook_run_id: str | None = None,
        origin_team_run_id: str | None = None,
        origin_cycle_id: str | None = None,
        origin_request_id: str | None = None,
    ) -> ArchiveEntry:
        if actor_type != "team":
            raise ValueError("Only a documentation team can save a generated Archive draft")
        clean_source_type = origin_source_type.strip()
        clean_source_id = _required(origin_source_id, "Draft source id", 128)
        if clean_source_type not in _DRAFT_SOURCE_TYPES:
            raise ValueError(f"Invalid Archive draft source: {clean_source_type}")
        normalized = _validated_entry(
            kind,
            title,
            summary,
            content_markdown,
            tags,
            source_urls,
            persona_ids,
        )
        now = _now()
        with self._db.connection() as connection:
            existing = connection.execute(
                """
                select entry_id from archive_draft_origins
                where source_type = ? and source_id = ?
                """,
                (clean_source_type, clean_source_id),
            ).fetchone()
            if existing is not None:
                existing_id = existing["entry_id"]
            else:
                self._validate_personas(connection, normalized["persona_ids"])
                existing_id = uuid4().hex
                connection.execute(
                    """
                    insert into archive_entries (
                        id, kind, title, summary, content_markdown, tags_json,
                        source_urls_json, status, current_revision, created_by,
                        created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 'draft', 1, 'team', ?, ?)
                    """,
                    (
                        existing_id,
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    insert into archive_revisions (
                        id, entry_id, revision, kind, title, summary,
                        content_markdown, tags_json, source_urls_json,
                        change_summary, created_by, created_at
                    ) values (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'team', ?)
                    """,
                    (
                        uuid4().hex,
                        existing_id,
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        "Generated by documentation team",
                        now,
                    ),
                )
                self._replace_bindings(
                    connection,
                    existing_id,
                    normalized["persona_ids"],
                    now,
                )
                connection.execute(
                    """
                    insert into archive_draft_origins (
                        entry_id, source_type, source_id, hook_id, hook_run_id,
                        team_run_id, cycle_id, knowledge_request_id, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        existing_id,
                        clean_source_type,
                        clean_source_id,
                        origin_hook_id,
                        origin_hook_run_id,
                        origin_team_run_id,
                        origin_cycle_id,
                        origin_request_id,
                        now,
                    ),
                )
        return self.get_entry(existing_id)

    def save_team_note(
        self,
        *,
        actor_type: str,
        team_id: str,
        kind: str,
        title: str,
        summary: str,
        content_markdown: str,
        tags: list[str],
        source_urls: list[str],
        team_run_id: str | None = None,
        cycle_id: str | None = None,
    ) -> ArchiveEntry:
        """Write or revise the one note a team keeps about its own work.

        One note per team, revised in place -- not one per cycle. A note per
        cycle would leave twenty near-duplicates behind a single run, and the
        reader would have to work out which one is current. Revising keeps a
        single answer to "what does this team know", and the revision table
        already holds what each cycle changed.

        It is bound to the team, not to the run, so the next run of the same
        team starts with what the last one learned instead of deriving it
        again. It stays a draft: the user publishes it if it turns out to be
        worth more than this team.
        """
        if actor_type != "team":
            raise ValueError("Only a team can save its own note")
        clean_team_id = _required(team_id, "Team id", 128)
        if len(content_markdown.strip()) > TEAM_NOTE_MAX_CHARS:
            msg = f"Team note must be at most {TEAM_NOTE_MAX_CHARS} characters"
            raise ValueError(msg)
        normalized = _validated_entry(
            kind, title, summary, content_markdown, tags, source_urls, []
        )
        now = _now()
        with self._db.connection() as connection:
            existing = connection.execute(
                """
                select entry_id from archive_draft_origins
                where source_type = 'team_note' and source_id = ?
                """,
                (clean_team_id,),
            ).fetchone()
            if existing is None:
                entry_id = uuid4().hex
                revision = 1
                connection.execute(
                    """
                    insert into archive_entries (
                        id, kind, title, summary, content_markdown, tags_json,
                        source_urls_json, status, current_revision, created_by,
                        created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 'draft', 1, 'team', ?, ?)
                    """,
                    (
                        entry_id,
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    insert into archive_bindings (entry_id, scope, scope_id, created_at)
                    values (?, 'team', ?, ?)
                    """,
                    (entry_id, clean_team_id, now),
                )
                connection.execute(
                    """
                    insert into archive_draft_origins (
                        entry_id, source_type, source_id, team_run_id, cycle_id,
                        created_at
                    ) values (?, 'team_note', ?, ?, ?, ?)
                    """,
                    (entry_id, clean_team_id, team_run_id, cycle_id, now),
                )
            else:
                entry_id = str(existing["entry_id"])
                row = connection.execute(
                    "select current_revision from archive_entries where id = ?",
                    (entry_id,),
                ).fetchone()
                revision = int(row["current_revision"]) + 1
                connection.execute(
                    """
                    update archive_entries set kind = ?, title = ?, summary = ?,
                        content_markdown = ?, tags_json = ?, source_urls_json = ?,
                        current_revision = ?, updated_at = ?
                    where id = ?
                    """,
                    (
                        normalized["kind"],
                        normalized["title"],
                        normalized["summary"],
                        normalized["content_markdown"],
                        _json(normalized["tags"]),
                        _json(normalized["source_urls"]),
                        revision,
                        now,
                        entry_id,
                    ),
                )
                # The origin points at the cycle that last touched the note, so
                # a reader asking "when was this last true" lands on that cycle
                # rather than on the run that first created it.
                connection.execute(
                    """
                    update archive_draft_origins
                    set team_run_id = ?, cycle_id = ?
                    where entry_id = ?
                    """,
                    (team_run_id, cycle_id, entry_id),
                )
            connection.execute(
                """
                insert into archive_revisions (
                    id, entry_id, revision, kind, title, summary,
                    content_markdown, tags_json, source_urls_json,
                    change_summary, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'team', ?)
                """,
                (
                    uuid4().hex,
                    entry_id,
                    revision,
                    normalized["kind"],
                    normalized["title"],
                    normalized["summary"],
                    normalized["content_markdown"],
                    _json(normalized["tags"]),
                    _json(normalized["source_urls"]),
                    f"Team note revised in cycle {cycle_id or '(unknown)'}",
                    now,
                ),
            )
            self._index_entry(connection, entry_id, normalized)
        return self.get_entry(entry_id)

    def get_team_note(self, team_id: str) -> ArchiveEntry | None:
        """이 팀이 지금 들고 있는 노트. 없으면 None.

        검색을 거치지 않는다. 리드가 노트를 갈아치우려면 관련어가 걸리든
        말든 현재 내용 전부를 봐야 한다 -- 검색으로 가져오면 안 걸린 부분을
        모르는 채로 지우게 된다.
        """
        row = self._db.fetchone(
            """
            select e.* from archive_entries e
            join archive_draft_origins o on o.entry_id = e.id
            where o.source_type = 'team_note' and o.source_id = ?
            """,
            (team_id,),
        )
        return None if row is None else self._entry_from_row(row)

    def get_entry(self, entry_id: str) -> ArchiveEntry:
        row = self._db.fetchone("select * from archive_entries where id = ?", (entry_id,))
        if row is None:
            raise KeyError(f"Archive entry not found: {entry_id}")
        return self._entry_from_row(row)

    def delete_entry(self, entry_id: str) -> str:
        """Hard-delete an Archive entry in any state and return the status it had.

        Returning the prior status lets the route split its audit event without a
        second query.
        """
        with self._db.connection() as connection:
            row = connection.execute(
                "select status from archive_entries where id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Archive entry not found: {entry_id}")
            status = str(row["status"])
            now = _now()

            # A document reaches a knowledge request through two unrelated links, and a
            # single document can hold both: a draft records its origin request, while a
            # published document is recorded as the request's fulfiller. Reset both —
            # deleting the document makes that knowledge need open again either way.
            origin = connection.execute(
                "select knowledge_request_id from archive_draft_origins where entry_id = ?",
                (entry_id,),
            ).fetchone()
            if origin is not None and origin["knowledge_request_id"]:
                connection.execute(
                    """
                    update knowledge_requests
                    set status = 'open', assigned_team_run_id = null, updated_at = ?
                    where id = ? and status = 'in_progress'
                    """,
                    (now, origin["knowledge_request_id"]),
                )
            # Must run before the entry row is deleted: the foreign key is
            # `on delete set null`, so deleting first would erase the link.
            connection.execute(
                """
                update knowledge_requests
                set status = 'open',
                    fulfilled_by_entry_id = null,
                    assigned_team_run_id = null,
                    updated_at = ?
                where fulfilled_by_entry_id = ? and status = 'fulfilled'
                """,
                (now, entry_id),
            )
            connection.execute("delete from archive_entries_fts where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_bindings where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_revisions where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_draft_origins where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_entries where id = ?", (entry_id,))
            return status

    def list_entries(
        self,
        *,
        query: str = "",
        kind: str | None = None,
        status: str = "published",
        limit: int = 100,
    ) -> list[ArchiveEntry]:
        if kind is not None and kind not in _KINDS:
            raise ValueError(f"Invalid Archive kind: {kind}")
        safe_limit = max(1, min(limit, 200))
        params: list[object] = [status]
        clauses = ["e.status = ?"]
        join = ""
        order = "e.updated_at desc, e.id"
        fts_query = _fts_query(query)
        if fts_query:
            join = "join archive_entries_fts on archive_entries_fts.entry_id = e.id"
            clauses.append("archive_entries_fts match ?")
            params.append(fts_query)
            order = "bm25(archive_entries_fts), e.updated_at desc"
        if kind:
            clauses.append("e.kind = ?")
            params.append(kind)
        params.append(safe_limit)
        rows = self._db.fetchall(
            f"""
            select e.* from archive_entries e
            {join}
            where {' and '.join(clauses)}
            order by {order}
            limit ?
            """,
            params,
        )
        return [self._entry_from_row(row) for row in rows]

    def search_entries(
        self,
        query: str,
        *,
        persona_id: str | None,
        limit: int = 5,
    ) -> list[ArchiveEntry]:
        """Published knowledge that matches the message.

        Team notes are deliberately not searched here. A team has exactly one
        note and it is capped small, so there is nothing to choose between --
        and search misses: a short cycle instruction shares no word with the
        note and returns nothing, at the planning stage where the note matters
        most. `get_team_note` reads it directly instead.
        """
        terms = _query_terms(query)
        if not terms:
            return []
        fts_query = _fts_match(terms)
        scope_clause = (
            """
            exists (
                select 1 from archive_bindings binding
                where binding.entry_id = e.id
                  and (
                    binding.scope = 'global'
                    or (binding.scope = 'persona' and binding.scope_id = ?)
                  )
            )
            """
            if persona_id is not None
            else """
            exists (
                select 1 from archive_bindings binding
                where binding.entry_id = e.id and binding.scope = 'global'
            )
            """
        )
        capped = max(1, min(limit, 20))
        params: list[object] = [fts_query]
        if persona_id is not None:
            params.append(persona_id)
        # 걸러낼 여유를 두고 넉넉히 뽑는다. 검색이 준 순위는 그대로 쓰고,
        # 약하게 걸린 것만 아래에서 떨어뜨린다.
        params.append(capped * 4)
        rows = self._db.fetchall(
            f"""
            select e.* from archive_entries e
            join archive_entries_fts on archive_entries_fts.entry_id = e.id
            where e.status = 'published'
              and archive_entries_fts match ?
              and {scope_clause}
            order by bm25(archive_entries_fts), e.updated_at desc
            limit ?
            """,
            params,
        )
        required = min(_MIN_TERM_OVERLAP, len(terms))
        kept = []
        for row in rows:
            if _term_overlap(row, terms) >= required:
                kept.append(self._entry_from_row(row))
            if len(kept) == capped:
                break
        return kept

    def archive_entry(self, entry_id: str) -> ArchiveEntry:
        self.get_entry(entry_id)
        self._db.execute(
            "update archive_entries set status = 'archived', updated_at = ? where id = ?",
            (_now(), entry_id),
        )
        return self.get_entry(entry_id)

    def list_revisions(self, entry_id: str) -> list[ArchiveRevision]:
        self.get_entry(entry_id)
        rows = self._db.fetchall(
            "select * from archive_revisions where entry_id = ? order by revision desc",
            (entry_id,),
        )
        return [_revision_from_row(row) for row in rows]

    def create_knowledge_request(
        self,
        *,
        title: str,
        reason: str,
        suggested_outline: list[str],
        source_hints: list[str],
        requested_by_persona_id: str | None,
        session_id: str | None = None,
        team_run_id: str | None = None,
    ) -> KnowledgeRequest:
        clean_title = _required(title, "Request title", 160)
        clean_reason = _required(reason, "Request reason", 1000)
        outline = _clean_list(suggested_outline, max_items=12, max_length=240)
        hints = _clean_list(source_hints, max_items=12, max_length=500)
        with self._db.connection() as connection:
            existing = connection.execute(
                """
                select request.*, persona.name as requested_by_persona_name
                from knowledge_requests request
                left join personas persona on persona.id = request.requested_by_persona_id
                where lower(trim(request.title)) = ?
                  and request.status in ('open', 'in_progress', 'deferred')
                  and (
                    request.requested_by_persona_id = ?
                    or (request.requested_by_persona_id is null and ? is null)
                  )
                order by request.created_at desc
                limit 1
                """,
                (clean_title.casefold(), requested_by_persona_id, requested_by_persona_id),
            ).fetchone()
            if existing is not None:
                return _request_from_row(existing)
            if requested_by_persona_id is not None:
                persona = connection.execute(
                    "select id from personas where id = ?",
                    (requested_by_persona_id,),
                ).fetchone()
                if persona is None:
                    raise KeyError(f"Persona not found: {requested_by_persona_id}")
            request_id = uuid4().hex
            now = _now()
            connection.execute(
                """
                insert into knowledge_requests (
                    id, title, reason, suggested_outline_json, source_hints_json,
                    requested_by_persona_id, session_id, team_run_id, status,
                    fulfilled_by_entry_id, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, 'open', null, ?, ?)
                """,
                (
                    request_id,
                    clean_title,
                    clean_reason,
                    _json(outline),
                    _json(hints),
                    requested_by_persona_id,
                    session_id,
                    team_run_id,
                    now,
                    now,
                ),
            )
        return self.get_request(request_id)

    def get_request(self, request_id: str) -> KnowledgeRequest:
        row = self._db.fetchone(
            """
            select request.*, persona.name as requested_by_persona_name
            from knowledge_requests request
            left join personas persona on persona.id = request.requested_by_persona_id
            where request.id = ?
            """,
            (request_id,),
        )
        if row is None:
            raise KeyError(f"Knowledge request not found: {request_id}")
        return _request_from_row(row)

    def list_requests(
        self,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[KnowledgeRequest]:
        if status is not None and status not in _REQUEST_STATUSES:
            raise ValueError(f"Invalid request status: {status}")
        params: list[object] = []
        where = ""
        if status is not None:
            where = "where request.status = ?"
            params.append(status)
        params.append(max(1, min(limit, 500)))
        rows = self._db.fetchall(
            f"""
            select request.*, persona.name as requested_by_persona_name
            from knowledge_requests request
            left join personas persona on persona.id = request.requested_by_persona_id
            {where}
            order by
                case request.status
                    when 'open' then 0
                    when 'in_progress' then 1
                    when 'deferred' then 2
                    else 3
                end,
                request.created_at desc
            limit ?
            """,
            params,
        )
        return [_request_from_row(row) for row in rows]

    def update_request_status(self, request_id: str, status: str) -> KnowledgeRequest:
        if status not in _USER_REQUEST_STATUSES:
            raise ValueError(f"Invalid user request status: {status}")
        request = self.get_request(request_id)
        if request.status == "fulfilled":
            raise ValueError("A fulfilled request cannot be reopened")
        self._db.execute(
            "update knowledge_requests set status = ?, updated_at = ? where id = ?",
            (status, _now(), request_id),
        )
        return self.get_request(request_id)

    def record_draft_failure(
        self,
        request_id: str,
        *,
        error_code: str,
        message: str,
        cycle_id: str | None,
    ) -> KnowledgeRequest:
        request = self.get_request(request_id)
        if request.status == "fulfilled":
            raise ValueError("A fulfilled request cannot record a draft failure")
        if (
            request.last_draft_error_code == error_code
            and request.last_draft_cycle_id == cycle_id
        ):
            return request
        now = _now()
        self._db.execute(
            """
            update knowledge_requests
            set status = 'open',
                last_draft_error_code = ?,
                last_draft_error_message = ?,
                last_draft_failed_at = ?,
                last_draft_cycle_id = ?,
                updated_at = ?
            where id = ?
            """,
            (
                error_code,
                message.strip()[:_DRAFT_ERROR_MESSAGE_LIMIT],
                now,
                cycle_id,
                now,
                request_id,
            ),
        )
        return self.get_request(request_id)

    def clear_draft_failure(self, request_id: str) -> KnowledgeRequest:
        self.get_request(request_id)
        self._db.execute(
            """
            update knowledge_requests
            set last_draft_error_code = null,
                last_draft_error_message = null,
                last_draft_failed_at = null,
                last_draft_cycle_id = null,
                updated_at = ?
            where id = ?
            """,
            (_now(), request_id),
        )
        return self.get_request(request_id)

    def assign_request_team(
        self,
        request_id: str,
        team_run_id: str,
    ) -> KnowledgeRequest:
        request = self.get_request(request_id)
        if request.status not in _ACTIVE_REQUEST_STATUSES:
            raise ValueError("Only an active Knowledge Request can be delegated")
        if (
            request.status == "in_progress"
            and request.assigned_team_run_id is not None
            and request.assigned_team_run_id != team_run_id
        ):
            raise ValueError("Knowledge Request is already assigned to another Team Run")
        team = self._db.fetchone("select id from team_runs where id = ?", (team_run_id,))
        if team is None:
            raise KeyError(f"Team Run not found: {team_run_id}")
        self._db.execute(
            """
            update knowledge_requests
            set assigned_team_run_id = ?,
                status = 'in_progress',
                last_draft_error_code = null,
                last_draft_error_message = null,
                last_draft_failed_at = null,
                last_draft_cycle_id = null,
                updated_at = ?
            where id = ?
            """,
            (team_run_id, _now(), request_id),
        )
        return self.get_request(request_id)

    def capture_response_requests(
        self,
        content: str,
        *,
        persona_id: str | None,
        session_id: str | None = None,
        team_run_id: str | None = None,
    ) -> tuple[str, list[KnowledgeRequest]]:
        requests: list[KnowledgeRequest] = []
        for match in _REQUEST_PATTERN.finditer(content):
            payload = _request_payload(match.group(1))
            if payload is None:
                continue
            request = self.create_knowledge_request(
                title=payload["title"],
                reason=payload["reason"],
                suggested_outline=payload["suggested_outline"],
                source_hints=payload["source_hints"],
                requested_by_persona_id=persona_id,
                session_id=session_id,
                team_run_id=team_run_id,
            )
            requests.append(request)
        clean = _REQUEST_PATTERN.sub("", content).strip()
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        if requests:
            notice = "Library에 요청되었습니다"
            clean = f"{clean}\n\n{notice}" if clean else notice
        return clean, requests

    def prompt_context(
        self,
        query: str,
        *,
        persona_id: str | None,
        allow_request: bool = True,
    ) -> str:
        entries = self.search_entries(query, persona_id=persona_id)
        lines = [
            "ARCHIVE POLICY:",
            "- Archive entries below are user-authored, published knowledge.",
            "- Knowledge Requests are gaps, not facts, and are never included as evidence.",
        ]
        if entries:
            lines.append("")
            lines.append("RELEVANT PUBLISHED ARCHIVE ENTRIES:")
            for entry in entries:
                lines.extend(
                    [
                        f"[{entry.kind}] {entry.title}",
                        entry.summary,
                        _excerpt(entry.content_markdown, 1000),
                    ]
                )
                if entry.source_urls:
                    lines.append("Sources: " + ", ".join(entry.source_urls[:5]))
                lines.append("")
        else:
            lines.extend(["", "No relevant published Archive entry was found for this message."])
        if allow_request:
            lines.extend(
                [
                    "If reusable knowledge is materially missing, first ask the user to document it.",
                    "Only when no suitable published entry exists, append this machine-readable marker:",
                    (
                        '<knowledge_request>{"title":"short document title",'
                        '"reason":"why reusable documentation is needed",'
                        '"suggested_outline":["section"],'
                        '"source_hints":["source to check"]}</knowledge_request>'
                    ),
                    "Never put a document body in the marker. The user will author and publish it in Library.",
                ]
            )
        return "\n".join(lines).strip()

    def _entry_from_row(self, row: sqlite3.Row) -> ArchiveEntry:
        bindings = self._db.fetchall(
            """
            select scope_id from archive_bindings
            where entry_id = ? and scope = 'persona'
            order by scope_id
            """,
            (row["id"],),
        )
        origin = self._db.fetchone(
            "select * from archive_draft_origins where entry_id = ?",
            (row["id"],),
        )
        return ArchiveEntry(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            summary=row["summary"],
            content_markdown=row["content_markdown"],
            tags=list(json.loads(row["tags_json"])),
            source_urls=list(json.loads(row["source_urls_json"])),
            status=row["status"],
            current_revision=int(row["current_revision"]),
            created_by=row["created_by"],
            persona_ids=[binding["scope_id"] for binding in bindings],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            origin_source_type=origin["source_type"] if origin is not None else None,
            origin_source_id=origin["source_id"] if origin is not None else None,
            origin_hook_id=origin["hook_id"] if origin is not None else None,
            origin_hook_run_id=origin["hook_run_id"] if origin is not None else None,
            origin_team_run_id=origin["team_run_id"] if origin is not None else None,
            origin_cycle_id=origin["cycle_id"] if origin is not None else None,
            origin_request_id=(
                origin["knowledge_request_id"] if origin is not None else None
            ),
        )

    @staticmethod
    def _validate_personas(
        connection: sqlite3.Connection,
        persona_ids: list[str],
    ) -> None:
        for persona_id in persona_ids:
            row = connection.execute(
                "select id from personas where id = ?",
                (persona_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Persona not found: {persona_id}")

    @staticmethod
    def _replace_bindings(
        connection: sqlite3.Connection,
        entry_id: str,
        persona_ids: list[str],
        now: str,
    ) -> None:
        connection.execute("delete from archive_bindings where entry_id = ?", (entry_id,))
        if not persona_ids:
            connection.execute(
                """
                insert into archive_bindings (entry_id, scope, scope_id, created_at)
                values (?, 'global', '', ?)
                """,
                (entry_id, now),
            )
            return
        connection.executemany(
            """
            insert into archive_bindings (entry_id, scope, scope_id, created_at)
            values (?, 'persona', ?, ?)
            """,
            [(entry_id, persona_id, now) for persona_id in persona_ids],
        )

    @staticmethod
    def _index_entry(
        connection: sqlite3.Connection,
        entry_id: str,
        entry: dict[str, object],
    ) -> None:
        connection.execute(
            "delete from archive_entries_fts where entry_id = ?",
            (entry_id,),
        )
        connection.execute(
            """
            insert into archive_entries_fts (
                entry_id, title, summary, content_markdown, tags
            ) values (?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                entry["title"],
                entry["summary"],
                entry["content_markdown"],
                " ".join(entry["tags"]),
            ),
        )

    @staticmethod
    def _fulfill_request(
        connection: sqlite3.Connection,
        request_id: str,
        entry_id: str,
        now: str,
    ) -> None:
        row = connection.execute(
            "select id from knowledge_requests where id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Knowledge request not found: {request_id}")
        connection.execute(
            """
            update knowledge_requests
            set status = 'fulfilled', fulfilled_by_entry_id = ?, updated_at = ?
            where id = ?
            """,
            (entry_id, now, request_id),
        )


def _validated_entry(
    kind: str,
    title: str,
    summary: str,
    content_markdown: str,
    tags: list[str],
    source_urls: list[str],
    persona_ids: list[str],
) -> dict[str, object]:
    if kind not in _KINDS:
        raise ValueError(f"Invalid Archive kind: {kind}")
    clean_content = _required(content_markdown, "Archive content", 100_000)
    return {
        "kind": kind,
        "title": _required(title, "Archive title", 200),
        "summary": summary.strip()[:1000],
        "content_markdown": clean_content,
        "tags": _clean_list(tags, max_items=20, max_length=80),
        "source_urls": _clean_list(source_urls, max_items=20, max_length=2000),
        "persona_ids": list(dict.fromkeys(_clean_list(persona_ids, 100, 64))),
    }


def _draft_string(
    payload: dict[object, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004
            f"Library Draft field must be a string: {key}"
        )
    if not allow_empty and not value.strip():
        raise ValueError(f"Library Draft field is required: {key}")
    return value


def _draft_string_list(payload: dict[object, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Library Draft field must be a string list: {key}")
    return value


def _request_payload(raw: str) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    reason = payload.get("reason")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    outline = payload.get("suggested_outline")
    hints = payload.get("source_hints")
    return {
        "title": title,
        "reason": reason,
        "suggested_outline": outline if isinstance(outline, list) else [],
        "source_hints": hints if isinstance(hints, list) else [],
    }


def _request_from_row(row: sqlite3.Row) -> KnowledgeRequest:
    return KnowledgeRequest(
        id=row["id"],
        title=row["title"],
        reason=row["reason"],
        suggested_outline=list(json.loads(row["suggested_outline_json"])),
        source_hints=list(json.loads(row["source_hints_json"])),
        requested_by_persona_id=row["requested_by_persona_id"],
        requested_by_persona_name=row["requested_by_persona_name"],
        session_id=row["session_id"],
        team_run_id=row["team_run_id"],
        assigned_team_run_id=row["assigned_team_run_id"],
        status=row["status"],
        fulfilled_by_entry_id=row["fulfilled_by_entry_id"],
        last_draft_error_code=row["last_draft_error_code"],
        last_draft_error_message=row["last_draft_error_message"],
        last_draft_failed_at=row["last_draft_failed_at"],
        last_draft_cycle_id=row["last_draft_cycle_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _revision_from_row(row: sqlite3.Row) -> ArchiveRevision:
    return ArchiveRevision(
        id=row["id"],
        entry_id=row["entry_id"],
        revision=int(row["revision"]),
        kind=row["kind"],
        title=row["title"],
        summary=row["summary"],
        content_markdown=row["content_markdown"],
        tags=list(json.loads(row["tags_json"])),
        source_urls=list(json.loads(row["source_urls_json"])),
        change_summary=row["change_summary"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _clean_list(
    values: list[object],
    max_items: int,
    max_length: int,
) -> list[str]:
    clean: list[str] = []
    for value in values[:max_items]:
        text = str(value).strip()
        if not text:
            continue
        clean.append(text[:max_length])
    return list(dict.fromkeys(clean))


def _required(value: str, label: str, max_length: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} is required")
    if len(clean) > max_length:
        raise ValueError(f"{label} must be {max_length} characters or fewer")
    return clean


def _query_terms(query: str) -> list[str]:
    """검색에 쓸 단어. 중복을 지우고, 한 글자짜리는 버리고, 앞에서 자르지 않는다.

    앞에서 열두 개만 자르던 때는 실측에서 이런 일이 있었다: 일감이 "이거 근데
    번역 요청할 때 모델을..." 로 시작해서 `이거`, `근데`, `때`, `그` 가 자리를
    다 차지하고, 정작 그 일감의 핵심인 `문법`, `인용`, `어긋나는지` 는 설명
    뒷부분에 있어 검색에 들어가지도 못했다.
    """
    terms = re.findall(r"[\w-]+", query.casefold(), flags=re.UNICODE)
    unique = [term for term in dict.fromkeys(terms) if len(term) >= _MIN_TERM_LENGTH]
    return unique[:_MAX_QUERY_TERMS]


def _fts_query(query: str) -> str:
    return _fts_match(_query_terms(query))


def _fts_match(terms: list[str]) -> str:
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms)


def _term_overlap(entry_row: object, terms: list[str]) -> int:
    haystack = " ".join(
        str(entry_row[field]) for field in ("title", "summary", "content_markdown")
    ).casefold()
    return sum(1 for term in terms if term in haystack)


def _excerpt(content: str, length: int) -> str:
    if len(content) <= length:
        return content
    return content[: length - 1].rstrip() + "…"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()
