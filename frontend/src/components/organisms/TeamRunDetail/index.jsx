import { useEffect, useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { LoaderCube } from "../../molecules/LoaderCube/index.jsx";
import { TeamTaskCard } from "../../molecules/TeamTaskCard/index.jsx";
import { BuildEvidence, BuildEvidenceSummary } from "./BuildEvidence.jsx";
import { DocumentPreview } from "../DocumentPreview/index.jsx";
import { MarkdownContent } from "../MarkdownContent/index.jsx";
import { elapsedSeconds, fmtDateTime, fmtElapsed } from "../../../lib/time.js";
import { TASK_STATUS_GROUPS, groupForTaskStatus } from "../../../lib/taskStatusGroups.js";

const OPEN_TASK_STATUSES = new Set([
  "pending",
  "in_progress",
  "waiting_for_provider",
  "waiting_for_user",
  "blocked"
]);
const TERMINAL_STATUSES = [
  "completed",
  "completed_with_failures",
  "blocked",
  "failed",
  "canceled"
];

const DETAIL_TABS = [
  ["overview", "OVERVIEW"],
  ["tasks", "TASKS"],
  ["history", "HISTORY"],
  ["activity", "ACTIVITY"],
  ["files", "FILES"]
];

const RUN_PHASES = [
  { key: "planning", label: "Planning", statuses: ["planning"] },
  { key: "executing", label: "Executing", statuses: ["running"] },
  { key: "summarizing", label: "Summarizing", statuses: ["summarizing"] },
  {
    key: "done",
    label: "Done",
    statuses: ["completed", "completed_with_failures", "blocked", "failed", "canceled"]
  }
];

function phaseIndex(status) {
  const index = RUN_PHASES.findIndex((phase) => phase.statuses.includes(status));
  if (["interrupted", "waiting_for_user"].includes(status)) return -1;
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

function documentLabel(path) {
  const parts = String(path || "").split("/");
  return { name: parts.pop() || path, parent: parts.join("/") };
}

function newestFirst(items) {
  return [...items].sort((left, right) => {
    const byTime = String(right.created_at || "").localeCompare(String(left.created_at || ""));
    return byTime || String(right.id || "").localeCompare(String(left.id || ""));
  });
}

function buildHandoffs(messages) {
  const queries = messages.filter((message) => message.kind === "query");
  const answers = messages.filter((message) => message.kind === "answer");
  const answersByQuery = new Map(
    answers
      .filter((answer) => answer.metadata?.query_id)
      .map((answer) => [answer.metadata.query_id, answer])
  );
  const legacyAnswers = answers.filter((answer) => !answer.metadata?.query_id);
  let legacyIndex = 0;
  return queries
    .map((query) => {
      const linked = answersByQuery.get(query.id);
      const answer = linked || legacyAnswers[legacyIndex] || null;
      if (!linked && answer) legacyIndex += 1;
      return { query, answer };
    })
    .sort((left, right) => {
      const leftMessage = left.answer || left.query;
      const rightMessage = right.answer || right.query;
      const byTime = String(rightMessage.created_at || "").localeCompare(
        String(leftMessage.created_at || "")
      );
      return byTime || String(rightMessage.id || "").localeCompare(String(leftMessage.id || ""));
    });
}

export function currentWork(agent, task, runStatus) {
  if (task) {
    return {
      title: task.title,
      startedAt: task.status === "in_progress" ? task.started_at || null : null
    };
  }
  if (agent.role !== "leader") return { title: "No active task", startedAt: null };
  if (runStatus === "planning") return { title: "Planning tasks", startedAt: null };
  if (runStatus === "running") return { title: "Coordinating agents", startedAt: null };
  if (runStatus === "summarizing") return { title: "Summarizing results", startedAt: null };
  return { title: "No active task", startedAt: null };
}

function effectiveChildStatus(status, runStatus) {
  if (runStatus !== "failed") return status;
  if (status === "running" || status === "in_progress") return "failed";
  return status;
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

function groupAcceptanceReviewsByTask(messages) {
  const grouped = new Map();
  for (const message of messages) {
    if (message.kind !== "acceptance_review" || !message.metadata?.task_id) continue;
    const reviews = grouped.get(message.metadata.task_id) || [];
    reviews.push(message);
    grouped.set(message.metadata.task_id, reviews);
  }
  return grouped;
}

function taskFileCount(reports) {
  const files = new Set();
  for (const report of reports) {
    for (const key of ["files_created", "files_modified", "files_deleted"]) {
      const paths = report.metadata?.[key];
      if (!Array.isArray(paths)) continue;
      for (const path of paths) files.add(path);
    }
  }
  return files.size;
}

// The response text itself is never stored -- it can carry anything the model
// was working on -- so this shape is all there is to explain why a run stopped
// on a parse failure. Which key was missing is usually the whole answer.
function FailureShape({ shape }) {
  if (!shape) return null;
  const missing = shape.missing_expected_keys || [];
  return (
    <div>
      <div className="mono team-task-dialog-label">RESPONSE DID NOT PARSE</div>
      <div className="team-task-diagnostic mono">
        <div>{`${shape.length} chars · ${shape.parsed_json ? "valid JSON" : "not JSON"} · ${shape.fenced ? "code-fenced" : "unfenced"}`}</div>
        {missing.length ? <div>{`missing keys: ${missing.join(", ")}`}</div> : null}
        {shape.unexpected_key_count ? (
          <div>{`unexpected keys: ${shape.unexpected_key_count}`}</div>
        ) : null}
      </div>
    </div>
  );
}

function TaskDetailDialog({ task, reports, reviews, agents, canRetry, retrying, onRetry, onClose }) {
  if (!task) return null;
  const acceptance = task.acceptance || {};
  const outcome = task.outcome || {};
  const acceptanceResult = task.acceptance_result || {};
  const verifications = Array.isArray(outcome.verifications)
    ? outcome.verifications
    : [];
  const verificationByName = new Map(
    verifications.map((verification) => [verification.name, verification])
  );
  const verificationEvidence = acceptanceResult.evidence?.verifications || {};
  const reasonCode = acceptanceResult.reason_code || outcome.reason_code;
  const diagnostic = task.result
    || (task.error_message && task.error_message !== reasonCode
      ? task.error_message
      : outcome.summary || task.error_message);

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
            {task.description ? (
              <div className="team-task-dialog-copy">
                <MarkdownContent source={task.description} pathRegistration={false} />
              </div>
            ) : null}
          </div>

          {diagnostic ? (
            <div>
              <div className="mono team-task-dialog-label">RESULT</div>
              <div className="team-task-dialog-copy">
                <MarkdownContent source={diagnostic} pathRegistration={false} />
              </div>
            </div>
          ) : null}

          <FailureShape shape={task.failure_shape} />

          <BuildEvidence evidence={task.build_evidence} />

          <div>
            <div className="mono team-task-dialog-label">
              {task.required === false ? "OPTIONAL TASK" : "REQUIRED TASK"}
            </div>
            {acceptanceResult.evidence?.attested_only ? (
              <span className="mono team-task-attested">ATTESTED ONLY</span>
            ) : null}
            {reasonCode ? (
              <div className="team-task-dialog-copy">
                <span className="mono">{reasonCode}</span>
              </div>
            ) : null}
            <div className="team-task-dialog-copy">
              <div className="mono">REQUIRED OUTPUTS</div>
              {(acceptance.required_outputs || []).length ? (
                <ul>
                  {acceptance.required_outputs.map((path) => (
                    <li key={path}><span className="mono">{path}</span></li>
                  ))}
                </ul>
              ) : <div>None</div>}
              <div className="mono">VERIFICATIONS</div>
              {(acceptance.required_verifications || []).length ? (
                <ul>
                  {acceptance.required_verifications.map((item) => {
                    const name = typeof item === "string" ? item : item.name;
                    const check = typeof item === "string" ? null : item.check;
                    const verification = verificationByName.get(name);
                    const serverEntry = verificationEvidence[name];
                    const mode = serverEntry?.mode;
                    const verified = mode === "verified";
                    const status = verified ? serverEntry.status : verification?.status;
                    const evidenceText = verified ? serverEntry.evidence : verification?.evidence;
                    return (
                      <li key={name}>
                        <span className="mono">{name}</span>
                        {" · "}
                        <span className="mono">
                          {String(status || "missing").toUpperCase()}
                        </span>
                        {mode ? (
                          <>
                            {" · "}
                            <span className="mono">{mode.toUpperCase()}</span>
                          </>
                        ) : null}
                        {evidenceText ? <span>{` · ${evidenceText}`}</span> : null}
                        {check ? (
                          <div className="mono team-task-check">
                            {`${check.type} · ${check.path}`}
                            {check.value ? ` · ${check.value}` : ""}
                            {check.pattern ? ` · ${check.pattern}` : ""}
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              ) : <div>None</div>}
            </div>
          </div>

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
                    <div className="team-doc-body">
                      <MarkdownContent source={message.content} pathRegistration={false} />
                    </div>
                  </article>
                );
              }) : <div className="team-task-empty mono">No shared documents for this task.</div>}
            </div>
          </div>

          {reviews.length ? (
            <div>
              <div className="mono team-task-dialog-label">INTERNAL REVIEW · {reviews.length}</div>
              <div className="team-acceptance-review-list">
                {reviews.map((review) => {
                  const metadata = review.metadata || {};
                  return (
                    <article className="team-acceptance-review" key={review.id}>
                      <div className="team-acceptance-review-head mono">
                        <span>ATTEMPT {metadata.attempt || "-"}</span>
                        <span>{String(metadata.action || "").replaceAll("_", " ").toUpperCase()}</span>
                      </div>
                      {metadata.reason_code ? <div className="mono">{metadata.reason_code}</div> : null}
                      {metadata.reason ? <div className="team-task-dialog-copy">{metadata.reason}</div> : null}
                      {metadata.instruction ? <div className="team-task-dialog-copy">{metadata.instruction}</div> : null}
                      <details className="team-acceptance-review-contract">
                        <summary className="mono">ACCEPTANCE CONTRACT</summary>
                        <pre>{JSON.stringify({
                          acceptance_before: metadata.acceptance_before,
                          acceptance_after: metadata.acceptance_after
                        }, null, 2)}</pre>
                      </details>
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}
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

function ConflictContent({ label, content, deleted }) {
  return (
    <div className="team-delivery-conflict-version">
      <span className="mono team-delivery-k">{label}</span>
      <pre>{deleted ? "(deleted)" : (content ?? "(binary or too large to preview)")}</pre>
    </div>
  );
}

function ConflictEditor({ conflict, action, perform, onResolve }) {
  const [draft, setDraft] = useState(conflict.working_content || "");
  const busy = Boolean(action);

  return (
    <div className="team-delivery-conflict-editor">
      <div className="team-delivery-conflict-compare">
        <ConflictContent
          label="TARGET VERSION"
          content={conflict.target_content}
          deleted={conflict.target_deleted}
        />
        <ConflictContent
          label="TEAM RUN VERSION"
          content={conflict.team_content}
          deleted={conflict.team_deleted}
        />
      </div>
      <div className="team-delivery-conflict-choice">
        <Button
          size="btn-sm"
          disabled={busy || !onResolve}
          onClick={() => perform(
            `resolve-target-${conflict.id}`,
            () => onResolve(conflict.id, { mode: "target" })
          )}
        >
          Keep target
        </Button>
        <Button
          size="btn-sm"
          disabled={busy || !onResolve}
          onClick={() => perform(
            `resolve-team-${conflict.id}`,
            () => onResolve(conflict.id, { mode: "team" })
          )}
        >
          Use Team Run
        </Button>
      </div>
      <label className="team-delivery-conflict-manual">
        <span className="mono team-delivery-k">MERGED RESULT</span>
        <textarea
          aria-label={`Merged result for ${conflict.path}`}
          value={draft}
          disabled={!conflict.manual_allowed || busy}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <Button
        size="btn-sm"
        variant="primary"
        disabled={!conflict.manual_allowed || busy || !onResolve}
        onClick={() => perform(
          `resolve-manual-${conflict.id}`,
          () => onResolve(conflict.id, { mode: "manual", content: draft })
        )}
      >
        Save manual merge
      </Button>
    </div>
  );
}

function DeliveryConflictResolver({
  session, action, perform, onResolve, onContinue, onCancel
}) {
  const files = session.files || [];
  const [selectedId, setSelectedId] = useState(files[0]?.id || null);
  const selected = files.find((file) => file.id === selectedId) || files[0] || null;

  return (
    <div className="team-delivery-conflicts" role="region" aria-label="Repository conflicts">
      <div className="team-delivery-conflict-head">
        <div>
          <span className="mono team-delivery-conflict-kicker">CONFLICTS NEED RESOLUTION</span>
          <strong>{session.resolved_count} / {session.total_count} resolved</strong>
        </div>
        {session.target_changed ? (
          <span className="team-delivery-conflict-stale">Target HEAD changed. Cancel and apply again.</span>
        ) : null}
      </div>
      <div className="team-delivery-conflict-layout">
        <div className="team-delivery-conflict-list" role="list">
          {files.map((file) => (
            <button
              key={file.id}
              type="button"
              className={file.id === selected?.id ? "active" : ""}
              onClick={() => setSelectedId(file.id)}
            >
              <span>{file.path}</span>
              <span className="mono">{file.resolved ? `RESOLVED · ${file.resolution}` : "OPEN"}</span>
            </button>
          ))}
        </div>
        {selected ? (
          <ConflictEditor
            key={`${session.id}:${selected.id}:${selected.resolution || "open"}`}
            conflict={selected}
            action={action}
            perform={perform}
            onResolve={onResolve}
          />
        ) : null}
      </div>
      <div className="team-delivery-conflict-actions">
        <Button
          size="btn-sm"
          variant="destructive"
          disabled={Boolean(action) || !onCancel}
          onClick={() => perform("cancel-conflicts", onCancel)}
        >
          {action === "cancel-conflicts" ? "Canceling..." : "Cancel resolution"}
        </Button>
        <Button
          size="btn-sm"
          variant="primary"
          disabled={!session.can_continue || Boolean(action) || !onContinue}
          onClick={() => perform("continue-delivery", onContinue)}
        >
          {action === "continue-delivery" ? "Applying..." : "Resolve & apply"}
        </Button>
      </div>
    </div>
  );
}

function DeliveryPanel({
  runId, delivery, loading, onRefresh, onCommit, onApply,
  onResolve, onContinue, onCancelConflicts
}) {
  const [message, setMessage] = useState(`chore(team-run): deliver ${runId.slice(0, 8)}`);
  const [action, setAction] = useState(null);

  if (!delivery && !loading) return null;

  async function perform(name, callback) {
    if (!callback || action) return;
    setAction(name);
    try {
      await callback();
    } finally {
      setAction(null);
    }
  }

  return (
    <details className="team-delivery-panel" role="region" aria-label="Repository delivery" open>
      <summary className="team-delivery-summary">
        <span className="mono team-section-label">Repository Delivery</span>
        <span className="team-section-rule" />
        <Button
          size="btn-sm"
          disabled={loading || Boolean(action) || !onRefresh}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            perform("refresh", onRefresh);
          }}
        >
          {loading || action === "refresh" ? "Refreshing..." : "Refresh"}
        </Button>
      </summary>

      <div className="team-delivery-body">
        {loading && !delivery ? (
          <div className="team-delivery-empty mono">Inspecting worktree changes...</div>
        ) : delivery?.available === false ? (
          <div className="team-delivery-empty mono">{delivery.reason}</div>
        ) : delivery ? (
          <>
          <div className="team-delivery-paths">
            <div>
              <span className="mono team-delivery-k">SOURCE · {delivery.source?.branch}</span>
              <span className="mono team-delivery-path" title={delivery.source?.path}>{delivery.source?.path}</span>
            </div>
            <div>
              <span className="mono team-delivery-k">TARGET · {delivery.target?.branch}</span>
              <span className="mono team-delivery-path" title={delivery.target?.path}>{delivery.target?.path}</span>
            </div>
          </div>

          <div className="team-delivery-counts mono">
            <span>UNCOMMITTED · {delivery.uncommitted_files?.length || 0}</span>
            <span>PENDING COMMITS · {delivery.pending_commits?.length || 0}</span>
            <span>TARGET DIRTY · {delivery.target?.dirty_files?.length || 0}</span>
          </div>

          {delivery.uncommitted_files?.length ? (
            <div className="team-delivery-files">
              {delivery.uncommitted_files.map((file) => (
                <div className="mono team-delivery-file" key={`${file.status}:${file.path}`}>
                  <span>{file.status}</span><span>{file.path}</span>
                </div>
              ))}
            </div>
          ) : null}

          {delivery.pending_commits?.length ? (
            <div className="team-delivery-commits">
              {delivery.pending_commits.map((commit) => (
                <div className="team-delivery-commit" key={commit.sha}>
                  <span className="mono">{commit.short_sha}</span>
                  <span>{commit.subject}</span>
                </div>
              ))}
            </div>
          ) : null}

          {delivery.blocked_reasons?.length ? (
            <div className="team-delivery-blockers" role="status">
              {delivery.blocked_reasons.map((reason) => <div key={reason}>{reason}</div>)}
            </div>
          ) : null}

            {delivery.conflict_session ? (
              <DeliveryConflictResolver
                key={delivery.conflict_session.id}
                session={delivery.conflict_session}
                action={action}
                perform={perform}
                onResolve={onResolve}
                onContinue={onContinue}
                onCancel={onCancelConflicts}
              />
            ) : (
              <div className="team-delivery-actions">
                {delivery.uncommitted_files?.length ? (
                  <label className="team-delivery-message">
                    <span className="mono team-delivery-k">COMMIT MESSAGE</span>
                    <input
                      className="input-field"
                      value={message}
                      onChange={(event) => setMessage(event.target.value)}
                    />
                  </label>
                ) : null}
                <Button
                  size="btn-sm"
                  disabled={!delivery.can_commit || !message.trim() || Boolean(action) || !onCommit}
                  onClick={() => perform("commit", () => onCommit(message.trim()))}
                >
                  {action === "commit" ? "Committing..." : "Commit changes"}
                </Button>
                <Button
                  size="btn-sm"
                  variant="primary"
                  disabled={!delivery.can_apply || Boolean(action) || !onApply}
                  onClick={() => perform("apply", onApply)}
                >
                  {action === "apply" ? "Applying..." : "Apply to repository"}
                </Button>
              </div>
            )}
          </>
        ) : null}
      </div>
    </details>
  );
}

function DecisionRequestPanel({ request, onSubmit }) {
  const [answers, setAnswers] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const items = request.items || [];
  const complete = items.length > 0 && items.every((item) => String(answers[item.id] || "").trim());
  const intro = items.some((item) => item.stage === "planning")
    ? "Planning is paused for your decision. Answer every open question to start the work."
    : items.some((item) => item.stage === "synthesis")
      ? "Work is complete. Answer every open question to finalize the response."
      : "Independent work is complete. Answer every open question once, then the blocked tasks resume.";

  return (
    <section className="team-decision-panel" role="region" aria-label="Input needed">
      <div className="team-decision-head">
        <div>
          <div className="mono team-decision-kicker">INPUT NEEDED · {items.length}</div>
          <h2 className="headline team-decision-title">Leader needs your decisions</h2>
        </div>
        <span className="mono team-decision-revision">REV {request.revision}</span>
      </div>
      <p className="team-decision-intro">
        {intro}
      </p>
      <p className="team-decision-secret-warning">
        Do not enter passwords, tokens, recovery codes, or private keys here.
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!complete || !onSubmit) return;
          setSubmitting(true);
          try {
            await onSubmit(answers);
          } finally {
            setSubmitting(false);
          }
        }}
      >
        <div className="team-decision-list">
          {items.map((item) => {
            const options = item.options || [];
            const recommended = options.find(
              (option) => option.id === item.recommended_option_id
            );
            return (
              <fieldset className="team-decision-item" key={item.id}>
                <legend>
                  <span className="mono team-decision-item-id">{item.id} · {item.topic || "Decision"}</span>
                  <span className="team-decision-question">{item.question}</span>
                </legend>
                {item.why_needed ? (
                  <p className="team-decision-why">Why now: {item.why_needed}</p>
                ) : null}
                {recommended ? (
                  <p className="team-decision-recommended">Recommended: {recommended.label}</p>
                ) : null}
                {options.length ? (
                  <div className="team-decision-options">
                    {options.map((option) => (
                      <label className="team-decision-option" key={option.id}>
                        <input
                          type="radio"
                          name={`decision-${item.id}`}
                          value={option.id}
                          checked={answers[item.id] === option.id}
                          onChange={(event) => setAnswers((current) => ({
                            ...current,
                            [item.id]: event.target.value
                          }))}
                        />
                        <span>
                          <strong>{option.label}</strong>
                          {option.impact ? <small>{option.impact}</small> : null}
                        </span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <label className="team-decision-freeform">
                    <span className="mono">YOUR ANSWER</span>
                    <textarea
                      aria-label={`Answer for ${item.id}`}
                      value={answers[item.id] || ""}
                      onChange={(event) => setAnswers((current) => ({
                        ...current,
                        [item.id]: event.target.value
                      }))}
                    />
                  </label>
                )}
              </fieldset>
            );
          })}
        </div>
        <div className="team-decision-actions">
          <span className="mono">{Object.keys(answers).filter((id) => answers[id]?.trim()).length}/{items.length} answered</span>
          <Button type="submit" size="btn-sm" variant="primary" disabled={!complete || submitting || !onSubmit}>
            {submitting ? "RESUMING..." : "ANSWER & RESUME"}
          </Button>
        </div>
      </form>
    </section>
  );
}

export function TeamRunDetail({
  detail, documents = [], delivery = null, deliveryLoading = false,
  loading = false, loadError = false,
  onLoadDocument, onAddWork, onResume, onAnswerDecision,
  onRetryTask, onCancel, onTriggerCycle, onRetryAuto, onContinueAuto, onRestartAuto,
  onRefreshDelivery, onCommitDelivery, onApplyDelivery,
  onResolveDeliveryConflict, onContinueDelivery, onCancelDeliveryConflicts
}) {
  const [workInput, setWorkInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cycleInstruction, setCycleInstruction] = useState("");
  const [triggeringCycle, setTriggeringCycle] = useState(false);
  const [autoAction, setAutoAction] = useState(null);
  const [resuming, setResuming] = useState(false);
  const [retryingTaskId, setRetryingTaskId] = useState(null);
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [previewDoc, setPreviewDoc] = useState(null);
  const [activeTab, setActiveTab] = useState("overview");
  const [showAllTasks, setShowAllTasks] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const run = detail?.run;
  const nextRunAt = detail?.activeAutoSeries?.next_run_at || null;
  const [countdownNow, setCountdownNow] = useState(() => Date.now());

  useEffect(() => {
    if (!nextRunAt) return undefined;
    const deadline = new Date(nextRunAt).getTime();
    const initialNow = Date.now();
    setCountdownNow(initialNow);
    if (initialNow >= deadline) return undefined;

    let timerId = window.setInterval(() => {
      const now = Date.now();
      setCountdownNow(now);
      if (now >= deadline) {
        window.clearInterval(timerId);
        timerId = null;
      }
    }, 1000);
    return () => {
      if (timerId !== null) window.clearInterval(timerId);
    };
  }, [nextRunAt]);

  const hasRunningAgent = (detail?.agents || []).some(
    (agent) => effectiveChildStatus(agent.status, run?.status) === "running"
  );
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!hasRunningAgent) return undefined;
    setNowMs(Date.now());
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasRunningAgent]);

  if (loading) {
    return (
      <div className="team-run-empty" role="status" aria-live="polite">
        <LoaderCube label="LOADING TEAM RUN" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="team-run-empty mono" role="status">
        Team run could not be loaded. Use Retry request above.
      </div>
    );
  }

  if (!run) {
    return <div className="team-run-empty mono">No team run selected.</div>;
  }

  const agents = (detail.agents || []).map((agent) => ({
    ...agent,
    status: effectiveChildStatus(agent.status, run.status)
  }));
  const tasks = (detail.tasks || []).map((task) => ({
    ...task,
    status: effectiveChildStatus(task.status, run.status),
    error_message: task.error_message || (
      task.status === "in_progress" && run.status === "failed" ? run.error_message : null
    )
  }));
  const messages = detail.messages || [];
  const cycles = [...(detail.cycles || [])].sort((left, right) => right.sequence - left.sequence);
  const currentCycle = cycles[0] || null;
  const previousCycle = cycles.find(
    (cycle) => [
      "completed",
      "completed_with_failures",
      "blocked",
      "failed",
      "canceled"
    ].includes(cycle.status)
  );
  const policyStatus = detail.policyStatus || "ready";
  const activeAutoSeries = detail.activeAutoSeries;
  const nextRunCountdownSeconds = nextRunAt
    ? Math.max(0, Math.ceil((new Date(nextRunAt).getTime() - countdownNow) / 1000))
    : null;
  const leader = findAgent(agents, run.leader_agent_id);
  const reports = newestFirst(messages.filter((message) => message.kind === "agent_output"));
  const activity = newestFirst(messages.filter((message) => message.kind !== "acceptance_review"));
  const handoffs = buildHandoffs(messages);
  const reportsByTask = groupReportsByTask(messages);
  const acceptanceReviewsByTask = groupAcceptanceReviewsByTask(messages);
  const tasksHaveCycleIds = tasks.some((task) => task.cycle_id);
  const currentCycleTasks = currentCycle && tasksHaveCycleIds
    ? tasks.filter((task) => task.cycle_id === currentCycle.id)
    : tasks;
  const visibleTasks = showAllTasks ? tasks : currentCycleTasks;
  const taskTitlesById = new Map(tasks.map((task) => [task.id, task.title]));
  const decisionRequest = detail.decisionRequest;
  const decisionCycle = decisionRequest?.cycle_id
    ? cycles.find((cycle) => cycle.id === decisionRequest.cycle_id)
    : null;
  const canAnswerDecision = Boolean(
    run.status === "waiting_for_user"
      && decisionRequest?.status === "awaiting_user"
      && (!decisionRequest.cycle_id || decisionCycle?.status === "waiting_for_user")
  );
  const selectedTask = selectedTaskId ? findTask(tasks, selectedTaskId) : null;
  const selectedTaskReports = selectedTask ? (reportsByTask.get(selectedTask.id) || []) : [];
  const selectedTaskReviews = selectedTask
    ? newestFirst(acceptanceReviewsByTask.get(selectedTask.id) || [])
    : [];
  const canRetrySelectedTask = Boolean(
    onRetryTask
      && selectedTask?.status === "failed"
      && ["completed_with_failures", "failed"].includes(run.status)
  );
  const canAddWork = Boolean(
    onAddWork
      && run.run_mode === "plan_and_execute"
      && run.lifecycle_mode !== "continuous"
      && run.status !== "draft"
      && run.status !== "interrupted"
      && run.status !== "waiting_for_user"
  );
  const canResume = Boolean(onResume && run.status === "interrupted");
  const canCancel = Boolean(
    onCancel && ["planning", "running", "summarizing", "waiting_for_user"].includes(run.status)
  );

  return (
    <section className="team-run-detail" aria-label="Team run detail">
      <header className="team-run-hero">
        <div className="team-run-hero-main">
          <div className="team-run-detail-id-row">
            <span className="mono team-run-detail-id">TEAM RUN · {run.id.slice(0, 8)}</span>
            <StatusBadge kind={run.status} />
          </div>
          <h1 className="headline team-run-detail-goal">
            {run.team_name ? `${run.team_name} · ${run.id.slice(0, 8)}` : (run.goal || run.id.slice(0, 8))}
          </h1>
          {run.goal ? <div className="team-run-base-objective">BASE OBJECTIVE · {run.goal}</div> : null}
          <div className="team-run-hero-summary mono">
            <span>{String(run.execution_policy || run.lifecycle_mode || "standard").toUpperCase()}</span>
            <span>LEAD · {leader?.name || "-"}</span>
            <span>{currentCycle ? `CYCLE #${currentCycle.sequence}` : "NO CYCLE"}</span>
            <span>{tasks.filter((task) => OPEN_TASK_STATUSES.has(task.status)).length} OPEN TASKS</span>
          </div>
        </div>
        <div className="team-run-hero-actions">
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
          {canCancel ? (
            <Button
              size="btn-sm"
              variant="destructive"
              disabled={canceling}
              onClick={async () => {
                setCanceling(true);
                try {
                  await onCancel();
                } finally {
                  setCanceling(false);
                }
              }}
            >
              {canceling ? "Stopping..." : "Stop run"}
            </Button>
          ) : null}
        </div>
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

      <details className="team-run-details">
        <summary className="mono">RUN DETAILS</summary>
        <div className="team-run-meta">
          <div className="team-run-meta-cell">
            <div className="mono team-run-meta-k">ID</div>
            <div className="mono team-run-meta-v" title={run.id}>{run.id}</div>
          </div>
          <div className="team-run-meta-cell">
            <div className="mono team-run-meta-k">MODE</div>
            <div className="mono team-run-meta-v">{run.run_mode}</div>
          </div>
          <div className="team-run-meta-cell">
            <div className="mono team-run-meta-k">LIFECYCLE</div>
            <div className="mono team-run-meta-v">{run.lifecycle_mode || "standard"}</div>
          </div>
          <div className="team-run-meta-cell">
            <div className="mono team-run-meta-k">WORKERS</div>
            <div className="mono team-run-meta-v">{run.max_workers ?? "-"}</div>
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
      </details>

      <DeliveryPanel
        key={run.id}
        runId={run.id}
        delivery={delivery}
        loading={deliveryLoading}
        onRefresh={onRefreshDelivery}
        onCommit={onCommitDelivery}
        onApply={onApplyDelivery}
        onResolve={onResolveDeliveryConflict}
        onContinue={onContinueDelivery}
        onCancelConflicts={onCancelDeliveryConflicts}
      />

      {run.lifecycle_mode === "continuous" ? (
        <details className="team-policy-panel" role="region" aria-label="Cycle policy" open>
          <summary className="team-policy-summary">
            <span className="mono team-section-label">
              {String(run.execution_policy || "triggered").toUpperCase()}
              {" · "}
              {String(policyStatus).replaceAll("_", " ").toUpperCase()}
            </span>
            <span className="team-section-rule" />
          </summary>
          <div className="team-policy-body">

          {run.execution_policy === "triggered" ? (
            <>
              <details className="team-previous-cycle">
                <summary className="mono">
                  {previousCycle ? `PREVIOUS CYCLE #${previousCycle.sequence}` : "NO SETTLED CYCLE"}
                </summary>
                <div>
                  <MarkdownContent
                    source={previousCycle?.summary || "No previous Cycle summary."}
                    pathRegistration={false}
                  />
                </div>
              </details>
              <form
                className="team-cycle-trigger"
                onSubmit={async (event) => {
                  event.preventDefault();
                  const instruction = cycleInstruction.trim();
                  if (!instruction || !onTriggerCycle || triggeringCycle) return;
                  setTriggeringCycle(true);
                  try {
                    const accepted = await onTriggerCycle({
                      instruction,
                      previous_cycle_id: previousCycle?.id || null
                    });
                    if (accepted) setCycleInstruction("");
                  } finally {
                    setTriggeringCycle(false);
                  }
                }}
              >
                <label className="schedule-field">
                  <span className="schedule-field-label">Cycle instruction</span>
                  <textarea
                    className="schedule-textarea"
                    aria-label="Cycle instruction"
                    value={cycleInstruction}
                    onChange={(event) => setCycleInstruction(event.target.value)}
                  />
                </label>
                <Button
                  type="submit"
                  size="btn-sm"
                  variant="primary"
                  disabled={!cycleInstruction.trim() || triggeringCycle || !onTriggerCycle}
                >
                  {triggeringCycle ? "Triggering..." : "Trigger cycle"}
                </Button>
              </form>
            </>
          ) : null}

          {run.execution_policy === "auto" ? (
            <div className="team-auto-progress">
              {activeAutoSeries ? (
                <span className="mono">
                  {activeAutoSeries.settled_slots || 0} / {activeAutoSeries.target_slots || 0} SETTLED
                </span>
              ) : null}
              {nextRunCountdownSeconds !== null ? (
                <span className="mono" title={fmtDateTime(nextRunAt)}>
                  NEXT · {nextRunCountdownSeconds}s
                </span>
              ) : null}
              {policyStatus === "paused_failure" && activeAutoSeries ? (
                <>
                  <Button
                    size="btn-sm"
                    variant="primary"
                    disabled={Boolean(autoAction) || !onContinueAuto}
                    onClick={async () => {
                      if (!onContinueAuto || autoAction) return;
                      setAutoAction("continue");
                      try {
                        await onContinueAuto(activeAutoSeries.id);
                      } finally {
                        setAutoAction(null);
                      }
                    }}
                  >
                    {autoAction === "continue" ? "Continuing..." : "Continue"}
                  </Button>
                  <Button
                    size="btn-sm"
                    variant="primary"
                    disabled={Boolean(autoAction) || !onRetryAuto}
                    onClick={async () => {
                      if (!onRetryAuto || autoAction) return;
                      setAutoAction("retry");
                      try {
                        await onRetryAuto(activeAutoSeries.id);
                      } finally {
                        setAutoAction(null);
                      }
                    }}
                  >
                    {autoAction === "retry" ? "Retrying..." : "Retry"}
                  </Button>
                </>
              ) : null}
              {["completed", "auto_completed"].includes(policyStatus) ? (
                <Button
                  size="btn-sm"
                  variant="primary"
                  disabled={Boolean(autoAction) || !onRestartAuto}
                  onClick={async () => {
                    if (!onRestartAuto || autoAction) return;
                    setAutoAction("restart");
                    try {
                      await onRestartAuto();
                    } finally {
                      setAutoAction(null);
                    }
                  }}
                >
                  {autoAction === "restart" ? "Restarting..." : "Restart"}
                </Button>
              ) : null}
            </div>
          ) : null}

          <span className="team-queue-count mono">QUEUE · {detail.queueCount || 0}</span>
          {detail.activeRequest ? (
            <span className="team-queue-count mono">
              ACTIVE REQUEST · {detail.activeRequest.id}
            </span>
          ) : null}
          </div>
        </details>
      ) : null}

      {run.status === "interrupted" ? (
        <div className="team-interrupted-banner" role="status">
          <span className="headline team-interrupted-title">Run interrupted</span>
          <span className="team-interrupted-copy">Running work was returned to Pending. Resume when you are ready.</span>
        </div>
      ) : null}

      {run.status === "waiting_for_user" ? (
        canAnswerDecision ? (
          <DecisionRequestPanel
            key={`${decisionRequest.id}:${decisionRequest.revision}`}
            request={decisionRequest}
            onSubmit={onAnswerDecision}
          />
        ) : (
          <div className="team-decision-unavailable" role="status">
            Decision request is unavailable. Refresh this run.
          </div>
        )
      ) : null}

      <div className="team-detail-tabs" role="tablist" aria-label="Run detail views">
        {DETAIL_TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={activeTab === key}
            className={`team-detail-tab${activeTab === key ? " active" : ""}`}
            onClick={() => setActiveTab(key)}
          >
            <span>{label}</span>
            {key === "tasks" && tasks.length ? (
              <span className="team-detail-tab-badge mono">{tasks.length}</span>
            ) : null}
            {key === "history" && cycles.length ? (
              <span className="team-detail-tab-badge mono">{cycles.length}</span>
            ) : null}
            {key === "files" && documents.length ? (
              <span className="team-detail-tab-badge mono">{documents.length}</span>
            ) : null}
          </button>
        ))}
      </div>

      {activeTab === "history" ? (
        <section className="team-cycles team-tab-panel" aria-label="Team Run cycles">
          <div className="team-section-head">
            <span className="mono team-section-label">Cycle History</span>
            <span className="mono team-section-count">{cycles.length}</span>
            <span className="team-section-rule" />
          </div>
          {cycles.length ? (
            <div className="team-cycle-list">
              {cycles.map((cycle, index) => (
                <details className="team-cycle" key={cycle.id} open={index === 0 || undefined}>
                  <summary className="team-cycle-head">
                    <span className="mono team-cycle-sequence">CYCLE #{cycle.sequence}</span>
                    <span className="mono team-cycle-compact-meta">
                      {String(cycle.source_type || "manual").replaceAll("_", " ")} · {cycle.rounds_used}/{cycle.rounds_budget} ROUNDS
                    </span>
                    <StatusBadge kind={cycle.status} />
                  </summary>
                  <div className="team-cycle-content">
                    <div className="mono team-cycle-lineage" title={cycle.source_id || ""}>
                      {cycle.source_type || "manual"} · {cycle.source_id || cycle.id}
                    </div>
                    <div className="mono team-cycle-budget">
                      ROUNDS · {cycle.rounds_used}/{cycle.rounds_budget}
                      {cycle.finished_at ? ` · ${fmtDateTime(cycle.finished_at)}` : ""}
                    </div>
                    {cycle.summary ? (
                      <div className="team-cycle-summary">
                        <MarkdownContent source={cycle.summary} pathRegistration={false} />
                      </div>
                    ) : null}
                    {cycle.error_message ? <div className="hook-row-error mono">{cycle.error_message}</div> : null}
                  </div>
                </details>
              ))}
            </div>
          ) : (
            <div className="team-task-empty mono">No Cycle history yet.</div>
          )}
        </section>
      ) : null}

      {activeTab === "overview" ? (
        <>
          {run.summary ? (
            <div className="team-final-summary team-overview-summary">
              <div className="mono team-final-summary-head">LATEST SUMMARY · {leader?.name || "TEAM"}</div>
              <div className="team-final-summary-body">
                <MarkdownContent source={run.summary} pathRegistration={false} />
              </div>
            </div>
          ) : currentCycle?.summary ? (
            <div className="team-final-summary team-overview-summary">
              <div className="mono team-final-summary-head">CURRENT CYCLE · #{currentCycle.sequence}</div>
              <div className="team-final-summary-body">
                <MarkdownContent source={currentCycle.summary} pathRegistration={false} />
              </div>
            </div>
          ) : null}

          <div className="team-section-head team-section-toolbar">
            <span className="mono team-section-label">Agent Sessions</span>
            <span className="mono team-section-count">{agents.length}</span>
            <span className="team-section-rule" />
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
                    {(() => {
                      const work = currentWork(agent, currentTask, run.status);
                      const seconds = elapsedSeconds(work.startedAt, nowMs);
                      return (
                        <div className="team-lane-task">
                          <span className="team-lane-task-title">{work.title}</span>
                          {seconds === null ? null : (
                            <>
                              {" "}
                              <span className="mono team-lane-elapsed">{fmtElapsed(seconds)} 경과</span>
                            </>
                          )}
                        </div>
                      );
                    })()}
                    <details className="team-lane-runtime">
                      <summary className="mono">RUNTIME</summary>
                      <div className="mono team-lane-snapshot">{agent.backend}/{agent.model}</div>
                    </details>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      ) : null}

      {activeTab === "tasks" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Tasks">
          <div className="team-section-head team-section-toolbar">
            <span className="mono team-section-label">Task Board</span>
            <span className="mono team-section-count">
              {showAllTasks ? `${tasks.length} ALL CYCLES` : `${visibleTasks.length} CURRENT CYCLE`}
            </span>
            <BuildEvidenceSummary summary={detail.build_evidence_summary} />
            <span className="team-section-rule" />
            {currentCycle && tasksHaveCycleIds ? (
              <Button size="btn-sm" onClick={() => setShowAllTasks((value) => !value)}>
                {showAllTasks ? "Current cycle" : "All cycles"}
              </Button>
            ) : null}
          </div>
          <div className="team-task-board">
            {TASK_STATUS_GROUPS.map((group) => {
              const columnTasks = visibleTasks.filter(
                (task) => groupForTaskStatus(task.status) === group.key
              );
              return (
                <div className="team-task-column" key={group.key}>
                  <div className="team-task-column-head mono">
                    <span>{group.label}</span>
                    <span>{columnTasks.length}</span>
                  </div>
                  <div className="team-task-column-body">
                    {columnTasks.length ? (
                      columnTasks.map((task) => {
                        const taskReports = reportsByTask.get(task.id) || [];
                        return (
                          <TeamTaskCard
                            key={task.id}
                            task={task}
                            owner={findAgent(agents, task.owner_agent_id)}
                            prerequisiteTitles={(task.depends_on_task_ids || [])
                              .map((taskId) => taskTitlesById.get(taskId) || taskId)}
                            fileCount={taskFileCount(taskReports)}
                            reportCount={taskReports.length}
                            onOpen={() => setSelectedTaskId(task.id)}
                          />
                        );
                      })
                    ) : (
                      <div className="team-task-empty mono">-</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {activeTab === "overview" ? (
        <div className="team-overview-disclosures" role="tabpanel" aria-label="Overview">
          <details
            className="team-overview-disclosure"
            open={TERMINAL_STATUSES.includes(run.status)}
          >
            <summary className="mono">AGENT REPORTS <span>{reports.length}</span></summary>
            <div className="team-reports">
              {reports.length ? reports.map((message) => {
                const sender = findAgent(agents, message.sender_agent_id);
                const avatar = sender?.persona_snapshot?.avatar;
                return (
                  <article className="team-agent-report" key={message.id}>
                    <div className="team-agent-report-head">
                      {avatar ? (
                        <img className="team-agent-report-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
                      ) : (
                        <span className="team-agent-report-avatar team-doc-avatar-initials mono">{initials(sender?.name)}</span>
                      )}
                      <span className="mono team-agent-report-owner">{sender ? sender.name : "Agent"}</span>
                      <span className="team-agent-report-time mono">{fmtDateTime(message.created_at)}</span>
                    </div>
                    <div className="team-agent-report-body">
                      <MarkdownContent source={message.content} pathRegistration={false} />
                    </div>
                  </article>
                );
              }) : <div className="team-task-empty mono">No agent reports yet.</div>}
            </div>
          </details>

          <details className="team-overview-disclosure">
            <summary className="mono">SHARED / HANDOFFS <span>{handoffs.length}</span></summary>
            {handoffs.length ? (
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
                        <div className="team-handoff-a team-handoff-unanswered mono">awaiting answer</div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : <div className="team-task-empty mono">No handoffs yet.</div>}
          </details>
        </div>
      ) : null}

      {activeTab === "activity" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Live activity">
          <div className="timeline">
            {activity.map((message) => {
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
      ) : null}

      {activeTab === "files" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Files">
          <div className="team-docs-list">
            {documents.length ? documents.map((doc) => {
              const label = documentLabel(doc.path);
              return (
              <button
                key={doc.path}
                type="button"
                className="team-docs-list-row"
                aria-label={`Preview ${doc.path}`}
                disabled={!doc.previewable || !onLoadDocument}
                onClick={async () => {
                  if (!onLoadDocument) return;
                  try {
                    const loaded = await onLoadDocument(doc.path);
                    setPreviewDoc(loaded || { ...doc, previewable: false, reason: "load failed" });
                  } catch (_error) {
                    setPreviewDoc({ ...doc, previewable: false, reason: "load failed" });
                  }
                }}
              >
                <span className="team-docs-label">
                  <span className="mono team-docs-name">{label.name}</span>
                  {label.parent ? <span className="mono team-docs-parent">{label.parent}</span> : null}
                </span>
                <span className="mono team-docs-kind">{doc.kind}</span>
              </button>
              );
            }) : <div className="team-task-empty mono">No documents in the workspace yet.</div>}
          </div>
        </div>
      ) : null}

      <DocumentPreview open={Boolean(previewDoc)} doc={previewDoc} onClose={() => setPreviewDoc(null)} />

      <TaskDetailDialog
        task={selectedTask}
        reports={selectedTaskReports}
        reviews={selectedTaskReviews}
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
