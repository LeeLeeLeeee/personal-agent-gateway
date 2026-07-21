import { useEffect, useMemo, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const EMPTY_POLICY = {
  read_mode: "home",
  read_path: "",
  write_mode: "isolated",
  workspace_path: ""
};

function editablePolicy(policy) {
  return {
    read_mode: policy?.read_mode || "home",
    read_path: policy?.read_path || "",
    write_mode: policy?.write_mode || "isolated",
    workspace_path: policy?.workspace_path || ""
  };
}

function savePayload(policy) {
  return {
    read_mode: policy.read_mode,
    read_path: policy.read_mode === "all" ? null : policy.read_path || null,
    write_mode: policy.write_mode,
    workspace_path: policy.write_mode === "isolated" ? null : policy.workspace_path || null
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

  return (
    <section className="spaces-view" aria-label="Spaces">
      <div className="spaces-head">
        <div>
          <h1 className="headline" style={{ fontSize: 32 }}>Spaces</h1>
          <p>실행이 읽고 쓸 수 있는 범위를 관리합니다. 우선순위는 TEAM → PERSONA → GLOBAL입니다.</p>
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
            <strong>{selectedName}</strong>에는 전용 SPACE가 없습니다. 현재 GLOBAL 설정이 적용됩니다.
          </div>
          <Button variant="primary" onClick={save}>Create persona space</Button>
        </div>
      ) : null}

      {canEdit && !personaInherited ? (
        <div className="spaces-policy-grid">
          <div className="spaces-policy-card">
            <div className="spaces-card-head">
              <span className="mono">READ SCOPE</span>
              <span className="mono spaces-required">{scope === "persona" ? "OPTIONAL" : "REQUIRED"}</span>
            </div>
            <label>
              <span>Readable area</span>
              <select value={draft.read_mode}
                onChange={(event) => setDraft((value) => ({ ...value, read_mode: event.target.value }))}>
                <option value="home">Home directory</option>
                <option value="selected">Selected directory</option>
                <option value="all">All filesystem</option>
              </select>
            </label>
            {draft.read_mode !== "all" ? (
              <label>
                <span>{draft.read_mode === "home" ? "Resolved home" : "Directory path"}</span>
                <input value={draft.read_path}
                  disabled={draft.read_mode === "home"}
                  placeholder="C:\\Users\\you"
                  onChange={(event) => setDraft((value) => ({ ...value, read_path: event.target.value }))} />
              </label>
            ) : null}
            <p>읽기 범위는 참고 자료와 저장소 탐색에 사용됩니다.</p>
          </div>

          <div className="spaces-policy-card">
            <div className="spaces-card-head">
              <span className="mono">WRITE SCOPE</span>
              <span className="mono spaces-required">{scope === "persona" ? "OPTIONAL" : "REQUIRED"}</span>
            </div>
            <label>
              <span>Writable area</span>
              <select value={draft.write_mode}
                onChange={(event) => setDraft((value) => ({ ...value, write_mode: event.target.value }))}>
                <option value="isolated">Isolated run workspace</option>
                {scope === "team" ? <option value="worktree">Git worktree</option> : null}
                <option value="full_access">Selected workspace (full access)</option>
              </select>
            </label>
            {draft.write_mode !== "isolated" ? (
              <label>
                <span>{draft.write_mode === "worktree" ? "Git repository path" : "Workspace path"}</span>
                <input value={draft.workspace_path}
                  placeholder="C:\\path\\to\\repository"
                  onChange={(event) => setDraft((value) => ({ ...value, workspace_path: event.target.value }))} />
              </label>
            ) : null}
            <p>{draft.write_mode === "worktree"
              ? "각 Team Run마다 별도 branch와 worktree를 생성합니다."
              : "Isolated 모드는 실행별 workspace와 artifacts에만 쓰도록 제한합니다."}</p>
          </div>
        </div>
      ) : null}

      {canEdit && !personaInherited ? (
        <div className="spaces-actions">
          {scope === "persona" ? (
            <Button disabled={saving} onClick={inheritGlobal}>Use global space</Button>
          ) : null}
          <Button variant="primary" disabled={saving} onClick={save}>
            {saving ? "Saving..." : "Save space"}
          </Button>
        </div>
      ) : null}

      <div className="spaces-note mono">
        TEAM RUN은 시작 시 SPACE 설정을 고정합니다. 변경 사항은 새 Run과 새 Chat/Hook 실행부터 적용됩니다.
      </div>
    </section>
  );
}
