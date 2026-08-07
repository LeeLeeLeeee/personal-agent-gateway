# Cycle Add-Work Plan Contract Design

## Problem

`ADD_WORK_PROMPT` tells the leader model to return task objects without
`plan_task_id`, `depends_on_task_ids`, or `input_artifact_ids`. The production
parser requires `plan_task_id`, so a model response that follows the prompt is
rejected as `invalid_structured_output`. The repair request repeats the original
prompt, so the same mismatch survives the retry.

## Design

Keep the parser unchanged and align `ADD_WORK_PROMPT` with the already-working
`PLANNING_PROMPT` contract. Every add-work task must declare a unique
`plan_task_id`, explicit `depends_on_task_ids`, and explicit
`input_artifact_ids`. Dependencies may reference only IDs declared in the same
response, and a task consuming another task's output must declare that dependency.

Do not duplicate the full schema in the repair suffix. Repair already resends the
original prompt, so correcting the source prompt fixes both the initial and repair
attempts without creating another contract copy that can drift.

## Verification

Add a regression test requiring both planning prompts to advertise every field
required by `_parse_task_plan`. Run that test once before the production change to
confirm it fails for `ADD_WORK_PROMPT`, then run the focused runtime tests and the
full Python test suite after the change.
