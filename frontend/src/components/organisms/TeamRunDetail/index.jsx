import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { TeamTaskCard } from "../../molecules/TeamTaskCard/index.jsx";
import { DocumentPreview } from "../DocumentPreview/index.jsx";
import { fmtDateTime } from "../../../lib/time.js";

const TEAM_TASK_COLUMNS = ["pending", "in_progress", "blocked", "completed", "failed"];
const TERMINAL_STATUSES = ["completed", "completed_with_failures", "failed", "canceled"];

const RUN_PHASES = [
  { key: "planning", label: "Planning", statuses: ["planning"] },
  { key: "executing", label: "Executing", statuses: ["running"] },
  { key: "summarizing", label: "Summarizing", statuses: ["summarizing"] },
  { key: "done", label: "Done", statuses: ["completed", "completed_with_failures", "failed", "canceled"] }
];

function phaseIndex(status) {
  const index = RUN_PHASES.findIndex((phase) => phase.statuses.includes(status));
  if (status === "interrupted") return -1;
  return index < 0 ? 0 : index;
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

function findAgent(agents, id) {
  return agents.find((agent) => agent.id === id) || null;
}

function findTask(tasks, id) {
  return tasks.find((task) => task.id === id) || null;
}

function buildHandoffs(messages) {
  const queries = messages.filter((message) => message.kind === "query");
  const answers = messages.filter((message) => message.kind === "answer");
  return queries.map((query, index) => ({ query, answer: answers[index] || null }));
}

function groupReportsByTask(messages) {
  const grouped = new Map();
  for (const message of messages) {
    if (message.kind !== "agent_output" || !message.metadata?.task_id) continue;
    const taskReports = grouped.get(message.metadata.task_id) || [];
    taskReports.push(message);
    grouped.set(message.metadata.task_id, taskReports);
  }
  return grouped;
}

function TaskDetailDialog({ task, reports, agents, canRetry, retrying, onRetry, onClose }) {
  if (!task) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card team-task-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Task details: ${task.title}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className="mono">TASK DETAILS</span>
          <button type="button" className="modal-close" aria-label="Close task details" onClick={onClose}>×</button>
        </div>
        <div className="team-task-dialog-body">
          <div>
            <div className="mono team-task-dialog-label">TASK</div>
            <h2 className="headline team-task-dialog-title">{task.title}</h2>
            {task.description ? <p className="team-task-dialog-copy">{task.description}</p> : null}
          </div>

          {task.result || task.error_message ? (
            <div>
              <div className="mono team-task-dialog-label">RESULT</div>
              <div className="team-task-dialog-copy">{task.result || task.error_message}</div>
            </div>
          ) : null}

          <div>
            <div className="mono team-task-dialog-label">SHARED DOCUMENTS · {reports.length}</div>
            <div className="team-docs">
              {reports.length ? reports.map((message) => {
                const sender = findAgent(agents, message.sender_agent_id);
                const avatar = sender?.persona_snapshot?.avatar;
                return (
                  <article className="team-doc-card" key={message.id}>
                    <div className="team-doc-head">
                      {avatar ? (
                        <img className="team-doc-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
                      ) : (
                        <span className="team-doc-avatar team-doc-avatar-initials mono">{initials(sender?.name)}</span>
                      )}
                      <div className="team-doc-meta">
                        <span className="mono team-doc-owner">{sender ? sender.name : "Agent"}</span>
                        <span className="team-doc-task">{fmtDateTime(message.created_at)}</span>
                      </div>
                    </div>
                    <p className="team-doc-body">{message.content}</p>
                  </article>
                );
              }) : <div className="team-task-empty mono">No shared documents for this task.</div>}
            </div>
          </div>
        </div>
        {canRetry ? (
          <div className="team-add-work-dialog-actions">
            <Button size="btn-sm" variant="primary" disabled={retrying} onClick={onRetry}>
              {retrying ? "Retrying..." : "Retry failed task"}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AddWorkDialog({ open, runStatus, value, submitting, onChange, onClose, onSubmit }) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={submitting ? undefined : onClose}>
      <div
        className="modal-card team-add-work-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Add work"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className="mono">ADD WORK</span>
          <button type="button" className="modal-close" aria-label="Close add work" disabled={submitting} onClick={onClose}>×</button>
        </div>
        <div className="team-add-work-dialog-body">
          <label className="mono team-task-dialog-label" htmlFor="team-add-work-input">INSTRUCTION</label>
          <textarea
            id="team-add-work-input"
            className="team-add-work-input"
            aria-label="Additional work"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="Describe the additional work for the team."
            autoFocus
          />
        </div>
        <div className="team-add-work-dialog-actions">
          <Button size="btn-sm" disabled={submitting} onClick={onClose}>Cancel</Button>
          <Button size="btn-sm" variant="primary" disabled={submitting || !value.trim()} onClick={onSubmit}>
            {TERMINAL_STATUSES.includes(runStatus) ? "Reopen & request" : "Request work"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function TeamRunDetail({ detail, documents = [], onLoadDocument, onAddWork, onResume, onRetryTask }) {
  const [workInput, setWorkInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [retryingTaskId, setRetryingTaskId] = useState(null);
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [previewDoc, setPreviewDoc] = useState(null);
  const run = detail?.run;

  if (!run) {
    return <div className="team-run-empty mono">No team run selected.</div>;
  }

  const agents = detail.agents || [];
  const tasks = detail.tasks || [];
  const messages = detail.messages || [];
  const leader = findAgent(agents, run.leader_agent_id);
  const reports = messages.filter((message) => message.kind === "agent_output");
  const handoffs = buildHandoffs(messages);
  const reportsByTask = groupReportsByTask(messages);
  const selectedTask = selectedTaskId ? findTask(tasks, selectedTaskId) : null;
  const selectedTaskReports = selectedTask ? (reportsByTask.get(selectedTask.id) || []) : [];
  const canRetrySelectedTask = Boolean(
    onRetryTask
      && selectedTask?.status === "failed"
      && ["completed_with_failures", "failed"].includes(run.status)
  );
  const canAddWork = Boolean(
    onAddWork
      && run.run_mode === "plan_and_execute"
      && run.status !== "draft"
      && run.status !== "interrupted"
  );
  const canResume = Boolean(onResume && run.status === "interrupted");

  return (
    <section className="team-run-detail" aria-label="Team run detail">
      <header className="team-run-detail-head">
        <div className="team-run-detail-id-row">
          <span className="mono team-run-detail-id">{run.id}</span>
          <StatusBadge kind={run.status} />
        </div>
        <h1 className="headline team-run-detail-goal">{run.goal}</h1>
      </header>

      <div className="team-phase-stepper" aria-label="Run phase">
        {RUN_PHASES.map((phase, index) => {
          const activeIndex = phaseIndex(run.status);
          const isActive = index === activeIndex;
          const isDone = index < activeIndex;
          return (
            <div
              key={phase.key}
              className={`team-phase${isActive ? " active" : ""}${isDone ? " done" : ""}`}
              aria-current={isActive ? "step" : undefined}
            >
              <span className="team-phase-dot" />
              <span className="mono team-phase-label">{phase.label}</span>
            </div>
          );
        })}
      </div>

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
          <div className="mono team-run-meta-v">{fmtDateTime(run.started_at) || "-"}</div>
        </div>
        <div className="team-run-meta-cell team-run-meta-workspace">
          <div className="mono team-run-meta-k">WORKSPACE</div>
          <div className="mono team-run-meta-v team-run-meta-path" title={run.workspace_root || ""}>
            {run.workspace_root || "-"}
          </div>
        </div>
      </div>

      {run.status === "interrupted" ? (
        <div className="team-interrupted-banner" role="status">
          <span className="headline team-interrupted-title">Run interrupted</span>
          <span className="team-interrupted-copy">Running work was returned to Pending. Resume when you are ready.</span>
        </div>
      ) : null}

      <div className="team-section-head team-section-toolbar">
        <span className="mono team-section-label">Agent Sessions</span>
        <span className="team-section-rule" />
        {canResume ? (
          <Button
            size="btn-sm"
            variant="primary"
            disabled={resuming}
            onClick={async () => {
              setResuming(true);
              try {
                await onResume();
              } finally {
                setResuming(false);
              }
            }}
          >
            {resuming ? "Resuming..." : "Resume"}
          </Button>
        ) : null}
        {canAddWork ? (
          <Button size="btn-sm" variant="primary" onClick={() => setWorkDialogOpen(true)}>Add work</Button>
        ) : null}
      </div>
      <div className="team-lanes">
        {agents.map((agent) => {
          const currentTask = findTask(tasks, agent.current_task_id);
          const avatar = agent.persona_snapshot?.avatar;
          const roleLabel = agent.persona_snapshot?.role || agent.role;
          return (
            <article className={`team-lane team-lane-${agent.status}${agent.role === "leader" ? " team-lane-leader" : ""}`} key={agent.id}>
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
                <div className="team-lane-status-row">
                  <StatusBadge kind={agent.status} />
                  {agent.status === "running" ? <span className="mono team-lane-live">LIVE</span> : null}
                </div>
                <div className="team-lane-task">{currentTask ? currentTask.title : "No active task"}</div>
                <div className="mono team-lane-snapshot">SNAPSHOT · {agent.backend}/{agent.model}</div>
              </div>
            </article>
          );
        })}
      </div>

      <div className="team-section-head">
        <span className="mono team-section-label">Task Board</span>
        <span className="mono team-section-count">{reports.length} documents</span>
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
                    <TeamTaskCard
                      key={task.id}
                      task={task}
                      owner={findAgent(agents, task.owner_agent_id)}
                      documentCount={(reportsByTask.get(task.id) || []).length}
                      onOpen={() => setSelectedTaskId(task.id)}
                    />
                  ))
                ) : (
                  <div className="team-task-empty mono">-</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className={`team-activity-results${!handoffs.length && !run.summary ? " team-activity-results-single" : ""}`}>
        <div className="team-activity-col">
          <div className="team-section-head">
            <span className="mono team-section-label">Live Activity</span>
            <span className="team-section-rule" />
          </div>
          <div className="timeline">
            {messages.map((message) => {
              const sender = findAgent(agents, message.sender_agent_id);
              return (
                <div className={`tl-row tl-kind-${message.kind}`} key={message.id}>
                  <span className="tl-time mono">{fmtDateTime(message.created_at)}</span>
                  <span className="mono team-activity-agent">{sender ? sender.name : "SYSTEM"}</span>
                  <span className="mono tl-label">{message.kind}</span>
                  <span className="tl-detail">{message.content}</span>
                </div>
              );
            })}
          </div>
        </div>

        {handoffs.length || run.summary ? (
          <div className="team-results-col">
            {handoffs.length ? (
            <>
              <div className="team-section-head">
                <span className="mono team-section-label">Shared / Handoffs</span>
                <span className="team-section-rule" />
              </div>
              <div className="team-handoffs">
                {handoffs.map(({ query, answer }) => {
                  const asker = findAgent(agents, query.sender_agent_id);
                  const responder = answer ? findAgent(agents, answer.sender_agent_id) : null;
                  return (
                    <div className="team-handoff" key={query.id}>
                      <div className="team-handoff-q">
                        <span className="mono team-handoff-who">{asker ? asker.name : "Agent"} →</span>
                        <span className="team-handoff-text">{query.content}</span>
                      </div>
                      {answer ? (
                        <div className="team-handoff-a">
                          <span className="mono team-handoff-who">{responder ? responder.name : "Leader"} ↩</span>
                          <span className="team-handoff-text">{answer.content}</span>
                        </div>
                      ) : (
                        <div className="team-handoff-a team-handoff-unanswered mono">no answer (budget/cap reached)</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
            ) : null}

            {run.summary ? (
              <div className="team-final-summary">
                <div className="mono team-final-summary-head">FINAL SUMMARY · {leader?.name || ""}</div>
                <div className="team-final-summary-body">{run.summary}</div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="team-section-head">
        <span className="mono team-section-label">Documents</span>
        <span className="mono team-section-count">{documents.length} files</span>
        <span className="team-section-rule" />
      </div>
      <div className="team-docs-list">
        {documents.length ? documents.map((doc) => (
          <button
            key={doc.path}
            type="button"
            className="team-docs-list-row"
            aria-label={`Preview ${doc.path}`}
            disabled={!doc.previewable || !onLoadDocument}
            onClick={async () => {
              if (!onLoadDocument) return;
              const loaded = await onLoadDocument(doc.path);
              setPreviewDoc(loaded || { ...doc, previewable: false, reason: "load failed" });
            }}
          >
            <span className="mono team-docs-name">{doc.path}</span>
            <span className="mono team-docs-kind">{doc.kind}</span>
          </button>
        )) : <div className="team-task-empty mono">No documents in the workspace yet.</div>}
      </div>

      <DocumentPreview open={Boolean(previewDoc)} doc={previewDoc} onClose={() => setPreviewDoc(null)} />

      <TaskDetailDialog
        task={selectedTask}
        reports={selectedTaskReports}
        agents={agents}
        canRetry={canRetrySelectedTask}
        retrying={retryingTaskId === selectedTask?.id}
        onRetry={async () => {
          setRetryingTaskId(selectedTask.id);
          try {
            await onRetryTask(selectedTask.id);
          } finally {
            setRetryingTaskId(null);
          }
        }}
        onClose={() => setSelectedTaskId(null)}
      />
      <AddWorkDialog
        open={workDialogOpen}
        runStatus={run.status}
        value={workInput}
        submitting={submitting}
        onChange={setWorkInput}
        onClose={() => setWorkDialogOpen(false)}
        onSubmit={async () => {
          const text = workInput.trim();
          setSubmitting(true);
          try {
            const accepted = await onAddWork(text);
            if (accepted === false) return;
            setWorkInput("");
            setWorkDialogOpen(false);
          } finally {
            setSubmitting(false);
          }
        }}
      />
    </section>
  );
}
