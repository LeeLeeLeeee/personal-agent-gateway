import { useEffect, useMemo, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const EMPTY_POLICY = {
  read_mode: "none",
  read_path: "",
  write_mode: "isolated",
  workspace_path: ""
};

const PRESETS = [
  {
    value: "empty",
    label: "New empty workspace",
    description: "Start without existing files. All changes stay inside the run."
  },
  {
    value: "reference",
    label: "Reference folder copy",
    description: "Copy one folder into an isolated run workspace. Originals stay unchanged."
  },
  {
    value: "broad-read",
    label: "Broad read, isolated writes",
    description: "Reference the filesystem while keeping writes inside the run workspace."
  },
  {
    value: "worktree",
    label: "Git branch workspace",
    teamOnly: true,
    description: "Create a dedicated branch and worktree for every Team Run."
  },
  {
    value: "direct",
    label: "Direct workspace",
    description: "Read and write the selected folder directly. Existing files can change."
  }
];

function editablePolicy(policy) {
  const readMode = policy?.write_mode === "isolated" && policy?.read_mode === "home"
    ? "selected"
    : policy?.read_mode || "none";
  return {
    read_mode: readMode,
    read_path: policy?.read_path || "",
    write_mode: policy?.write_mode || "isolated",
    workspace_path: policy?.workspace_path || ""
  };
}

function presetFor(policy) {
  if (policy.write_mode === "worktree") return "worktree";
  if (policy.write_mode === "full_access") return "direct";
  if (["home", "selected"].includes(policy.read_mode)) return "reference";
  if (policy.read_mode === "all") return "broad-read";
  return "empty";
}

function applyPreset(policy, preset) {
  if (preset === "reference") {
    return { ...policy, read_mode: "selected", write_mode: "isolated", workspace_path: "" };
  }
  if (preset === "broad-read") {
    return { ...policy, read_mode: "all", read_path: "", write_mode: "isolated", workspace_path: "" };
  }
  if (preset === "worktree") {
    return { ...policy, read_mode: "none", read_path: "", write_mode: "worktree" };
  }
  if (preset === "direct") {
    return { ...policy, read_mode: "none", read_path: "", write_mode: "full_access" };
  }
  return { ...policy, read_mode: "none", read_path: "", write_mode: "isolated", workspace_path: "" };
}

function savePayload(policy) {
  const readMode = policy.write_mode === "isolated" && policy.read_mode === "home"
    ? "selected"
    : policy.read_mode;
  return {
    read_mode: readMode,
    read_path: ["none", "all"].includes(readMode) ? null : policy.read_path || null,
    write_mode: policy.write_mode,
    workspace_path: policy.write_mode === "isolated" ? null : policy.workspace_path || null
  };
}

function samePolicy(draft, saved) {
  if (!saved) return false;
  return draft.read_mode === saved.read_mode
    && (draft.read_path || null) === (saved.read_path || null)
    && draft.write_mode === saved.write_mode
    && (draft.workspace_path || null) === (saved.workspace_path || null);
}

function previewCapability(policy) {
  if (policy.write_mode === "worktree") {
    return {
      ready: Boolean(policy.workspace_path),
      read_summary: "Reads the selected Git repository",
      write_summary: "Changes stay on a new Team Run branch",
      changes_originals: false,
      issues: policy.workspace_path ? [] : ["Git repository path is required"]
    };
  }
  if (policy.write_mode === "full_access") {
    return {
      ready: Boolean(policy.workspace_path),
      read_summary: "Reads the selected workspace directly",
      write_summary: "Changes are written to the selected workspace",
      changes_originals: true,
      issues: policy.workspace_path ? [] : ["Workspace path is required"]
    };
  }
  if (["selected", "home"].includes(policy.read_mode)) {
    return {
      ready: Boolean(policy.read_path) && policy.read_mode !== "home",
      read_summary: policy.read_path ? `Copies ${policy.read_path}` : "Select a bounded source directory",
      write_summary: "Original files are not changed",
      changes_originals: false,
      issues: policy.read_mode === "home" || !policy.read_path
        ? ["Select a bounded source directory for isolated execution"]
        : []
    };
  }
  return {
    ready: true,
    read_summary: policy.read_mode === "all" ? "Can reference the full filesystem" : "Starts without existing files",
    write_summary: "Original files are not changed",
    changes_originals: false,
    issues: []
  };
}

export function SpacesView({
  policies,
  teams = [],
  personas = [],
  onSaveGlobal,
  onSavePersona,
  onDeletePersona,
  onSaveTeam
}) {
  const [scope, setScope] = useState("global");
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState(EMPTY_POLICY);
  const [saving, setSaving] = useState(false);

  const records = scope === "team" ? policies?.teams || [] : policies?.personas || [];
  const selectedPolicy = useMemo(() => {
    if (scope === "global") return policies?.global || null;
    return records.find((item) => item.scope_id === selectedId) || null;
  }, [policies, records, scope, selectedId]);
  const personaInherited = scope === "persona" && selectedId && !selectedPolicy;
  const capabilityVerified = samePolicy(draft, selectedPolicy || policies?.global);
  const capability = capabilityVerified
    ? (selectedPolicy || policies?.global)?.capability || previewCapability(draft)
    : previewCapability(draft);

  useEffect(() => {
    if (scope === "team" && !selectedId && teams[0]) setSelectedId(teams[0].id);
    if (scope === "persona" && !selectedId && personas[0]) setSelectedId(personas[0].id);
  }, [scope, selectedId, teams, personas]);

  useEffect(() => {
    setDraft(editablePolicy(selectedPolicy || policies?.global));
  }, [selectedPolicy, policies?.global]);

  function switchScope(nextScope) {
    setScope(nextScope);
    setSelectedId("");
  }

  async function save() {
    setSaving(true);
    try {
      const payload = savePayload(draft);
      if (scope === "global") await onSaveGlobal(payload);
      else if (scope === "persona") await onSavePersona(selectedId, payload);
      else await onSaveTeam(selectedId, payload);
    } finally {
      setSaving(false);
    }
  }

  async function inheritGlobal() {
    setSaving(true);
    try {
      await onDeletePersona(selectedId);
    } finally {
      setSaving(false);
    }
  }

  const selectedName = scope === "team"
    ? teams.find((item) => item.id === selectedId)?.name
    : personas.find((item) => item.id === selectedId)?.name;
  const canEdit = scope === "global" || Boolean(selectedId);
  const selectedPreset = PRESETS.find((item) => item.value === presetFor(draft));

  return (
    <section className="spaces-view" aria-label="Workspace access">
      <div className="spaces-head">
        <div>
          <h1 className="headline" style={{ fontSize: 32 }}>Workspace access</h1>
          <p>Choose what an execution can read, where it writes, and whether original files can change.</p>
        </div>
        <div className="spaces-precedence mono" aria-label="Space precedence">
          <strong>TEAM</strong><span>›</span><strong>PERSONA</strong><span>›</span><strong>GLOBAL</strong>
        </div>
      </div>

      <div className="spaces-tabs" role="tablist" aria-label="Space scope">
        {["global", "persona", "team"].map((item) => (
          <button key={item} type="button" role="tab" aria-selected={scope === item}
            className={`spaces-tab${scope === item ? " active" : ""}`}
            onClick={() => switchScope(item)}>{item.toUpperCase()}</button>
        ))}
      </div>

      {scope !== "global" ? (
        <label className="spaces-target">
          <span className="mono">{scope.toUpperCase()}</span>
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
            <option value="">Select {scope}</option>
            {(scope === "team" ? teams : personas).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
      ) : null}

      {personaInherited ? (
        <div className="spaces-inherit">
          <div>
            <span className="mono spaces-inherit-tag">INHERITS GLOBAL</span>
            <strong>{selectedName}</strong> uses the current Global workspace access.
          </div>
          <Button variant="primary" onClick={save}>Create persona space</Button>
        </div>
      ) : null}

      {canEdit && !personaInherited ? (
        <>
          <div className="spaces-policy-card spaces-preset-card">
            <div className="spaces-card-head">
              <span className="mono">WORKSPACE BEHAVIOR</span>
              <span className="mono spaces-required">{scope === "persona" ? "OPTIONAL" : "REQUIRED"}</span>
            </div>
            <label>
              <span>How should this execution use files?</span>
              <select value={presetFor(draft)} onChange={(event) => setDraft((value) => applyPreset(value, event.target.value))}>
                {PRESETS.filter((item) => !item.teamOnly || scope === "team").map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>
            {presetFor(draft) === "reference" ? (
              <label>
                <span>Source directory</span>
                <input value={draft.read_path} placeholder="/absolute/path/to/source"
                  onChange={(event) => setDraft((value) => ({ ...value, read_path: event.target.value }))} />
              </label>
            ) : null}
            {["worktree", "direct"].includes(presetFor(draft)) ? (
              <label>
                <span>{presetFor(draft) === "worktree" ? "Git repository path" : "Workspace path"}</span>
                <input value={draft.workspace_path} placeholder="/absolute/path/to/workspace"
                  onChange={(event) => setDraft((value) => ({ ...value, workspace_path: event.target.value }))} />
              </label>
            ) : null}
            <p>{selectedPreset?.description}</p>
          </div>

          <div className="spaces-capability-grid" aria-label="Workspace capability summary">
            <div className="spaces-capability"><span>CAN READ</span><strong>{capability.read_summary}</strong></div>
            <div className="spaces-capability">
              <span>CHANGES ORIGINALS</span>
              <strong>{capability.changes_originals ? "Yes" : "No"}</strong>
              <small>{capability.write_summary}</small>
            </div>
            <div className={`spaces-capability${capability.ready ? " ready" : " attention"}`}>
              <span>READY TO RUN</span>
              <strong>{capability.ready ? capabilityVerified ? "Ready" : "Save to verify" : "Needs attention"}</strong>
            </div>
          </div>
          {capability.issues?.length ? (
            <ul className="spaces-issues">
              {capability.issues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          ) : null}
        </>
      ) : null}

      {canEdit && !personaInherited ? (
        <div className="spaces-actions">
          {scope === "persona" ? (
            <Button disabled={saving} onClick={inheritGlobal}>Use global space</Button>
          ) : null}
          <Button variant="primary" disabled={saving} onClick={save}>
            {saving ? "Saving..." : "Save workspace access"}
          </Button>
        </div>
      ) : null}

      <div className="spaces-note mono">
        A Team Run snapshots this access when it starts. Changes apply to new runs and new Chat or Hook executions.
      </div>
    </section>
  );
}
