import { deriveLive } from "../../../lib/timeline.js";

function SseIndicator({ busy, sseState, eventCount }) {
  let color;
  let label;
  let blink = false;
  if (busy) {
    color = "var(--c-warn)";
    label = "STREAMING /api/events";
    blink = true;
  } else if (sseState === "error") {
    color = "var(--c-danger)";
    label = "DISCONNECTED";
  } else {
    color = "var(--c-ok)";
    label = eventCount ? "CONNECTED" : "IDLE · CONNECTED";
  }

  return (
    <div className="sse-wrap">
      <span className="sse-dot" style={{ background: color, animation: blink ? "blink-hard 1s step-end infinite" : undefined }} />
      <span className="sse-label">{label}</span>
    </div>
  );
}

export function Statusbar({ status, entries, busy, turnStart, turnEnd, sseState, navOpen, onToggleNav }) {
  const live = deriveLive({ entries, busy, turnStart, turnEnd });
  const items = [
    ["WORKSPACE", status?.workspace_root || "--"],
    ["MODEL", `${status?.provider || "codex"}/${status?.model || "default"}`],
    ["SESSION", `${status?.session_status || "idle"} ${(status?.session_id || "").slice(0, 8)}`],
    ["PHASE", live.phase],
    ["RUNNING", String(live.running)],
    ["PENDING", status?.pending_approval ? "1" : "0"],
    ["EVENTS", String(live.events)]
  ];

  return (
    <header className="statusbar">
      <button className="nav-toggle" type="button" aria-expanded={navOpen} onClick={onToggleNav}>Menu</button>
      {items.map(([key, value]) => (
        <div className="status-item" key={key}>
          <span className="status-k">{key}</span>
          <span className="status-v">{String(value)}</span>
        </div>
      ))}
      <SseIndicator busy={busy} sseState={sseState} eventCount={entries.length} />
    </header>
  );
}
