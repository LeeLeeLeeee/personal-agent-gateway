const STATUS_LABEL = {
  awaiting_approval: "승인 대기",
  approved: "승인됨",
  superseded: "대체됨",
  abandoned: "합의 실패"
};

const DECISION_LABEL = { approve: "승인", object: "이의" };

function agentName(agents, agentId) {
  return agents.find((agent) => agent.id === agentId)?.name || agentId;
}

// task_ref labels (T-01, T-02, ...) are assigned fresh per revision, so the
// same label points at a different task in a different revision. Every
// objection below is rendered inside its own revision's block, never pulled
// out into a flat list, so the label is never read as pointing at the
// current plan.
export function PlanNegotiation({ revisions = [], agents = [] }) {
  if (!revisions.length) return null;

  return (
    <div className="team-plan-negotiation" role="region" aria-label="Plan negotiation">
      <div className="mono team-task-dialog-label">계획 협의</div>
      {revisions.map((revision) => {
        const objectionEntries = Object.entries(revision.objections || {});
        return (
          <div className="team-plan-revision" key={revision.revision}>
            <div className="team-plan-revision-head mono">
              <span>{`개정 ${revision.revision}`}</span>
              <span>{STATUS_LABEL[revision.status] || revision.status}</span>
            </div>
            <div className="team-plan-revision-approvers mono">
              {(revision.required_approver_agent_ids || []).map((agentId) => {
                const decision = revision.reviews?.[agentId];
                return (
                  <span className="team-plan-approver" key={agentId}>
                    {agentName(agents, agentId)}
                    {" · "}
                    {decision ? (DECISION_LABEL[decision] || decision) : "미확인"}
                  </span>
                );
              })}
            </div>
            {objectionEntries.length ? (
              <div className="team-plan-objections">
                {objectionEntries.flatMap(([agentId, objections]) =>
                  (objections || []).map((objection, index) => (
                    <div className="team-plan-objection" key={`${agentId}-${index}`}>
                      <div className="mono team-plan-objection-meta">
                        {`${agentName(agents, agentId)} · ${objection.task_ref} · ${objection.kind}`}
                      </div>
                      <div className="team-plan-objection-detail">{objection.detail}</div>
                    </div>
                  ))
                )}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
