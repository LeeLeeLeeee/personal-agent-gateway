# Knowledge Request Draft Failure Visibility Design

## Goal

Make a failed Library draft generation visible on the Knowledge Request that
caused it, so the user can see that a delegated Team Run produced nothing
usable, why it failed, and can re-delegate from the same screen.

## Problem

When a Knowledge Request is delegated to a Team Run, the settlement path in
`HookRunner._settle_knowledge_request` turns the completed cycle summary into
an Archive draft through `parse_library_draft_response`. If the summary does
not carry exactly one `<library_draft>` marker, the parse raises `ValueError`,
the handler calls `_reopen_knowledge_request`, publishes an
`archive.draft.failed` SSE event, and returns.

Nothing about that failure is persisted. The SSE event is not stored, no
Archive view subscribes to it, and the request silently returns to `open`.
From the Archive screen the outcome is indistinguishable from "the Team Run
never ran".

Request `080bcee47a0f4954935a48a30eb3f122` on 2026-08-03 showed the failure
mode. The delegated cycle `bbc37fe14bb144c1a1c68d56d40a4776` completed at
00:42:36 UTC and wrote three deliverable files, but its final summary was an
ordinary Korean completion report with no marker. Replaying the stored summary
through the parser reproduces the failure:

```
ValueError: Team response must contain exactly one Library Draft marker
```

`archive_entries` and `archive_draft_origins` stayed empty, and the request
stayed in the Requests list unchanged.

## Scope

In scope:

- persist the last draft failure on the Knowledge Request;
- expose it through the existing requests payload;
- render it on the request card in `ArchiveView`.

Out of scope:

- automatic retry of a contract violation;
- the hook-sourced draft path (only the Knowledge Request path builds Library
  drafts today);
- backfilling requests that already failed. Their columns stay null until the
  next delegation.

## Data Model

Migration 21, `knowledge-request-draft-failure`, adds four nullable columns to
`knowledge_requests`:

| Column | Meaning |
| --- | --- |
| `last_draft_error_code` | Stable machine code, e.g. `draft_contract_violation` |
| `last_draft_error_message` | Redacted human-readable reason, truncated to 500 characters |
| `last_draft_failed_at` | RFC3339 timestamp |
| `last_draft_cycle_id` | Cycle that produced the unusable output |

`KnowledgeRequest` gains the four matching fields, and `_request_from_row`
reads them.

The status set is unchanged. A failed request stays representable with the
existing `open` status; the failure columns carry the extra meaning. This
keeps `_REQUEST_STATUSES`, `_ACTIVE_REQUEST_STATUSES`, the delegate guard, and
existing tests untouched.

## Domain Layer

`ArchiveService` gains two methods:

- `record_draft_failure(request_id, *, error_code, message, cycle_id)` writes
  the four columns and resets the status to `open` in one statement. It
  absorbs what `HookRunner._reopen_knowledge_request` does today. A `fulfilled`
  request rejects the call with `ValueError`, matching
  `update_request_status`.
- `clear_draft_failure(request_id)` sets the four columns to null.

`assign_request_team` clears the failure columns as part of its update, so a
re-delegation starts from a clean card.

## Settlement Path

`HookRunner._settle_knowledge_request` replaces `_reopen_knowledge_request`
with `record_draft_failure`. Exceptions map to stable codes:

| Situation | `error_code` |
| --- | --- |
| Marker missing, duplicated, or followed by trailing content | `draft_contract_violation` |
| Marker present but payload fails validation | `draft_invalid_payload` |
| The Archive save call itself fails (`KeyError`, `RuntimeError`) | `draft_save_failed` |
| Cycle ended `blocked`, `failed`, `canceled`, or `interrupted` | `cycle_<status>` |

`parse_library_draft_response` and `save_draft` both raise `ValueError`, so the
first two codes are distinguished by which call raised: the parse is performed
first and its failure is caught separately from the save.

The `archive.draft.failed` SSE event stays as is for live subscribers. The
persisted columns are the source of truth.

On success the handler calls `clear_draft_failure` before publishing
`archive.draft.created`.

`HookRunner._reconcile_knowledge_requests`, the startup reconciliation path,
has the same silent failure and records through the same helper. It runs
synchronously, so it records the failure without publishing an SSE event.

## API

`_request_payload` in `api/archive.py` adds `last_draft_error_code`,
`last_draft_error_message`, `last_draft_failed_at`, and `last_draft_cycle_id`.
No new endpoint.

## UI

`ArchiveView` renders a failure banner on a request card when
`last_draft_error_code` is set and the status is active:

```
⚠ DRAFT FAILED · draft_contract_violation · 2026-08-03 00:42 UTC
Team response must contain exactly one Library Draft marker
CYCLE bbc37fe1…
```

No retry button is added. A failed request is back to `open`, so the existing
`Send to team` control is already visible, and the team select already falls
back to `item.assigned_team_run_id`, which preselects the run that just
failed.

## Error Handling

Recording a failure must never break settlement. If `record_draft_failure`
raises, the handler logs and continues to publish the SSE event; the cycle is
still settled.

`last_draft_error_message` passes through `redact_text` and is truncated to
500 characters before it is stored, because it originates from model output.

## Testing

Test-driven, in this order:

- `tests/test_migrations.py` — the four columns exist after upgrading an
  existing database.
- `tests/test_archive.py` — `record_draft_failure` writes the columns and
  moves the status to `open`; a `fulfilled` request rejects it;
  `clear_draft_failure` nulls the columns; `assign_request_team` clears them.
- `tests/test_hook_runner.py` — settling a completed cycle whose summary has
  no marker records `draft_contract_violation`, sets `open`, and still
  publishes `archive.draft.failed`; a valid marker clears an earlier failure;
  a `failed` cycle records `cycle_failed`.
- `tests/test_api_archive.py` — the requests payload exposes the four fields.
- `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx` — the
  banner renders when the fields are present and is absent otherwise.
