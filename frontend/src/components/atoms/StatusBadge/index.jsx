const LABELS = {
  running: "RUNNING",
  working: "WORKING",
  completed: "COMPLETED",
  failed: "FAILED",
  error: "ERROR",
  idle: "IDLE"
};

export function StatusBadge({ kind = "idle" }) {
  const showDot = kind === "running" || kind === "working";
  return (
    <span className={`badge badge-${kind}`}>
      {showDot ? <span className="dot" /> : null}
      {LABELS[kind] || "IDLE"}
    </span>
  );
}
