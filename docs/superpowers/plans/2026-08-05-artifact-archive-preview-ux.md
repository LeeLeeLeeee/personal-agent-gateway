# Artifact Archive Preview UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Archive artifacts legible by retention/provenance and let users safely review, pin, and explicitly clean expired temporary outputs.

**Architecture:** `ArtifactsView` remains the Archive-owned UI boundary and holds segment, search, preview, and selection state. The API client exposes the three existing retention endpoints; it never duplicates retention eligibility or Team-input protection. `ArchiveView` continues to supply the normal artifact list and refresh callback.

**Tech Stack:** React 19, Vite, Vitest, Testing Library, existing UiProvider confirm/toast APIs.

## Global Constraints

- The default segment is 보관됨: `pinned` and `durable` only.
- Cleanup preview is read-only until the user selects IDs and confirms.
- Do not add select-all or automatic deletion.
- Do not infer final status from title or free-form artifact `type`.
- Team provenance uses `metadata.team_run_id`, `metadata.task_id`, and `metadata.cycle_id`.
- Existing retention endpoints remain the sole authority for eligibility and protected inputs.

---

### Task 1: Add artifact-retention API client methods

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/api/client.test.js`

**Interfaces:**
- Produces `api.artifactCleanupPreview(): Promise<object>`.
- Produces `api.cleanupArtifacts(artifactIds: string[]): Promise<object | null>`.
- Produces `api.updateArtifactRetention(id: string, payload: object): Promise<object | null>`.

- [ ] **Step 1: Write failing client tests**

Add mocked fetch assertions for `GET /api/artifacts/cleanup-preview`, `POST /api/artifacts/cleanup` with `{"artifact_ids":["a1"]}`, and `PATCH /api/artifacts/a1/retention` with `{"retention_class":"pinned"}`. Assert the methods return the parsed response body, not only a boolean.

- [ ] **Step 2: Verify red state**

Run: `npm test -- --run src/api/client.test.js`

Expected: FAIL because the three client methods do not exist.

- [ ] **Step 3: Implement the three thin wrappers**

Use `jsonOrNull` and `Content-Type: application/json` for both mutations. `artifactCleanupPreview` requests the fixed preview path without query parameters. `cleanupArtifacts` serializes only the provided ID array. `updateArtifactRetention` URL-encodes its ID and serializes its supplied payload.

- [ ] **Step 4: Verify and commit**

Run: `npm test -- --run src/api/client.test.js`

Expected: PASS.

Commit: `git add frontend/src/api/client.js frontend/src/api/client.test.js; git commit -m "feat: add artifact retention client actions"`

### Task 2: Replace the all-file card grid with retention and provenance list segments

**Files:**
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Modify: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

**Interfaces:**
- `ArtifactsView({ artifacts, onChange })` keeps its public props.
- Local `segment` is `saved | recent | cleanup`; local search and broad type filter refine the active segment.
- Produces artifact rows with title, retention badge, provenance, creation time, size, and open/pin action.

- [ ] **Step 1: Write failing view tests**

Use fixtures for pinned, durable, temporary Team task output, and temporary local output. Assert first render shows pinned/durable but not temporary output; clicking 최근 산출물 shows the temporary Team row with Team run/task provenance; entering a title or run ID into search narrows rows. Assert a temporary row's 보관 button calls `api.updateArtifactRetention(id, { retention_class: "pinned" })` and invokes `onChange` after success.

- [ ] **Step 2: Verify red state**

Run: `npm test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

Expected: FAIL because the grid defaults to all artifacts and has no retention/provenance rows or pin action.

- [ ] **Step 3: Implement list derivation and row presentation**

Define pure local helpers for retention label, Team provenance label, grouping key, and broad type label. Group Team rows by run then task; place artifacts without Team metadata in a local section. Keep `ArtifactModal` as the open-detail action. Use button segment controls with `aria-pressed`; use a labelled search input and existing type chips as secondary filters. Do not introduce a global component: this is Archive-only state and presentation.

- [ ] **Step 4: Verify and commit**

Run: `npm test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

Expected: PASS, including existing modal and image-preview behaviour adapted to the list controls.

Commit: `git add frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx; git commit -m "feat: organize archive artifacts by retention"`

### Task 3: Add unchecked cleanup review and confirmation flow

**Files:**
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Modify: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

**Interfaces:**
- Consumes `api.artifactCleanupPreview`, `api.cleanupArtifacts`, `api.updateArtifactRetention`, `useConfirm`, and `useToast`.
- Cleanup selection is `Set<string>` and starts empty on every loaded preview.
- Calls `api.cleanupArtifacts([...selectedIds])` only after `useConfirm` resolves true.

- [ ] **Step 1: Write failing cleanup-review tests**

Mock preview with two candidate artifacts and click 정리 후보. Assert both checkboxes are unchecked, each row shows an expiry reason, and the selection bar is absent. Check one candidate and assert the bar reports one selected item and its bytes. Verify canceling confirmation makes no cleanup request; verify confirming posts only the checked ID, displays skipped IDs when returned, clears selection, and calls `onChange`. Verify 보관 on a candidate calls pin and reloads preview.

- [ ] **Step 2: Verify red state**

Run: `npm test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

Expected: FAIL because no preview endpoint is loaded and no review/selection/confirmation state exists.

- [ ] **Step 3: Implement review behaviour**

Load preview only when entering 정리 후보; render loading, error, empty, protected-policy summary, and grouped candidates. Each candidate has a title-labelled checkbox, expiry/reason text, and 보관 action. Compute selected bytes from preview artifacts. Call existing `useConfirm` with selected titles, count, and formatted byte total; preserve selection after an API error, clear it after a successful cleanup, show skipped IDs in a toast/result note, and refresh both normal artifacts and preview.

- [ ] **Step 4: Verify and commit**

Run: `npm test -- --run src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`

Expected: PASS, including default-unchecked, cancellation, selected-ID request, skipped result, pin, loading/error, and existing viewer coverage.

Commit: `git add frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx; git commit -m "feat: add artifact cleanup review"`

### Task 4: Build and run focused frontend regression checks

**Files:**
- Test: `frontend/src/api/client.test.js`
- Test: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`
- Test: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`

- [ ] **Step 1: Run focused UI tests**

Run: `npm test -- --run src/api/client.test.js src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/components/organisms/ArchiveView/ArchiveView.test.jsx`

Expected: PASS.

- [ ] **Step 2: Build the frontend**

Run: `npm run build`

Expected: Vite completes with exit code 0.

- [ ] **Step 3: Check diff scope**

Run: `git diff --check; git status --short`

Expected: no whitespace errors; only planned client, artifact view, and test files are changed.
