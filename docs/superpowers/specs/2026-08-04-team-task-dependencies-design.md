# Team Task Dependency Design

## Goal

Make task execution order explicit so a task can run only after every declared
prerequisite task has completed successfully.

## Scope

- Add `depends_on_task_ids` to planner task JSON. Omitted means `[]` for
  compatibility with existing operations.
- Persist dependencies in a `team_task_dependencies` table.
- Validate dependency references when applying the immutable planning result.
- Schedule only dependency-ready pending tasks.
- When a prerequisite is `failed`, `blocked`, or `canceled`, mark every pending
  dependent task `blocked` without invoking a provider.

## Data model

`team_task_dependencies` has one row per edge:

```text
task_id            dependent task
depends_on_task_id prerequisite task
```

Both references cascade on task deletion. `(task_id, depends_on_task_id)` is the
primary key. A dependency must point to a task in the same Team Run and cycle.

## Planning validation

The planner continues to return a JSON array. Each task has a stable plan-local
key `plan_task_id`, and dependencies reference those keys:

```json
{
  "plan_task_id": "draft",
  "title": "Write draft",
  "depends_on_task_ids": ["research"]
}
```

The server rejects duplicate plan keys, self-dependencies, missing references,
and cycles before creating any task. When persisting, it maps the plan-local
keys to generated database task IDs atomically.

## Scheduling and failure propagation

For each pending task, the runtime checks its persisted prerequisites:

- all `completed` → eligible for normal assignment;
- any `failed`, `blocked`, or `canceled` → change the task to `blocked` with
  `blocked_by_dependency` and do not invoke a worker;
- otherwise → leave it pending.

Existing tasks without dependency rows remain eligible exactly as before. Task
selection preserves the current creation-order behavior among eligible tasks.

## Example

```text
research ──completed──> draft ──completed──> QA
```

If `research` fails, both `draft` and `QA` become blocked. A task description
or a file path never creates a dependency by inference.

## Testing

- Planner rejects invalid dependency graphs atomically.
- Plan application persists dependency edges and supports replay.
- Scheduler skips an unfinished prerequisite and runs it first.
- Direct and transitive dependents become blocked after prerequisite failure.
- Legacy plans/tasks without dependency data preserve their current behavior.
