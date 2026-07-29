# Team Cycle SPACE Snapshot Design

## Goal

Continuous Team Runs must resolve the Team's current SPACE policy when each
cycle is created. A cycle then keeps that policy for its entire lifetime so a
later SPACE edit cannot change an active or historical cycle.

This replaces the current behavior where every cycle reuses the SPACE snapshot
captured when the parent Team Run was created.

## Decisions

### Store SPACE on each cycle

`team_run_cycles` gains a `space_policy_snapshot_json` column. Cycle creation
resolves the current Team policy and writes its snapshot in the same
transaction as the cycle row.

The existing Run-level snapshot remains for standard Runs and legacy data. It
is not overwritten when a continuous cycle starts.

### Resolve execution policy by task cycle

When a Team agent has a task with a `cycle_id`, the model factory uses that
cycle's SPACE snapshot. Standard Runs and legacy cycles without a cycle
snapshot fall back to the Run snapshot.

This gives each cycle a stable, auditable policy while allowing the next cycle
to pick up Team SPACE changes.

### Preserve idempotency

Repeated creation for the same request or source returns the existing cycle and
its original SPACE snapshot. It does not refresh the snapshot on an already
created cycle.

Task-retry cycles also capture the Team policy that is current when the retry
cycle is created.

### Honor `all + isolated`

For `read_mode="all"` with `write_mode="isolated"`, execution keeps the
isolated workspace as the writable root and permits unbounded read access
without source staging. Bounded `selected` reads continue to use staged input
snapshots. `none` continues to expose no declared source access.

This changes only the unbounded `all` case. `home + isolated` continues to
require a bounded selection.

## Data Flow

1. The dispatcher claims a cycle request.
2. `TeamRunService.create_cycle()` resolves the Team's current SPACE.
3. The service stores the cycle and SPACE snapshot atomically.
4. Planning creates tasks linked to the cycle.
5. The model factory resolves the task's cycle snapshot and compiles execution.
6. SPACE edits affect only cycles created after the edit.

## Compatibility

- Standard Team Runs continue using their Run-level snapshot.
- Existing continuous cycles without a cycle snapshot fall back to the
  Run-level snapshot.
- API cycle payloads expose the cycle SPACE snapshot for diagnosis and audit.
- No existing Run snapshot is rewritten.

## Focused Verification

- A new cycle captures the latest Team SPACE after the Run was created.
- An existing cycle remains unchanged after a later Team SPACE edit.
- Duplicate cycle creation preserves the first snapshot.
- A Team agent executes with its task cycle's snapshot instead of the Run
  snapshot.
- A retry cycle captures the current Team SPACE.
- `all + isolated` compiles to isolated writes with unbounded reads.
- Existing `selected`, `none`, standard-Run, and legacy fallback behavior
  remains covered by related tests.
