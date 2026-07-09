import { fmtElapsed, fmtTime, nowHM, nowHMS } from "./time.js";

export function normalizeApproval(value) {
  if (!value || typeof value !== "object") return null;
  if (typeof value.id !== "string" || typeof value.command !== "string") return null;
  return { id: value.id, command: value.command };
}

export function linesFrom(text) {
  if (!text) return [];
  return String(text).replace(/\s+$/, "").split("\n").map((line) => ({ text: line, color: "#E6E6E6" }));
}

export function timelineFromHistory(events) {
  const out = [];
  const sortedEvents = [...events].sort((left, right) => {
    const leftTime = Date.parse(left.created_at || "");
    const rightTime = Date.parse(right.created_at || "");
    return (Number.isNaN(leftTime) ? 0 : leftTime) - (Number.isNaN(rightTime) ? 0 : rightTime);
  });
  sortedEvents.forEach((event, index) => {
    const payload = event.payload || {};
    const time = fmtTime(event.created_at, false);
    if (event.kind === "user" && typeof payload.content === "string") {
      out.push({ type: "user", text: payload.content, time, order: index });
    } else if (event.kind === "assistant" && typeof payload.content === "string") {
      out.push({ type: "agent", text: payload.content, time, order: index });
    } else if (event.kind === "tool_result" && typeof payload.command === "string") {
      out.push({
        type: "command",
        key: `h${index}`,
        command: payload.command,
        status: payload.exit_code === 0 ? "completed" : "failed",
        exit: payload.exit_code,
        lines: linesFrom(`${payload.stdout || ""}${payload.stderr || ""}`),
        time: fmtTime(event.created_at, true),
        duration: "",
        order: index
      });
    } else if (event.kind === "tool_denial" && typeof payload.command === "string") {
      out.push({
        type: "event_row",
        label: "tool_denial",
        detail: payload.command,
        dotColor: "#FF0000",
        time: fmtTime(event.created_at, true),
        order: index
      });
    } else if (event.kind === "runtime_error" && typeof payload.message === "string") {
      out.push({ type: "runtime_error", message: payload.message, time: fmtTime(event.created_at, true), order: index });
    }
  });
  return out;
}

export function entryFromSse(event) {
  const item = event.item;
  if (item && typeof item === "object") {
    if (item.type === "command_execution") {
      let status = item.status === "in_progress" ? "running" : (item.status || "running");
      if (status === "completed" && item.exit_code != null && item.exit_code !== 0) status = "failed";
      const done = status === "completed" || status === "failed";
      return {
        type: "command",
        key: `c${item.id || item.command || ""}`,
        command: item.command || "command",
        status,
        exit: item.exit_code,
        lines: linesFrom(item.aggregated_output || ""),
        time: nowHMS(),
        duration: done ? "" : "live"
      };
    }
    if (item.type === "agent_message") {
      return { type: "agent", text: item.text || "", time: nowHM(), streaming: false };
    }
    return null;
  }
  if (event.type === "runtime.user_message.started") {
    return { type: "event_row", label: "runtime.user_message.started", detail: "message accepted", dotColor: "#000", time: nowHMS() };
  }
  if (event.type === "runtime.completed") {
    return { type: "event_row", label: "runtime.completed", detail: "session finished", dotColor: "#008000", time: nowHMS() };
  }
  if (event.type === "runtime.error") {
    return { type: "runtime_error", message: typeof event.message === "string" ? event.message : "runtime error", time: nowHMS() };
  }
  return null;
}

export function deriveLive({ entries, busy, turnStart, turnEnd }) {
  const running = entries.filter((entry) => entry.type === "command" && entry.status !== "completed" && entry.status !== "failed").length;
  let phase;
  let color;
  if (running > 0) {
    phase = "COMMAND RUNNING";
    color = "var(--c-warn)";
  } else if (busy) {
    phase = "WORKING";
    color = "var(--c-warn)";
  } else if (!entries.length) {
    phase = "IDLE";
    color = "var(--c-grey)";
  } else {
    let significant = null;
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const entry = entries[index];
      if (entry.type === "runtime_error" || entry.type === "command") {
        significant = entry;
        break;
      }
      if (entry.type === "user") break;
    }
    if (significant?.type === "runtime_error") {
      phase = "ERROR";
      color = "var(--c-danger)";
    } else if (significant?.type === "command" && significant.status === "failed") {
      phase = "FAILED";
      color = "var(--c-danger)";
    } else {
      phase = "DONE";
      color = "var(--c-ok)";
    }
  }

  const kindMap = { "COMMAND RUNNING": "running", WORKING: "working", DONE: "completed", FAILED: "failed", ERROR: "error", IDLE: "idle" };
  const end = busy ? Date.now() : (turnEnd || Date.now());
  const elapsed = turnStart ? fmtElapsed((end - turnStart) / 1000) : "-";
  return { phase, color, running, lastKind: kindMap[phase], elapsed, events: entries.length };
}
