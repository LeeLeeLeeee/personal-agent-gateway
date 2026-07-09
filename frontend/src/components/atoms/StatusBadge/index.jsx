const LABELS = {
  running: "RUNNING",
  working: "WORKING",
  completed: "COMPLETED",
  succeeded: "SUCCEEDED",
  failed: "FAILED",
  error: "ERROR",
  idle: "IDLE",
  draft: "DRAFT",
  planning: "PLANNING",
  summarizing: "SUMMARIZING",
  canceled: "CANCELED",
  pending: "PENDING",
  waiting: "WAITING",
  waiting_approval: "WAITING",
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
