import { fmtDateTime, fmtElapsed, nowDateTime } from "./time.js";

function legacyAgentKeySuffix(text) {
  const source = typeof text === "string" && text ? text : "empty";
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function parseCreatedAtMs(createdAt) {
  const parsed = Date.parse(createdAt || "");
  return Number.isNaN(parsed) ? 0 : parsed;
}

function timelineRank(entry) {
  if (entry.type === "user") return 0;
  if (entry.type === "event_row" && entry.label === "runtime.user_message.started") return 1;
  if (entry.type === "command" || entry.type === "runtime_error") return 2;
  if (entry.type === "reasoning") return 3;
  if (entry.type === "agent") return 4;
  if (entry.type === "event_row" && entry.label === "runtime.completed") return 5;
  if (entry.type === "event_row" && entry.label === "runtime.interrupted") return 6;
  return 7;
}

export function compareEntries(left, right) {
  const byTime = (left.createdAtMs ?? 0) - (right.createdAtMs ?? 0);
  if (byTime) return byTime;
  const byRank = timelineRank(left) - timelineRank(right);
  if (byRank) return byRank;
  const leftSeq = left.serverOrder ?? left.historyOrder ?? left.activityOrder ?? left.order ?? 0;
  const rightSeq = right.serverOrder ?? right.historyOrder ?? right.activityOrder ?? right.order ?? 0;
  if (leftSeq !== rightSeq) return leftSeq - rightSeq;
  return String(left.key || left.type).localeCompare(String(right.key || right.type));
}

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
    const time = fmtDateTime(event.created_at);
    const createdAtMs = parseCreatedAtMs(event.created_at);
    if (event.kind === "user" && typeof payload.content === "string") {
      out.push({ type: "user", text: payload.content, time, order: index, createdAtMs, historyOrder: index, source: "history" });
    } else if (event.kind === "assistant" && typeof payload.content === "string") {
      out.push({ type: "agent", text: payload.content, time, order: index, createdAtMs, historyOrder: index, source: "history" });
    } else if (event.kind === "tool_result" && typeof payload.command === "string") {
      out.push({
        type: "command",
        key: `h${index}`,
        command: payload.command,
        status: payload.exit_code === 0 ? "completed" : "failed",
        exit: payload.exit_code,
        lines: linesFrom(`${payload.stdout || ""}${payload.stderr || ""}`),
        time: fmtDateTime(event.created_at),
        duration: "",
        order: index,
        createdAtMs,
        historyOrder: index,
        source: "history"
      });
    } else if (event.kind === "tool_denial" && typeof payload.command === "string") {
      out.push({
        type: "event_row",
        label: "tool_denial",
        detail: payload.command,
        dotColor: "#FF0000",
        time: fmtDateTime(event.created_at),
        order: index,
        createdAtMs,
        historyOrder: index,
        source: "history"
      });
    } else if (event.kind === "runtime_error" && typeof payload.message === "string") {
      out.push({
        type: "runtime_error",
        message: payload.message,
        time: fmtDateTime(event.created_at),
        order: index,
        createdAtMs,
        historyOrder: index,
        source: "history"
      });
    }
  });
  return out;
}

function toolActivityEntry(event, sid, runId, time, createdAtMs) {
  const tool = event.tool && typeof event.tool === "object" ? event.tool : {};
  const args = tool.arguments && typeof tool.arguments === "object" ? tool.arguments : {};
  const commandSource = typeof args.command === "string"
    ? args.command
    : (typeof tool.name === "string" && tool.name ? tool.name : null);
  const exit = typeof args.exit_code === "number" ? args.exit_code : null;
  let status = tool.status === "started" ? "running" : (tool.status || "running");
  if (status === "completed" && exit != null && exit !== 0) status = "failed";
  const done = status === "completed" || status === "failed";
  const output = typeof tool.result === "string" ? tool.result : (tool.result == null ? "" : JSON.stringify(tool.result));
  const entry = {
    type: "command",
    key: `command:${sid}:${runId}:${tool.id || tool.name || ""}`,
    status,
    exit,
    lines: linesFrom(output),
    time,
    duration: done ? "" : "live",
    serverOrder: event.event_seq,
    createdAtMs
  };
  // Only set `command` when a real label source exists. A claude tool_result
  // (completion) has no name; omitting the key lets the merge preserve the
  // running row's real command (e.g. "Read") instead of clobbering it.
  if (commandSource != null) entry.command = commandSource;
  return entry;
}

