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

// A contest objects to the plan the team is working from, so the form only
// belongs on a run that is still working from one. The server accepts a
// contest on anything but a canceled run, which is why this has to be decided
// here: on an interrupted or finished run the request would queue against a
// plan nobody is executing, and offering it next to the action that actually
// applies -- Resume, or a new request -- reads as if objecting were the way
// forward.
//
// Past contests still show. They are what happened, and hiding the history
// with the form would make a settled objection vanish the moment its run
// stopped.
const CONTESTABLE_STATUSES = new Set([
  "planning",
  "running",
  "summarizing",
  "waiting_for_provider"
]);

export function ContestPanel({ runId, runStatus, hasPlan, contests = [], onContestPlan }) {
  const [objection, setObjection] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const canContest = Boolean(hasPlan && onContestPlan) && CONTESTABLE_STATUSES.has(runStatus);

  if (!canContest && !contests.length) return null;

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
    <section className="team-contest-panel" aria-label="사용자 계획 이의">
      {contests.length ? (
        <div>
          <div className="mono team-task-dialog-label">제기한 이의 · {contests.length}</div>
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
        </div>
      ) : null}
      {error ? <div className="mono team-contest-error">{error}</div> : null}
      {canContest ? (
        <div className="team-contest-form">
          <label className="mono team-task-dialog-label" htmlFor="team-contest-input">계획에 이의 제기</label>
          <p className="team-contest-help">
            누락된 작업, 잘못된 순서, 충돌하는 요구사항을 구체적으로 적어주세요.
          </p>
          <textarea
            id="team-contest-input"
            className="team-contest-input"
            value={objection}
            onChange={(event) => setObjection(event.target.value)}
            disabled={submitting}
            rows={4}
            placeholder="예: 배포 전에 접근성 검증 작업이 필요합니다."
          />
          <div className="team-contest-actions">
            <span className="mono">제출하면 리더가 현재 계획 기준으로 판정합니다.</span>
            <Button
              size="btn-sm"
              variant="primary"
              disabled={submitting || !objection.trim()}
              onClick={handleSubmit}
            >
              {submitting ? "보내는 중..." : "이의 보내기"}
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
