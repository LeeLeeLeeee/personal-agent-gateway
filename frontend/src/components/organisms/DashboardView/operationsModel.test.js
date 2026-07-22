import { describe, expect, it } from "vitest";
import { isOperationsPayload, operationsDashboardModel } from "./operationsModel.js";

const payload = {
  intake_open: true,
  diagnostics: { workspace_writable: true },
  health: [
    { name: "worker", ready: true, detail: "ready" },
    { name: "scheduler", ready: false, detail: "not running" }
  ],
  items: [
    { id: "job-queued", domain: "job", title: "Queued job", status: "queued", updated_at: "2026-07-22T09:00:00Z" },
    { id: "team-run", domain: "team_run", title: "Planning run", status: "planning", updated_at: "2026-07-22T10:00:00Z" },
    { id: "job-failed", domain: "job", title: "Failed job", status: "failed", updated_at: "2026-07-22T11:00:00Z", retryable: true },
    { id: "schedule-paused", domain: "schedule", title: "Paused schedule", status: "paused", updated_at: "2026-07-22T12:00:00Z", resumable: true },
    { id: "done", domain: "job", title: "Done job", status: "succeeded", updated_at: "2026-07-22T13:00:00Z" }
  ]
};

describe("operationsDashboardModel", () => {
  it("uses the declared operations status signals without counting terminal work as active", () => {
    const model = operationsDashboardModel(payload);

    expect(model.activeItems.map((item) => item.id)).toEqual(["team-run", "job-queued"]);
    expect(model.attentionItems.map((item) => item.id)).toEqual(["schedule-paused", "job-failed"]);
    expect(model.healthyCount).toBe(1);
    expect(model.systemAttention).toEqual([
      expect.objectContaining({ id: "health:scheduler", kind: "failed" })
    ]);
  });

  it("reports stopped intake and blocked workspace as distinct server-provided warnings", () => {
    const model = operationsDashboardModel({
      ...payload,
      intake_open: false,
      diagnostics: { workspace_writable: false },
      health: []
    });

    expect(model.systemAttention.map((item) => item.id)).toEqual(["intake", "workspace"]);
  });

  it("requires the fields needed to render an operations dashboard", () => {
    expect(isOperationsPayload(payload)).toBe(true);
    expect(isOperationsPayload({ items: [], health: [] })).toBe(false);
    expect(isOperationsPayload(null)).toBe(false);
  });
});
