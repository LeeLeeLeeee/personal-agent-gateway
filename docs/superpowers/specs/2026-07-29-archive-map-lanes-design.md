# Archive Knowledge Lane Map Design

## Context

The Archive map currently lays out every persona in one source column, even
when a persona has no relationship to a request, draft, or published entry.
Nodes are positioned independently by kind, so related items can land on
different rows and create crossing connectors. A shared black arrow marker
also makes colored relationship lines look like unexplained black arrows.

## Goal

Make the map describe knowledge lifecycle progress rather than act as a
persona inventory.

## Information hierarchy

The map is divided into three labeled sections:

1. `SHARED KNOWLEDGE` for globally scoped requests and entries.
2. `PERSONA-SPECIFIC` for knowledge requested by or published to personas.
3. `AUTOMATION` for drafts or entries originating from hooks.

Each row is a lifecycle lane for one request or one standalone knowledge
item:

```text
SOURCE BADGE | KNOWLEDGE REQUEST | DOCUMENTATION TEAM | DRAFT / LIBRARY
```

Persona, shared-scope, and hook sources are compact badges inside a lane.
They are not full-size inventory nodes.

## Lane construction

The frontend derives lanes from the existing Archive graph response; the API
contract does not change.

1. Create one lane for every request.
2. Add its incoming source, delegated team, and produced draft or entry to
   that lane.
3. Mark downstream drafts and entries already represented by a request lane.
4. Create one lane for each remaining standalone draft or entry.
5. Add all directly related persona, shared-scope, or hook sources as compact
   badges.
6. Omit nodes that do not participate in any request or knowledge-item lane.

Section precedence is deterministic: a hook source places the lane in
`AUTOMATION`; otherwise a persona source places it in `PERSONA-SPECIFIC`;
all remaining lanes belong to `SHARED KNOWLEDGE`.

The same documentation team may appear in more than one lane because each
lane represents a separate knowledge lifecycle.

## Connectors

Arrowheads are removed. Left-to-right columns already communicate direction,
and removing arrowheads eliminates the misleading shared black marker.

Connectors stay within one lane and carry short labels:

- `GAP` for source-to-request relationships.
- `DELEGATED` for request-to-team relationships.
- `DRAFT` for team or source output to a private draft.
- `PUBLISHED` for a published Library relationship.

Line styles remain distinct:

- Black solid: published knowledge.
- Orange dashed: knowledge gap.
- Gray dashed: delegated work.
- Blue solid: private draft output.

## Interaction

- Existing mouse-wheel zoom, zoom buttons, drag-to-pan, and Fit controls stay.
- Selecting any full node opens the existing Map Inspector.
- Compact source badges may select their corresponding source node.
- Fit uses the total height of the rendered sections and lanes.
- Empty sections are not rendered.

## Accessibility

- Each section has a visible heading and an accessible group label.
- Each lane exposes a label based on its request or knowledge-item title.
- Connector labels are visible text; understanding does not depend only on
  color or dash style.
- Existing node buttons and keyboard selection remain available.

## Testing

Frontend tests must verify:

1. Unconnected personas are omitted.
2. Connected request, team, and result nodes share one lane.
3. Shared, persona-specific, and automation lanes appear in the correct
   sections.
4. Connectors have text labels and no arrow marker.
5. Zoom, pan, Fit, selection, and empty-map behavior remain functional.

## Non-goals

- Do not change the Archive graph API.
- Do not add persona filtering or expand/collapse controls.
- Do not add a general-purpose graph-layout dependency.
- Do not change Library, Draft, Request, or Artifact lifecycle behavior.
