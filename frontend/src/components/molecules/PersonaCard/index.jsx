function initials(name) {
  return (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";
}

export function PersonaCard({ persona }) {
  const responsibilities = (persona.responsibilities || []).slice(0, 3);

  return (
    <article className="persona-card">
      <div className="persona-card-head">
        {persona.avatar ? (
          <img className="persona-avatar" src={`/static/avatars/${persona.avatar}.png`} alt="" />
        ) : (
          <span className="persona-avatar persona-avatar-initials mono">{initials(persona.name)}</span>
        )}
        <div className="persona-card-title">
          <div className="headline" style={{ fontSize: 13 }}>{persona.name}</div>
          <div className="mono" style={{ fontSize: 10, color: "var(--c-grey)" }}>{persona.role}</div>
        </div>
      </div>
      {persona.description ? <p style={{ fontSize: 12 }}>{persona.description}</p> : null}
      {responsibilities.length ? (
        <div className="chip-row">
          {responsibilities.map((item) => (
            <span className="chip" key={item}>{item}</span>
          ))}
        </div>
      ) : null}
    </article>
  );
}
