# Cycle Add-Work Plan Contract Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make add-work planning instructions produce task JSON accepted by the production task-plan parser.

**Architecture:** Preserve `_parse_task_plan` as the canonical runtime validator and update only the stale add-work prompt. Protect the boundary with a focused contract regression test covering both planning entry points.

**Tech Stack:** Python 3, pytest

## Global Constraints

- Keep the change limited to the add-work planning contract and its regression test.
- Do not change previous-cycle context construction or persisted cycle data.
- Do not add a second full schema to the repair suffix; it reuses the original prompt.

---

### Task 1: Align the add-work planning contract

**Files:**
- Modify: `tests/test_team_runtime.py`
- Modify: `src/personal_agent_gateway/team_runtime.py`

**Interfaces:**
- Consumes: `PLANNING_PROMPT`, `ADD_WORK_PROMPT`, and `_parse_task_plan`'s required task fields.
- Produces: Add-work model instructions containing `plan_task_id`, `depends_on_task_ids`, and `input_artifact_ids`.

- [ ] **Step 1: Write the failing contract test**

```python
def test_planning_prompts_require_task_identity_and_dependency_fields() -> None:
    for prompt in (PLANNING_PROMPT, ADD_WORK_PROMPT):
        assert '"plan_task_id"' in prompt
        assert '"depends_on_task_ids"' in prompt
        assert '"input_artifact_ids"' in prompt
```

- [ ] **Step 2: Run the test and verify the current add-work prompt fails**

Run: `pytest tests/test_team_runtime.py::test_planning_prompts_require_task_identity_and_dependency_fields -q`

Expected: FAIL because `ADD_WORK_PROMPT` omits `plan_task_id`.

- [ ] **Step 3: Update the add-work schema and dependency rules**

Add the three fields to the JSON example and state that `plan_task_id` is unique,
dependencies reference task IDs in the same response, and unused lists are `[]`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_team_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Run the full Python suite**

Run: `pytest -q`

Expected: PASS.
