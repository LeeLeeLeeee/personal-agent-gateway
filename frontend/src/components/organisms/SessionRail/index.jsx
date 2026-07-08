import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";

export function SessionRail({ sessions, onSearch, onActivate, onReset, onRename, onDelete }) {
  const [editingSession, setEditingSession] = useState(null);
  const [editTitle, setEditTitle] = useState("");

  async function save(sessionId) {
    const title = editTitle.trim();
    if (title) await onRename(sessionId, title);
    setEditingSession(null);
    setEditTitle("");
  }

  return (
    <div className="sess-rail" aria-label="Sessions">
      <div className="sess-head">
        <span className="headline" style={{ fontSize: 12 }}>Sessions</span>
        <Button size="btn-sm" onClick={onReset}>+</Button>
      </div>
      <div style={{ padding: "10px 12px" }}>
        <InputField type="search" placeholder="Search" onChange={(event) => onSearch(event.target.value.trim())} />
      </div>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {(sessions || []).map((session) => {
          if (editingSession === session.id) {
            return (
              <div key={session.id} className={`sess-item${session.is_active ? " sess-item-active" : ""}`}>
                <input
                  className="sess-edit"
                  type="text"
                  maxLength="120"
                  value={editTitle}
                  onChange={(event) => setEditTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") save(session.id);
                    if (event.key === "Escape") setEditingSession(null);
                  }}
                />
                <div className="sess-actions">
                  <Button size="btn-sm" variant="primary" onClick={() => save(session.id)}>Save</Button>
                  <Button size="btn-sm" onClick={() => setEditingSession(null)}>Cancel</Button>
                </div>
              </div>
            );
          }

          return (
            <div key={session.id} className={`sess-item${session.is_active ? " sess-item-active" : ""}`} style={{ cursor: "pointer" }} onClick={() => onActivate(session.id)}>
              <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{session.title || "Untitled"}</div>
              <div className="mono" style={{ fontSize: 10, color: "var(--c-grey)", marginTop: 3 }}>{session.status} · {session.message_count} msg</div>
              <div className="sess-actions">
                <Button
                  size="btn-sm"
                  onClick={(event) => {
                    event.stopPropagation();
                    setEditingSession(session.id);
                    setEditTitle(session.title || "");
                  }}
                >
                  Rename
                </Button>
                <Button
                  size="btn-sm"
                  variant="destructive"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (window.confirm("Delete session?")) onDelete(session.id);
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
