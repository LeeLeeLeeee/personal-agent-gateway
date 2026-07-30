# Chat Working Indicator Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the busy Chat working indicator inside the centered Timeline stream without its bordered panel styling.

**Architecture:** `Timeline` owns the visual working status and receives `turnStart` from `ChatView`. `ChatView` retains the Escape interruption effect and only forwards state required for rendering.

**Tech Stack:** React 19, JavaScript, CSS, Vitest, Testing Library, Vite

## Global Constraints

- Render the indicator as the final `.stream` child only while `busy` is true.
- Keep `role="status"`, `aria-live="polite"`, elapsed time, warning dot, and interrupt hint.
- Keep the Escape effect and `onInterrupt` ownership in `ChatView`.
- Do not introduce a generic child or footer slot.
- Remove the indicator border, background, border radius, and detached outer margin.
- Run only the focused `ChatView` test file and frontend production build.

---

### Task 1: Move the working indicator into Timeline

**Files:**
- Modify: `frontend/src/components/organisms/ChatView/index.jsx`
- Modify: `frontend/src/components/organisms/Timeline/index.jsx`
- Modify: `frontend/src/components/organisms/ChatView/ChatView.test.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Replace: `src/personal_agent_gateway/frontend_dist/assets/index-BlxqRnGu.css`
- Replace: `src/personal_agent_gateway/frontend_dist/assets/index-Dzl9tKeL.js`
- Modify: `src/personal_agent_gateway/frontend_dist/index.html`

**Interfaces:**
- Consumes: `Timeline({ entries, busy, turnStart, sessionId, registeredByPath, onRegistered })`
- Produces: `.stream > .working-indicator` while busy

- [x] **Step 1: Write the failing stream-ownership test**

Extend the existing working-indicator test:

```jsx
it("shows a live working indicator inside the timeline stream while busy", () => {
  render(<ChatView {...props([])} busy turnStart={Date.now() - 5000} turnStreamed />);
  const indicator = document.querySelector(".working-indicator");
  expect(indicator).toBeTruthy();
  expect(indicator.closest(".stream")).not.toBeNull();
  expect(indicator.textContent).toContain("WORKING");
  expect(indicator.textContent).toContain("esc to interrupt");
});
```

- [x] **Step 2: Run the test to verify RED**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ChatView/ChatView.test.jsx
```

Expected: one failure because `.working-indicator` is currently a sibling of
the Timeline `.stream`.

- [x] **Step 3: Move visual ownership to Timeline**

In `ChatView`:

- change the time import to `fmtDateTime` only;
- remove the local `WorkingIndicator`;
- pass `turnStart={turnStart}` to `Timeline`;
- remove the separate busy indicator sibling.

In `Timeline`:

```jsx
import { fmtElapsed } from "../../../lib/time.js";

function WorkingIndicator({ turnStart }) {
  const elapsed = turnStart ? fmtElapsed((Date.now() - turnStart) / 1000) : "0s";
  return (
    <div className="working-indicator" role="status" aria-live="polite">
      <span className="working-dot" />
      <span className="working-label mono">WORKING · {elapsed}</span>
      <span className="working-hint mono">esc to interrupt</span>
    </div>
  );
}
```

Change the Timeline signature to accept `turnStart = null` and append:

```jsx
return (
  <div className="stream">
    {nodes}
    {busy ? <WorkingIndicator turnStart={turnStart} /> : null}
  </div>
);
```

- [x] **Step 4: Remove the panel styling**

Replace the `.working-indicator` layout declaration with:

```css
.working-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  padding: 8px 0;
  border: none;
  background: transparent;
}
```

- [x] **Step 5: Run focused verification**

Run:

```powershell
npm --prefix frontend test -- src/components/organisms/ChatView/ChatView.test.jsx
npm run build:frontend
```

Expected: nine ChatView tests pass and the frontend build exits with code 0.

- [x] **Step 6: Inspect and commit the scoped diff**

Run:

```powershell
git diff --check
git diff -- frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/ChatView/ChatView.test.jsx src/personal_agent_gateway/static/styles.css
git add -- frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/ChatView/ChatView.test.jsx src/personal_agent_gateway/static/styles.css docs/superpowers/plans/2026-07-30-chat-working-indicator-stream.md
git add -f -- src/personal_agent_gateway/frontend_dist
git commit -m "fix(chat): working 상태를 timeline stream에 배치"
```
