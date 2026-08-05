---
title: Artifact browser provenance, search, preview, and deletion design
type: adr
domain: artifacts
feature: artifact-browser
status: draft
aliases:
  - artifact grouping design
  - artifact search and delete
  - Chat Team artifact browser
  - artifact 그룹 검색 삭제
tags:
  - artifacts
  - archive
  - provenance
  - search
  - deletion
updated_at: 2026-08-05
---

# Artifact browser provenance, search, preview, and deletion design

## Goal

Replace the raw artifact card gallery with a readable browser that answers four questions without
requiring the user to interpret IDs or paths:

1. What is this file?
2. Which Chat turn, Team task, scheduled job, or manual action created it?
3. How can I find it again?
4. Can I delete it safely, including several files at once?

The design covers the data model, read API, search, deletion, grouping, and preview. It supersedes
the grouping and list sections of
`docs/superpowers/specs/2026-08-05-artifact-archive-preview-ux-design.md`; the existing retention
and cleanup eligibility rules remain valid.

## Success criteria

- Chat and Team artifacts share one browser without losing their distinct hierarchy.
- The default list contains human-readable source labels, not raw run/task/session IDs.
- Search examines the complete artifact catalog on the server, not only the first client page.
- A user can delete one artifact or a reviewed selection; referenced Team inputs remain protected.
- Markdown, text, JSON, PDF, image, audio, and video artifacts have useful previews.
- Internal path and identifier details are available but collapsed by default.
- Existing artifacts receive a deterministic legacy origin during migration.

## Non-goals

- Full-text indexing of artifact file contents.
- Automatic deletion when `expires_at` passes.
- Content-addressed blob deduplication.
- A generic provenance graph or arbitrary nested collection system.
- A recycle bin or undo after confirmed deletion.
- User-created collections in this iteration. The schema must not prevent adding them later.

## Approaches considered

### A. Persisted generic collection/producer tree

Store every Chat session, turn, Team run, cycle, task, and job as generic collection or producer
rows with polymorphic `source_type` and `source_id` fields.

This is flexible but rejected. It duplicates domain hierarchies already owned by Chat, Team, and
Jobs; copied labels and parent links can drift. Polymorphic IDs also give SQLite no useful foreign
key integrity.

### B. Explicit artifact origin plus computed browser context — selected

Keep one immutable creation origin on each artifact using explicit foreign-key columns. Resolve the
human-readable Chat/Team/Job hierarchy by joining the owning domain records when the browser API is
called. Preserve labels as snapshots so archived files remain understandable if the source record
is later deleted.

This matches the current system, where each artifact row is already one published file instance and
contains `source_job_id` and `source_session_id`. It adds the minimum structure needed for reliable
grouping without introducing a second hierarchy.

### C. Generic many-to-many artifact/context links

Represent every relationship as `artifact_context_links(artifact_id, context_kind, context_id)`.

This supports arbitrary reuse but weakens integrity and makes it unclear which relationship is the
creation origin. It is deferred. Existing Team input tables already represent artifact consumption
with stronger domain-specific constraints.

## Core distinction

```text
Artifact
 ├── creation origin: exactly one
 ├── consumption references: zero or many
 └── manual collections: zero or many, deferred
```

An artifact created from a Chat-triggered Job remains a Chat/Job artifact even if a later Team task
uses it as input. The Team input relationship must not rewrite its origin.

## Data model

### Durable Chat turns

Add `chat_turns`:

| Column | Meaning |
|---|---|
| `id` | Stable turn ID; use the request ID currently created at the Chat API boundary |
| `session_id` | Owning transcript/session |
| `user_event_id` | Transcript event containing the initiating user message |
| `prompt_excerpt` | Bounded display snapshot used by the artifact browser |
| `status` | `running`, `completed`, `cancelled`, or `failed` |
| `created_at` | Turn start |
| `finished_at` | Terminal time, nullable while running |

The Chat API creates the row before invoking `AgentRuntime`. The runtime receives `chat_turn_id` and
passes it to every Job it creates. Add nullable `jobs.source_chat_turn_id` with `on delete set null`.
`jobs.source_session_id` remains for compatibility and efficient session queries.

This preserves both parts of the real provenance chain:

```text
Chat session → Chat turn → Job → Artifact
```

### Artifact origin columns

Extend `artifacts`:

| Column | Meaning |
|---|---|
| `origin_kind` | Controlled creation origin |
| `artifact_role` | Controlled semantic role, separate from MIME/type |
| `source_chat_turn_id` | Direct Chat/manual publication, normally null for Job output |
| `source_team_task_id` | Team task deliverable |
| `source_team_run_id` | Team run package artifact |
| `source_cycle_id` | Cycle for a Team run package, nullable for run-level package |
| `origin_group_label_snapshot` | Session title, Team goal, schedule name, or local label at publication |
| `origin_item_label_snapshot` | Turn excerpt, task title, job title, or package label at publication |

