# Frontend Redesign Design — Slice 1 (Shell + Login + Chat)

**Date:** 2026-07-07
**Status:** Approved design (pre-plan)
**Track:** Frontend / design (owns `src/personal_agent_gateway/static/**`)

## Goal

Bring the `Agent Gateway.dc.html` neo-brutalist design into the real app's vanilla-JS frontend. This spec covers **Slice 1 only**: the app shell (sidebar + top status bar), the Login (OTP) screen, and the Chat screen (layout A). The other five screens (Jobs, Schedules, Capabilities, Artifacts, Settings) are later slices and appear in Slice 1 only as navigable PLANNED placeholders.

## Source of Truth

`Agent Gateway.dc.html` (claude.ai/design project `3f896375-eae4-496d-b1bf-c1eee9eedf4f`) is the design source of truth for layout, visual system, components, and screen flow. Implementation follows the mockup; decisions the mockup already makes are not re-litigated. This spec only records what the mockup does **not** specify: frontend architecture, API binding, PLANNED handling, and responsive adaptation.

- Chat default layout = **A** (session rail + right context drawer) — the mockup's `defaultChatLayout` default.
- Login = **OTP-first** (already corrected in the mockup to match the real `/api/auth/*`).

## Ownership Boundary

- This track owns `src/personal_agent_gateway/static/**`. Codex (backend) does not edit it.
- Backend/API is Codex's, tracked separately in `docs/superpowers/plans/2026-07-07-backend-api-gaps-plan.md`.
- Slice 1 touches **only** the three already-served static files — `index.html`, `app.js`, `styles.css` — so `app.py` (a shared file) is **not** modified. If a later slice needs additional static files, request a one-line `/static` mount from the backend track rather than editing `app.py` ad hoc.

## Architecture

- **No build step** (matches the backend plan's "React/Vite deferred"). Plain HTML/CSS/JS served as-is.
- **`index.html`** — app-shell skeleton: sidebar (nav + auth footer), top status bar, main content mount, right-drawer mount, plus the auth screen container. Mount points are empty; `app.js` renders into them.
- **`styles.css`** — neo-brutalist design tokens (heavy black borders, mono/headline/body font trio, square corners, black status bars, semantic colors: warning `#FFA500`, success `#008000`, danger `#FF0000`, link `#0000FF`) and component classes (Button variants primary/secondary/destructive/ghost, Chip, StatusChip, Input).
- **`app.js`** — single module (Slice 1 stays in one file). Responsibilities, kept as internally separable units:
  - *api* — fetch wrappers for the endpoints below.
  - *state* — in-memory app state (auth stage, current screen, sessions, messages, pending approval, status).
  - *router* — in-memory screen switch mirroring the mockup's `screen` state (no hash/history needed for Slice 1); sidebar nav sets the active screen.
  - *views* — render functions for the shell, Login (3 auth stages), and Chat (layout A regions).
- **Absorb, don't discard**: the current `app.js` already wires OTP setup/login, sessions, chat, and approvals. All existing API calls are preserved and re-skinned/re-structured; no working behavior is lost.

## Screens in Slice 1

**Sidebar** shows all 7 nav items. Chat is active/functional. The other 5 (Jobs, Schedules, Capabilities, Artifacts, Settings) render a **PLANNED placeholder** panel when selected. Auth footer shows authenticated state + Log out.

**Login (OTP-first)** — binds to real endpoints:
- `GET /api/auth/status` → `{authenticated, totp_configured}` drives which stage to show.
- Stage *login* (totp_configured=true): 6-digit OTP → `POST /api/auth/login {otp}` → `agent_session` cookie → enter shell.
- Stage *setup* (totp_configured=false): `POST /api/auth/setup/start` → `{secret, otpauth_uri, qr_svg}` (render inline QR SVG + manual key) → `POST /api/auth/setup/verify {otp}` → `{enabled, recovery_codes}`.
- Stage *recovery*: show the 10 recovery codes once, then continue.
- Invalid OTP → 401 → destructive error box.
- Logout → `POST /api/auth/logout`.
- Recovery-code **login** is PLANNED (no endpoint yet).

**Chat (layout A)** — binds to real endpoints:
- Transcript from `GET /api/history`; send via `POST /api/chat`; refresh `GET /api/status`.
- Session rail: `GET /api/sessions`, `GET /api/sessions/search?q=`, `POST /api/sessions/{id}/activate`, `DELETE /api/sessions/{id}`; "+" = `POST /api/reset` (new session, transcript kept on disk).
- Job Proposal card = current shell approval: capability `shell.run`, command preview, risk HIGH, Approve/Deny → `POST /api/approvals/{id}/approve|deny`. Result renders as a mono console (`$ cmd` / `exit N` / stdout / stderr). No live progress/pid, no artifact.
- Right context drawer: pending-approval panel is real. Session-artifacts and activity-timeline sections are **PLANNED** (no API yet).

**Status bar** — binds only to `GET /api/status` fields: WORKSPACE (`workspace_root`), MODEL (`provider`/`model`), SESSION (`session_status` + short `session_id`), PENDING (`pending_approval`). RUNNING count and TUNNEL are **PLANNED**.

## Responsive Adaptation

The mockup is a fixed 1440×900 frame; the real app must be fluid. Keep the neo-brutalist look while: sidebar collapses (icon/toggle) below a narrow breakpoint, right context drawer becomes an overlay rather than a fixed column, main content and composer stay usable. This adaptation is not specified by the mockup and is a design-track decision.

## PLANNED Policy

Anything the current API cannot back is shown as a greyed **PLANNED** state (never faked as working): the 5 non-Chat screens, RUNNING/TUNNEL status, session-artifacts/activity drawer sections, recovery-code login. As Codex lands the corresponding endpoints (see the backend-api-gaps plan), each PLANNED area is promoted in a later slice.

## Verification

No JS test harness exists (Python-only repo); adding one is out of scope for Slice 1. Verify by running the app and driving the real flow in a browser: **Login (OTP) → shell renders → Chat send → shell-command approval → console result**. Confirm status bar reflects `/api/status` and PLANNED areas are clearly non-interactive.

## Out of Scope (Later Slices)

- Jobs, Schedules, Capabilities, Artifacts, Settings screens (build once their APIs are stable).
- Module splitting / `/static` mount, hash routing, JS test harness.
- Chat layout B, artifact viewers/zoom, ffmpeg/capture/schedule UIs.

## References

- Design mockup: `Agent Gateway.dc.html` (claude.ai/design `3f896375-…`)
- UI/UX brief: `docs/design/2026-07-06-personal-agent-gateway-uiux-brief.md`
- Backend API gaps (Codex): `docs/superpowers/plans/2026-07-07-backend-api-gaps-plan.md`
- Backend architecture (done): `docs/superpowers/plans/2026-07-06-local-backend-architecture-plan.md`
