import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { fmtDateTime } from "../../../lib/time.js";

// kind is null until the leader rules on the objection -- but it is also null
// when the contest's cycle died before a ruling, and refiling the same objection
// is idempotent, so showing that as "판정 대기" left a dead contest waiting
// forever with no way to retry. A settled request with no verdict is failed.
const KIND_LABEL = { amend: "수정", partial: "부분 인정", reject: "기각", ask_back: "재질문" };

function contestStatusText(contest) {
  if (contest.kind) {
    return `${KIND_LABEL[contest.kind] || contest.kind} · ${contest.reason}`;
  }
  if (contest.status === "settled" || contest.status === "canceled" || contest.error_message) {
    return `실패 · ${contest.error_message || "판정 전에 사이클이 종료되었습니다."}`;
  }
  return "판정 대기";
}

export function ContestPanel({ runId, contests = [], onContestPlan }) {
  const [objection, setObjection] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  if (!onContestPlan && !contests.length) return null;

  async function handleSubmit() {
    const text = objection.trim();
    if (!text) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await onContestPlan(runId, text);
      if (result && result.ok === false) {
        setError(result.detail || "이의를 접수하지 못했습니다.");
        return;
      }
      setObjection("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="team-contest-panel">
      <div className="mono team-task-dialog-label">계획 이의</div>
      {contests.length ? (
        <div className="team-contest-list">
          {contests.map((contest, index) => (
            <div className="team-contest-item" key={contest.created_at ? `${contest.created_at}-${index}` : index}>
              <div>{contest.objection}</div>
              <div className="mono team-task-diagnostic">{contestStatusText(contest)}</div>
              {(contest.supersedes || []).map((entry, supersedeIndex) => (
                <div className="mono team-task-diagnostic" key={supersedeIndex}>
                  {`${entry.document_path} · ${entry.decision}`}
                </div>
              ))}
              {contest.created_at ? (
                <div className="mono team-contest-time">{fmtDateTime(contest.created_at)}</div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="team-task-empty mono">No contests yet.</div>
      )}
      {error ? <div className="mono team-contest-error">{error}</div> : null}
      {onContestPlan ? (
        <>
          <label className="mono team-task-dialog-label" htmlFor="team-contest-input">계획에 이의 제기</label>
          <textarea
            id="team-contest-input"
            className="team-contest-input"
            value={objection}
            onChange={(event) => setObjection(event.target.value)}
            disabled={submitting}
          />
          <Button
            size="btn-sm"
            variant="primary"
            disabled={submitting || !objection.trim()}
            onClick={handleSubmit}
          >
            이의 보내기
          </Button>
        </>
      ) : null}
    </div>
  );
}
