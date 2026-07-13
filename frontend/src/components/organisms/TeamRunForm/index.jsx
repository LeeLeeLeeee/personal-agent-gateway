import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const RUN_MODES = [
  { value: "planning_only", label: "PLANNING ONLY", desc: "Leader decomposes the goal and drafts tasks. Nothing executes." },
  { value: "plan_and_execute", label: "PLAN + EXECUTE", desc: "Leader plans, then members execute their tasks and report back." },
  { value: "review_only", label: "REVIEW ONLY", desc: "Members review existing work against their persona and report findings." }
];

function initials(name) {
  return (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";
}

function PersonaMark({ persona }) {
  if (persona.avatar) {
    return <img className="team-run-member-initials" src={`/static/avatars/${persona.avatar}.png`} alt="" />;
  }
  return <span className="team-run-member-initials mono">{initials(persona.name)}</span>;
}

export function TeamRunForm({ personas = [], onSubmit }) {
  const [goal, setGoal] = useState("");
  const [leaderPersonaId, setLeaderPersonaId] = useState("");
  const [memberPersonaIds, setMemberPersonaIds] = useState([]);
  const [runMode, setRunMode] = useState("planning_only");
  const [maxWorkers, setMaxWorkers] = useState(3);

  useEffect(() => {
    if (!leaderPersonaId && personas.length) setLeaderPersonaId(personas[0].id);
  }, [personas, leaderPersonaId]);

  useEffect(() => {
    setMemberPersonaIds((prev) => prev.filter((id) => id !== leaderPersonaId));
  }, [leaderPersonaId]);

  function toggleMember(personaId) {
    setMemberPersonaIds((prev) =>
      prev.includes(personaId) ? prev.filter((id) => id !== personaId) : [...prev, personaId]
    );
  }

  function submit(event) {
    event.preventDefault();
    onSubmit({
      goal: goal.trim(),
      leader_persona_id: leaderPersonaId,
      member_persona_ids: memberPersonaIds,
      run_mode: runMode,
      max_workers: Number(maxWorkers) || 1
    });
  }

  const leaderPersona = personas.find((persona) => persona.id === leaderPersonaId) || null;
  const activeMode = RUN_MODES.find((mode) => mode.value === runMode) || RUN_MODES[0];

  return (
    <form className="team-run-new" onSubmit={submit} aria-label="New team run">
      <div className="team-run-form">
        <div className="team-run-field">
          <span className="team-run-field-label">Goal</span>
          <textarea
            className="team-run-goal"
            aria-label="Goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="What should the team accomplish, end to end?"
          />
        </div>

        <div className="team-run-field">
          <span className="team-run-field-label">Leader persona</span>
          <div className="team-run-leaders">
            {personas.map((persona) => {
              const active = leaderPersonaId === persona.id;
              return (
                <button
                  key={persona.id}
                  type="button"
                  aria-pressed={active}
                  aria-label={`Select ${persona.name} as leader`}
                  className={`team-run-leader${active ? " active" : ""}`}
                  onClick={() => setLeaderPersonaId(persona.id)}
                >
                  <span className="team-run-leader-top">
                    <span className="team-run-radio" />
                    <span className="team-run-leader-name">{persona.name}</span>
                  </span>
                  <span className="team-run-leader-role">{persona.role || "—"}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="team-run-field">
          <div className="team-run-field-head">
            <span className="team-run-field-label">Member personas</span>
            <span className="team-run-field-count">{memberPersonaIds.length} SELECTED</span>
          </div>
          <div className="team-run-members">
            {personas.map((persona) => {
              const isLeader = persona.id === leaderPersonaId;
              const active = memberPersonaIds.includes(persona.id);
              return (
                <button
                  key={persona.id}
                  type="button"
                  disabled={isLeader}
                  aria-pressed={active}
                  aria-label={isLeader ? `${persona.name} is the leader` : `Toggle ${persona.name} as member`}
                  className={`team-run-member${active ? " active" : ""}${isLeader ? " is-leader" : ""}`}
                  onClick={() => { if (!isLeader) toggleMember(persona.id); }}
                >
                  <span className="team-run-member-top">
                    <span className="team-run-check">{isLeader ? "" : active ? "✓" : ""}</span>
                    <PersonaMark persona={persona} />
                    <span className="team-run-member-title">
                      <span className="team-run-member-name">{persona.name}</span>
                      <span className="team-run-member-role">{isLeader ? "LEADER" : persona.role || "—"}</span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="team-run-settings">
          <div className="team-run-field">
            <span className="team-run-field-label">Run mode</span>
            <div className="team-run-mode" role="group" aria-label="Run mode">
              {RUN_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  aria-pressed={runMode === mode.value}
                  className={`team-run-mode-btn${runMode === mode.value ? " active" : ""}`}
                  onClick={() => setRunMode(mode.value)}
                >
                  {mode.label}
                </button>
              ))}
            </div>
            <div className="team-run-mode-desc">{activeMode.desc}</div>
          </div>
          <div className="team-run-field">
            <span className="team-run-field-label">Max workers</span>
            <div className="team-run-workers">
              <button
                type="button"
                className="team-run-workers-btn"
                aria-label="Decrease workers"
                onClick={() => setMaxWorkers((value) => Math.max(1, value - 1))}
              >
                −
              </button>
              <div className="team-run-workers-val" aria-label="Max workers">{maxWorkers}</div>
              <button
                type="button"
                className="team-run-workers-btn"
                aria-label="Increase workers"
                onClick={() => setMaxWorkers((value) => Math.min(8, value + 1))}
              >
                +
              </button>
            </div>
            <div className="team-run-workers-hint">concurrent agent sessions</div>
          </div>
        </div>
      </div>

      <aside className="team-run-preview">
        <div className="team-run-preview-head">RUN PREVIEW</div>
        <div className="team-run-preview-body">
          <div className="team-run-preview-kv">
            <div className="k">LEADER</div>
            <div>{leaderPersona ? leaderPersona.name : "—"}</div>
            <div className="k">MEMBERS</div>
            <div>{memberPersonaIds.length} agents</div>
            <div className="k">MODE</div>
            <div>{activeMode.label}</div>
            <div className="k">WORKERS</div>
            <div>max {maxWorkers} concurrent</div>
            <div className="k">WORKSPACE</div>
            <div>~/agent-workspace</div>
          </div>
          <div className="team-run-preview-snap">
            <div className="team-run-preview-snap-k">PERSONA SNAPSHOT</div>
            <div className="team-run-preview-snap-d">
              On start, each agent session is frozen with its persona. Later edits in the library do not affect a running team.
            </div>
          </div>
          <div className="team-run-preview-action">
            <Button type="submit" variant="primary" size="btn-lg">Start team run</Button>
          </div>
        </div>
      </aside>
    </form>
  );
}
