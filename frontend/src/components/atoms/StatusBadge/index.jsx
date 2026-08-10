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
  waiting_for_provider: "PROVIDER WAIT",
  queued: "QUEUED",
  in_progress: "IN PROGRESS",
  blocked: "차단됨",
  skipped: "건너뜀",
  enabled: "ENABLED",
  paused: "PAUSED",
  active: "ACTIVE",
  ready: "READY",
  auto_waiting: "AUTO WAITING",
  needs_attention: "NEEDS ATTENTION"
};

const ACTIVE = new Set([
  "running",
  "working",
  "planning",
  "summarizing",
  "waiting",
  "waiting_approval",
  "waiting_for_user",
  "waiting_for_provider",
  "queued",
  "pending",
  "in_progress",
  "active",
  "auto_waiting"
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
