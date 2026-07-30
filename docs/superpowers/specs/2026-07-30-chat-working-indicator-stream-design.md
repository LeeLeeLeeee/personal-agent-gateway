# Chat Working Indicator Stream Design

## Goal

Display the active Chat `WORKING` state as part of the centered transcript
stream instead of as a full-width bordered status panel.

## Ownership

`Timeline` owns the ordered visual stream of user messages, agent activity,
reasoning, and runtime status. `WorkingIndicator` moves from `ChatView` into
`Timeline` and is rendered as the final `.stream` child while `busy` is true.

`ChatView` continues to own the Escape keyboard effect because it owns the
injected `onInterrupt` callback and the effect lifecycle. Moving the visual
indicator does not move interruption behavior or runtime state ownership.

## Component Contract

`Timeline` already receives `busy`. It additionally receives `turnStart` so
the local `WorkingIndicator` can calculate and display elapsed time.

`ChatView` passes `turnStart` to `Timeline` and removes its separate
`WorkingIndicator` sibling. No generic child or footer slot is introduced
because there is only one consumer.

## Layout

The indicator becomes the last child of `.stream`, inheriting its
`max-width: 760px` and centered layout.

The indicator keeps:

- the animated warning-colored dot;
- `WORKING · <elapsed>`;
- the right-aligned `esc to interrupt` hint;
- `role="status"` and `aria-live="polite"`.

The indicator removes:

- border;
- background;
- border radius;
- extra outer margin that makes it look detached.

Horizontal padding becomes zero so the status aligns with the stream content.

## Behavior

- `busy=true` renders the indicator inside `.stream`.
- `busy=false` renders no indicator.
- Pressing Escape while busy still calls `onInterrupt` from `ChatView`.
- Timeline event ordering, transcript scrolling, approvals, and Composer
  behavior remain unchanged.

## Focused Verification

- A busy Chat renders `.stream .working-indicator`.
- The indicator still shows elapsed time and the interrupt hint.
- An idle Chat does not render the indicator.
- Existing busy and idle Escape behavior remains covered.
- The focused `ChatView` test file passes.
- The frontend production build succeeds.
