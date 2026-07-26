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

export function TeamTaskCard({ task, owner, fileCount = 0, reportCount = 0, onOpen }) {
  const avatar = owner?.persona_snapshot?.avatar;
  const noteText = note(task);

  return (
    <button
      type="button"
      className="team-task-card"
      aria-label={`Open task ${task.title}`}
      onClick={onOpen}
    >
      <div className="team-task-title-row">
        <div className="team-task-title">{task.title}</div>
        <span className={`team-task-status mono team-task-status-${task.status}`}>
          {String(task.status || "pending").replace("_", " ").toUpperCase()}
        </span>
      </div>
      {task.result ? <div className="team-task-result">{task.result}</div> : null}
      <div className="team-task-meta">
        <span className="team-task-owner-profile" title={owner?.name || "UNASSIGNED"}>
          {avatar ? (
            <img className="team-task-owner-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
          ) : owner ? (
            <span className="team-task-owner mono">{initials(owner.name)}</span>
          ) : null}
          <span className="team-task-owner-name mono">{owner?.name || "UNASSIGNED"}</span>
        </span>
        {noteText ? (
          <span className={`team-task-note mono team-task-note-${task.status === "failed" ? "danger" : "warning"}`}>
            {noteText}
          </span>
        ) : null}
        <span className={`team-task-file-count mono${fileCount ? " has-files" : ""}`}>
          FILES {fileCount}
        </span>
        <span className={`team-task-report-count mono${reportCount ? " has-reports" : ""}`}>
          REPORTS {reportCount}
        </span>
      </div>
    </button>
  );
}
