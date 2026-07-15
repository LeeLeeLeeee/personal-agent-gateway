import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const EMPTY = { name: "", description: "", leader_persona_id: "", member_persona_ids: [] };

function initials(name) {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
}

function PersonaChoice({ persona, active, selectedLabel, onClick }) {
  return (
    <button type="button" aria-label={persona.name} aria-pressed={active}
      className={`teams-persona${active ? " active" : ""}`} onClick={onClick}>
      {persona.avatar ? (
        <img className="teams-persona-avatar" src={`/static/avatars/${persona.avatar}.png`} alt="" />
      ) : (
        <span className="teams-persona-avatar teams-persona-initials mono" aria-hidden="true">
          {initials(persona.name)}
        </span>
      )}
      <span className="teams-persona-copy">
        <span className="teams-persona-name">{persona.name}</span>
        <span className="teams-persona-role">{persona.role || "ROLE NOT SET"}</span>
      </span>
      <span className="teams-persona-state mono" aria-hidden="true">
        {active ? `✓ ${selectedLabel}` : "SELECT"}
      </span>
    </button>
  );
}

export function TeamsView({ teams = [], personas = [], onCreate, onUpdate, onDelete }) {
  const [editingId, setEditingId] = useState(null); // null = none, "new" = create
  const [draft, setDraft] = useState(EMPTY);

  useEffect(() => {
    if (editingId === "new" && !draft.leader_persona_id && personas.length) {
      setDraft((d) => ({ ...d, leader_persona_id: personas[0].id }));
    }
  }, [editingId, personas, draft.leader_persona_id]);

  function startCreate() { setDraft(EMPTY); setEditingId("new"); }
  function startEdit(team) {
    setDraft({
      name: team.name, description: team.description || "",
      leader_persona_id: team.leader_persona_id,
      member_persona_ids: [...(team.member_persona_ids || [])]
    });
    setEditingId(team.id);
  }
  function toggleMember(id) {
    setDraft((d) => ({
      ...d,
      member_persona_ids: d.member_persona_ids.includes(id)
        ? d.member_persona_ids.filter((x) => x !== id)
        : [...d.member_persona_ids, id]
    }));
  }
  async function save() {
    const payload = { ...draft, member_persona_ids: draft.member_persona_ids.filter((id) => id !== draft.leader_persona_id) };
    const result = editingId === "new" ? await onCreate(payload) : await onUpdate(editingId, payload);
    if (result) setEditingId(null);
  }
  const selectedMemberCount = draft.member_persona_ids.filter((id) => id !== draft.leader_persona_id).length;

  return (
    <section className="teams-view" aria-label="Teams">
      <div className="teams-view-head">
        <div>
          <h1 className="headline" style={{ fontSize: 34 }}>Teams</h1>
          <div className="teams-view-sub">팀에 페르소나를 할당하고 실행을 시작할 로스터를 구성합니다.</div>
        </div>
        <Button variant="primary" onClick={startCreate}>New team</Button>
      </div>

      <div className="teams-grid">
        <div className="teams-list">
          {teams.map((team) => (
            <div key={team.id} className={`teams-list-row${editingId === team.id ? " active" : ""}`}>
              <button type="button" className="teams-list-open" onClick={() => startEdit(team)}>
                <span className="mono teams-list-name">{team.name}</span>
                <span className="teams-list-lead">{team.leader?.name || "—"} · {(team.members || []).length} members</span>
              </button>
              <Button variant="destructive" size="btn-sm" aria-label={`Delete team ${team.name}`}
                onClick={() => onDelete(team.id)}>Delete</Button>
            </div>
          ))}
          {teams.length === 0 ? <div className="teams-empty mono">아직 팀이 없습니다.</div> : null}
        </div>

        {editingId ? (
          <div className="teams-edit">
            <div className="teams-edit-head mono">{editingId === "new" ? "NEW TEAM" : "EDIT TEAM"}</div>
            <div className="teams-edit-body">
              <label className="teams-edit-field">
                <span className="mono teams-edit-k">TEAM NAME</span>
                <input aria-label="Team name" value={draft.name}
                  onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
              </label>
              <label className="teams-edit-field">
                <span className="mono teams-edit-k">DESCRIPTION</span>
                <input aria-label="Team description" value={draft.description}
                  onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} />
              </label>
              <div className="teams-edit-field">
                <div className="teams-edit-section-head">
                  <span className="mono teams-edit-k">LEADER</span>
                  <span className="mono teams-persona-count">1 REQUIRED</span>
                </div>
                <div className="teams-persona-choices" role="group" aria-label="Leader persona">
                  {personas.map((persona) => (
                    <PersonaChoice key={persona.id} persona={persona}
                      active={draft.leader_persona_id === persona.id} selectedLabel="LEADER"
                      onClick={() => setDraft((d) => ({ ...d, leader_persona_id: persona.id }))} />
                  ))}
                </div>
              </div>
              <div className="teams-edit-field">
                <div className="teams-edit-section-head">
                  <span className="mono teams-edit-k">MEMBERS</span>
                  <span className="mono teams-persona-count">{selectedMemberCount} SELECTED</span>
                </div>
                <div className="teams-persona-choices" role="group" aria-label="Member personas">
                  {personas.filter((p) => p.id !== draft.leader_persona_id).map((persona) => (
                    <PersonaChoice key={persona.id} persona={persona}
                      active={draft.member_persona_ids.includes(persona.id)} selectedLabel="MEMBER"
                      onClick={() => toggleMember(persona.id)} />
                  ))}
                </div>
              </div>
              <div className="teams-edit-actions">
                <Button size="btn-sm" onClick={() => setEditingId(null)}>Cancel</Button>
                <Button size="btn-sm" variant="primary" disabled={!draft.name.trim() || !draft.leader_persona_id}
                  onClick={save}>Save team</Button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
