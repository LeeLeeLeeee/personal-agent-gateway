import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { TeamTaskCard } from "../../molecules/TeamTaskCard/index.jsx";

const TEAM_TASK_COLUMNS = ["pending", "in_progress", "blocked", "completed", "failed"];

function initials(name) {
  return (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";
}

function findAgent(agents, id) {
  return agents.find((agent) => agent.id === id) || null;
}

function findTask(tasks, id) {
  return tasks.find((task) => task.id === id) || null;
}

export function TeamRunDetail({ detail }) {
  const run = detail?.run;

  if (!run) {
    return <div className="team-run-empty mono">No team run selected.</div>;
  }

  const agents = detail.agents || [];
  const tasks = detail.tasks || [];
  const messages = detail.messages || [];
  const leader = findAgent(agents, run.leader_agent_id);
  const reports = messages.filter((message) => message.kind === "agent_output");

  return (
    <section className="team-run-detail" aria-label="Team run detail">
      <header className="team-run-detail-head">
        <div className="team-run-detail-id-row">
          <span className="mono team-run-detail-id">{run.id}</span>
          <StatusBadge kind={run.status} />
        </div>
        <h1 className="headline team-run-detail-goal">{run.goal}</h1>
      </header>

      <div className="team-run-meta">
        <div className="team-run-meta-cell">
          <div className="mono team-run-meta-k">MODE</div>
          <div className="mono team-run-meta-v">{run.run_mode}</div>
        </div>
        <div className="team-run-meta-cell">
          <div className="mono team-run-meta-k">WORKERS</div>
          <div className="mono team-run-meta-v">{run.max_workers ?? "-"}</div>
        </div>
        <div className="team-run-meta-cell">
          <div className="mono team-run-meta-k">LEADER</div>
          <div className="mono team-run-meta-v">{leader?.name || "-"}</div>
        </div>
        <div className="team-run-meta-cell">
          <div className="mono team-run-meta-k">STARTED</div>
          <div className="mono team-run-meta-v">{run.started_at || "-"}</div>
        </div>
        <div className="team-run-meta-cell">
          <div className="mono team-run-meta-k">WORKSPACE</div>
          <div className="mono team-run-meta-v">{run.workspace_root || "-"}</div>
        </div>
      </div>

      <div className="team-section-head">
        <span className="mono team-section-label">Agent Sessions</span>
        <span className="team-section-rule" />
      </div>
      <div className="team-lanes">
        {agents.map((agent) => {
          const currentTask = findTask(tasks, agent.current_task_id);
          const avatar = agent.persona_snapshot?.avatar;
          const roleLabel = agent.persona_snapshot?.role || agent.role;
          return (
            <article className="team-lane" key={agent.id}>
              <div className="team-lane-head">
                {avatar ? (
                  <img className="team-lane-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
                ) : (
                  <span className="team-lane-avatar team-lane-avatar-initials mono">{initials(agent.name)}</span>
                )}
                <div className="team-lane-title">
                  <div className="mono team-lane-name">{agent.name}</div>
                  <div className="team-lane-role">{roleLabel}</div>
                </div>
                {agent.role === "leader" ? <span className="team-lane-lead mono">LEAD</span> : null}
              </div>
              <div className="team-lane-body">
                <StatusBadge kind={agent.status} />
                <div className="team-lane-task">{currentTask ? currentTask.title : "No active task"}</div>
                <div className="mono team-lane-snapshot">SNAPSHOT · {agent.backend}/{agent.model}</div>
              </div>
            </article>
          );
        })}
      </div>

      <div className="team-section-head">
        <span className="mono team-section-label">Task Board</span>
        <span className="team-section-rule" />
      </div>
      <div className="team-task-board">
        {TEAM_TASK_COLUMNS.map((column) => {
          const columnTasks = tasks.filter((task) => task.status === column);
          return (
            <div className="team-task-column" key={column}>
              <div className="team-task-column-head mono">
                <span>{column.replace("_", " ")}</span>
                <span>{columnTasks.length}</span>
              </div>
              <div className="team-task-column-body">
                {columnTasks.length ? (
                  columnTasks.map((task) => (
                    <TeamTaskCard key={task.id} task={task} owner={findAgent(agents, task.owner_agent_id)} />
                  ))
                ) : (
                  <div className="team-task-empty mono">-</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="team-activity-results">
        <div className="team-activity-col">
          <div className="team-section-head">
            <span className="mono team-section-label">Live Activity</span>
            <span className="team-section-rule" />
          </div>
          <div className="timeline">
            {messages.map((message) => {
              const sender = findAgent(agents, message.sender_agent_id);
              return (
                <div className="tl-row" key={message.id}>
                  <span className="tl-time mono">{message.created_at}</span>
                  <span className="mono team-activity-agent">{sender ? sender.name : "SYSTEM"}</span>
                  <span className="mono tl-label">{message.kind}</span>
                  <span className="tl-detail">{message.content}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="team-results-col">
          <div className="team-section-head">
            <span className="mono team-section-label">Results</span>
            <span className="team-section-rule" />
          </div>
          <div className="team-reports">
            {reports.map((message) => {
              const sender = findAgent(agents, message.sender_agent_id);
              return (
                <article className="team-report-card" key={message.id}>
                  <div className="team-report-head">
                    <span className="mono team-report-owner">{initials(sender?.name)}</span>
                    <span className="mono team-report-name">{sender ? sender.name : "Agent"}</span>
                  </div>
                  <p className="team-report-body">{message.content}</p>
                </article>
              );
            })}
          </div>
          {run.summary ? (
            <div className="team-final-summary">
              <div className="mono team-final-summary-head">FINAL SUMMARY · {leader?.name || ""}</div>
              <div className="team-final-summary-body">{run.summary}</div>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
