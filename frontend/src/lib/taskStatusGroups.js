export const TASK_STATUS_GROUPS = [
  { key: "pending", label: "PENDING", statuses: ["pending"] },
  {
    key: "in_progress",
    label: "IN PROGRESS",
    statuses: ["in_progress", "waiting_for_user", "waiting_for_provider"]
  },
  { key: "completed", label: "COMPLETED", statuses: ["completed", "skipped"] },
  { key: "unresolved", label: "UNRESOLVED", statuses: ["blocked", "failed", "canceled"] }
];

// An unmapped status is not known to be progressing or done, and a task that
// lands in the wrong column is recoverable while an invisible one is not.
export function groupForTaskStatus(status) {
  const group = TASK_STATUS_GROUPS.find((candidate) => candidate.statuses.includes(status));
  return group ? group.key : "unresolved";
}
