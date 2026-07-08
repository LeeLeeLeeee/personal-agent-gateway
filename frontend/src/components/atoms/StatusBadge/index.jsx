const LABELS = {
  running: "RUNNING",
  working: "WORKING",
  completed: "COMPLETED",
  failed: "FAILED",
  error: "ERROR",
  idle: "IDLE",
  draft: "DRAFT",
  planning: "PLANNING",
  summarizing: "SUMMARIZING",
  canceled: "CANCELED",
  pending: "PENDING",
  waiting: "WAITING",
  in_progress: "IN PROGRESS",
  blocked: "BLOCKED"
};

const ACTIVE = new Set([
  "running",
  "working",
  "planning",
  "summarizing",
  "waiting",
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
