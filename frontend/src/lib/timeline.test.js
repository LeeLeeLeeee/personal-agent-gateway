import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { compareEntries, deriveLive, entryFromSse, timelineFromHistory, timelineFromSession } from "./timeline.js";

describe("timeline model", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 14, 18, 0, 0));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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
      key: "command::cmd-1",
      command: "npm test",
      status: "failed",
      exit: 1
    }));

    expect(entryFromSse({ item: { type: "agent_message", text: "done" } }))
      .toEqual(expect.objectContaining({ type: "agent", text: "done" }));
  });

  it("merges transcript and durable activity in deterministic order", () => {
    const timeline = timelineFromSession(
      [
        { kind: "user", created_at: "2026-07-09T01:00:00Z", payload: { content: "hello" } },
        { kind: "assistant", created_at: "2026-07-09T01:00:02Z", payload: { content: "done" } }
      ],
      [
        {
          id: 10,
          event_seq: 1,
          type: "runtime.user_message.started",
          created_at: "2026-07-09T01:00:01Z",
          payload: { message: "hello" }
        },
        {
          id: 11,
          event_seq: 2,
          type: "runtime.completed",
          created_at: "2026-07-09T01:00:03Z",
          payload: {}
        }
      ]
    );

    expect(timeline.map((entry) => entry.type)).toEqual(["user", "event_row", "agent", "event_row"]);
    expect(timeline.map((entry) => entry.order)).toEqual([0, 1, 2, 3]);
    expect(timeline[1].key).toBe("event:1");
    expect(timeline[2].text).toBe("done");
    expect(timeline[3]).toEqual(expect.objectContaining({
      key: "event:2",
      serverOrder: 2,
      label: "runtime.completed",
      time: "09일 10시 00분 03초"
    }));
  });

  it("dedupes streamed agent activity already persisted as assistant transcript", () => {
    const timeline = timelineFromSession(
      [
        { kind: "user", created_at: "2026-07-09T01:00:00Z", payload: { content: "hello" } },
        { kind: "assistant", created_at: "2026-07-09T01:00:02Z", payload: { content: "same answer" } }
      ],
      [{
        id: 11,
        event_seq: 2,
        session_id: "session-1",
        type: "codex.event",
        created_at: "2026-07-09T01:00:02Z",
        payload: { item: { type: "agent_message", id: "agent-1", text: "same answer" } }
      }]
    );

    expect(timeline.filter((entry) => entry.type === "agent").map((entry) => entry.text))
      .toEqual(["same answer"]);
  });

  it("reconciles persisted command updates by stable command key", () => {
    const timeline = timelineFromSession(
      [],
      [
        {
          event_seq: 1,
          session_id: "session-1",
          type: "codex.event",
          created_at: "2026-07-09T01:00:01Z",
          payload: { item: { type: "command_execution", id: "cmd-1", command: "npm test", status: "in_progress" } }
        },
        {
          event_seq: 2,
          session_id: "session-1",
          type: "codex.event",
          created_at: "2026-07-09T01:00:02Z",
          payload: { item: { type: "command_execution", id: "cmd-1", command: "npm test", status: "completed", exit_code: 0, aggregated_output: "ok" } }
        }
      ]
    );

    const commands = timeline.filter((entry) => entry.type === "command");
    expect(commands).toHaveLength(1);
    expect(commands[0]).toEqual(expect.objectContaining({ command: "npm test", status: "completed", exit: 0 }));
  });

  it("maps normalized SSE envelopes and keeps legacy raw Codex events working", () => {
    expect(entryFromSse({
      session_id: "session-1",
      event_seq: 3,
      type: "codex.event",
      created_at: "2026-07-09T01:00:02Z",
      payload: { item: { type: "agent_message", id: "agent-1", text: "done" } }
    })).toEqual(expect.objectContaining({
      type: "agent",
      key: "agent:session-1:agent-1",
      text: "done"
    }));

    const firstLegacy = entryFromSse({ item: { type: "agent_message", text: "legacy one" } });
    const secondLegacy = entryFromSse({ item: { type: "agent_message", text: "legacy two" } });

    expect(firstLegacy).toEqual(expect.objectContaining({ type: "agent", text: "legacy one" }));
    expect(secondLegacy).toEqual(expect.objectContaining({ type: "agent", text: "legacy two" }));
    expect(firstLegacy.key).toMatch(/^agent:legacy:[a-z0-9]+$/);
    expect(secondLegacy.key).toMatch(/^agent:legacy:[a-z0-9]+$/);
    expect(firstLegacy.key).not.toBe(secondLegacy.key);
  });

  it("maps normalized runtime error and completed events with stable keys and server time", () => {
    expect(entryFromSse({
      event_seq: 7,
      type: "runtime.completed",
      created_at: "2026-07-09T01:05:06Z",
      payload: {}
    })).toEqual(expect.objectContaining({
      type: "event_row",
      key: "event:7",
      serverOrder: 7,
      label: "runtime.completed",
      time: "09일 10시 05분 06초"
    }));

    expect(entryFromSse({
      event_seq: 8,
      type: "runtime.error",
      created_at: "2026-07-09T01:05:07Z",
      payload: { message: "normalized failure" },
      message: "raw failure"
    })).toEqual(expect.objectContaining({
      type: "runtime_error",
      key: "event:8",
      serverOrder: 8,
      message: "normalized failure",
      time: "09일 10시 05분 07초"
    }));
  });

  it("maps runtime.interrupted to an event row", () => {
    const entry = entryFromSse({
      type: "runtime.interrupted", session_id: "s1", event_seq: 9,
      created_at: "2026-07-10T00:00:02Z"
    });
    expect(entry).toMatchObject({ type: "event_row", label: "runtime.interrupted" });
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

describe("reasoning mapping", () => {
  it("maps codex reasoning items to reasoning entries", () => {
    const entry = entryFromSse({
      type: "item.completed",
      session_id: "s1",
      event_seq: 7,
      created_at: "2026-07-10T00:00:01Z",
      item: { id: "r1", type: "reasoning", text: "thinking about it" }
    });
    expect(entry).toMatchObject({ type: "reasoning", text: "thinking about it", serverOrder: 7 });
    expect(entry.key).toContain("reasoning:");
  });

  it("ignores reasoning items with no text as empty string", () => {
    const entry = entryFromSse({
      type: "item.completed", session_id: "s1", event_seq: 8,
      item: { id: "r2", type: "reasoning" }
    });
    expect(entry).toMatchObject({ type: "reasoning", text: "" });
  });

  it("reconciles persisted reasoning updates by stable reasoning key on reload", () => {
    const timeline = timelineFromSession(
      [],
      [
        {
          event_seq: 1,
          session_id: "session-1",
          type: "item.completed",
          created_at: "2026-07-10T00:00:01Z",
          item: { id: "r1", type: "reasoning", text: "" }
        },
        {
          event_seq: 2,
          session_id: "session-1",
          type: "item.completed",
          created_at: "2026-07-10T00:00:02Z",
          item: { id: "r1", type: "reasoning", text: "final thought" }
        }
      ]
    );

    const reasonings = timeline.filter((entry) => entry.type === "reasoning");
    expect(reasonings).toHaveLength(1);
    expect(reasonings[0].text).toBe("final thought");
  });
});

describe("compareEntries", () => {
  it("orders by createdAtMs first", () => {
    const a = { type: "agent", createdAtMs: 200, serverOrder: 1 };
    const b = { type: "user", createdAtMs: 100, serverOrder: 9 };
    expect([a, b].sort(compareEntries).map((e) => e.type)).toEqual(["user", "agent"]);
  });

  it("breaks createdAtMs ties by logical rank (reasoning before agent)", () => {
    const reasoning = { type: "reasoning", createdAtMs: 100, serverOrder: 5 };
    const agent = { type: "agent", createdAtMs: 100, serverOrder: 2 };
    expect([agent, reasoning].sort(compareEntries).map((e) => e.type)).toEqual(["reasoning", "agent"]);
  });

  it("breaks rank ties by serverOrder", () => {
    const first = { type: "command", createdAtMs: 100, serverOrder: 1 };
    const second = { type: "command", createdAtMs: 100, serverOrder: 2 };
    expect([second, first].sort(compareEntries).map((e) => e.serverOrder)).toEqual([1, 2]);
  });
});

describe("normalized event mapping", () => {
  const base = { type: "model.event", session_id: "s1", run_id: "r1", event_seq: 3 };

  it("maps message.delta to a streaming agent entry with append flag", () => {
    const e = entryFromSse({ ...base, kind: "message.delta", text: "Hel" });
    expect(e).toMatchObject({ type: "agent", text: "Hel", streaming: true, append: true });
    expect(e.key).toBe("agent:s1:r1");
  });

  it("maps message.completed to a finalized agent entry (no append)", () => {
    const e = entryFromSse({ ...base, kind: "message.completed", text: "Hello" });
    expect(e).toMatchObject({ type: "agent", text: "Hello", streaming: false });
    expect(e.append).toBeUndefined();
    expect(e.key).toBe("agent:s1:r1");
  });

  it("maps reasoning.delta to an appending reasoning entry", () => {
    const e = entryFromSse({ ...base, kind: "reasoning.delta", text: "think" });
    expect(e).toMatchObject({ type: "reasoning", text: "think", append: true });
    expect(e.key).toBe("reasoning:s1:r1");
  });

  it("maps tool.activity to a command entry keyed by tool id", () => {
    const e = entryFromSse({
      ...base, kind: "tool.activity",
      tool: { id: "c1", name: "shell", arguments: { command: "npm test", exit_code: 0 }, status: "completed", result: "ok" }
    });
    expect(e).toMatchObject({ type: "command", command: "npm test", status: "completed", exit: 0 });
    expect(e.key).toBe("command:s1:r1:c1");
    expect(e.lines.map((l) => l.text)).toEqual(["ok"]);
  });

  it("marks tool.activity failed when exit_code is non-zero", () => {
    const e = entryFromSse({
      ...base, kind: "tool.activity",
      tool: { id: "c2", name: "shell", arguments: { command: "false", exit_code: 1 }, status: "completed", result: "" }
    });
    expect(e.status).toBe("failed");
  });

  it("marks tool.activity running while started", () => {
    const e = entryFromSse({
      ...base, kind: "tool.activity",
      tool: { id: "c3", name: "Read", arguments: {}, status: "started" }
    });
    expect(e).toMatchObject({ type: "command", command: "Read", status: "running", duration: "live" });
  });

  it("ignores run.started, session.updated, and terminal events", () => {
    expect(entryFromSse({ ...base, kind: "run.started" })).toBeNull();
    expect(entryFromSse({ ...base, kind: "session.updated", upstream_session_id: "u1" })).toBeNull();
    expect(entryFromSse({ ...base, kind: "run.completed", content: "x" })).toBeNull();
    expect(entryFromSse({ ...base, kind: "run.failed", error: "boom" })).toBeNull();
  });
});
