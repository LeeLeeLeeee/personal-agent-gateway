import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { AvatarPicker } from "../AvatarPicker/index.jsx";

const EMPTY_FORM = {
  name: "",
  role: "",
  description: "",
  responsibilities: "",
  constraints: "",
  default_backend: "codex",
  default_model: "default",
  avatar: ""
};

function initials(name) {
  return (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";
}

function splitLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function formFromPersona(persona) {
  if (!persona) return EMPTY_FORM;
  return {
    name: persona.name || "",
    role: persona.role || "",
    description: persona.description || "",
    responsibilities: (persona.responsibilities || []).join("\n"),
    constraints: (persona.constraints || []).join("\n"),
    default_backend: persona.default_backend || "codex",
    default_model: persona.default_model || "default",
    avatar: persona.avatar || ""
  };
}

export function PersonaLibrary({ personas = [], avatars = [], onCreate, onSave, onDelete }) {
  // editingId: undefined = not yet decided, null = new persona, string = existing persona id
  const [editingId, setEditingId] = useState(undefined);
  const [form, setForm] = useState(EMPTY_FORM);
  const [avatarModalOpen, setAvatarModalOpen] = useState(false);
  const [avatarDraft, setAvatarDraft] = useState("");

  const isNew = editingId === null;
  const selected = isNew ? null : personas.find((persona) => persona.id === editingId) || null;

  // Default to the first persona once the library loads.
  useEffect(() => {
    if (editingId === undefined && personas.length) setEditingId(personas[0].id);
  }, [personas, editingId]);

  // Sync the editable form whenever the selected persona changes.
  useEffect(() => {
    if (editingId === undefined) return;
    setForm(formFromPersona(selected));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingId]);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function startNew() {
    setEditingId(null);
    setForm(EMPTY_FORM);
  }

  function submit(event) {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) return;
    const payload = {
      name,
      role: form.role.trim(),
      description: form.description.trim(),
      responsibilities: splitLines(form.responsibilities),
      constraints: splitLines(form.constraints),
      default_backend: form.default_backend.trim() || "codex",
      default_model: form.default_model.trim() || "default",
      avatar: form.avatar.trim()
    };
    if (isNew) {
      onCreate(payload);
      setEditingId(undefined);
    } else {
      onSave?.(editingId, payload);
    }
  }

  function handleDelete() {
    if (isNew || !editingId) return;
    if (!window.confirm(`Delete persona "${selected?.name || ""}"? This cannot be undone.`)) return;
    const remaining = personas.filter((persona) => persona.id !== editingId);
    onDelete?.(editingId);
    setEditingId(remaining.length ? remaining[0].id : null);
  }

  function openAvatarModal() {
    setAvatarDraft(form.avatar);
    setAvatarModalOpen(true);
  }

  function confirmAvatar() {
    update("avatar", avatarDraft);
    setAvatarModalOpen(false);
  }

  const headMeta = isNew ? "NEW" : `PRESET · ${(editingId || "").slice(0, 8).toUpperCase()}`;

  return (
    <section className="persona-library" aria-label="Persona library">
      <div className="persona-library-head">
        <div>
          <h1 className="headline" style={{ fontSize: 34 }}>Personas</h1>
          <div className="persona-library-sub">
            Reusable presets · the judgement criteria an agent runs on, not just its tone
          </div>
        </div>
        <Button variant="secondary" size="btn-sm" onClick={startNew}>New persona</Button>
      </div>

      <div className="persona-master">
        <div className="persona-list" aria-label="Persona list">
          {personas.length ? (
            personas.map((persona) => {
              const active = !isNew && editingId === persona.id;
              return (
                <button
                  key={persona.id}
                  type="button"
                  aria-pressed={active}
                  aria-label={`Select ${persona.name}`}
                  className={`persona-row${active ? " persona-row-active" : ""}`}
                  onClick={() => setEditingId(persona.id)}
                >
                  <span className="persona-row-avatar">
                    {persona.avatar
                      ? <img src={`/static/avatars/${persona.avatar}.png`} alt="" />
                      : <span className="mono">{initials(persona.name)}</span>}
                  </span>
                  <span className="persona-row-title">
                    <span className="persona-row-name">{persona.name}</span>
                    <span className="persona-row-role">{persona.role || "—"}</span>
                  </span>
                </button>
              );
            })
          ) : (
            <div className="persona-list-empty mono">No personas yet. Create one →</div>
          )}
        </div>

        <form className="persona-edit" onSubmit={submit} aria-label={isNew ? "New persona" : "Edit persona"}>
          <div className="persona-edit-head">
            <span className="persona-edit-head-title">{isNew ? "NEW PERSONA" : "EDIT PERSONA"}</span>
            <span className="persona-edit-head-meta">{headMeta}</span>
          </div>
          <div className="persona-edit-body">
            <div className="persona-avatar-block">
              <div>
                <div className="persona-field-label">AVATAR</div>
                <button type="button" className="persona-avatar-btn" onClick={openAvatarModal} aria-label="Open avatar picker">
                  {form.avatar
                    ? <img src={`/static/avatars/${form.avatar}.png`} alt="" />
                    : <span>{initials(form.name)}</span>}
                  {form.avatar ? <span className="persona-avatar-code">{form.avatar.toUpperCase()}</span> : null}
                </button>
              </div>
              <div className="persona-avatar-info">
                <div className="persona-field-label">SESSION AVATAR</div>
                <div className="persona-avatar-info-desc">
                  Pick from the shared avatar library on the server — <span className="mono">{avatars.length}</span> images available. No upload.
                </div>
                <div style={{ marginTop: 12 }}>
                  <Button variant="secondary" size="btn-sm" onClick={openAvatarModal}>Change avatar</Button>
                </div>
                <div className="persona-avatar-note">SELECTION SNAPSHOTTED WITH THE PERSONA</div>
              </div>
            </div>

            <label className="persona-field">
              <span className="persona-field-label">NAME</span>
              <input className="persona-input" aria-label="Name" value={form.name} onChange={(event) => update("name", event.target.value)} />
            </label>
            <label className="persona-field">
              <span className="persona-field-label">ROLE</span>
              <input className="persona-input" aria-label="Role" value={form.role} onChange={(event) => update("role", event.target.value)} />
            </label>
            <label className="persona-field persona-field-wide">
              <span className="persona-field-label">DESCRIPTION</span>
              <textarea className="persona-textarea" aria-label="Description" value={form.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <label className="persona-field">
              <span className="persona-field-label">RESPONSIBILITIES · ONE PER LINE</span>
              <textarea className="persona-textarea" aria-label="Responsibilities" value={form.responsibilities} onChange={(event) => update("responsibilities", event.target.value)} />
            </label>
            <label className="persona-field">
              <span className="persona-field-label">CONSTRAINTS · ONE PER LINE</span>
              <textarea className="persona-textarea persona-textarea-constraints" aria-label="Constraints" value={form.constraints} onChange={(event) => update("constraints", event.target.value)} />
            </label>
            <div className="persona-edit-actions">
              <label className="persona-field">
                <span className="persona-field-label">BACKEND</span>
                <input className="persona-input" aria-label="Backend" value={form.default_backend} onChange={(event) => update("default_backend", event.target.value)} />
              </label>
              <label className="persona-field">
                <span className="persona-field-label">MODEL · OPTIONAL</span>
                <input className="persona-input" aria-label="Model" value={form.default_model} onChange={(event) => update("default_model", event.target.value)} />
              </label>
              {!isNew ? (
                <Button type="button" variant="destructive" onClick={handleDelete}>Delete</Button>
              ) : null}
              <Button type="submit" variant="primary">{isNew ? "Create persona" : "Save persona"}</Button>
            </div>
          </div>
        </form>
      </div>

      {avatarModalOpen ? (
        <div className="avatar-modal-backdrop" onClick={() => setAvatarModalOpen(false)}>
          <div className="avatar-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-label="Choose avatar">
            <div className="avatar-modal-head">
              <span className="avatar-modal-title">Choose avatar</span>
              <span className="avatar-modal-sub">SERVED FROM /avatars · {avatars.length} AVAILABLE · SELECT ONLY</span>
              <button type="button" className="avatar-modal-close" aria-label="Close" onClick={() => setAvatarModalOpen(false)}>✕</button>
            </div>
            <div className="avatar-modal-body">
              <AvatarPicker avatars={avatars} value={avatarDraft} onSelect={setAvatarDraft} />
            </div>
            <div className="avatar-modal-foot">
              <span className="avatar-modal-foot-preview">
                {avatarDraft ? <img src={`/static/avatars/${avatarDraft}.png`} alt="" /> : null}
              </span>
              <span className="avatar-modal-foot-label">SELECTED · {avatarDraft ? avatarDraft.toUpperCase() : "NONE"}</span>
              <span className="avatar-modal-foot-actions">
                <Button variant="secondary" size="btn-sm" onClick={() => setAvatarModalOpen(false)}>Cancel</Button>
                <Button variant="primary" size="btn-sm" onClick={confirmAvatar}>Use avatar</Button>
              </span>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
