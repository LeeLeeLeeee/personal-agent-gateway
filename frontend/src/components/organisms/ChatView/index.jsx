import { deriveLive } from "../../../lib/timeline.js";
import { fmtTime } from "../../../lib/time.js";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { Composer } from "../../molecules/Composer/index.jsx";
import { LoaderCube } from "../../molecules/LoaderCube/index.jsx";
import { SessionRail } from "../SessionRail/index.jsx";
import { Timeline } from "../Timeline/index.jsx";

function ChatHeader({ sessions }) {
  const active = (sessions || []).find((session) => session.is_active);
  const title = active?.title || "New session";
  const started = active?.created_at ? fmtTime(active.created_at, false) : "";
  return (
    <div className="chat-header">
      <span className="title">{title}</span>
      <span className="meta">SESSION · {title.slice(0, 28)}{started ? ` · started ${started}` : ""}</span>
    </div>
  );
}

function LiveStatusSummary({ entries, busy, turnStart, turnEnd }) {
  const live = deriveLive({ entries, busy, turnStart, turnEnd });
  const cell = (key, node) => (
    <div className="summary-cell" key={key}>
      <span className="summary-k">{key}</span>
      {node}
    </div>
  );
  return (
    <div className="summary-bar">
      {cell("CURRENT PHASE", <span className="summary-v" style={{ color: live.color }}>{live.phase}</span>)}
      {cell("RUNNING", <span className="summary-v" style={{ color: live.running > 0 ? "var(--c-warn)" : "var(--c-grey)" }}>{live.running}</span>)}
      {cell("LAST EVENT", <div style={{ marginTop: 4 }}><StatusBadge kind={live.lastKind} /></div>)}
      {cell("ELAPSED", <span className="summary-v">{live.elapsed}</span>)}
    </div>
  );
}

function Proposal({ approval, onResolve }) {
  if (!approval) return null;
  return (
    <div className="proposal">
      <div className="proposal-head">
        <span>JOB PROPOSAL</span>
        <span style={{ color: "var(--c-warn)" }}>WAITING APPROVAL</span>
      </div>
      <div style={{ padding: 16 }}>
        <div className="kv">
          <div className="k">CAPABILITY</div><div>shell.run</div>
          <div className="k">RISK</div><div style={{ color: "var(--c-danger)" }}>HIGH · runs a local command</div>
        </div>
        <div style={{ marginTop: 12 }}>
          <div className="mono" style={{ fontSize: 10, letterSpacing: 1, color: "var(--c-grey)", marginBottom: 4 }}>COMMAND PREVIEW</div>
          <div className="console">{approval.command}</div>
        </div>
        <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
          <Button size="btn-sm" variant="primary" onClick={() => onResolve("approve")}>Approve</Button>
          <Button size="btn-sm" variant="destructive" onClick={() => onResolve("deny")}>Deny</Button>
        </div>
      </div>
    </div>
  );
}

export function ChatView({
  sessions,
  entries,
  busy,
  turnStart,
  turnEnd,
  pendingApproval,
  turnStreamed,
  onSend,
  onSearch,
  onActivate,
  onReset,
  onRename,
  onDelete,
  onResolveApproval
}) {
  return (
    <div className="chat">
      <SessionRail sessions={sessions} onSearch={onSearch} onActivate={onActivate} onReset={onReset} onRename={onRename} onDelete={onDelete} />
      <div className="chat-col">
        <ChatHeader sessions={sessions} />
        <LiveStatusSummary entries={entries} busy={busy} turnStart={turnStart} turnEnd={turnEnd} />
        <div className="transcript">
          <Timeline entries={entries} busy={busy} />
          {busy && !turnStreamed ? <LoaderCube label="AGENT WORKING" /> : null}
          <Proposal approval={pendingApproval} onResolve={onResolveApproval} />
        </div>
        <Composer busy={busy} onSend={onSend} />
      </div>
    </div>
  );
}
