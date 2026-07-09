import { deriveLive } from "../../../lib/timeline.js";
import { useToast } from "../../providers/UiProvider/index.jsx";

// Show only the distinguishable tail of a long workspace path (last two segments).
function shortWorkspace(path) {
  if (!path) return "--";
  const segments = path.split(/[\\/]+/).filter(Boolean);
  if (segments.length <= 2) return path;
  return `…/${segments.slice(-2).join("/")}`;
}

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
  const toast = useToast();
  const live = deriveLive({ entries, busy, turnStart, turnEnd });
  const effort = status?.session_config?.options?.effort;
  const workspace = status?.workspace_root || "";
  const items = [
    ["AGENT", status?.provider || status?.session_config?.agent_id || "codex"],
    ["MODEL", status?.model || status?.session_config?.model || "default"],
    ["EFFORT", effort ? String(effort).toUpperCase() : "--"],
    ["SESSION", `${status?.session_status || "idle"} ${(status?.session_id || "").slice(0, 8)}`],
    ["PHASE", live.phase],
    ["RUNNING", String(live.running)],
    ["PENDING", status?.pending_approval ? "1" : "0"],
    ["EVENTS", String(live.events)]
  ];

  async function copyWorkspace() {
    if (!workspace) return;
    try {
      await navigator.clipboard.writeText(workspace);
      toast("복사되었습니다", "success");
    } catch (_error) {
      toast("클립보드 복사에 실패했습니다", "error");
    }
  }

  return (
    <header className="statusbar">
      <button className="nav-toggle" type="button" aria-expanded={navOpen} onClick={onToggleNav}>Menu</button>
      <button
        type="button"
        className="status-item status-item-copy"
        title={workspace ? `${workspace} — 클릭하여 복사` : ""}
        aria-label={workspace ? `Copy workspace path ${workspace}` : "Workspace"}
        onClick={copyWorkspace}
      >
        <span className="status-k">WORKSPACE</span>
        <span className="status-v">{shortWorkspace(workspace)}<span className="status-copy-glyph" aria-hidden="true">⧉</span></span>
      </button>
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
