import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { MarkdownContent } from "../MarkdownContent/index.jsx";
import { compareEntries } from "../../../lib/timeline.js";

function UserMessage({ entry }) {
  return (
    <div className="msg-user">
      <div className="msg-meta">YOU · {entry.time || ""}</div>
      <div className="bubble">{entry.text}</div>
    </div>
  );
}

function AgentMessage({ entry, sessionId, registeredByPath, onRegistered }) {
  const label = entry.streaming ? "AGENT RESPONSE" : "FINAL ANSWER";
  return (
    <div className={`msg-agent${entry.streaming ? " msg-agent-streaming" : " msg-agent-final"}`}>
      <div className="msg-agent-head">
        <span>{label}</span>
        {entry.time ? <span>{entry.time}</span> : null}
      </div>
      <div className="bubble">
        <MarkdownContent source={entry.text || ""} sessionId={sessionId} registeredByPath={registeredByPath} onRegistered={onRegistered} />
        {entry.streaming ? <span className="agent-cursor" /> : null}
      </div>
    </div>
  );
}

function CommandBlock({ entry }) {
  const defaultOpen = entry.status !== "completed";
  const [open, setOpen] = useState(defaultOpen);
  const badgeKind = entry.status === "completed" ? "completed" : (entry.status === "failed" ? "failed" : "running");
  const dotColor = badgeKind === "completed" ? "#008000" : (badgeKind === "failed" ? "#FF0000" : "#FFA500");
  const exit = entry.exit == null ? "-" : String(entry.exit);
  const exitColor = entry.status === "completed" ? "var(--c-ok)" : (entry.status === "failed" ? "var(--c-danger)" : "var(--c-grey)");
  const lines = entry.lines?.length ? entry.lines : [{ text: "(no output)", color: "#606060" }];

  return (
    <div className="tl-cmd">
      <span className="tl-dot" style={{ background: dotColor, animation: badgeKind === "running" ? "blink-hard 1s step-end infinite" : undefined }} />
      <div className="cmd-block">
        <button className="cmd-head" type="button" onClick={() => setOpen((value) => !value)}>
          {entry.time ? <span className="tl-time">{entry.time}</span> : null}
          <span className="cmd-name">$ {entry.command}</span>
          <StatusBadge kind={badgeKind} />
        </button>
        {open ? (
          <div className="cmd-out">
            <div className="cmd-out-label">AGGREGATED OUTPUT</div>
            {lines.map((line, index) => <div key={index} className="cmd-line" style={{ color: line.color }}>{line.text}</div>)}
          </div>
        ) : null}
        <div className="cmd-foot">
          <span>EXIT <span style={{ color: exitColor }}>{exit}</span></span>
          {entry.duration ? <span>{entry.duration}</span> : null}
          <span className="toggle">{open ? "▾ HIDE OUTPUT" : `▸ SHOW OUTPUT · ${(entry.lines || []).length} LINES`}</span>
        </div>
      </div>
    </div>
  );
}

function ReasoningBlock({ steps }) {
  const [open, setOpen] = useState(false);
  const text = steps.map((step) => step.text).filter(Boolean).join("\n\n");
  const label = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  return (
    <div className="tl-reasoning">
      <button type="button" className="reasoning-head" onClick={() => setOpen((value) => !value)}>
        <span className="reasoning-dot" />
        <span className="reasoning-title">REASONING · {label}</span>
        <span className="reasoning-toggle">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <div className="reasoning-body">{text}</div> : null}
    </div>
  );
}

function EventRow({ entry }) {
  return (
    <div className="tl-row">
      <span className="tl-dot" style={{ background: entry.dotColor || "#000", animation: entry.dotColor === "#FFA500" ? "blink-hard 1s step-end infinite" : undefined }} />
      {entry.time ? <span className="tl-time">{entry.time}</span> : null}
      <span className="tl-label">{entry.label || ""}</span>
      {entry.detail ? <span className="tl-detail">{entry.detail}</span> : null}
    </div>
  );
}

function ArtifactCard({ entry }) {
  const artifact = entry.artifact || {};
  const type = String(artifact.type || "");
  const glyph = type.startsWith("audio") ? "♪" : type.startsWith("image") ? "▣" : type.startsWith("video") ? "►" : "▤";
  const size = artifact.size_bytes ? `${(artifact.size_bytes / 1024 / 1024).toFixed(1)} MB` : "";
  return (
    <div className="artifact-card">
      <div className="head">OUTPUT GENERATED</div>
      <div className="body">
        <span className="glyph">{glyph}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{artifact.title || "artifact"}</span>
          <span style={{ display: "block", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--c-grey)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {(artifact.type || "FILE").toUpperCase()}{size ? ` · ${size}` : ""}{artifact.relative_path ? ` · ${artifact.relative_path}` : ""}
          </span>
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: 1, color: "var(--c-grey)", border: "1px solid var(--c-grey)", padding: "2px 6px", flex: "none", whiteSpace: "nowrap" }}>ARTIFACT READY</span>
      </div>
    </div>
  );
}

function RuntimeError({ entry }) {
  return (
    <div className="rt-error">
      <div className="head">RUNTIME ERROR{entry.time ? ` · ${entry.time}` : ""}</div>
      <div className="body">{entry.message}</div>
    </div>
  );
}

function IdleEmpty() {
  return (
    <div className="idle-empty">
      <div className="k">AGENT IDLE</div>
      <div className="d">No active runtime. Send a message and the live activity stream appears here in real time.</div>
    </div>
  );
}

function orderedEntries(entries) {
  return [...entries].sort(compareEntries);
}

export function Timeline({ entries, busy, sessionId = null, registeredByPath = null, onRegistered = null }) {
  if (!entries.length && !busy) return <div className="stream"><IdleEmpty /></div>;

  const nodes = [];
  let cluster = [];
  let reasoning = [];
  const flushCluster = () => {
    if (!cluster.length) return;
    nodes.push(
      <div className="tl-wrap" key={`cluster-${nodes.length}`}>
        <div className="tl-label-head">AGENT ACTIVITY</div>
        <div className="timeline">
          {cluster.map((entry, index) => entry.type === "command"
            ? <CommandBlock key={entry.key || index} entry={entry} />
            : <EventRow key={`${entry.label}-${index}`} entry={entry} />)}
        </div>
      </div>
    );
    cluster = [];
  };
  const flushReasoning = () => {
    if (!reasoning.length) return;
    nodes.push(<ReasoningBlock key={`reasoning-${nodes.length}`} steps={reasoning} />);
    reasoning = [];
  };

  for (const entry of orderedEntries(entries)) {
    if (entry.type === "reasoning") {
      flushCluster();
      reasoning.push(entry);
      continue;
    }
    if (entry.type === "event_row" || entry.type === "command") {
      flushReasoning();
      cluster.push(entry);
      continue;
    }
    flushReasoning();
    flushCluster();
    if (entry.type === "user") nodes.push(<UserMessage key={`u-${nodes.length}`} entry={entry} />);
    if (entry.type === "agent") nodes.push(<AgentMessage key={`a-${nodes.length}`} entry={entry} sessionId={sessionId} registeredByPath={registeredByPath} onRegistered={onRegistered} />);
    if (entry.type === "artifact") nodes.push(<ArtifactCard key={`ar-${nodes.length}`} entry={entry} />);
    if (entry.type === "runtime_error") nodes.push(<RuntimeError key={`e-${nodes.length}`} entry={entry} />);
  }
  flushReasoning();
  flushCluster();
  return <div className="stream">{nodes}</div>;
}
