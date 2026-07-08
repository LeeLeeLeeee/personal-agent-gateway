function initials(name) {
  return (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";
}

function note(task) {
  if (task.status === "failed" || task.status === "blocked") return task.error_message || null;
  return null;
}

export function TeamTaskCard({ task, owner }) {
  const avatar = owner?.persona_snapshot?.avatar;
  const noteText = note(task);

  return (
    <article className="team-task-card">
      <div className="team-task-title">{task.title}</div>
      <div className="team-task-meta">
        {avatar ? (
          <img className="team-task-owner-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
        ) : (
          <span className="team-task-owner mono">{initials(owner?.name)}</span>
        )}
        {noteText ? (
          <span className={`team-task-note mono team-task-note-${task.status === "failed" ? "danger" : "warning"}`}>
            {noteText}
          </span>
        ) : null}
      </div>
    </article>
  );
}
