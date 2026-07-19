const LABELS = {
  running: "RUNNING",
  working: "WORKING",
  completed: "COMPLETED",
  completed_with_failures: "COMPLETED*",
  succeeded: "SUCCEEDED",
  failed: "FAILED",
  error: "ERROR",
  idle: "IDLE",
  draft: "DRAFT",
  planning: "PLANNING",
  summarizing: "SUMMARIZING",
  canceled: "CANCELED",
  interrupted: "INTERRUPTED",
  pending: "PENDING",
  waiting: "WAITING",
  waiting_approval: "WAITING",
  waiting_for_user: "INPUT NEEDED",
  queued: "QUEUED",
  in_progress: "IN PROGRESS",
  blocked: "BLOCKED",
  enabled: "ENABLED",
  paused: "PAUSED"
};

const ACTIVE = new Set([
  "running",
  "working",
  "planning",
  "summarizing",
  "waiting",
  "waiting_approval",
  "waiting_for_user",
  "queued",
  "pending",
  "in_progress"
]);

export function StatusBadge({ kind = "idle" }) {
  const showDot = ACTIVE.has(kind);
  return (
    <span className={`badge badge-${kind}`}>
      {showDot ? <span className="dot" /> : null}
      {LABELS[kind] || "IDLE"}
    </span>
  );
}
