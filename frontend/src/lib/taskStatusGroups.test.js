import { describe, expect, it } from "vitest";
import { TASK_STATUS_GROUPS, groupForTaskStatus } from "./taskStatusGroups.js";

// Mirrors the backend TaskStatus union in
// src/personal_agent_gateway/team_lifecycle.py. Adding a state there without
// placing it in a display group must fail the coverage test below.
const TASK_STATUSES = [
  "pending",
  "in_progress",
  "waiting_for_user",
  "waiting_for_provider",
  "completed",
  "skipped",
  "blocked",
  "failed",
  "canceled"
];

describe("TASK_STATUS_GROUPS", () => {
  it("declares the four display columns in board order", () => {
    expect(TASK_STATUS_GROUPS.map((group) => group.key)).toEqual([
      "pending",
      "in_progress",
      "completed",
      "unresolved"
    ]);
    expect(TASK_STATUS_GROUPS.map((group) => group.label)).toEqual([
      "PENDING",
      "IN PROGRESS",
      "COMPLETED",
      "UNRESOLVED"
    ]);
  });

  it("covers every backend task status exactly once", () => {
    const placed = TASK_STATUS_GROUPS.flatMap((group) => group.statuses);
    expect([...placed].sort()).toEqual([...TASK_STATUSES].sort());
  });
});

describe("groupForTaskStatus", () => {
  it.each([
    ["pending", "pending"],
    ["in_progress", "in_progress"],
    ["waiting_for_user", "in_progress"],
    ["waiting_for_provider", "in_progress"],
    ["completed", "completed"],
    ["skipped", "completed"],
    ["blocked", "unresolved"],
    ["failed", "unresolved"],
    ["canceled", "unresolved"]
  ])("maps %s to the %s column", (status, expected) => {
    expect(groupForTaskStatus(status)).toBe(expected);
  });

  it("maps unknown states to unresolved so a task is never invisible", () => {
    expect(groupForTaskStatus("invented_state")).toBe("unresolved");
    expect(groupForTaskStatus("")).toBe("unresolved");
    expect(groupForTaskStatus(undefined)).toBe("unresolved");
    expect(groupForTaskStatus(null)).toBe("unresolved");
  });
});
