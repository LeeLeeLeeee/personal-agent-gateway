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
