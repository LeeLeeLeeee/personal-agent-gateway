# Templates

Project-local Atomic Design catalog for the Vite React frontend.

### AuthTemplate
- Path: `src/components/templates/AuthTemplate/`
- Props: `{ children }`
- Use when: Centering unauthenticated auth content.
- Don't use when: Rendering the authenticated app shell.

### AppShell
- Path: `src/components/templates/AppShell/`
- Props: shell slots plus navigation/runtime status props.
- Use when: Composing sidebar, statusbar, and main content.
- Don't use when: A route owns data fetching directly.
