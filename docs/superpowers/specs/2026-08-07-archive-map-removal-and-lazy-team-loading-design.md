# Archive Map Removal and Lazy Documentation Team Loading Design

## Goal

Remove the Archive Map across frontend and backend, then stop loading Team Runs
until the user opens the Requests tab. Preserve every Library, Draft, Artifact,
and Knowledge Request mutation workflow.

This is Phase 1 of the broader PAG information-architecture cleanup. It must
produce a small, independently reversible change before navigation and screen
ownership are reorganized.

## Context

Archive Map is a derived visualization. `ArchiveService.graph()` converts
Library entries, drafts, requests, personas, hooks, and Team Runs into generic
nodes and edges. `ArchiveView` converts that response again into four fixed
columns and three sections. The Map owns no durable state or mutation; its
inspector redirects users to the existing Library and Requests workflows.

The Map does provide one unique presentation: a cross-domain binding and
provenance overview. This phase intentionally removes that overview because
the edge labels mix binding, creation provenance, and inferred workflow
semantics. No replacement board is included without evidence that users need a
global knowledge-progress view.

The current Archive screen performs seven requests on entry:

1. published entries;
2. draft entries;
3. personas;
4. knowledge requests;
5. Team Runs;
6. Archive Map;
7. Artifacts loaded by `GatewayApp`.

After this phase, the screen performs five requests on entry. The Map request
is removed and Team Runs are loaded only when Requests is opened.

## Scope

### In scope

- Remove the Map tab, count, layout, viewport, inspector, and Map-only state.
- Remove `api.archiveMap()` from the frontend client.
- Remove `GET /api/archive/map` without a compatibility period.
- Remove `ArchiveService.graph()` and its graph-contract tests.
- Remove Map-only CSS and test fixtures.
- Load Team Runs on the first Requests-tab entry instead of Archive mount.
- Keep successful Team Run results cached for the lifetime of `ArchiveView`.
- Let the user retry Team Run loading after a failure.
- Preserve direct Library entries and direct, Hook-origin, Team-origin, and
  Knowledge-Request-origin Draft flows.

### Out of scope

- Splitting `ArchiveView` into separate panel components or controller hooks.
- Renaming Archive, Library, or Artifacts.
- Moving Artifacts from `GatewayApp` ownership.
- Adding a request status board or another provenance visualization.
- Combining Jobs, Schedules, and Hooks into Automations.
- Combining Rules and Spaces into Configuration or Policies.
- Changing database schema, stored Archive data, or migration code.
- Adding usage telemetry.

## Architecture

### Frontend ownership

`ArchiveView` remains the owner of Library entries, drafts, personas,
Knowledge Requests, editor state, and documentation Team Run selection.
`GatewayApp` continues to own the Artifact list and refresh callback.

The initial `loadData()` reads only:

```text
entries + drafts + personas + knowledge requests
```

It no longer reads Team Runs or a graph. Existing publish, revise, delete,
request-status, and delegation mutations may still invalidate this base
Archive data through `loadData()`; they do not invalidate cached Team Runs.

### Requests-only Team Run loading

Keep `teamRuns` as an array and add this explicit request state:

```text
idle | loading | ready | error
```

The Requests tab loads Team Runs automatically only when the state is `idle`.
`loading` and `ready` prevent a duplicate request, while `error` waits for the
user's explicit retry action. A successful response is cached until
`ArchiveView` unmounts. Navigating to another top-level screen unmounts Archive,
so returning to Archive starts with `idle` and obtains a fresh Team Run list.

Changing the injected `client` resets Team Runs and their request state to
`idle` and invalidates any in-flight response from the previous client. This
keeps component tests and non-production client injection deterministic.

### Backend removal

Delete the `/map` handler from the Archive router and delete
`ArchiveService.graph()`. The entry, request, delegation, revision, archive,
and delete endpoints remain unchanged.

The removed endpoint returns the normal API `404`. There is no deprecated
alias and no empty graph response. Repository production code has no consumer
other than the removed Archive Map, and the user has accepted the external
compatibility break.

### Removed frontend dependencies

Remove all Map-only items together so no orphan remains:

- `MAP_COLUMNS`, `MAP_SECTIONS`, graph classification and lane helpers;
- `ArchiveMap`;
- `graph` and `selectedNodeId` state;
- the `archiveMap()` call and graph setters;
- the Map tab and panel wiring;
- Map-only source, node, edge, viewport, inspector, legend, focus, and
  responsive CSS selectors;
