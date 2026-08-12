import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { fmtDateTime } from "../../../lib/time.js";

// kind is null until the leader rules on the objection. reason is required on
// every verdict, so "판정 대기" (no kind yet) and "기각 · <reason>" (ruled) are
// the only two states -- there is no ruled-with-no-reason state to render.
const KIND_LABEL = { amend: "수정", partial: "부분 인정", reject: "기각", ask_back: "재질문" };

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
              <div className="mono team-task-diagnostic">
                {contest.kind ? `${KIND_LABEL[contest.kind] || contest.kind} · ${contest.reason}` : "판정 대기"}
              </div>
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
