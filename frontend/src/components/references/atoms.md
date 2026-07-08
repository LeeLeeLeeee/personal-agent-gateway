# Atoms

Project-local Atomic Design catalog for the Vite React frontend.

### Button
- Path: `src/components/atoms/Button/`
- Props: native button props plus `{ size?, variant? }`
- Use when: Rendering clickable command buttons with existing gateway button classes.
- Don't use when: Semantic navigation links are needed.

### InputField
- Path: `src/components/atoms/Field/`
- Props: native input/textarea props plus `{ as? }`
- Use when: Rendering gateway form controls with the shared `.input-field` class.
- Don't use when: A compound labelled field is needed.

### StatusBadge
- Path: `src/components/atoms/StatusBadge/`
- Props: `{ kind }`
- Use when: Showing command/live status labels.
- Don't use when: A larger status summary cell is needed.
