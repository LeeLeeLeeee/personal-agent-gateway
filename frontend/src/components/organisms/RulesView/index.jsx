import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

function nextRules(rules, index, patch) {
  return rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule));
}

export function RulesView({ rules, teams = [], onSaveGlobal, onSavePersonaBaseline, onSaveTeam }) {
  const [scope, setScope] = useState("team"); // "team" | "persona"
  const [selTeam, setSelTeam] = useState("global"); // "global" | teamId
  const [personality, setPersonality] = useState("");
  const [ruleList, setRuleList] = useState([]);
  const [saving, setSaving] = useState(false);

  function currentSet() {
    if (scope === "persona") return rules?.persona_baseline || { personality: "", rules: [] };
    if (selTeam === "global") return rules?.global || { personality: "", rules: [] };
    return (rules?.teams || []).find((t) => t.team_id === selTeam) || { personality: "", rules: [] };
  }

  useEffect(() => {
    const set = currentSet();
    setPersonality(set.personality || "");
    setRuleList((set.rules || []).map((r) => ({ ...r })));
  }, [scope, selTeam, rules]); // eslint-disable-line react-hooks/exhaustive-deps

  const isIndividualTeam = scope === "team" && selTeam !== "global";
  const reqCount = ruleList.filter((r) => r.level === "REQUIRED").length;

  async function save() {
    setSaving(true);
    try {
      const payload = { personality, rules: ruleList };
      if (scope === "persona") await onSavePersonaBaseline(payload);
      else if (selTeam === "global") await onSaveGlobal(payload);
      else await onSaveTeam(selTeam, payload);
    } finally { setSaving(false); }
  }

  return (
    <section className="rules-view" aria-label="Rules">
      <div className="rules-view-head">
        <h1 className="headline" style={{ fontSize: 32 }}>Rules</h1>
        <div className="rules-view-sub">모든 실행과 페르소나가 상속하는 규칙 — 팀 전체에 걸쳐 유지되는 성격과 규칙.</div>
      </div>

      <div className="rules-tabs">
        <button type="button" aria-pressed={scope === "team"}
          className={`rules-tab${scope === "team" ? " active" : ""}`} onClick={() => setScope("team")}>TEAM RULES</button>
        <button type="button" aria-pressed={scope === "persona"}
          className={`rules-tab${scope === "persona" ? " active" : ""}`} onClick={() => setScope("persona")}>PERSONA BASELINE</button>
      </div>

      {scope === "team" ? (
        <div className="rules-team-selector">
          <span className="mono rules-team-k">TEAM</span>
          <button type="button" aria-pressed={selTeam === "global"}
            className={`rules-team-btn${selTeam === "global" ? " active" : ""}`} onClick={() => setSelTeam("global")}>GLOBAL</button>
          {teams.map((team) => (
            <button key={team.id} type="button" aria-pressed={selTeam === team.id}
              className={`rules-team-btn${selTeam === team.id ? " active" : ""}`} onClick={() => setSelTeam(team.id)}>
              {team.name}
            </button>
          ))}
        </div>
      ) : null}

      {isIndividualTeam ? (
        <div className="rules-inherit mono">
          <span className="rules-inherit-tag">INHERITS GLOBAL</span>
          Global 규칙이 그대로 적용됩니다. 아래는 이 팀에만 추가되는 규칙입니다.
        </div>
      ) : null}

      <div className="rules-grid">
        <div>
          <div className="rules-personality">
            <div className="rules-personality-head mono">
              <span>PERSONALITY &amp; VOICE</span>
              <span className="rules-personality-note">FROZEN AT RUN START</span>
            </div>
            <textarea className="rules-personality-input" aria-label="Personality and voice"
              value={personality} onChange={(e) => setPersonality(e.target.value)} />
          </div>

          <div className="rules-list-head">
            <span className="mono">{isIndividualTeam ? "ADDED RULES" : "RULES"}</span>
            <span className="mono rules-counts">{reqCount} required · {ruleList.length - reqCount} guideline</span>
          </div>
          <div className="rules-list">
            {ruleList.map((rule, index) => (
              <div className="rules-row" key={index}>
                <span className="mono rules-n">{String(index + 1).padStart(2, "0")}</span>
                <button type="button"
                  className={`rules-level${rule.level === "REQUIRED" ? " req" : ""}`}
                  aria-label={`Toggle level for rule ${index + 1}`}
                  onClick={() => setRuleList((list) => nextRules(list, index, {
                    level: rule.level === "REQUIRED" ? "GUIDELINE" : "REQUIRED"
                  }))}>
                  {rule.level}
                </button>
                <input className="rules-text" placeholder="rule text"
                  value={rule.text}
                  onChange={(e) => setRuleList((list) => nextRules(list, index, { text: e.target.value }))} />
                <button type="button" className="rules-del" aria-label={`Delete rule ${index + 1}`}
                  onClick={() => setRuleList((list) => list.filter((_, i) => i !== index))}>×</button>
              </div>
            ))}
            <button type="button" className="rules-add mono"
              onClick={() => setRuleList((list) => [...list, { level: "GUIDELINE", text: "" }])}>+ ADD RULE</button>
          </div>

          <div className="rules-save">
            <Button variant="primary" disabled={saving} onClick={save}>{saving ? "Saving..." : "Save"}</Button>
          </div>
        </div>

        <aside className="rules-meta">
          <div className="rules-meta-head mono">{scope === "persona" ? "PERSONA BASELINE" : (selTeam === "global" ? "GLOBAL" : (teams.find((t) => t.id === selTeam)?.name || "TEAM"))}</div>
          <div className="rules-meta-body">
            <div className="rules-enforce">
              <div className="mono rules-enforce-k">ENFORCEMENT</div>
              <div className="rules-enforce-row"><span className="rules-enforce-req" /> <span className="mono">REQUIRED · 강한 지시(가이드)</span></div>
              <div className="rules-enforce-row"><span className="rules-enforce-guide" /> <span className="mono">GUIDELINE · 권고</span></div>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
