# Archive Map Knowledge Lanes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the persona-inventory Archive graph with sectioned, row-based knowledge lifecycle lanes.

**Architecture:** Derive a frontend-only lane view-model from the existing Archive graph response. Render each request or standalone knowledge item as one SVG lane, keep source identities as compact badges, and preserve the current viewport interaction and inspector.

**Tech Stack:** React 19, SVG, CSS, Vitest, Testing Library

## Global Constraints

- Keep the Archive graph API unchanged.
- Omit graph nodes that are unrelated to a request, draft, or Library entry.
- Render `SHARED KNOWLEDGE`, `PERSONA-SPECIFIC`, and `AUTOMATION` sections.
- Remove arrowheads and label connectors with text.
- Preserve mouse-wheel zoom, button zoom, drag-to-pan, Fit, and selection.
- Do not change Archive lifecycle behavior or `health.py`.

---

### Task 1: Define the lane rendering contract

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`

**Interfaces:**
- Consumes: the existing `archiveMap()` response shape
- Produces: UI assertions for section groups, lifecycle lane groups, compact source badges, and labeled connectors

- [ ] **Step 1: Add a graph fixture with connected and unconnected personas**

Create a client response containing:

```js
nodes: [
  { id: "persona:connected", kind: "persona", label: "Operator" },
  { id: "persona:unused", kind: "persona", label: "Unused persona" },
  { id: "request:request-1", kind: "request", label: "Rollback checklist" },
  { id: "team_run:team-1", kind: "team_run", label: "Documentation team" },
  { id: "draft:draft-1", kind: "draft", label: "Rollback checklist" }
],
edges: [
  { source: "persona:connected", target: "request:request-1", kind: "needs" },
  { source: "request:request-1", target: "team_run:team-1", kind: "delegates" },
  { source: "team_run:team-1", target: "draft:draft-1", kind: "produced" }
]
```

- [ ] **Step 2: Assert the desired lane structure**

Verify:

```js
const personaSection = within(graph).getByRole("group", {
  name: "Persona-specific knowledge"
});
const lane = within(personaSection).getByRole("group", {
  name: "Rollback checklist knowledge lane"
});
expect(within(lane).getByRole("button", { name: "Operator persona source" }))
  .toBeInTheDocument();
expect(within(graph).queryByText("Unused persona")).not.toBeInTheDocument();
expect(within(lane).getByText("GAP")).toBeInTheDocument();
expect(within(lane).getByText("DELEGATED")).toBeInTheDocument();
expect(within(lane).getByText("DRAFT")).toBeInTheDocument();
expect(container.querySelector("[marker-end]")).not.toBeInTheDocument();
```

- [ ] **Step 3: Add shared and automation section assertions**

Use a shared Library entry and hook-produced draft to verify the accessible
groups `Shared knowledge` and `Automation knowledge`.

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```powershell
npm test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx -t "knowledge lanes"
```

Expected: FAIL because section and lane groups do not exist, unused personas
are rendered, connectors have no text labels, and paths still have markers.

---

### Task 2: Build and render knowledge lanes

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx`

**Interfaces:**
- Produces: `buildKnowledgeLaneLayout(nodes, edges)`
- Produces lane objects with `id`, `label`, `section`, `sources`, `request`,
  `teams`, `results`, `edges`, `height`, and SVG positions

- [ ] **Step 1: Implement lane derivation**

Create request-anchored lanes first, mark represented result IDs, then create
standalone result lanes. Gather only directly related sources and teams.
Classify sections with hook > persona > shared precedence.

- [ ] **Step 2: Calculate section and lane positions**

Use the existing four x-axis stages:

```js
const MAP_COLUMNS = {
  source: 40,
  request: 350,
  team: 660,
  knowledge: 970
};
```

Calculate each lane height from the largest stage stack so nodes never overlap.
Return the complete content height for Fit.

- [ ] **Step 3: Render sections and lanes**

Render:

- one accessible `<g>` for each non-empty section;
- a bordered lane background for each lifecycle;
- compact source badge buttons;
- existing full node buttons for request, team, draft, and Library stages.

- [ ] **Step 4: Keep inspector and viewport behavior**

Selection continues to use original graph node IDs. Replace the old
kind-column layout height with the lane-layout height without changing the
viewport state API.

- [ ] **Step 5: Run the focused lane tests**

Run:

```powershell
npm test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx -t "knowledge lanes"
```

Expected: section, lane, omission, and node assertions pass; connector styling
assertions may remain RED until Task 3.

---

### Task 3: Clarify connector semantics

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Produces connector labels `GAP`, `DELEGATED`, `DRAFT`, and `PUBLISHED`

- [ ] **Step 1: Remove the shared SVG arrow marker**

Delete the marker definition and all `markerEnd` attributes.

- [ ] **Step 2: Derive connector presentation**

Map `needs` and `requested` to `GAP`, `delegates` to `DELEGATED`, draft output
to `DRAFT`, and entry relationships to `PUBLISHED`.

- [ ] **Step 3: Render connector labels inside each lane**

Place text at each path midpoint with a white paint-order stroke so it remains
legible over the grid and connector.

- [ ] **Step 4: Add scoped lane styles**

Add styles for section headings, lane backgrounds, compact source badges,
connector labels, and the four legend line variants. Remove no unrelated CSS.

- [ ] **Step 5: Run Archive tests**

Run:

```powershell
npm test -- src/components/organisms/ArchiveView/ArchiveView.test.jsx
```

Expected: all Archive tests pass.

---

### Task 4: Verify the complete frontend

**Files:**
- Verify only

- [ ] **Step 1: Run the complete frontend suite with bounded concurrency**

```powershell
npm test -- --maxWorkers=1
```

- [ ] **Step 2: Build the production frontend**

```powershell
npm run build
```

- [ ] **Step 3: Check the scoped diff**

```powershell
git diff --check -- frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx frontend/src/components/organisms/ArchiveView/index.jsx src/personal_agent_gateway/static/styles.css docs/superpowers/plans/2026-07-29-archive-map-knowledge-lanes.md
```

Expected: all tests pass, the build succeeds, and the diff check has no errors.
