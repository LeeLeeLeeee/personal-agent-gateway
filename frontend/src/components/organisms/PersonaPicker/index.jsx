export function PersonaPicker({ personas = [], value = "", onChange }) {
  const selected = personas.find((persona) => persona.id === value) || null;

  return (
    <div className="config-bar persona-config-bar" aria-label="Persona config">
      <div className="config-bar-row">
        <label className="config-field">
          <span className="config-field-label mono">PERSONA</span>
          <select
            className="config-sel persona-config-select"
            aria-label="Persona"
            value={value}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="" disabled>Select persona</option>
            {personas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}{persona.role ? ` — ${persona.role}` : ""}
              </option>
            ))}
          </select>
        </label>
        {selected ? (
          <>
            <span className="config-chip">
              <span className="config-chip-k mono">RUNTIME</span>
              <span className="config-chip-v mono">
                {selected.default_backend} / {selected.default_model}
              </span>
            </span>
            {selected.description ? (
              <span className="config-chip">
                <span className="config-chip-k mono">PURPOSE</span>
                <span className="config-chip-v">{selected.description}</span>
              </span>
            ) : null}
          </>
        ) : (
          <span className="config-unavailable mono">
            {personas.length ? "SELECT A PERSONA" : "NO PERSONAS — CREATE ONE IN PERSONAS"}
          </span>
        )}
      </div>
    </div>
  );
}
