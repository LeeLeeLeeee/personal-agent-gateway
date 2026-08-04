# Artifact archive and cleanup preview UX

## Goal

Make the Archive > Artifacts area useful for deciding what to keep and what to
remove. A user must be able to understand an artifact's Team provenance,
retention state, and cleanup reason without interpreting model-generated
`type` strings or opening every file.

## Scope

This is a frontend change using the existing artifact retention APIs. It
replaces the all-artifact card grid with a compact archive list and adds a
dedicated cleanup-review view. It does not change retention policy, infer a
final artifact from a filename, or automatically delete anything.

## Archive list

The Artifacts tab opens on a list, not a thumbnail grid. Its top-level
segments are:

- **보관됨**: `pinned` and `durable` artifacts; this is the default segment.
- **최근 산출물**: non-expired `temporary` artifacts.
- **정리 후보**: the result of `GET /api/artifacts/cleanup-preview`.

The header shows counts for all three segments. A search input matches title,
Team run ID, task ID, and cycle ID. A secondary filter limits by broad file
kind using the current type categories, but does not expose arbitrary
model-supplied type values as a primary navigation system.

Rows show a readable title, a retention badge, a provenance label, creation
time, size, and a context action. Provenance comes from existing metadata:
`team_run_id`, `task_id`, and `cycle_id`; artifacts without Team metadata are
labelled as user-registered or local artifacts. Team artifacts are grouped by
Team run and then task, with ungrouped artifacts in a separate section.

`보관` is available for a temporary artifact and calls
`PATCH /api/artifacts/{id}/retention` with `{"retention_class":"pinned"}`.
The updated artifact stays visible and moves to 보관됨 after refresh.

## Cleanup review

Selecting 정리 후보 loads `GET /api/artifacts/cleanup-preview` on demand.
It is a review screen, not a destructive action:

- Candidate rows are grouped with the same Team run/task context as the list.
- Every row states its reason: temporary Team output or diagnostic output and
  the expiry date.
- Every checkbox is initially unchecked.
- Each row has 보관, which removes it from the selected cleanup list without
  deleting it.
- A compact protected summary explains that pinned, durable, and Team-input
  artifacts are excluded by policy; it is informational and has no delete
  action.
- A sticky action bar appears only after selection and shows selected count
  and reclaimable bytes.
- The action opens a confirmation dialog that repeats the count, total bytes,
  and selected titles. Confirming calls `POST /api/artifacts/cleanup` with
  only selected IDs, then refreshes artifacts and preview.

The UI never sends an empty cleanup request and never presents an all-select
action. API skipped IDs are shown as a non-blocking result message so a
candidate that became protected between preview and confirmation is explained.

## Components and API client

Keep `ArchiveView` as the tab owner. Evolve `ArtifactsView` into the
feature-specific Artifact archive list/review component; do not create a new
global primitive for this one Archive workflow. Add only these API client
methods:

- `artifactCleanupPreview()`
- `cleanupArtifacts(artifactIds)`
- `updateArtifactRetention(id, payload)`

The container refresh callback remains the single source for the normal
artifact list. Cleanup preview state stays local to the artifact view because
it is a transient selection/review state.

## Accessibility and error handling

- Segment controls and filters use buttons with pressed state; cleanup
  candidate count is announced in the visible heading.
- Checkboxes have labels containing the artifact title.
- The confirmation uses the existing modal/dialog pattern and returns focus to
  the cleanup action after close.
- Loading, API failure, and empty states are explicit. A cleanup API error
  preserves selection for retry; successful cleanup clears selection.

## Tests

Frontend tests cover default 보관됨 segment, Team grouping, search, preview
loading, unchecked-by-default selection, pinning, cleanup request IDs and byte
total, skipped-result feedback, confirmation cancellation, and empty/error
states. API client tests cover the three new endpoints.

## Success criteria

- A user can distinguish final/preserved artifacts from temporary Team outputs
  without opening a modal.
- A user can see why every cleanup candidate is eligible and retain it with
  one action.
- No artifact can be deleted through the UI unless the user selects it and
  confirms the resulting selection.
- The existing retention API remains the sole source of cleanup eligibility
  and Team-input protection.
