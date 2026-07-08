# Molecules

Project-local Atomic Design catalog for the Vite React frontend.

### AuthCard
- Path: `src/components/molecules/AuthCard/`
- Props: auth stage data and callbacks.
- Use when: Rendering the OTP setup/login/recovery card content.
- Don't use when: Fetching auth state; the container owns data loading.

### Composer
- Path: `src/components/molecules/Composer/`
- Props: `{ busy, onSend }`
- Use when: Rendering the chat textarea and send button.
- Don't use when: Mutating timeline state directly.

### LoaderCube
- Path: `src/components/molecules/LoaderCube/`
- Props: `{ label? }`
- Use when: Showing the existing inline 3D loading indicator.
- Don't use when: Full-page loading is needed.

### AgentAvailabilityBadge
- Path: `src/components/molecules/AgentAvailabilityBadge/`
- Props: `{ available, reason? }`
- Use when: Rendering compact local agent availability state.
- Don't use when: Command/runtime execution status is needed; use `StatusBadge`.

### AgentOptionField
- Path: `src/components/molecules/AgentOptionField/`
- Props: `{ option, value, disabled?, onChange }`
- Use when: Rendering registry-defined agent option controls from schema.
- Don't use when: A standalone raw field is enough without schema-driven behavior.
