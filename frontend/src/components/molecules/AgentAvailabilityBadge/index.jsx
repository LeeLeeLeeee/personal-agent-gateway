export function AgentAvailabilityBadge({ available, reason = "" }) {
  return (
    <span className={`badge ${available ? "badge-completed" : "badge-failed"}`} title={reason}>
      {available ? "AVAILABLE" : "UNAVAILABLE"}
    </span>
  );
}
