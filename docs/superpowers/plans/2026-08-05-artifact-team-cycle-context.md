# Artifact Team/Cycle Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Team, Team Run, Cycle, and Task provenance for Team-generated artifacts.

**Architecture:** Resolve existing provenance IDs in `ArtifactStore.browser_page`; do not add duplicate label columns. The UI continues grouping by the first breadcrumb and presents deeper breadcrumbs in each artifact row.

**Tech Stack:** Python, SQLite, React, Vitest, pytest.

## Global Constraints

- Modify the root `main` workspace directly, as requested.
- Preserve existing Chat, Job, manual, and legacy breadcrumb behavior.
- Use current entity labels with snapshot labels only as fallbacks.

---

### Task 1: Resolve Team/Cycle browser breadcrumbs

**Files:**
- Modify: `src/personal_agent_gateway/artifacts.py`
- Test: `tests/test_artifacts.py`

- [ ] **Step 1: Write the failing test**

```python
assert [crumb.label for crumb in item.breadcrumbs] == [
    "Documentation team", "Design system review", "Cycle 3", "Verify chart examples"
]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_artifacts.py -q`

- [ ] **Step 3: Write minimal implementation**

Resolve `teams.name`, `team_runs.goal`, `team_run_cycles.sequence`, and `team_tasks.title` from artifact provenance IDs. Emit only existing levels in that order.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_artifacts.py -q`

### Task 2: Surface the hierarchy in Archive rows

**Files:**
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Test: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

- [ ] **Step 1: Write the failing test**

```jsx
expect(screen.getByText("Design system review · Cycle 3 · Verify chart examples")).toBeInTheDocument();
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

- [ ] **Step 3: Write minimal implementation**

Render all breadcrumb labels after the grouping breadcrumb in their server-provided order.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`
