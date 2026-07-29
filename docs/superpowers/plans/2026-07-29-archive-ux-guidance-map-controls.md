# Archive UX Guidance and Map Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Archive understandable on first use, keep Artifacts conceptually separate from the knowledge lifecycle, and add usable pan/zoom controls to Map.

**Architecture:** Keep `ArchiveView` as the owner of Archive tabs and map state, and keep `ArtifactsView` as the independent artifact browser. Add no new dependency: SVG navigation uses React state plus pointer events, while CSS preserves the existing visual language.

**Tech Stack:** React 18, Vitest, Testing Library, plain CSS, SVG.

## Global Constraints

- Artifacts are work outputs and are not an automatic step toward Library publication.
- The knowledge lifecycle is `Requests → Drafts → Library`; Map explains and visualizes that lifecycle.
- Map must support zoom in, zoom out, fit/reset, pointer drag, and keyboard-operable controls.
- Existing Archive API calls and data contracts remain unchanged.
- Do not add a graph library for this bounded interaction.

---

### Task 1: Archive guidance and independent Artifacts presentation

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx`
- Modify: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: existing `tab`, `artifacts`, and `onArtifactChange` props.
- Produces: visible Archive overview copy and an `artifacts` panel explicitly described as independent work outputs.

- [ ] **Step 1: Write the failing tests**

Add assertions that Archive renders separate “Knowledge lifecycle” and “Work outputs” guidance, and that the Artifacts panel states it is not automatically published to Library.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
npm test -- --run frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx
```

Expected: FAIL because the new guidance text and layout hooks do not exist.

- [ ] **Step 3: Implement minimal UI and CSS**

Render two concise guidance blocks below the Archive header. Keep `ArtifactsView` independent and constrain `.artifact-grid` to fixed-width cards aligned at the start so sparse results retain intentional whitespace.

- [ ] **Step 4: Run tests to verify GREEN**

Run the same targeted test command and expect all tests to pass.

### Task 2: Map explanation and viewport controls

**Files:**
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx`
- Modify: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`

**Interfaces:**
- Consumes: existing `graph.nodes`, `graph.edges`, `selectedNodeId`, and `onSelect`.
- Produces: local `{ scale, x, y }` viewport state and accessible buttons named `Zoom in`, `Zoom out`, and `Fit map`.

- [ ] **Step 1: Write the failing tests**

Assert that Map explains the four columns, exposes all three controls, changes the viewport transform after zoom, and resets it via `Fit map`.

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
npm test -- --run frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx
```

Expected: FAIL because controls and transform state do not exist.

- [ ] **Step 3: Implement minimal viewport behavior**

Wrap the existing graph content in an SVG `<g>` with `transform="translate(x y) scale(scale)"`. Clamp scale to `0.5–2`, implement pointer capture for dragging, and reset to `{ scale: 1, x: 0, y: 0 }`.

- [ ] **Step 4: Run test to verify GREEN**

Run the same targeted command and expect all tests to pass.

### Task 3: Regression verification

**Files:**
- Verify only.

- [ ] **Step 1: Run frontend tests**

```powershell
npm test -- --run
```

- [ ] **Step 2: Build frontend**

```powershell
npm run build
```

- [ ] **Step 3: Inspect the final diff**

Confirm that API contracts, Archive persistence behavior, and unrelated screens are unchanged.

