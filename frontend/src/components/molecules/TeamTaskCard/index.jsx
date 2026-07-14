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

export function TeamTaskCard({ task, owner, documentCount = 0, onOpen }) {
  const avatar = owner?.persona_snapshot?.avatar;
  const noteText = note(task);

  return (
    <button
      type="button"
      className="team-task-card"
      aria-label={`Open task ${task.title}`}
      onClick={onOpen}
    >
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
        <span className={`team-task-doc-count mono${documentCount ? " has-documents" : ""}`}>
          DOCS {documentCount}
        </span>
      </div>
    </button>
  );
}
