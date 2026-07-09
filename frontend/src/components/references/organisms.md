# Organisms

Project-local Atomic Design catalog for the Vite React frontend.

### Sidebar
- Path: `src/components/organisms/Sidebar/`
- Props: navigation state and callbacks.
- Use when: Rendering app-level navigation.
- Don't use when: Feature-local tabs are needed.

### Statusbar
- Path: `src/components/organisms/Statusbar/`
- Props: runtime status, timeline entries, SSE state.
- Use when: Rendering the top app telemetry bar.
- Don't use when: Showing per-command status inside the timeline.

### SessionRail
- Path: `src/components/organisms/SessionRail/`
- Props: session collection and session actions.
- Use when: Rendering chat session search/switch/rename/delete controls.
- Don't use when: Loading session data; the container owns API calls.

### ChatView
- Path: `src/components/organisms/ChatView/`
- Props: chat state and action callbacks.
- Use when: Rendering the chat page composition.
- Don't use when: Owning global auth/bootstrap state.

### Timeline
- Path: `src/components/organisms/Timeline/`
- Props: `{ entries, busy }`
- Use when: Rendering persisted and live agent activity entries.
- Don't use when: Transforming raw API events; use `lib/timeline`.

### MarkdownContent
- Path: `src/components/organisms/MarkdownContent/`
- Props: `{ source }`
- Use when: Rendering agent markdown with code/table/mermaid blocks.
- Don't use when: Rendering trusted arbitrary HTML.

### AgentPicker
- Path: `src/components/organisms/AgentPicker/`
- Props: `{ agents, config, onChange, error? }`
- Use when: Rendering editable or locked session agent configuration state.
- Don't use when: Loading agents or persisting config changes; the container owns API calls.

### PersonaLibrary
- Path: `src/components/organisms/PersonaLibrary/`
- Props: `{ personas, avatars, onCreate, onSave? }`
- Use when: Rendering the persona master-detail (left list → right EDIT PERSONA panel for the selected persona; a New persona button opens a blank editable panel). Save calls `onCreate` for a new persona or `onSave(id, payload)` for an existing one; the avatar block opens a modal that reuses `AvatarPicker`.
- Don't use when: Loading personas or the avatar manifest; the container owns API calls.

### AvatarPicker
- Path: `src/components/organisms/AvatarPicker/`
- Props: `{ avatars, value, onSelect }`
- Use when: Rendering the 60-avatar grid (grouped by category: People/Tech/Animals/Creatures) for picking a persona avatar slug.
- Don't use when: Loading the avatar manifest; the container fetches `/static/avatars/manifest.json` and passes `avatars` down.

### TeamRunForm
- Path: `src/components/organisms/TeamRunForm/`
- Props: `{ personas, onSubmit }`
- Use when: Rendering the new-team-run form (goal, leader select, member checkboxes, run mode, max workers).
- Don't use when: Loading personas or creating the team run via API; the container owns that call.

### TeamRunDetail
- Path: `src/components/organisms/TeamRunDetail/`
- Props: `{ detail: { run, agents, tasks, messages } }`
- Use when: Rendering a team run's header/meta strip, agent session lanes (persona/role/status/current task), the pending/in_progress/blocked/completed/failed task board, the live activity list, and per-agent result reports plus the final summary. Renders "No team run selected." when `detail?.run` is absent.
- Don't use when: Loading team run data or reacting to `/api/events` SSE updates; the container owns fetching and refetches `detail` on `team.*` events for the selected run.