Keep the existing `source_job_id` and `source_session_id` fields. `metadata_json` remains available
for producer-specific diagnostic data but is no longer the authoritative source for grouping.

Allowed `origin_kind` values and references:

| `origin_kind` | Required source |
|---|---|
| `job_output` | `source_job_id` |
| `team_task_output` | `source_team_task_id` |
| `team_run_package` | `source_team_run_id`; optional `source_cycle_id` |
| `chat_upload` | `source_session_id`; optional `source_chat_turn_id` |
| `manual_upload` | no runtime source required; session is optional |
| `legacy` | no guaranteed source |

Application validation enforces these combinations. Migration-created indexes cover each non-null
source column and `(origin_kind, created_at, id)`.

`artifact_role` is a stable semantic label such as `deliverable`, `job_output`, `run_result`,
`manifest`, `verification`, or `attachment`. MIME/type continues to decide rendering; role decides
the user-facing badge and purpose.

### Deletion of source records

Artifact files outlive operational source records. Source foreign keys therefore use `on delete set
null`; the two label snapshots remain. Browser resolution uses the current source label when it
exists and falls back to the snapshot.

### Legacy backfill

Backfill existing rows in this order:

1. `metadata.task_id` plus a matching Team task → `team_task_output`.
2. `metadata.team_run_id` plus `metadata.package_kind` → `team_run_package`.
3. `source_job_id` → `job_output`; copy session context from the Job when available.
4. `source_session_id` → `chat_upload`.
5. Remaining rows → `legacy`.

Copy current Team goals, task titles, Job titles, and session titles into snapshots. Missing or stale
metadata does not fail migration; it produces a `legacy` item under “이전 파일”. Existing metadata
is retained for compatibility during the transition.

## Browser read API

Add:

```http
GET /api/artifacts/browser
    ?segment=saved|recent|cleanup
    &q=<query>
    &file_kind=<kind>
    &source_kind=<chat|team|job|schedule|manual|legacy>
    &limit=<1..200>
    &cursor=<opaque>
```

The endpoint returns flat, cursor-paged items with resolved breadcrumbs. The frontend merges loaded
pages by stable breadcrumb keys, avoiding a nested persistence model while still rendering groups.

```json
{
  "items": [
    {
      "artifact": {},
      "role": {"code": "deliverable", "label": "Task deliverable"},
      "source_kind": "team",
      "breadcrumbs": [
        {"kind": "team_run", "id": "...", "label": "D3 guide research"},
        {"kind": "cycle", "id": "...", "label": "Cycle 2"},
        {"kind": "team_task", "id": "...", "label": "Verify chart examples"}
      ],
      "deletion": {"allowed": true, "blocked_reason": null}
    }
  ],
  "counts": {"saved": 0, "recent": 0, "cleanup": 0},
  "next_cursor": null
}
```

Chat breadcrumbs are `session → turn`; scheduled work is `schedule → job`; an unscheduled Job is a
single Job group; manual and legacy artifacts use stable synthetic group keys.

The cleanup segment applies the existing cleanup eligibility policy before search and pagination.
It does not make expired artifacts automatically disappear.

## Search design

Search is server-side and case-insensitive. It matches:

- artifact title and stored filename;
- `artifact_role`, broad file kind, and tags;
- current and snapshot Chat session title/turn excerpt;
- current and snapshot Team goal, cycle label, and task title;
- current and snapshot Job or schedule title;
- raw source IDs as a secondary technical lookup.

It does not read file bodies. For the local catalog size, indexed joins plus escaped `LIKE` matching
are sufficient; SQLite FTS and content extraction are deferred until measured data requires them.

The UI debounces query changes, resets the cursor when filters change, and distinguishes loading,
no results, and request failure. Matching groups open automatically and matching text is emphasized
without injecting HTML.

## Deletion API and policy

Keep `DELETE /api/artifacts/{id}` for compatibility. Add a structured batch endpoint used by both
the row action and multi-selection UI:

```http
POST /api/artifacts/delete
Content-Type: application/json

{"artifact_ids": ["..."]}
```

Response:

```json
{
  "deleted_ids": ["..."],
  "blocked": [
    {
      "artifact_id": "...",
      "code": "artifact_in_use",
      "references": [
        {"kind": "team_task_input", "id": "...", "label": "Verify chart examples"}
      ]
    }
  ],
  "missing_ids": []
}
```