- frontend graph fixtures and zoom, pan, Fit, section, lane, and edge tests.

`editEntry()` and `beginRequestDraft()` remain because Library, Drafts, and
Requests use them directly. They are not Map-only callbacks.

## Data flow

### Archive entry

```text
GatewayApp selects Archive
  -> GatewayApp requests Artifacts
  -> ArchiveView requests entries, drafts, personas, and requests in parallel
  -> Library renders
```

### Requests entry

```text
User opens Requests
  -> Requests panel renders immediately
  -> teamRunsStatus idle: request Team Runs
  -> teamRunsStatus loading: disable delegation-only controls
  -> success: cache Team Runs and enable valid documentation teams
  -> failure: preserve all non-delegation Request actions and wait for explicit retry
```

### Request mutations

- `Write in Library` does not require Team Runs.
- Later, Dismiss, and Reopen do not require Team Runs.
- Send to team requires `teamRunsStatus === ready` and a valid selected Team
  Run.
- Delegation success refreshes Archive entries, drafts, personas, and requests;
  it retains the cached Team Runs.

## Loading and error behavior

- The existing Archive-level loading state continues to cover the four base
  requests.
- While Team Runs load, the Requests list, outline, source hints, and direct
  actions remain visible.
- Documentation Team selectors and Send buttons are disabled while the Team
  Run state is not `ready`.
- A Team Run failure uses the existing Archive error presentation and adds a
  visible `Retry team loading` action in the Requests panel.
- Retry changes `error` to `loading`, clears the relevant error, and repeats
  only `client.teamRuns()`.
- An empty successful response is `ready`, not `error`; the UI continues to
  show `No triggered team available`.
- Base Archive load failures keep the existing Archive alert and loading-state
  behavior.

## Testing strategy

### Frontend component tests

Update `ArchiveView.test.jsx` to prove:

- the Map tab and Map content are absent;
- initial render does not call `client.teamRuns()`;
- the first Requests entry calls `client.teamRuns()` once;
- reopening Requests after success reuses the result;
- loading Team Runs disables delegation controls only;
- a Team Run failure preserves Write, Later, and Dismiss actions;
- retry calls only `client.teamRuns()` and restores delegation;
- existing Library search, draft review, publish, delete, request status, and
  delegation workflows continue to pass.

Remove assertions for Map item counts, section lanes, edge labels, zoom, pan,
wheel behavior, and Fit.

### Frontend client and container tests

- Remove the `archiveMap()` client contract and its fetch assertion.
- Remove `/api/archive/map` from `GatewayApp` fetch fixtures.
- Update request-order assertions affected by deleting the client method.
- Prove the Archive screen no longer fetches Map data.

### Backend tests

- Replace the `/api/archive/map` success expectation with an authenticated
  `404` expectation.
- Remove `ArchiveService.graph()` node and edge assertions.
- Keep Archive entry, request, delegation, revision, deletion, and audit tests
  unchanged.

### Static and full verification

Repository search must find no production or test reference to:

```text
ArchiveMap
archiveMap
/api/archive/map
archive-map-
archive-legend-line
```

Run focused frontend and backend tests first, then the full frontend suite,
production build, and diff check. No database migration or restore test is
required because persisted data does not change.

## Rollout and rollback

This change has no feature flag. It ships as direct removal of an unused
derived screen and endpoint. A rollback reverts code only; there is no stored
state conversion to reverse.

The previous Archive Map design and implementation plan remain historical
records. This design supersedes their product direction but does not rewrite
their completed history.

## Follow-up project sequence

Each item receives its own design and implementation plan after the previous
one is verified:

1. Split Archive navigation into Knowledge and Outputs while keeping current
   data ownership stable.
2. Combine Jobs, Schedules, and Hooks under an Automations shell without
   merging their backend lifecycles.
3. Group Teams, Personas, Rules, Spaces, and Settings under Configuration,
   keeping Rules and Space permission semantics distinct.
4. Separate Home summary from Operations recovery and emergency controls.
5. Add local, content-free usage counts and decide whether mail Hooks should
   become an optional connector.

Deep links and URL routing are evaluated with the first navigation-shell
project rather than introduced during Map removal.
