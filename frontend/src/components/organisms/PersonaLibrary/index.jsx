import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";
import { PersonaCard } from "../../molecules/PersonaCard/index.jsx";
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

function splitLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function PersonaLibrary({ personas = [], avatars = [], onCreate, onSeedDefaults }) {
  const [form, setForm] = useState(EMPTY_FORM);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) return;

    onCreate({
      name,
      role: form.role.trim(),
      description: form.description.trim(),
      responsibilities: splitLines(form.responsibilities),
      constraints: splitLines(form.constraints),
      default_backend: form.default_backend.trim() || "codex",
      default_model: form.default_model.trim() || "default",
      avatar: form.avatar.trim()
    });
    setForm(EMPTY_FORM);
  }

  return (
    <section className="persona-library" aria-label="Persona library">
      <div className="persona-library-head">
        <div>
          <h1 className="headline" style={{ fontSize: 28 }}>Personas</h1>
          <div className="mono" style={{ fontSize: 12, color: "var(--c-grey)" }}>
            Reusable presets - the judgement criteria an agent runs on, not just its tone
          </div>
        </div>
        {onSeedDefaults ? (
          <Button variant="secondary" size="btn-sm" onClick={onSeedDefaults}>Seed defaults</Button>
        ) : null}
      </div>

      <div className="persona-grid">
        {personas.map((persona) => (
          <PersonaCard key={persona.id} persona={persona} />
        ))}
      </div>

      <form className="persona-edit" onSubmit={submit} aria-label="New persona">
        <div className="persona-edit-head mono">EDIT PERSONA</div>
        <div className="persona-edit-body">
          <label className="persona-field">
            <span className="mono persona-field-label">Name</span>
            <InputField aria-label="Name" value={form.name} onChange={(event) => update("name", event.target.value)} />
          </label>
          <label className="persona-field">
            <span className="mono persona-field-label">Role</span>
            <InputField aria-label="Role" value={form.role} onChange={(event) => update("role", event.target.value)} />
          </label>
          <label className="persona-field persona-field-wide">
            <span className="mono persona-field-label">Description</span>
            <InputField
              as="textarea"
              aria-label="Description"
              value={form.description}
              onChange={(event) => update("description", event.target.value)}
            />
          </label>
          <label className="persona-field">
            <span className="mono persona-field-label">Responsibilities (one per line)</span>
            <InputField
              as="textarea"
              aria-label="Responsibilities"
              value={form.responsibilities}
              onChange={(event) => update("responsibilities", event.target.value)}
            />
          </label>
          <label className="persona-field persona-field-danger">
            <span className="mono persona-field-label">Constraints (one per line)</span>
            <InputField
              as="textarea"
              aria-label="Constraints"
              value={form.constraints}
              onChange={(event) => update("constraints", event.target.value)}
            />
          </label>
          <label className="persona-field">
            <span className="mono persona-field-label">Backend</span>
            <InputField
              aria-label="Backend"
              value={form.default_backend}
              onChange={(event) => update("default_backend", event.target.value)}
            />
          </label>
          <label className="persona-field">
            <span className="mono persona-field-label">Model</span>
            <InputField
              aria-label="Model"
              value={form.default_model}
              onChange={(event) => update("default_model", event.target.value)}
            />
          </label>
          <label className="persona-field persona-field-wide">
            <span className="mono persona-field-label">Avatar (optional)</span>
            <div className="persona-avatar-picker-row">
              {form.avatar ? (
                <img
                  className="persona-avatar-preview"
                  src={`/static/avatars/${form.avatar}.png`}
                  alt="Selected avatar"
                />
              ) : null}
              <AvatarPicker avatars={avatars} value={form.avatar} onSelect={(slug) => update("avatar", slug)} />
            </div>
          </label>
        </div>
        <div className="persona-edit-foot">
          <Button type="submit" variant="primary">Save persona</Button>
        </div>
      </form>
    </section>
  );
}
