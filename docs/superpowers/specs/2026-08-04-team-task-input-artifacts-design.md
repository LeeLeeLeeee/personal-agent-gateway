# Team task artifact inputs design

## Problem

A Team Run task currently carries only prose. A planner or a prior task report
can name an artifact from another run, but that artifact is not a declared
input and is not staged into the current isolated workspace. Providers enforce
that missing boundary differently: a task may discover and cite the external
path, while a later task is denied access to the same path. A required task
then fails the whole cycle.

## Decision

Treat cross-run artifacts as typed task inputs, never as paths embedded in
task prose.

1. A cycle owns an immutable catalog of selected artifact IDs.
2. A task declares zero or more IDs from that catalog as its inputs.
3. Before dispatch, PAG copies every declared input into the task's isolated
   `inputs/` directory and records a content-hashed manifest.
4. The worker prompt lists only those staged relative paths. A host path in a
   prior task report is not an input and grants no access.
5. A planner may only reference IDs provided in its cycle catalog. Unknown,
   duplicate, or cross-cycle IDs reject the plan before any task is created.

The first implementation supports existing stored artifacts only. It does not
add automatic artifact discovery, external read roots, or broad filesystem
access.

## Data model

Add `team_cycle_input_artifacts`:

- `cycle_id` and `artifact_id` identify the selected immutable source.
- `relative_path`, `sha256`, and `size_bytes` snapshot the artifact identity
  when the cycle is created.
- `created_at` records selection time.

Add `team_task_input_artifacts`:

- `task_id` and `artifact_id` identify a declared task dependency.
- `relative_path`, `sha256`, and `size_bytes` duplicate the frozen cycle
  snapshot, avoiding a later artifact mutation changing the task's input.
- `staged_path` is the task-local relative path under `inputs/`.

Both tables use unique `(owner_id, artifact_id)` constraints. Task inputs must
reference a catalog entry for the same cycle.

## Flow

```text
delegate knowledge request + selected artifact IDs
  -> persist cycle input catalog (snapshot)
  -> planner receives ID, title, and staged-relative-path catalog
  -> validated task plan references catalog IDs only
  -> persist task input declarations
  -> copy frozen artifacts to workspace/inputs/<task-id>/
  -> create input manifest and expose its relative paths to the worker
  -> worker reads only current workspace plus staged inputs
```

For a knowledge request with no explicitly selected artifacts, the catalog is
empty. The planner must create tasks from the request fields and research;
it cannot turn a historical Team Run artifact into a review target.

Task outputs remain available in the current shared run workspace as today.
Only artifacts crossing a run boundary require typed input selection.

## API and planner contract

The knowledge-request delegation endpoint accepts an optional list of artifact
IDs. It validates that every artifact exists and snapshots it on the newly
created cycle.

The planner task schema gains `input_artifact_ids: string[]`. The parser
requires the field (empty list is valid), rejects IDs not in the cycle catalog,
and persists the declarations atomically with the task plan.

The planner and worker prompts show artifact IDs, display titles, and staged
relative paths. They never show a host filesystem path for an input artifact.

## Failure handling

- A missing or changed artifact at staging time fails before model invocation
  with `input_artifact_unavailable`.
- An invalid planner artifact reference produces an invalid structured plan;
  no partial task plan is applied.
- A task with no declared input that attempts an arbitrary host path is still
  denied by the provider sandbox. Its output cannot turn that path into an
  input for a later task.

## Verification

Tests cover:

1. Delegation snapshots a selected artifact and rejects unknown IDs.
2. A valid task plan can reference only cycle catalog artifact IDs.
3. Staging copies an artifact into the task `inputs/` tree and verifies its
   manifest hash.
4. A prior task report containing an external absolute path does not create a
   later task input.
5. The `dfbf2063` shape—an unselected historical artifact—cannot be selected
   by the planner and never reaches a provider invocation.
