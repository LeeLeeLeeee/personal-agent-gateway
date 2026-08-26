import { useEffect, useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { LoaderCube } from "../../molecules/LoaderCube/index.jsx";
import { TeamTaskCard } from "../../molecules/TeamTaskCard/index.jsx";
import { BuildEvidence, BuildEvidenceSummary } from "./BuildEvidence.jsx";
import { ContestPanel } from "./ContestPanel.jsx";
import { PlanNegotiation } from "./PlanNegotiation.jsx";
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
  ["run", "RUN"],
  ["tasks", "TASK"],
  ["config", "CONFIGURATION"],
  ["history", "HISTORY"]
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
  if (["interrupted", "waiting_for_user", "paused"].includes(status)) return -1;
  return index < 0 ? 0 : index;
}

// Shared by the "정지 요청됨" banner and the ask-a-question dialog's waiting
// state -- both are describing the exact same wait, so the copy has to match.
function pauseWaitCopy(status) {
  return "돌고 있는 작업이 끝나면 멈춥니다."
    + (status === "planning" ? " 계획 단계라 계획이 끝날 때까지 걸립니다." : "");
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
            {/* attested_only is verified_count == 0 -- the gate ran no check.
                It is NOT "the worker vouched for it": a task whose only required
                verification came back checked: false sets this flag while the
                worker vouched for nothing. The badge names what the gate did. */}
            {acceptanceResult.evidence?.attested_only ? (
              <span className="mono team-task-attested">NO GATE CHECK</span>
            ) : null}
            {reasonCode ? (
              <div className="team-task-dialog-copy">
                <span className="mono">{reasonCode}</span>
              </div>
            ) : null}
            <div className="team-task-dialog-copy">
              <div className="mono team-task-dialog-label">REQUIRED OUTPUTS</div>
              {(acceptance.required_outputs || []).length ? (
                <ul>
                  {acceptance.required_outputs.map((path) => (
                    <li key={path}><span className="mono">{path}</span></li>
                  ))}
                </ul>
              ) : <div>None</div>}
              <div className="mono team-task-dialog-label">VERIFICATIONS</div>
              {(acceptance.required_verifications || []).length ? (
                <ul>
                  {acceptance.required_verifications.map((item) => {
                    const name = typeof item === "string" ? item : item.name;
                    const check = typeof item === "string" ? null : item.check;
                    const verification = verificationByName.get(name);
                    const serverEntry = verificationEvidence[name];
                    const mode = serverEntry?.mode;
                    const verified = mode === "verified";
                    // An unchecked verification must read the gate's recorded
                    // status, not the worker's null one: `status || "missing"`
                    // below would print MISSING, which until now meant the one
                    // thing this is not -- a verification the worker never
                    // reported at all. Collapsing those two is the confusion
                    // this whole feature exists to end.
                    const status =
                      verified || mode === "unverified"
                        ? serverEntry.status
                        : verification?.status;
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
        aria-label="일감 추가"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className="mono">ADD WORK</span>
          <button type="button" className="modal-close" aria-label="일감 추가 닫기" disabled={submitting} onClick={onClose}>×</button>
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

function compactTokens(count) {
  // 토큰은 만 단위로 가는데 자릿수를 다 적으면 진행 단계 줄이 숫자로 덮인다.
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(count);
}

function useElapsedSeconds(active) {
  // 진행 이벤트가 아직 하나도 안 온 구간에도 살아 있다는 신호가 필요하다.
  // 리드가 파일을 읽는 동안은 답이 한 글자도 안 나오는데, 그 구간이 보통
  // 가장 길다.
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return undefined;
    }
    const started = Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return seconds;
}

function toExchanges(messages) {
  // 서버는 질문과 답을 한 쌍으로 적는다. 그래도 짝이 안 맞는 행을 버리지
  // 않는 이유: 기록을 화면에서 삼키면 사용자는 답이 사라졌다고 읽는다.
  const exchanges = [];
  for (const message of messages) {
    if (message.kind === "user_question") {
      exchanges.push({ id: message.id, question: message, answer: null });
      continue;
    }
    const pending = exchanges[exchanges.length - 1];
    if (pending && !pending.answer) pending.answer = message;
    else exchanges.push({ id: message.id, question: null, answer: message });
  }
  return exchanges;
}

function QuestionExchange({ exchange }) {
  return (
    <div className="team-question-exchange">
      {exchange.question ? (
        <div className="team-question-history-question">{exchange.question.content}</div>
      ) : null}
      {exchange.answer ? (
        // 리드의 답은 거의 항상 제목과 목록이 있는 마크다운이다. 날것으로
        // 두면 "## " 와 "- " 가 그대로 보여 읽는 사람이 직접 해독해야 한다.
        <div className="team-question-history-answer">
          <MarkdownContent source={exchange.answer.content} pathRegistration={false} />
        </div>
      ) : null}
    </div>
  );
}

function AskQuestionDialog({
  open, awaitingPause, runStatus, history, value, submitting, failed,
  pauseFailed, onRetryPause, progress, onChange, onClose, onSubmit
}) {
  const elapsed = useElapsedSeconds(Boolean(open && submitting));
  const [olderShown, setOlderShown] = useState(false);
  useEffect(() => {
    // 닫았다 열면 다시 접힌다. 대화가 길어진 런에서 열 때마다 전부 펼쳐져
    // 있으면 방금 주고받은 것을 찾기 위해 매번 스크롤해야 한다.
    if (!open) setOlderShown(false);
  }, [open]);
  if (!open) return null;
  const inputDisabled = submitting || awaitingPause;
  const showProgress = submitting || progress?.activity || progress?.answerPartial;
  const exchanges = toExchanges(history);
  const latest = exchanges.length ? exchanges[exchanges.length - 1] : null;
  // 최신순. 바로 위에 방금 것이 있으므로, 이어서 거슬러 올라가는 순서가 맞다.
  const older = exchanges.slice(0, -1).reverse();

  return (
    <div className="modal-backdrop" onClick={submitting ? undefined : onClose}>
      <div
        className="modal-card team-question-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="물어보기"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className="mono">QUESTION</span>
          <button type="button" className="modal-close" aria-label="질문 닫기" disabled={submitting} onClick={onClose}>×</button>
        </div>
        <div className="team-question-dialog-body">
          {/* 다음에 물을 것이 맨 위에 있어야 한다. 기록을 위에 쌓으면 대화가
              길어질수록 입력칸이 아래로 밀려 매번 스크롤해야 한다. */}
          <label className="mono team-task-dialog-label" htmlFor="team-question-input">QUESTION</label>
          <textarea
            id="team-question-input"
            className="team-question-input"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="리드에게 물어볼 질문을 적어주세요."
            disabled={inputDisabled}
            autoFocus
          />
          <div className="team-question-dialog-actions">
            <Button size="btn-sm" disabled={submitting} onClick={onClose}>닫기</Button>
            <Button size="btn-sm" variant="primary" disabled={inputDisabled || !value.trim()} onClick={onSubmit}>
              {submitting ? "보내는 중..." : "보내기"}
            </Button>
          </div>
          {awaitingPause && pauseFailed ? (
            // 정지 요청이 실패하면 팀은 계속 돌고 있으므로 입력은 계속 막혀
            // 있어야 한다. 그대로 두면 대화상자는 영영 기다리는 모습으로
            // 남고 출구가 닫는 것뿐이므로, 여기서 다시 걸 수 있게 한다.
            <div className="team-question-pause-failed mono" role="status">
              <span>정지를 요청하지 못했습니다</span>
              <Button size="btn-sm" onClick={onRetryPause}>정지 다시 요청</Button>
            </div>
          ) : awaitingPause ? (
            <div className="team-question-waiting mono" role="status">{pauseWaitCopy(runStatus)}</div>
          ) : null}
          {showProgress ? (
            <div className="team-question-progress" role="status">
              {submitting ? (
                <div className="team-question-progress-head mono">
                  <span>리드가 파일을 읽고 답을 쓰는 중입니다. 몇 분 걸릴 수 있습니다.</span>
                  <span className="team-question-progress-elapsed">{elapsed}초</span>
                </div>
              ) : null}
              {progress?.activity ? (
                <div className="team-question-progress-activity mono">{progress.activity}</div>
              ) : null}
              {progress?.answerPartial ? (
                <div className="team-question-progress-answer">{progress.answerPartial}</div>
              ) : null}
            </div>
          ) : null}
          {failed ? <div className="team-question-error mono">답을 받지 못했습니다</div> : null}
          {latest ? (
            <div className="team-question-history">
              <QuestionExchange exchange={latest} />
            </div>
          ) : null}
          {older.length ? (
            <div className="team-question-older">
              {olderShown ? (
                older.map((item) => <QuestionExchange key={item.id} exchange={item} />)
              ) : (
                <Button size="btn-sm" onClick={() => setOlderShown(true)}>
                  {`이전 대화 ${older.length}개 불러오기`}
                </Button>
              )}
            </div>
          ) : null}
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
  onPause, onAskQuestion, questionProgress = null,
  onRefreshDelivery, onCommitDelivery, onApplyDelivery,
  onResolveDeliveryConflict, onContinueDelivery, onCancelDeliveryConflicts,
  onContestPlan, onOpenSettings, onViewOutputs
}) {
  const [workInput, setWorkInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cycleInstruction, setCycleInstruction] = useState("");
  const [triggeringCycle, setTriggeringCycle] = useState(false);
  const [autoAction, setAutoAction] = useState(null);
  const [resuming, setResuming] = useState(false);
  const [retryingTaskId, setRetryingTaskId] = useState(null);
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [questionDialogOpen, setQuestionDialogOpen] = useState(false);
  const [questionInput, setQuestionInput] = useState("");
  const [askingQuestion, setAskingQuestion] = useState(false);
  const [askQuestionFailed, setAskQuestionFailed] = useState(false);
  const [pauseFailed, setPauseFailed] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [previewDoc, setPreviewDoc] = useState(null);
  // 물어보기와 일감 추가가 있는 자리라 가장 자주 쓴다.
  const [activeTab, setActiveTab] = useState("run");
  // Cycle 기록과 에이전트 보고는 성격이 다른 두 기록이다. 세로로 쌓으면
  // 사이클이 많은 런에서 보고를 보려고 한참 스크롤해야 한다.
  const [historyTab, setHistoryTab] = useState("cycles");
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
  const planRevisions = detail.planRevisions || [];
  const contests = detail.contests || [];
  const hasPlanReview = Boolean(planRevisions.length || contests.length);
  const cycles = [...(detail.cycles || [])].sort((left, right) => right.sequence - left.sequence);
  // 서버가 잘리지 않은 전체 일감으로 계산해 보낸다. 화면에서 세면 목록이
  // 잘린 큰 런에서 숫자가 조용히 작아진다.
  // elapsedSeconds 는 기준 시각을 반드시 받는다. 안 넘기면 undefined 로
  // 빼면서 NaN 이 되고, 화면에는 "NaN:NaN" 이 찍힌다.
  const nowSince = (startedAt) => elapsedSeconds(startedAt, nowMs);
  const usageTotals = detail.usage_totals || null;
  // 캐시는 기록분과 조회분을 합쳐 한 숫자로 보여준다. 둘을 나눠 적으면 줄이
  // 길어지는데, 이 자리에서 궁금한 것은 "캐시가 얼마나 먹었나" 하나다.
  const usageCache = usageTotals
    ? (usageTotals.cache_creation_input_tokens || 0) + (usageTotals.cache_read_input_tokens || 0)
    : 0;
  const showUsage = Boolean(
    usageTotals
    && ((usageTotals.input_tokens || 0) + (usageTotals.output_tokens || 0) + usageCache > 0)
  );
  const planShape = detail.plan_shape || null;
  // 하나짜리는 나눈 것이 아니므로 판정할 것이 없다.
  const showPlanShape = Boolean(planShape && planShape.task_count > 1);
  // 일감 수와 최장 사슬이 같으면 전부 차례로 지나야 한다는 뜻이다 -- 나눈
  // 만큼 인수인계 비용은 치르는데 끝나는 시각은 한 명이 하는 것과 같다.
  const splitBoughtNothing = Boolean(
    showPlanShape && planShape.longest_chain >= planShape.task_count
  );
  const currentCycle = cycles[0] || null;
  const currentInstruction = run.current_objective
    || currentCycle?.instruction
    || currentCycle?.effective_instruction?.split("\n\nPREVIOUS CYCLE CONTEXT\n", 1)[0]
    || run.goal
    || "No request submitted yet";
  const distinctBaseObjective = run.goal && run.goal !== currentInstruction ? run.goal : null;
  const focusTask = tasks.find((task) => task.status === "in_progress")
    || tasks.find((task) => OPEN_TASK_STATUSES.has(task.status))
    || null;
  // 지금 실제로 돌고 있는 것 전부. 동시에 최대 세 개까지 돌 수 있는데
  // 예전에는 헤더가 "NOW · 제목" 하나만 보여줘서 나머지가 화면 어디에도 없었다.
  const runningTasks = tasks.filter((task) => task.status === "in_progress");
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
  const selectedTaskRetry = selectedTask
    ? tasks.find((task) => task.retry_of_task_id === selectedTask.id)
    : null;
  const canRetrySelectedTask = Boolean(
    onRetryTask
      && selectedTask?.status === "failed"
      && !selectedTaskRetry
      && ["completed_with_failures", "failed"].includes(run.status)
  );
  const canAddWork = Boolean(
    onAddWork
      && run.run_mode === "plan_and_execute"
      && run.lifecycle_mode !== "continuous"
      && run.status !== "draft"
      && run.status !== "interrupted"
      && run.status !== "waiting_for_user"
      && run.status !== "paused"
  );
  const canResume = Boolean(onResume && ["interrupted", "paused"].includes(run.status));
  const canCancel = Boolean(
    onCancel && ["planning", "running", "summarizing", "waiting_for_user", "paused"].includes(run.status)
  );
  const isExecuting = ["planning", "running", "summarizing"].includes(run.status);
  // 끝난 런도 뺄 이유가 없다. API 로 만든 팀런은 모두 continuous 이고,
  // 그런 런에서 사이클 사이와 마지막 사이클 뒤의 대기 상태가 바로
  // completed / completed_with_failures 다 -- 설계가 "정지 단계를 건너뛰고
  // 바로 질문"이라고 말하는 자리가 여기서 통째로 막혀 있었다. 정지 분기는
  // isExecuting 으로 따로 걸려 있으므로 다른 것은 그대로다.
  const canAskQuestion = Boolean(onAskQuestion);
  // 서버가 가진 기록만 그린다. 보낸 것을 화면에서 덧붙이면 다음 갱신 때
  // 저장된 행과 겹쳐 같은 문답이 두 번 나온다.
  const questionHistory = messages.filter(
    (message) => message.kind === "user_question" || message.kind === "lead_answer"
  );
  const retriedTaskIds = new Set(
    tasks.map((task) => task.retry_of_task_id).filter(Boolean)
  );
  const failedTasks = newestFirst(tasks.filter((task) => task.status === "failed"));
  const failureTask = failedTasks.find((task) => !retriedTaskIds.has(task.id))
    || failedTasks[0]
    || null;
  const failureRetryTask = failureTask
    ? tasks.find((task) => task.retry_of_task_id === failureTask.id)
    : null;
  const showFailurePanel = ["failed", "completed_with_failures"].includes(run.status)
    || Boolean(run.status === "interrupted" && failureRetryTask);
  const failureCause = failureTask?.error_message
    ? `${failureTask.title}: ${failureTask.error_message}`
    : run.error_message || "실패 원인이 기록되지 않았습니다. Tasks와 Activity에서 진단 정보를 확인하세요.";
  const runtimeFailure = /(?:capabilit|provider|model).*unavailable/i.test(failureCause);
  const failureAgent = failureTask ? findAgent(agents, failureTask.owner_agent_id) : null;
  const completedTaskCount = tasks.filter((task) => task.status === "completed").length;
  const canRetryFailure = Boolean(
    onRetryTask
      && failureTask
      && !failureRetryTask
      && !runtimeFailure
      && ["failed", "completed_with_failures"].includes(run.status)
  );
  const canResumeFailure = Boolean(onResume && run.status === "interrupted" && failureRetryTask);

  async function requestPause() {
    if (!isExecuting || !onPause) return;
    setPausing(true);
    setPauseFailed(false);
    try {
      await onPause(run.id);
    } catch {
      // 대화상자는 그래도 연다. 실패는 그 안에서 말하고 그 안에서 다시
      // 시도한다 -- 여기서 닫아버리면 무엇이 잘못됐는지 볼 자리가 없다.
      setPauseFailed(true);
    } finally {
      setPausing(false);
    }
  }

  async function resumeRun() {
    setResuming(true);
    try {
      await onResume();
    } finally {
      setResuming(false);
    }
  }

  function reviewFailureTask(task) {
    if (!task) return;
    setSelectedTaskId(task.id);
    setActiveTab("tasks");
  }

  return (
    <section className="team-run-detail" aria-label="Team run detail">
      <header className="team-run-hero">
        <div className="team-run-hero-main">
          <div className="team-run-detail-id-row">
            <span className="mono team-run-detail-id">
              TEAM RUN · {run.team_name ? `${run.team_name} · ` : ""}{run.id.slice(0, 8)}
            </span>
            <StatusBadge kind={run.status} />
          </div>
          <div className="team-run-hero-summary mono">
            <span>{String(run.execution_policy || run.lifecycle_mode || "standard").toUpperCase()}</span>
            <span>LEAD · {leader?.name || "-"}</span>
            <span>{currentCycle ? `CYCLE #${currentCycle.sequence}` : "NO CYCLE"}</span>
            <span>{tasks.filter((task) => OPEN_TASK_STATUSES.has(task.status)).length} OPEN TASKS</span>
          </div>
        </div>
        <div className="team-run-hero-actions">
          {canResume && !showFailurePanel ? (
            <Button
              size="btn-sm"
              variant="primary"
              disabled={resuming}
              onClick={resumeRun}
            >
              {run.status === "paused"
                ? (resuming ? "재개하는 중..." : "재개")
                : (resuming ? "Resuming..." : "Resume")}
            </Button>
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

      {showFailurePanel ? (
        <section className="team-failure-panel" role="alert" aria-labelledby="team-failure-title">
          <div className="team-failure-copy">
            <span className="mono team-failure-kicker">RECOVERY REQUIRED</span>
            <h2 id="team-failure-title" className="headline">Team Run을 완료하지 못했습니다</h2>
            <p>{failureCause}</p>
            <div className="team-failure-meta mono">
              <span>{currentCycle ? `CYCLE #${currentCycle.sequence}` : "CYCLE 미확인"}</span>
              <span>TASK · {failureTask?.title || "미확인"}</span>
              <span>AGENT · {failureAgent?.name || "미확인"}</span>
            </div>
            <p className="team-failure-preserved">
              완료된 Task {completedTaskCount}개와 Files {documents.length}개는 유지됩니다.
            </p>
            {failureRetryTask ? (
              <p>재시도 Task가 준비되었습니다. 저장된 결과를 유지한 채 Cycle을 이어가세요.</p>
            ) : runtimeFailure ? (
              <p>현재 runtime에서 필요한 provider 또는 capability를 사용할 수 없습니다.</p>
            ) : null}
          </div>
          <div className="team-failure-actions">
            {canResumeFailure ? (
              <Button
                size="btn-sm"
                variant="primary"
                disabled={resuming}
                onClick={resumeRun}
              >
                {resuming ? "Resuming..." : "Resume cycle"}
              </Button>
            ) : runtimeFailure && onOpenSettings ? (
              <Button size="btn-sm" variant="primary" onClick={onOpenSettings}>Change runtime</Button>
            ) : canRetryFailure ? (
              <Button
                size="btn-sm"
                variant="primary"
                disabled={retryingTaskId === failureTask.id}
                onClick={async () => {
                  setRetryingTaskId(failureTask.id);
                  try {
                    await onRetryTask(failureTask.id);
                  } finally {
                    setRetryingTaskId(null);
                  }
                }}
              >
                {retryingTaskId === failureTask.id ? "Retrying..." : `Retry ${failureTask.title}`}
              </Button>
            ) : null}
            {canResumeFailure ? (
              <Button size="btn-sm" onClick={() => reviewFailureTask(failureRetryTask)}>Review retry task</Button>
            ) : canRetryFailure ? (
              <Button size="btn-sm" onClick={() => reviewFailureTask(failureTask)}>Open task</Button>
            ) : (
              <Button size="btn-sm" onClick={() => setActiveTab("tasks")}>Open diagnostics</Button>
            )}
            {!failureTask ? (
              <Button size="btn-sm" onClick={() => setActiveTab("activity")}>View activity</Button>
            ) : null}
          </div>
        </section>
      ) : null}

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
        {showUsage ? (
          <span className="mono team-phase-usage">
            <span>{`입력 ${compactTokens(usageTotals.input_tokens || 0)}`}</span>
            <span>{`출력 ${compactTokens(usageTotals.output_tokens || 0)}`}</span>
            <span>{`캐시 ${compactTokens(usageCache)}`}</span>
            {usageTotals.unreported_calls ? (
              // 총합만 보이면 그것이 전부인 줄 읽는다. 보고하지 않은 호출이
              // 섞여 있으면 실제 사용량은 이보다 크다.
              <span className="team-phase-usage-gap">
                {`${usageTotals.unreported_calls}건 미보고`}
              </span>
            ) : null}
          </span>
        ) : null}
      </div>




      {run.status === "interrupted" ? (
        <div className="team-interrupted-banner" role="status">
          <span className="headline team-interrupted-title">Run interrupted</span>
          <span className="team-interrupted-copy">Running work was returned to Pending. Resume when you are ready.</span>
        </div>
      ) : null}

      {run.status === "paused" ? (
        <div className="team-paused-banner" role="status">
          <span className="headline team-paused-title">정지됨</span>
          <span className="team-paused-copy">
            물어보기로 리드에게 질문할 수 있습니다. 재개하면 하던 일을 이어서 합니다.
          </span>
        </div>
      ) : null}

      {run.status !== "paused" && run.pause_requested_at ? (
        <div className="team-paused-banner" role="status">
          <span className="headline team-paused-title">정지 요청됨</span>
          <span className="team-paused-copy">{pauseWaitCopy(run.status)}</span>
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


      {/* 열자마자 보여야 하는 것만 모은다. 나머지는 탭 뒤로 보냈다 -- 예전에는
          전달·정책·검토 패널이 전부 이 자리에 세로로 쌓여서, 지금 무슨 요청이
          돌고 있는지가 스크롤 아래로 밀렸다. */}
      <section className="team-dashboard" role="region" aria-label="Dashboard">
        <div className="team-dashboard-request">
          <span className="mono team-run-request-label">CURRENT REQUEST</span>
          <h1
            className="headline team-run-detail-goal team-run-current-request"
            title={currentInstruction}
          >
            {currentInstruction}
          </h1>
          {distinctBaseObjective ? (
            <div className="team-run-base-objective">BASE OBJECTIVE · {distinctBaseObjective}</div>
          ) : null}
          {showPlanShape ? (
            <div className={splitBoughtNothing ? "team-plan-shape team-plan-shape-flat mono" : "team-plan-shape mono"}>
              {`일감 ${planShape.task_count}개 · 최대 ${planShape.longest_chain}단계 대기 · ${planShape.ready_at_start}개 즉시 시작`}
              {splitBoughtNothing ? " · 나눈 이득이 없습니다" : ""}
            </div>
          ) : null}
        </div>
        <div className="team-dashboard-now">
          <div className="team-section-head team-section-toolbar">
            <span className="mono team-section-label">진행 중</span>
            <span className="mono team-section-count">{runningTasks.length}</span>
            <span className="team-section-rule" />
          </div>
          {runningTasks.length ? (
            <ul className="team-dashboard-now-list">
              {runningTasks.map((task) => (
                <li key={task.id} className="team-dashboard-now-item">
                  <span className="team-dashboard-now-title">{task.title}</span>
                  <span className="mono team-dashboard-now-owner">
                    {agents.find((agent) => agent.id === task.owner_agent_id)?.name || "미배정"}
                  </span>
                  {nowSince(task.started_at) !== null ? (
                    <span className="mono team-dashboard-now-since">{fmtElapsed(nowSince(task.started_at))}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mono team-dashboard-now-empty">돌고 있는 일감이 없습니다</div>
          )}
        </div>
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
      </section>

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
            {/* 현재 사이클 기준이다. 전체를 세면 사이클을 여러 번 돈 런에서
                지금 할 일이 몇 개인지가 지난 것에 묻힌다. */}
            {key === "tasks" && currentCycleTasks.length ? (
              <span className="team-detail-tab-badge mono">{currentCycleTasks.length}</span>
            ) : null}
            {key === "history" && cycles.length ? (
              <span className="team-detail-tab-badge mono">{cycles.length}</span>
            ) : null}
          </button>
        ))}
      </div>

      {activeTab === "run" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Run">
          <div className="team-run-actions">
            {canAskQuestion ? (
              <Button
                size="btn-sm"
                variant="primary"
                disabled={pausing}
                onClick={async () => {
                  await requestPause();
                  setQuestionDialogOpen(true);
                }}
              >
                {pausing ? "정지 요청 중..." : "물어보기"}
              </Button>
            ) : null}
            {canAddWork ? (
              <Button size="btn-sm" variant="primary" onClick={() => setWorkDialogOpen(true)}>일감 추가</Button>
            ) : null}
          </div>
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
      {hasPlanReview ? (
        <details className="team-plan-review">
          <summary className="team-plan-review-summary">
            <span className="mono team-plan-review-label">PLAN REVIEW</span>
            <span className="team-plan-review-title">계획 검토</span>
            <span className="mono team-plan-review-count">
              {planRevisions.length}개 계획 · {contests.length}개 이의
            </span>
          </summary>
          <div className="team-plan-review-body">
            {/* Worker reviews and operator objections are kept separate so the
                source of each objection remains explicit. */}
            <PlanNegotiation revisions={planRevisions} agents={agents} />
            <ContestPanel
              runId={run.id}
              runStatus={run.status}
              hasPlan={Boolean(planRevisions.length)}
              contests={contests}
              onContestPlan={onContestPlan}
            />
          </div>
        </details>
      ) : null}
        </div>
      ) : null}

      {activeTab === "tasks" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Tasks">
          <div className="team-section-head team-section-toolbar">
            <span className="mono team-section-label">Task Board</span>
            <span className="mono team-section-count">
              {showAllTasks ? `${tasks.length} ALL CYCLES` : `${visibleTasks.length} CURRENT CYCLE`}
            </span>
            <BuildEvidenceSummary summary={detail.buildEvidenceSummary} />
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


      {activeTab === "config" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="Configuration">
      <details className="team-run-details">
        <summary className="mono">RUN DETAILS</summary>
        <div className="team-run-meta">
          <div className="team-run-meta-cell">
            <div className="mono team-run-meta-k">ID</div>
            <div className="mono team-run-meta-v team-run-meta-copy">{run.id}</div>
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
          <div className="team-run-meta-cell team-run-meta-wide team-run-meta-workspace">
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
          <div className="team-config-outputs">
          {onViewOutputs ? (
            <div className="team-files-actions">
              <Button size="btn-sm" onClick={() => onViewOutputs(run.id)}>Outputs에서 모두 보기</Button>
            </div>
          ) : null}
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
        </div>
      ) : null}

      {activeTab === "history" ? (
        <div className="team-tab-panel" role="tabpanel" aria-label="History">
          <div className="team-subtabs" role="tablist" aria-label="History views">
            {[["cycles", "CYCLE HISTORY", cycles.length], ["reports", "AGENT REPORTS", reports.length]].map(
              ([key, label, count]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={historyTab === key}
                  className={`team-subtab${historyTab === key ? " active" : ""}`}
                  onClick={() => setHistoryTab(key)}
                >
                  <span>{label}</span>
                  {count ? <span className="team-detail-tab-badge mono">{count}</span> : null}
                </button>
              )
            )}
          </div>
        {historyTab === "cycles" ? (
        <section className="team-cycles" aria-label="Team Run cycles">
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
                    {Array.isArray(cycle.coverage_gaps) ? (
                      cycle.coverage_gaps.length ? (
                        <div className="team-cycle-coverage-gaps mono">
                          <span className="team-cycle-coverage-label">COVERAGE GAPS</span>
                          <ul>
                            {cycle.coverage_gaps.map((gap, gapIndex) => (
                              <li key={gapIndex}>
                                {gap.obligation}
                                {gap.document ? ` · ${gap.document}` : ""}
                                {gap.note ? ` — ${gap.note}` : ""}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : (
                        <div className="team-cycle-coverage mono">누락 없다고 보고함</div>
                      )
                    ) : (
                      <div className="team-cycle-coverage mono">커버리지를 보고하지 않음</div>
                    )}
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
        {historyTab === "reports" ? (
        <div className="team-overview-disclosures" aria-label="Agent reports">
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
      <AskQuestionDialog
        open={questionDialogOpen}
        awaitingPause={isExecuting}
        runStatus={run.status}
        history={questionHistory}
        value={questionInput}
        submitting={askingQuestion}
        failed={askQuestionFailed}
        pauseFailed={pauseFailed}
        onRetryPause={requestPause}
        progress={questionProgress}
        onChange={setQuestionInput}
        onClose={() => setQuestionDialogOpen(false)}
        onSubmit={async () => {
          const text = questionInput.trim();
          setAskingQuestion(true);
          setAskQuestionFailed(false);
          try {
            await onAskQuestion(run.id, text);
            setQuestionInput("");
          } catch {
            setAskQuestionFailed(true);
          } finally {
            setAskingQuestion(false);
          }
        }}
      />
    </section>
  );
}
