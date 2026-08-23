import { useEffect, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

// Capped at the executor's own ceiling (team_lifecycle.MAX_CONCURRENT_WORKERS).
// Offering more would store and display a number the executor will not honour.
const WORKER_CHOICES = [1, 2, 3];

function Avatar({ person }) {
  if (person?.avatar) return <img className="tp-avatar" src={`/static/avatars/${person.avatar}.png`} alt="" />;
  return <span className="tp-avatar tp-avatar-initials mono">{(person?.name || "?").slice(0, 2).toUpperCase()}</span>;
}

export function TeamPicker({ teams = [], teamRuns = [], onStart, runtime = null, workspacePolicies }) {
  const [teamId, setTeamId] = useState("");
  const [baseObjective, setBaseObjective] = useState("");
  const [parentTeamRunId, setParentTeamRunId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // One, like every run created before this control existed. Raising it is
  // a choice the operator makes per run, not a default they inherit.
  const [maxWorkers, setMaxWorkers] = useState("1");

  useEffect(() => {
    if (!teamId && teams.length) setTeamId(teams[0].id);
  }, [teams, teamId]);

  if (!teams.length) {
    return <div className="tp-empty mono">먼저 팀을 만드세요 — Teams 화면에서 팀과 로스터를 구성할 수 있습니다.</div>;
  }

  const team = teams.find((item) => item.id === teamId) || teams[0];
  const workspacePolicy = workspacePolicies?.teams?.find((item) => item.scope_id === team.id)
    || workspacePolicies?.global
    || null;
  const workspaceCapability = workspacePolicy?.capability || null;
  const workspaceManaged = workspacePolicies !== undefined;
  const workspaceLoading = workspacePolicies === null;
  const workspaceReady = !workspaceManaged || (!workspaceLoading && workspaceCapability?.ready !== false);
  const canInheritWorkspace = !workspacePolicy || workspacePolicy.write_mode === "isolated";
  const executionMode = (runtime?.team_execution_mode || "sequential").toUpperCase();
  const inheritableRuns = teamRuns.filter((run) => [
    "completed", "completed_with_failures", "blocked", "failed", "canceled"
  ].includes(run.status || run.display_status));
  const parentRun = inheritableRuns.find((run) => run.id === parentTeamRunId);

  return (
    <form className="tp" aria-label="New team run" onSubmit={async (event) => {
      event.preventDefault();
      if (submitting) return;
      const instruction = baseObjective.trim();
      const payload = {
        team_id: team.id,
        execution_policy: "triggered",
        max_workers: Number(maxWorkers) || 1,
        initial_instruction: instruction
      };
      if (canInheritWorkspace && parentTeamRunId) payload.parent_team_run_id = parentTeamRunId;
      setSubmitting(true);
      try {
        await onStart(payload);
      } finally {
        setSubmitting(false);
      }
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
          <label className="tp-label" htmlFor="tp-base-objective">
            First request
          </label>
          <textarea
            id="tp-base-objective"
            className="tp-goal"
            value={baseObjective}
            onChange={(event) => setBaseObjective(event.target.value)}
            placeholder="What should the team do first?"
            required
          />
          <div className="tp-mode-desc">
            The run is created and this request starts immediately.
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
          <label className="tp-label" htmlFor="tp-max-workers">Parallel assignments</label>
          <select
            id="tp-max-workers"
            className="tp-select"
            value={maxWorkers}
            onChange={(event) => setMaxWorkers(event.target.value)}
          >
            {WORKER_CHOICES.map((count) => (
              <option key={count} value={String(count)}>
                {count === 1 ? "1 (sequential)" : `${count} at a time`}
              </option>
            ))}
          </select>
          <div className="tp-mode-desc">
            {executionMode === "SEQUENTIAL"
              ? "Overlap is off on this gateway, so assignments run one at a time whatever is chosen here."
              : "Only assignments whose promised files do not collide overlap; the rest still run in order."}
          </div>
        </div>

        {canInheritWorkspace ? (
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
        ) : null}

        <div className={`tp-workspace${workspaceReady ? "" : " attention"}`}>
          <div className="tp-label">Workspace check</div>
          <strong>{workspaceCapability?.read_summary || "Workspace access will be checked when the run starts"}</strong>
          <span>{workspaceCapability?.write_summary || "The configured Team workspace policy will be used"}</span>
          <b>{workspaceLoading ? "Checking workspace..." : workspaceReady ? "Workspace ready" : "Workspace needs attention"}</b>
          {!workspaceLoading && !workspaceReady && workspaceCapability?.issues?.[0] ? (
            <small>{workspaceCapability.issues[0]}</small>
          ) : null}
        </div>

        <div className="tp-settings">
          <div className="tp-form">
            <div className="tp-field">
              <span className="tp-label">Run behavior</span>
              <div className="tp-workers-val">TRIGGERED · CONTINUOUS</div>
              <div className="tp-mode-desc">The first request starts now. Later requests create another cycle in this run.</div>
            </div>
          </div>
          <div className="tp-field">
            <span className="tp-label">Execution</span>
            <div className="tp-workers">
              <div className="tp-workers-val">{maxWorkers} · {executionMode}</div>
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
            <div className="k">POLICY</div><div>Triggered, continuous</div>
            <div className="k">WORKSPACE</div><div>{parentRun && canInheritWorkspace
              ? `Inherit ${parentRun.id.slice(0, 8)}`
              : workspacePolicy ? "Configured Team workspace" : "Team workspace policy"}</div>
            <div className="k">FIRST REQUEST</div>
            <div>{baseObjective.trim() || "Required"}</div>
            <div className="k">WORKERS</div><div>{maxWorkers} · {executionMode}</div>
          </div>
          <div className="tp-preview-action">
            <Button
              type="submit"
              variant="primary"
              size="btn-lg"
              disabled={submitting || !baseObjective.trim() || !workspaceReady}
            >
              {submitting ? "Starting..." : "Start team run"}
            </Button>
          </div>
        </div>
      </aside>
    </form>
  );
}
