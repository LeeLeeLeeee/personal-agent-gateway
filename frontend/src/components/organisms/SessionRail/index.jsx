import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";

export function SessionRail({ sessions, activeConfig, onSearch, onActivate, onReset, onRename, onDelete }) {
  const confirm = useConfirm();
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

          const agentId = session.is_active ? (activeConfig?.agent_id || session.agent_id) : session.agent_id;
          const model = session.is_active ? (activeConfig?.model || session.model) : session.model;
          return (
            <div key={session.id} className={`sess-item${session.is_active ? " sess-item-active" : ""}`} style={{ cursor: "pointer" }} onClick={() => onActivate(session.id)}>
              <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{session.title || "Untitled"}</div>
              {agentId ? (
                <div className="sess-meta">
                  <span className="sess-meta-agent mono">{String(agentId).toUpperCase()}</span>
                  <span className="sess-meta-model mono">{model}</span>
                </div>
              ) : (
                <div className="mono" style={{ fontSize: 10, color: "var(--c-grey)", marginTop: 3 }}>{session.status} · {session.message_count} msg</div>
              )}
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
                  onClick={async (event) => {
                    event.stopPropagation();
                    if (await confirm({ title: "DELETE SESSION", message: "Delete this session? This cannot be undone.", confirmLabel: "Delete", danger: true })) {
                      onDelete(session.id);
                    }
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
