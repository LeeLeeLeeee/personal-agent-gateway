import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { AvatarPicker } from "../AvatarPicker/index.jsx";
import { AgentPicker } from "../AgentPicker/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";

const EMPTY_FORM = {
  name: "",
  role: "",
  description: "",
  responsibilities: "",
  constraints: "",
  default_backend: "codex",
  default_model: "default",
  default_options: {},
  avatar: ""
};

function agentModels(agent, currentModel = "") {
  const models = agent?.model_options?.length
    ? agent.model_options
    : (agent?.models || []).map((id) => ({ id, label: id, efforts: [] }));
  if (!currentModel || models.some((model) => model.id === currentModel)) return models;
  return [{ id: currentModel, label: currentModel, efforts: [] }, ...models];
}

function modelMetadata(agent, modelId) {
  return agentModels(agent, modelId).find((model) => model.id === modelId) || null;
}

function optionChoices(agent, modelId, option) {
  const model = modelMetadata(agent, modelId);
  if (option.name === "effort" && model?.efforts?.length) return model.efforts;
  return option.choices || [];
}

function normalizedOptions(agent, modelId, current = {}) {
  const options = {};
  for (const option of agent?.options_schema || []) {
    if (option.kind !== "select") continue;
    const choices = optionChoices(agent, modelId, option);
    if (!choices.length) continue;
    const model = modelMetadata(agent, modelId);
    const preferred = option.name === "effort" ? model?.default_effort : "";
    const value = current[option.name];
    options[option.name] = choices.includes(value)
      ? value
      : choices.includes(preferred)
        ? preferred
        : choices.includes(agent.defaults?.[option.name])
          ? agent.defaults[option.name]
          : choices[0];
  }
  return options;
}

function initialForm(agents) {
  const agent = agents.find((candidate) => candidate.id === "codex" && candidate.available)
    || agents.find((candidate) => candidate.available);
  if (!agent) return { ...EMPTY_FORM, default_options: {} };
  return {
    ...EMPTY_FORM,
    default_backend: agent.id,
    default_model: agent.default_model,
    default_options: normalizedOptions(agent, agent.default_model, agent.defaults)
  };
}

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
    default_options: { ...(persona.default_options || {}) },
    avatar: persona.avatar || ""
  };
}

export function PersonaLibrary({ personas = [], avatars = [], agents = [], onCreate, onSave, onDelete }) {
  const confirm = useConfirm();
  // editingId: undefined = not yet decided, null = new persona, string = existing persona id
  const [editingId, setEditingId] = useState(undefined);
  const [form, setForm] = useState(() => personas.length ? EMPTY_FORM : initialForm(agents));
  const [avatarModalOpen, setAvatarModalOpen] = useState(false);
  const [avatarDraft, setAvatarDraft] = useState("");

  // "new" whenever no existing persona is selected: null (explicit new) or
  // undefined (empty library / nothing loaded) — so the first save creates
  // instead of PATCHing /api/personas/undefined.
  const isNew = editingId === null || editingId === undefined;
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
    setForm(initialForm(agents));
  }

  // The persona picker reuses the chat AgentPicker; map its config shape back onto the form.
  function handlePickerChange(next) {
    setForm((previous) => ({
      ...previous,
      default_backend: next.agent_id ?? previous.default_backend,
      default_model: next.model ?? previous.default_model,
      default_options: next.options || {}
    }));
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
      default_options: { ...form.default_options },
      avatar: form.avatar.trim()
    };
    if (isNew) {
      onCreate(payload);
      setEditingId(undefined);
    } else {
      onSave?.(editingId, payload);
    }
  }

  async function handleDelete() {
    if (isNew || !editingId) return;
    const ok = await confirm({
      title: "DELETE PERSONA",
      message: `Delete persona "${selected?.name || ""}"? This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true
    });
    if (!ok) return;
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
  const pickerConfig = {
    agent_id: form.default_backend,
    model: form.default_model,
    options: form.default_options || {},
    editable: true
  };

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
            {agents.length ? (
              <div className="persona-field persona-field-wide">
                <span className="persona-field-label">MODEL &amp; RUNTIME</span>
                <AgentPicker agents={agents} config={pickerConfig} onChange={handlePickerChange} />
              </div>
            ) : null}
            <div className="persona-edit-actions">
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
