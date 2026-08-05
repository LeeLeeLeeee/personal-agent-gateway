# Artifact Team/Cycle Context Design

## Goal

Make a Team-generated artifact explain which Team, Team Run, Cycle, and Task produced it without duplicating those labels into artifact storage.

## Chosen design

Keep the persisted provenance IDs as the canonical relationship. On the artifact browser read path, resolve the current labels from `teams`, `team_runs`, `team_run_cycles`, and `team_tasks`, then return ordered breadcrumbs:

`Team → Team Run → Cycle N → Task`

When a run has no `team_id`, omit the Team breadcrumb. When a Cycle or Task is absent, omit only that level. Existing snapshot labels remain fallbacks when related records are unavailable.

The Archive UI groups by the first breadcrumb and renders the remaining hierarchy in the file row and inspector. This lets one Team group its multiple runs while retaining run/cycle/task context per artifact.

## Verification

- Browser unit tests create a Team, Team Run, Cycle, and Task and assert the ordered breadcrumb labels.
- Archive UI test asserts the full context is rendered as the compact file-row detail.
- Run the focused artifact Python suite and the relevant frontend test.
