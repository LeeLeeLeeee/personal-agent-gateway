import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";

const MENU_WIDTH = 128;
const MENU_HEIGHT = 96;
const MENU_GAP = 6;
const VIEWPORT_MARGIN = 8;

function getMenuPosition(anchor) {
  const rect = anchor.getBoundingClientRect();
  const maxLeft = window.innerWidth - MENU_WIDTH - VIEWPORT_MARGIN;
  const left = Math.min(Math.max(VIEWPORT_MARGIN, rect.right - MENU_WIDTH), maxLeft);
  const below = rect.bottom + MENU_GAP;
  const top = below + MENU_HEIGHT > window.innerHeight - VIEWPORT_MARGIN
    ? Math.max(VIEWPORT_MARGIN, rect.top - MENU_HEIGHT - MENU_GAP)
    : below;

  return { left, top };
}

function SessionMenu({ position, onRename, onDelete }) {
  return createPortal(
    <div
      className="sess-menu config-menu"
      role="menu"
      aria-label="Session actions"
      style={{ left: position.left, top: position.top }}
      onClick={(event) => event.stopPropagation()}
    >
      <button type="button" className="config-menu-item" role="menuitem" onClick={onRename}>
        Rename
      </button>
      <button type="button" className="config-menu-item danger" role="menuitem" onClick={onDelete}>
        Delete
      </button>
    </div>,
    document.body
  );
}

export function SessionRail({ sessions, activeConfig, onSearch, onActivate, onReset, onRename, onDelete }) {
  const confirm = useConfirm();
  const [editingSession, setEditingSession] = useState(null);
  const [editTitle, setEditTitle] = useState("");
  const [openMenu, setOpenMenu] = useState(null);

  useEffect(() => {
    if (!openMenu) return undefined;

    function closeOnOutsidePointer(event) {
      if (event.target.closest(".sess-menu, .sess-menu-button")) return;
      setOpenMenu(null);
    }

    function closeOnEscape(event) {
      if (event.key === "Escape") setOpenMenu(null);
    }

    function closeMenu() {
      setOpenMenu(null);
    }

    document.addEventListener("mousedown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);

    return () => {
      document.removeEventListener("mousedown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, [openMenu]);

  async function save(sessionId) {
    const title = editTitle.trim();
    if (title) await onRename(sessionId, title);
    setEditingSession(null);
    setEditTitle("");
  }

  function startRename(session) {
    setOpenMenu(null);
    setEditingSession(session.id);
    setEditTitle(session.title || "");
  }

  async function confirmDelete(session) {
    setOpenMenu(null);
    if (await confirm({ title: "DELETE SESSION", message: "Delete this session? This cannot be undone.", confirmLabel: "Delete", danger: true })) {
      onDelete(session.id);
    }
  }

  function toggleMenu(session, event) {
    event.stopPropagation();
    if (openMenu?.sessionId === session.id) {
      setOpenMenu(null);
      return;
    }
    setOpenMenu({ sessionId: session.id, ...getMenuPosition(event.currentTarget) });
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
          const working = session.status === "running";
          return (
            <div key={session.id} className={`sess-item${session.is_active ? " sess-item-active" : ""}`} style={{ cursor: "pointer" }} onClick={() => onActivate(session.id)}>
              <div className="sess-top">
                <div className="sess-title">{session.title || "Untitled"}</div>
                {working ? <span className="badge badge-working">WORKING</span> : null}
              </div>
              {agentId ? (
                <div className="sess-meta">
                  <span className="sess-meta-agent mono">{String(agentId).toUpperCase()}</span>
                  <span className="sess-meta-model mono">{model}</span>
                </div>
              ) : (
                <div className="mono" style={{ fontSize: 10, color: "var(--c-grey)", marginTop: 3 }}>{session.status} · {session.message_count} msg</div>
              )}
              <div className="sess-menu-wrap">
                <button
                  type="button"
                  className="sess-menu-button"
                  aria-label={`Session actions ${session.title || "Untitled"}`}
                  aria-haspopup="menu"
                  aria-expanded={openMenu?.sessionId === session.id}
                  onClick={(event) => toggleMenu(session, event)}
                >
                  <span className="sess-menu-icon" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                </button>
                {openMenu?.sessionId === session.id ? (
                  <SessionMenu
                    position={openMenu}
                    onRename={() => startRename(session)}
                    onDelete={() => confirmDelete(session)}
                  />
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
