import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const RUN_MODES = [
  { value: "planning_only", label: "PLANNING ONLY", desc: "Leader decomposes the goal and drafts tasks. Nothing executes." },
  { value: "plan_and_execute", label: "PLAN + EXECUTE", desc: "Leader plans, then members execute their tasks and report back." },
  { value: "review_only", label: "REVIEW ONLY", desc: "Members review existing work against their persona and report findings." }
];

function Avatar({ person }) {
  if (person?.avatar) return <img className="tp-avatar" src={`/static/avatars/${person.avatar}.png`} alt="" />;
  return <span className="tp-avatar tp-avatar-initials mono">{(person?.name || "?").slice(0, 2).toUpperCase()}</span>;
}

export function TeamPicker({ teams = [], onStart }) {
  const [teamId, setTeamId] = useState("");
  const [goal, setGoal] = useState("");
  const [runMode, setRunMode] = useState("planning_only");
  const [maxWorkers, setMaxWorkers] = useState(3);

  useEffect(() => {
    if (!teamId && teams.length) setTeamId(teams[0].id);
  }, [teams, teamId]);

  if (!teams.length) {
    return <div className="tp-empty mono">먼저 팀을 만드세요 — Teams 화면에서 팀과 로스터를 구성할 수 있습니다.</div>;
  }

  const team = teams.find((t) => t.id === teamId) || teams[0];
  const activeMode = RUN_MODES.find((m) => m.value === runMode) || RUN_MODES[0];

  return (
    <form className="tp" aria-label="New team run" onSubmit={(event) => {
      event.preventDefault();
      onStart({ team_id: team.id, goal: goal.trim(), run_mode: runMode, max_workers: Number(maxWorkers) || 1 });
    }}>
      <div className="tp-form">
        <div className="tp-field">
          <span className="tp-label">Team</span>
          <div className="tp-teams">
            {teams.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-pressed={t.id === team.id}
                className={`tp-team${t.id === team.id ? " active" : ""}`}
                onClick={() => setTeamId(t.id)}
              >
                {t.name}
              </button>
            ))}
          </div>
        </div>

        <div className="tp-field">
          <span className="tp-label">Roster (locked)</span>
          <div className="tp-roster">
            <div className="tp-roster-row">
              <Avatar person={team.leader} />
              <span className="mono tp-roster-name">{team.leader?.name || "—"}</span>
              <span className="mono tp-roster-role">LEADER</span>
            </div>
            {(team.members || []).map((member, index) => (
              <div className="tp-roster-row" key={index}>
                <Avatar person={member} />
                <span className="mono tp-roster-name">{member.name}</span>
                <span className="mono tp-roster-role">MEMBER</span>
              </div>
            ))}
          </div>
        </div>

        <div className="tp-field">
          <span className="tp-label" id="tp-goal-label">Goal</span>
          <textarea
            className="tp-goal"
            aria-labelledby="tp-goal-label"
            aria-label="Goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="What should the team accomplish, end to end?"
          />
        </div>

        <div className="tp-settings">
          <div className="tp-field">
            <span className="tp-label">Run mode</span>
            <div className="tp-mode" role="group" aria-label="Run mode">
              {RUN_MODES.map((mode) => (
                <button key={mode.value} type="button" aria-pressed={runMode === mode.value}
                  className={`tp-mode-btn${runMode === mode.value ? " active" : ""}`}
                  onClick={() => setRunMode(mode.value)}>{mode.label}</button>
              ))}
            </div>
            <div className="tp-mode-desc">{activeMode.desc}</div>
          </div>
          <div className="tp-field">
            <span className="tp-label">Max workers</span>
            <div className="tp-workers">
              <button type="button" aria-label="Decrease workers" onClick={() => setMaxWorkers((v) => Math.max(1, v - 1))}>−</button>
              <div className="tp-workers-val" aria-label="Max workers">{maxWorkers}</div>
              <button type="button" aria-label="Increase workers" onClick={() => setMaxWorkers((v) => Math.min(8, v + 1))}>+</button>
            </div>
          </div>
        </div>
      </div>

      <aside className="tp-preview">
        <div className="tp-preview-head">RUN PREVIEW</div>
        <div className="tp-preview-body">
          <div className="tp-preview-kv">
            <div className="k">TEAM</div><div>{team.name}</div>
            <div className="k">MEMBERS</div><div>{(team.members || []).length} agents</div>
            <div className="k">MODE</div><div>{activeMode.label}</div>
            <div className="k">WORKERS</div><div>max {maxWorkers} concurrent</div>
          </div>
          <div className="tp-preview-action">
            <Button type="submit" variant="primary" size="btn-lg">Start team run</Button>
          </div>
        </div>
      </aside>
    </form>
  );
}