export function entryFromSse(event) {
  const createdAtMs = parseCreatedAtMs(event.created_at);
  const time = fmtDateTime(event.created_at) || nowDateTime();
  const sid = event.session_id || "legacy";
  const runId = event.run_id || "";
  if (typeof event.kind === "string" && event.kind.includes(".")) {
    switch (event.kind) {
      case "message.delta":
        return { type: "agent", key: `agent:${sid}:${runId}`, text: event.text || "", streaming: true, append: true, time, serverOrder: event.event_seq, createdAtMs };
      case "message.completed":
        return { type: "agent", key: `agent:${sid}:${runId}`, text: event.text || "", streaming: false, time, serverOrder: event.event_seq, createdAtMs };
      case "reasoning.delta":
        return { type: "reasoning", key: `reasoning:${sid}:${runId}`, text: event.text || "", append: true, time, serverOrder: event.event_seq, createdAtMs };
      case "tool.activity":
        return toolActivityEntry(event, sid, runId, time, createdAtMs);
      case "run.started":
      case "session.updated":
      case "run.completed":
      case "run.failed":
        return null;
      default:
        return null;
    }
  }
  const payload = event.payload && typeof event.payload === "object" ? event.payload : event;
  const item = payload.item;
  if (item && typeof item === "object") {
    if (item.type === "command_execution") {
      let status = item.status === "in_progress" ? "running" : (item.status || "running");
      if (status === "completed" && item.exit_code != null && item.exit_code !== 0) status = "failed";
      const done = status === "completed" || status === "failed";
      return {
        type: "command",
        key: `command:${event.session_id || ""}:${item.id || item.command || ""}`,
        command: item.command || "command",
        status,
        exit: item.exit_code,
        lines: linesFrom(item.aggregated_output || ""),
        time: nowDateTime(),
        duration: done ? "" : "live",
        serverOrder: event.event_seq,
        createdAtMs
      };
    }
    if (item.type === "agent_message") {
      const agentId = item.id || event.event_seq || (event.session_id ? "" : legacyAgentKeySuffix(item.text));
      return {
        type: "agent",
        key: `agent:${event.session_id || "legacy"}:${agentId}`,
        text: item.text || "",
        time: fmtDateTime(event.created_at) || nowDateTime(),
        streaming: false,
        serverOrder: event.event_seq,
        createdAtMs
      };
    }
    if (item.type === "reasoning") {
      return {
        type: "reasoning",
        key: `reasoning:${event.session_id || "legacy"}:${item.id || event.event_seq || ""}`,
        text: item.text || "",
        time: fmtDateTime(event.created_at) || nowDateTime(),
        serverOrder: event.event_seq,
        createdAtMs
      };
    }
    return null;
  }
  if (event.type === "runtime.user_message.started") {
    return {
      type: "event_row",
      key: `event:${event.event_seq || event.id || event.type}`,
      label: "runtime.user_message.started",
      detail: "message accepted",
      dotColor: "#000",
      time: fmtDateTime(event.created_at) || nowDateTime(),
      serverOrder: event.event_seq,
      createdAtMs
    };
  }
  if (event.type === "runtime.completed") {
    return {
      type: "event_row",
      key: `event:${event.event_seq || event.id || event.type}`,
      label: "runtime.completed",
      detail: "session finished",
      dotColor: "#008000",
      time: fmtDateTime(event.created_at) || nowDateTime(),
      serverOrder: event.event_seq,
      createdAtMs
    };
  }
  if (event.type === "runtime.interrupted") {
    return {
      type: "event_row",
      key: `event:${event.event_seq || event.id || event.type}`,
      label: "runtime.interrupted",
      detail: "interrupted by user",
      dotColor: "#FFA500",
      time: fmtDateTime(event.created_at) || nowDateTime(),
      serverOrder: event.event_seq,
      createdAtMs
    };
  }
  if (event.type === "runtime.error") {
    return {
      type: "runtime_error",
      key: `event:${event.event_seq || event.id || event.type}`,
      message: typeof payload.message === "string" ? payload.message : (typeof event.message === "string" ? event.message : "runtime error"),
      time: fmtDateTime(event.created_at) || nowDateTime(),
      serverOrder: event.event_seq,
      createdAtMs
    };
  }
  return null;
}

export function timelineFromSession(historyEvents, activityEvents) {
  const historyEntries = timelineFromHistory(historyEvents);
  const activityEntries = activityEvents
    .map((event) => entryFromSse(event))
    .filter(Boolean)
    .map((entry, index) => ({ ...entry, activityOrder: index, source: "activity" }));
  const sortedEntries = [...historyEntries, ...activityEntries]
    .sort(compareEntries)
    .filter((entry, _index, entries) => {
      if (entry.type !== "agent" || entry.source !== "activity" || typeof entry.text !== "string") return true;
      return !entries.some((candidate) => (
        candidate !== entry
        && candidate.type === "agent"
        && candidate.source === "history"
        && candidate.text === entry.text
        && Math.abs((candidate.createdAtMs ?? 0) - (entry.createdAtMs ?? 0)) <= 1000
      ));
    });
  const reconciled = [];
  for (const entry of sortedEntries) {
    if ((entry.type === "command" || entry.type === "reasoning") && entry.key) {
      const index = reconciled.findIndex((candidate) => candidate.type === entry.type && candidate.key === entry.key);
      if (index >= 0) {
        reconciled[index] = { ...entry, order: reconciled[index].order ?? entry.order };
        continue;
      }
    }
    reconciled.push(entry);
  }
  return reconciled
    .map((entry, index) => ({ ...entry, order: index }));
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
