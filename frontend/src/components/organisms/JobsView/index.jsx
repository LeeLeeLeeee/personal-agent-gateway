import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { useToast } from "../../providers/UiProvider/index.jsx";

const STATUS = [
  ["all", "All"],
  ["waiting_approval", "Waiting"],
  ["queued", "Queued"],
  ["running", "Running"],
  ["succeeded", "Succeeded"],
  ["failed", "Failed"],
  ["canceled", "Canceled"],
  ["draft", "Draft"]
];

const SOURCE = [
  ["all", "All"],
  ["chat", "Chat"],
  ["manual", "Manual"],
  ["schedule", "Schedule"],
  ["api", "API"]
];

function fmtWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function eventLine(event) {
  return event.payload?.line || JSON.stringify(event.payload);
}

function JobDrawer({ job, events, onClose }) {
  const toast = useToast();

  async function handleCopyCommand() {
    await navigator.clipboard.writeText(job.command_preview || "");
    toast("명령이 복사되었습니다", "success");
  }

  return (
    <aside className="jobs-drawer" aria-label="Job detail">
      <div className="jobs-drawer-head">
        <span className="mono">JOB DETAIL</span>
        <button type="button" className="jobs-drawer-close" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      <div className="jobs-drawer-body">
        <div className="jobs-drawer-title">{job.title}</div>
        <StatusBadge kind={job.status} />

        <div className="settings-block jobs-drawer-meta">
          <div className="settings-row">
            <span className="settings-k mono">CAPABILITY</span>
            <span className="settings-v mono">{job.capability_id}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">SOURCE</span>
            <span className="settings-v mono">{job.source}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">INPUT</span>
            <span className="settings-v mono">{JSON.stringify(job.input)}</span>
          </div>
        </div>

        <div className="mono jobs-drawer-label">COMMAND</div>
        <div className="console jobs-drawer-command">{job.command_preview}</div>

        <div className="jobs-drawer-label-row">
          <span className="mono jobs-drawer-label">LOGS</span>
          {job.status === "running" ? <span className="mono jobs-drawer-live">● LIVE</span> : null}
        </div>
        <div className="console jobs-drawer-logs">
          {events.length ? events.map((event) => (
            <div className="cmd-line" key={event.id}>{eventLine(event)}</div>
          )) : <div className="cmd-line">No events.</div>}
        </div>

        {job.error_message ? <div className="jobs-drawer-error mono">{job.error_message}</div> : null}

        <div className="jobs-drawer-actions">
          <button type="button" className="btn btn-sm" onClick={handleCopyCommand}>Copy command</button>
        </div>
      </div>
    </aside>
  );
}

export function JobsView({ jobs = [], onLoadEvents }) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const [events, setEvents] = useState([]);

  const rows = jobs.filter((job) => (
    (statusFilter === "all" || job.status === statusFilter)
    && (sourceFilter === "all" || job.source === sourceFilter)
  ));
  const selected = jobs.find((job) => job.id === selectedId) || null;

  function selectJob(id) {
    setSelectedId(id);
    setEvents([]);
    onLoadEvents(id).then(setEvents);
  }

  return (
    <div className="jobs-view">
      <div className="jobs-main">
        <h1 className="headline">Jobs</h1>
        <div className="jobs-sub mono">{rows.length} shown</div>

        <div className="jobs-filters">
          {STATUS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${statusFilter === key ? " chip-active" : ""}`}
              aria-pressed={statusFilter === key}
              onClick={() => setStatusFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="jobs-filters">
          {SOURCE.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${sourceFilter === key ? " chip-active" : ""}`}
              aria-pressed={sourceFilter === key}
              onClick={() => setSourceFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {rows.length ? (
          <div className="jobs-table">
            <div className="jobs-row jobs-row-head mono">
              <span>TITLE</span>
              <span>CAPABILITY</span>
              <span>SOURCE</span>
              <span>STATUS</span>
              <span>TIME</span>
            </div>
            {rows.map((job) => (
              <button
                key={job.id}
                type="button"
                className={`jobs-row${selectedId === job.id ? " jobs-row-selected" : ""}`}
                aria-label={`Open ${job.title}`}
                onClick={() => selectJob(job.id)}
              >
                <span className="jobs-cell-title">{job.title}</span>
                <span className="mono jobs-cell-capability">{job.capability_id}</span>
                <span className="mono jobs-cell-source">{job.source}</span>
                <span><StatusBadge kind={job.status} /></span>
                <span className="mono jobs-cell-time">{fmtWhen(job.finished_at || job.started_at || job.created_at)}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="planned">NO JOBS MATCH</div>
        )}
      </div>

      {selected ? <JobDrawer job={selected} events={events} onClose={() => setSelectedId(null)} /> : null}
    </div>
  );
}
