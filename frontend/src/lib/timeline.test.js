import { describe, expect, it } from "vitest";
import { deriveLive, entryFromSse, timelineFromHistory } from "./timeline.js";

describe("timeline model", () => {
  it("maps persisted transcript events to renderable timeline entries", () => {
    const timeline = timelineFromHistory([
      { kind: "user", created_at: "2026-07-08T01:02:03Z", payload: { content: "hello" } },
      { kind: "assistant", created_at: "2026-07-08T01:03:03Z", payload: { content: "hi" } },
      {
        kind: "tool_result",
        created_at: "2026-07-08T01:04:03Z",
        payload: { command: "pytest", stdout: "ok\n", stderr: "", exit_code: 0 }
      },
      { kind: "tool_denial", created_at: "2026-07-08T01:05:03Z", payload: { command: "rm -rf" } },
      { kind: "runtime_error", created_at: "2026-07-08T01:06:03Z", payload: { message: "boom" } }
    ]);

    expect(timeline).toEqual([
      expect.objectContaining({ type: "user", text: "hello" }),
      expect.objectContaining({ type: "agent", text: "hi" }),
      expect.objectContaining({ type: "command", command: "pytest", status: "completed", exit: 0 }),
      expect.objectContaining({ type: "event_row", label: "tool_denial", detail: "rm -rf" }),
      expect.objectContaining({ type: "runtime_error", message: "boom" })
    ]);
  });

  it("orders persisted transcript events by created_at", () => {
    const timeline = timelineFromHistory([
      { kind: "assistant", created_at: "2026-07-08T01:03:03Z", payload: { content: "second" } },
      { kind: "user", created_at: "2026-07-08T01:02:03Z", payload: { content: "first" } }
    ]);

    expect(timeline.map((entry) => entry.text)).toEqual(["first", "second"]);
    expect(timeline.map((entry) => entry.order)).toEqual([0, 1]);
  });

  it("maps Codex SSE command and agent events without changing API contracts", () => {
    expect(entryFromSse({
      item: {
        type: "command_execution",
        id: "cmd-1",
        command: "npm test",
        status: "completed",
        exit_code: 1,
        aggregated_output: "failed"
      }
    })).toEqual(expect.objectContaining({
      type: "command",
      key: "ccmd-1",
      command: "npm test",
      status: "failed",
      exit: 1
    }));

    expect(entryFromSse({ item: { type: "agent_message", text: "done" } }))
      .toEqual(expect.objectContaining({ type: "agent", text: "done" }));
  });

  it("derives live status from busy state and command outcomes", () => {
    expect(deriveLive({ entries: [], busy: false, turnStart: null, turnEnd: null }))
      .toEqual(expect.objectContaining({ phase: "IDLE", lastKind: "idle", running: 0 }));

    expect(deriveLive({
      entries: [{ type: "command", status: "running" }],
      busy: true,
      turnStart: Date.now(),
      turnEnd: null
    })).toEqual(expect.objectContaining({ phase: "COMMAND RUNNING", lastKind: "running", running: 1 }));

    expect(deriveLive({
      entries: [{ type: "user" }, { type: "command", status: "failed" }],
      busy: false,
      turnStart: null,
      turnEnd: null
    })).toEqual(expect.objectContaining({ phase: "FAILED", lastKind: "failed" }));
  });
});
