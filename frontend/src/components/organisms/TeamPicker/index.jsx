import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const EXECUTION_POLICIES = [
  { value: "triggered", label: "TRIGGERED", desc: "Runs a new cycle when a manual or Hook trigger is queued." },
  { value: "auto", label: "AUTO", desc: "Runs a fixed number of cycles separated by an interval." }
];

function Avatar({ person }) {
  if (person?.avatar) return <img className="tp-avatar" src={`/static/avatars/${person.avatar}.png`} alt="" />;
  return <span className="tp-avatar tp-avatar-initials mono">{(person?.name || "?").slice(0, 2).toUpperCase()}</span>;
}

export function TeamPicker({ teams = [], teamRuns = [], onStart, runtime = null }) {
  const [teamId, setTeamId] = useState("");
  const [baseObjective, setBaseObjective] = useState("");
  const [executionPolicy, setExecutionPolicy] = useState("triggered");
  const [repeatCount, setRepeatCount] = useState("3");
  const [intervalMinutes, setIntervalMinutes] = useState("5");
  const [parentTeamRunId, setParentTeamRunId] = useState("");

  useEffect(() => {
    if (!teamId && teams.length) setTeamId(teams[0].id);
  }, [teams, teamId]);

  if (!teams.length) {
    return <div className="tp-empty mono">먼저 팀을 만드세요 — Teams 화면에서 팀과 로스터를 구성할 수 있습니다.</div>;
  }

  const team = teams.find((item) => item.id === teamId) || teams[0];
  const activePolicy = EXECUTION_POLICIES.find((policy) => policy.value === executionPolicy);
  const executionMode = (runtime?.team_execution_mode || "sequential").toUpperCase();
  const inheritableRuns = teamRuns.filter((run) => [
    "completed", "completed_with_failures", "blocked", "failed", "canceled"
  ].includes(run.status || run.display_status));
  const parentRun = inheritableRuns.find((run) => run.id === parentTeamRunId);

  return (
    <form className="tp" aria-label="New team run" onSubmit={(event) => {
      event.preventDefault();
      const payload = {
        team_id: team.id,
        execution_policy: executionPolicy
      };
      if (executionPolicy === "auto") {
        payload.goal = baseObjective.trim();
        payload.auto_repeat_count = Number(repeatCount);
        payload.auto_interval_minutes = Number(intervalMinutes);
      }
      if (parentTeamRunId) payload.parent_team_run_id = parentTeamRunId;
      onStart(payload);
    }}>
      <div className="tp-form">
        <div className="tp-field">
          <span className="tp-label">Team</span>
          <div className="tp-teams">
            {teams.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === team.id}
                className={`tp-team${item.id === team.id ? " active" : ""}`}
                onClick={() => setTeamId(item.id)}
              >
                {item.name}
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
          <label className="tp-label" htmlFor="tp-parent-run">Inherit workspace</label>
          <select
            id="tp-parent-run"
            className="tp-select"
            value={parentTeamRunId}
            onChange={(event) => setParentTeamRunId(event.target.value)}
          >
            <option value="">Start with an empty workspace</option>
            {inheritableRuns.map((run) => (
              <option key={run.id} value={run.id}>
                {run.team_name || run.goal || "Unnamed Team Run"} · {run.id.slice(0, 8)}
              </option>
            ))}
          </select>
          <div className="tp-mode-desc">Copies safe files into a new writable, isolated workspace.</div>
        </div>

        {executionPolicy === "auto" ? (
          <div className="tp-field">
            <label className="tp-label" htmlFor="tp-base-objective">Base objective</label>
            <textarea
              id="tp-base-objective"
              className="tp-goal"
              value={baseObjective}
              onChange={(event) => setBaseObjective(event.target.value)}
              placeholder="What should every AUTO cycle continue working toward?"
              required
            />
          </div>
        ) : null}

        <div className="tp-settings">
          <div className="tp-form">
            <div className="tp-field">
              <span className="tp-label">Lifecycle</span>
              <div className="tp-workers-val">CONTINUOUS · FIXED</div>
            </div>
            <div className="tp-field">
              <span className="tp-label">Execution policy</span>
              <div className="tp-mode" role="group" aria-label="Execution policy">
                {EXECUTION_POLICIES.map((policy) => (
                  <button
                    key={policy.value}
                    type="button"
                    aria-pressed={executionPolicy === policy.value}
                    className={`tp-mode-btn${executionPolicy === policy.value ? " active" : ""}`}
                    onClick={() => setExecutionPolicy(policy.value)}
                  >
                    {policy.label}
                  </button>
                ))}
              </div>
              <div className="tp-mode-desc">{activePolicy.desc}</div>
            </div>
            {executionPolicy === "auto" ? (
              <div className="tp-field">
                <label className="tp-label" htmlFor="tp-repeat-count">Repeat count</label>
                <input
                  id="tp-repeat-count"
                  type="number"
                  min="1"
                  value={repeatCount}
                  onChange={(event) => setRepeatCount(event.target.value)}
                />
                <label className="tp-label" htmlFor="tp-interval-minutes">Interval minutes</label>
                <input
                  id="tp-interval-minutes"
                  type="number"
                  min="1"
                  value={intervalMinutes}
                  onChange={(event) => setIntervalMinutes(event.target.value)}
                />
              </div>
            ) : null}
          </div>
          <div className="tp-field">
            <span className="tp-label">Execution</span>
            <div className="tp-workers">
              <div className="tp-workers-val">1 · {executionMode}</div>
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
            <div className="k">POLICY</div><div>{activePolicy.label}</div>
            <div className="k">WORKSPACE</div><div>{parentRun ? `Inherit ${parentRun.id.slice(0, 8)}` : "Empty"}</div>
            {executionPolicy === "auto" ? (
              <>
                <div className="k">OBJECTIVE</div><div>{baseObjective.trim() || "Required"}</div>
                <div className="k">REPEAT</div><div>{repeatCount} cycles</div>
                <div className="k">INTERVAL</div><div>{intervalMinutes} minutes</div>
              </>
            ) : null}
            <div className="k">WORKERS</div><div>1 · {executionMode}</div>
          </div>
          <div className="tp-preview-action">
            <Button
              type="submit"
              variant="primary"
              size="btn-lg"
              disabled={executionPolicy === "auto" && !baseObjective.trim()}
            >
              Create team run
            </Button>
          </div>
        </div>
      </aside>
    </form>
  );
}
