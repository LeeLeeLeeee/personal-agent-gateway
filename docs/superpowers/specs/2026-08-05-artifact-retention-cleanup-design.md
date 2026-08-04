# Artifact retention and manual cleanup

## Goal

Keep the artifact archive usable as Team runs accumulate outputs, without
automatically deleting a user's work or breaking artifacts that a Team cycle
or task still needs as input.

## Scope

This change adds retention metadata, a manual cleanup preview/execution flow,
and an explicit way to pin an artifact. It does not move artifacts to another
table, infer whether an output is a final document from its title or model
supplied type, or run an automatic deletion scheduler.

## Retention model

Each artifact has one retention class and an optional expiry timestamp.

| Class | Meaning | Expiry |
| --- | --- | --- |
| `pinned` | Explicitly preserved by the user | none |
| `durable` | User-registered file | none |
| `temporary` | Generated Team output or diagnostic artifact | set by its producer |

`expires_at` means that an artifact is eligible to appear in cleanup preview.
It never deletes an artifact by itself. There is no `deleted_at` column and no
soft-delete phase: an artifact selected for manual cleanup is removed using
the existing file-and-row deletion behaviour.

The initial defaults are deliberately narrow:

- The existing `/api/artifacts/register` path creates `durable` artifacts.
- `TeamArtifactPublisher` creates `temporary` artifacts with a 30-day expiry.
- Existing artifacts receive `durable` with no expiry during migration, so
  applying the feature cannot make existing files candidates for deletion.
- No producer is classified from free-form `type` values. Type normalization is
  a separate follow-up concern.

## Protection rules

The cleanup preview excludes an artifact when any of these is true:

- its retention class is `pinned` or `durable`;
- its `expires_at` is absent or later than the current time;
- it is referenced by `team_cycle_request_input_artifacts`,
  `team_cycle_input_artifacts`, or `team_task_input_artifacts`.

The three reference checks are repeated at execution time. If a candidate has
become protected after preview, it is skipped and reported rather than
deleted. This complements the database foreign-key `on delete restrict`
constraints and avoids an all-or-nothing cleanup operation.

## API and data flow

1. `GET /api/artifacts/cleanup-preview` returns eligible artifacts, their
   aggregate size, and the timestamp used for evaluation. It makes no changes.
2. `POST /api/artifacts/cleanup` accepts an explicit, non-empty list of
   artifact IDs from the preview. The service re-evaluates the protection rules
   immediately before removing each eligible item with `ArtifactStore`.
   The response lists deleted and skipped IDs.
3. `PATCH /api/artifacts/{artifact_id}/retention` lets the user select
   `pinned`, `durable`, or `temporary`. Setting `pinned` clears expiry. Setting
   `temporary` requires an explicit expiry timestamp; setting `durable`
   clears it. This is the only way to mark a Team deliverable as a final item
   in this scope.
4. Normal list and get payloads expose `retention_class` and `expires_at`.

The existing single-artifact `DELETE` endpoint retains its current immediate
delete semantics. It will return a conflict for artifacts protected by Team
input references, rather than surfacing a raw database integrity error.

## Storage changes

A new migration adds:

- `retention_class text not null default 'durable'`;
- `expires_at text`;
- an index that supports temporary-artifact expiry queries.

Existing rows use the default and are therefore preserved. The `Artifact`
domain object and all artifact payloads carry both fields.

## Error handling and audit

- Invalid retention classes, malformed timestamps, and empty cleanup requests
  return `400`.
- An unknown artifact returns `404` for pin/update and is listed as skipped in
  batch cleanup.
- A protected artifact returns `409` from direct delete and is skipped in batch
  cleanup.
- Retention updates and cleanup execution create domain audit events. Preview
  does not.
- Cleanup only removes resolved paths inside the artifact root, preserving the
  existing path-safety check.

## Tests

Tests cover migration defaults, registration and Team-publisher defaults,
retention update validation, preview eligibility and byte totals, all three
Team input protections, execution-time revalidation, direct-delete conflicts,
and API/audit payloads. Existing artifact pagination and content tests remain
unchanged except for the two new response fields.

## Success criteria

- Existing archive data is never made eligible merely by deploying the change.
- No cleanup occurs without an explicit cleanup request.
- A referenced Team input cannot be deleted through either direct delete or
  batch cleanup.
- A user can pin a final artifact regardless of its model-generated type.
- The cleanup preview accurately reports what manual execution can remove.