Deletion is explicit hard deletion. `durable`, `pinned`, unexpired, and temporary artifacts may all
be manually deleted after confirmation. Retention class controls cleanup eligibility, not owner
authority.

Artifacts referenced by a Team cycle request, cycle input, or task input are blocked. The API
returns the resolved usage rather than a boolean or generic 409 string. Batch deletion is partial:
safe items are deleted, blocked items remain selected and visible, and missing IDs are reported.
Every successful deletion is audited with the batch correlation ID.

The endpoint rejects an empty or duplicate ID list and caps one request at 200 IDs. It re-checks
references immediately before each deletion; the earlier browser `deletion.allowed` value is only a
display hint.

## Archive UI

### Information architecture

The existing card gallery becomes a compact list. The top bar contains:

- saved/recent/cleanup segments with server counts;
- one prominent search field;
- source and file-kind filters;
- a clear-filters action shown only when filters are active.

Results render as collapsible source groups. A row shows a small kind icon or image thumbnail,
title, semantic role badge, source breadcrumb, creation time, size, and retention state. Raw IDs and
paths never appear in the default list.

### Selection and deletion

Normal saved/recent rows support a deliberate selection mode. Cleanup rows retain their existing
unchecked checkboxes. There is no implicit select-all.

A sticky action bar shows selected count and total bytes:

- saved/recent: `선택 삭제`;
- cleanup: `선택 정리` and per-row `보관`.

Confirmation lists the count, total bytes, and up to five filenames, with the remaining count
summarized. After a partial batch result, deleted rows disappear, blocked rows remain selected, and
the toast/detail message names the blocking usages.

### Preview and details

Selecting a row opens an inspector that is a right-side panel on wide screens and a modal on narrow
screens. The list position and filters remain intact.

Renderer selection uses MIME and extension:

- image → zoom/pan image viewer;
- video/audio → native controlled media;
- PDF → embedded document;
- Markdown → fetched text rendered through `MarkdownContent` with path registration disabled;
- `text/*`, JSON, YAML, XML, source code, and logs → scrollable text/code view;
- unsupported binary → honest fallback with download action.

The inspector header shows title, role, readable source breadcrumbs, size, and created time.
Download and pin/keep are primary actions. Delete is a secondary danger action. A collapsed
“기술 정보” section contains MIME, relative path, source IDs, hashes, and raw metadata. Copy path
remains inside that section.

## Component boundaries

- `ArchiveView` remains the tab owner.
- `ArtifactsView` remains the Archive-specific browser owner.
- Extract browser query, pagination, selection, mutation, and refresh state into a feature-owned
  hook/model rather than continuing to grow the render body.
- Split same-owner presentation into toolbar, source group, row, selection action bar, and
  inspector sections. Do not promote them to global primitives without another real consumer.
- Keep `ArtifactModal` compatible with its Chat/Markdown usage. Its preview renderer may be shared;
  Archive selects the responsive inspector presentation.

## Error handling

- Browser request failure keeps the previous successful page and shows retry.
- Search request races are ignored using request identity or abort control.
- Preview text failure shows download and retry, not an empty document.
- Delete failure preserves selection.
- Partial deletion gives separate deleted/blocked/missing counts.
- A source record deleted after the list response falls back to origin snapshots.
- Malformed legacy metadata never breaks the whole browser response.

## Testing

### Backend

- migration and backfill for every `origin_kind`;
- Chat turn persisted and propagated through Job to artifact;
- Team task and package origins use explicit references rather than metadata grouping;
- browser breadcrumbs for Chat, Team, scheduled Job, manual, and legacy artifacts;
- search across title, filename, role, current labels, snapshots, and raw IDs;
- cursor/reset behavior with filters and search;
- cleanup segment retains current eligibility and reference protection;
- single, batch, partial, duplicate, empty, over-limit, blocked, and missing deletion cases;
- source deletion falls back to snapshots.

### Frontend

- compact rows and readable Chat/Team breadcrumbs without raw IDs;
- debounced server search, filter reset, pagination merge, loading/error/empty states;
- selection mode, byte/count confirmation, cancellation, partial result retention;
- cleanup and normal deletion selections remain separate;
- Markdown/text/JSON/PDF/image/audio/video/fallback previews;
- technical details collapsed by default;
- wide inspector and narrow dialog accessibility, Escape close, and focus return;
- Archive and Chat consumers continue to open artifact details.

## Rollout

1. Add schema, origin write paths, Chat turn propagation, and legacy backfill.
2. Add browser/search and structured deletion APIs while retaining existing endpoints.
3. Replace the Archive card grid with the compact browser and responsive inspector.
4. Remove frontend parsing of Team IDs from `metadata` after compatibility tests pass.

No automatic cleanup, source deletion, or artifact deletion is performed by migration.
