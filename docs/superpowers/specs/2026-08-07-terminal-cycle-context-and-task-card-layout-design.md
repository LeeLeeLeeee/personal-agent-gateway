# Terminal Cycle Context and Task Card Layout Design

## Goal

Allow a triggered Team Run to use any final previous cycle as context, including a
failed, blocked, or canceled cycle, and preserve enough state for the next cycle's
leader to judge that context correctly. Keep long failure text inside its task card
without hiding the full detail available in the task dialog.

## Previous-cycle eligibility

The latest cycle in one of these final states is eligible as previous context:

- `completed`
- `completed_with_failures`
- `failed`
- `blocked`
- `canceled`

An active or resumable cycle, including `queued`, `running`, `waiting_for_provider`,
`waiting_for_user`, and `interrupted`, is not final previous context. The server
continues to reject a cycle from another Team Run.

## Context snapshot

At enqueue time, the server builds an immutable plain-text snapshot from the selected
cycle and stores it in the existing `team_cycle_requests.previous_summary_text`
column. Reusing the existing snapshot field avoids a migration while preserving the
current guarantee that queued requests do not observe later changes.

The snapshot contains:

1. the previous cycle status;
2. the cycle summary when present;
3. the cycle error when present;
4. each cycle task's title, final status, result or outcome summary, and error when
   present.

The dispatcher labels this block `PREVIOUS CYCLE CONTEXT`, not `PREVIOUS CYCLE
SUMMARY`, before appending it to the leader instruction. Failed cycles with no cycle
summary therefore still carry their useful completed and failed task outcomes.

This is a leader-planning input only. It does not change worker prompt injection or
the historical cycle and task records.

## UI layout

`TeamTaskCard` renders a failed or blocked explanation as a separate, clamped content
block above the metadata row. The metadata row remains reserved for owner, reason
code, file count, and report count. Opening the task dialog continues to show the full
unclamped explanation.

The board uses zero-minimum grid tracks and its columns and cards may shrink below
their content's intrinsic width. Long hashes, file paths, and reason codes wrap or
clip inside the card instead of widening the grid column.

## Error behavior

- A final cycle from the same Team Run is accepted and snapshotted.
- A missing cycle, a cycle from another Team Run, or a non-final cycle returns the
  existing conflict response.
- A final cycle with no summary, error, or tasks still supplies its explicit status.
- Existing idempotent cycle requests keep their original snapshot.

## Verification

- Domain tests cover all five eligible final statuses.
- Domain tests prove that a failed cycle snapshot includes status, cycle error, and
  completed and failed task information.
- Domain tests keep rejecting foreign and non-final previous cycles.
- Dispatcher tests assert the `PREVIOUS CYCLE CONTEXT` label and snapshot contents.
- Component tests assert that long failure text is outside the metadata row while the
  full task detail remains available.
- Focused backend and frontend tests, followed by the frontend production build,
  verify the change.

