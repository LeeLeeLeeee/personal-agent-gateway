import { useEffect, useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";
import { buildCron, describeCron } from "../../../lib/cron.js";
import { fmtDateTime } from "../../../lib/time.js";

const MODES = [
  { value: "daily", label: "DAILY" },
  { value: "weekly", label: "WEEKLY" },
  { value: "interval", label: "INTERVAL" }
];

const WEEKDAYS = [
  [0, "Sunday"], [1, "Monday"], [2, "Tuesday"], [3, "Wednesday"], [4, "Thursday"], [5, "Friday"], [6, "Saturday"]
];

function ScheduleForm({ onCreate, disabled }) {
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [mode, setMode] = useState("daily");
  const [time, setTime] = useState("09:00");
  const [weekday, setWeekday] = useState(5);
  const [everyMinutes, setEveryMinutes] = useState(30);

  const spec = { mode, time, weekday: Number(weekday), everyMinutes: Number(everyMinutes) || 1 };
  const cron = buildCron(spec);
  const description = describeCron(spec);

  const canSubmit = name.trim() !== "" && instruction.trim() !== "";

  function submit(event) {
    event.preventDefault();
    if (!canSubmit || disabled) return;
    onCreate({
      name: name.trim(),
      capability_id: "agent.instruct",
      cron_expression: cron,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      input_template: { prompt: instruction.trim() }
    });
    setName("");
    setInstruction("");
  }

  return (
    <form className="schedule-form" onSubmit={submit} aria-label="New schedule">
      <div className="schedule-form-head mono">NEW SCHEDULE</div>
      <div className="schedule-form-body">
        <label className="schedule-field">
          <span className="schedule-field-label">Name</span>
          <input className="schedule-input" aria-label="Name" value={name} onChange={(event) => setName(event.target.value)} />
        </label>

        <label className="schedule-field">
          <span className="schedule-field-label">Instruction</span>
          <textarea
            className="schedule-textarea"
            aria-label="Instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="What should the agent do each time this runs?"
          />
        </label>

        <div className="schedule-field">
          <span className="schedule-field-label">Frequency</span>
          <div className="team-run-mode" role="group" aria-label="Frequency">
            {MODES.map((item) => (
              <button
                key={item.value}
                type="button"
                aria-pressed={mode === item.value}
                className={`team-run-mode-btn${mode === item.value ? " active" : ""}`}
                onClick={() => setMode(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {mode === "interval" ? (
          <label className="schedule-field">
            <span className="schedule-field-label">Every (minutes)</span>
            <input
              type="number"
              min="1"
              className="schedule-input"
              aria-label="Every minutes"
              value={everyMinutes}
              onChange={(event) => setEveryMinutes(event.target.value)}
            />
          </label>
        ) : (
          <>
            {mode === "weekly" ? (
              <label className="schedule-field">
                <span className="schedule-field-label">Weekday</span>
                <select
                  className="schedule-input"
                  aria-label="Weekday"
                  value={weekday}
                  onChange={(event) => setWeekday(event.target.value)}
                >
                  {WEEKDAYS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="schedule-field">
              <span className="schedule-field-label">Time</span>
              <input
                type="time"
                className="schedule-input"
                aria-label="Time"
                value={time}
                onChange={(event) => setTime(event.target.value)}
              />
            </label>
          </>
        )}

        <div className="schedule-cron-preview console">
          <div className="cmd-line">CRON · {cron}</div>
          <div className="cmd-line schedule-cron-desc">{description}</div>
        </div>

        <div className="schedule-policy mono">Auto-approve · runs the local agent</div>

        <button type="submit" className="btn btn-primary btn-lg schedule-submit" disabled={!canSubmit || disabled}>Create schedule</button>
      </div>
    </form>
  );
}

function ScheduleRow({ schedule, onPause, onResume, onDelete, onRunNow, onOpenDetail, automationReady, automationUnavailableReason }) {
  const confirm = useConfirm();

  async function handleDelete() {
    const ok = await confirm({
      title: "DELETE SCHEDULE",
      message: `Delete schedule "${schedule.name}"? This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true
    });
    if (!ok) return;
    onDelete(schedule.id);
  }

  return (
    <div className="schedule-row">
      <div className="schedule-row-main">
        <div className="schedule-row-name">{schedule.name}</div>
        <div className="schedule-row-prompt mono">{schedule.input_template?.prompt}</div>
        <div className="schedule-row-meta">
          <span className="schedule-cron mono">{schedule.cron_expression}</span>
          <StatusBadge kind={schedule.enabled ? "enabled" : "paused"} />
          <span className="mono schedule-row-when">NEXT · {fmtDateTime(schedule.next_run_at) || "—"}</span>
          <span className="mono schedule-row-when">LAST · {fmtDateTime(schedule.last_run_at) || "never"}</span>
        </div>
      </div>
      <div className="schedule-row-actions">
        <button
          type="button"
          className="btn btn-sm"
          disabled={!onOpenDetail}
          aria-label={`History for ${schedule.name}`}
          onClick={() => onOpenDetail?.(schedule.id)}
        >
          History
        </button>
        {schedule.enabled ? (
          <button type="button" className="btn btn-sm" onClick={() => onPause(schedule.id)}>Pause</button>
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => onResume(schedule.id)}>Resume</button>
        )}
        <button
          type="button"
          className="btn btn-sm"
          disabled={!automationReady}
          title={automationReady ? undefined : automationUnavailableReason}
          onClick={() => onRunNow(schedule.id)}
        >
          Run now
        </button>
        <button type="button" className="btn btn-sm btn-destructive" onClick={handleDelete}>Delete</button>
      </div>
    </div>
  );
}

function ScheduleDetail({ detail, onClose }) {
  const successRate = detail.stats?.success_rate;
  return (
    <aside className="schedule-detail" aria-label="Schedule detail">
      <div className="jobs-drawer-head">
        <span className="mono">SCHEDULE HISTORY</span>
        <button type="button" className="jobs-drawer-close" aria-label="Close history" onClick={onClose}>✕</button>
      </div>
      <div className="jobs-drawer-body">
        <div className="jobs-drawer-title">{detail.schedule?.name}</div>
        <div className="settings-block jobs-drawer-meta">
          <div className="settings-row">
            <span className="settings-k mono">TIMEZONE</span>
            <span className="settings-v mono">{detail.schedule?.timezone || "—"}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">RUNS</span>
            <span className="settings-v mono">{detail.stats?.total ?? 0}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">SUCCESS RATE</span>
            <span className="settings-v mono">
              {successRate == null ? "—" : `${Math.round(successRate * 100)}%`}
            </span>
          </div>
        </div>

        {detail.last_failure?.error_message ? (
          <div className="jobs-drawer-error mono">{detail.last_failure.error_message}</div>
        ) : null}

        <div className="mono jobs-drawer-label schedule-detail-label">NEXT 3 RUNS</div>
        <div className="console" aria-label="Next run preview">
          {(detail.next_runs || []).map((value) => (
            <div className="cmd-line" key={value}>{fmtDateTime(value)}</div>
          ))}
        </div>

        <div className="mono jobs-drawer-label schedule-detail-label">JOB HISTORY</div>
        <div className="schedule-history-list">
          {(detail.jobs || []).map((job) => (
            <div className="settings-row" key={job.id}>
              <span className="settings-k mono">{job.id}</span>
              <StatusBadge kind={job.status} />
            </div>
          ))}
          {!detail.jobs?.length ? <div className="mono schedule-policy">No runs yet.</div> : null}
        </div>
      </div>
    </aside>
  );
}

export function SchedulesView({
  schedules = [],
  automationReady = false,
  automationUnavailableReason = "Automation is not ready",
  onCreate,
  onPause,
  onResume,
  onDelete,
  onRunNow,
  onLoadDetail,
  focusScheduleId = null,
  onFocusHandled
}) {
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  useEffect(() => {
    if (!focusScheduleId || !onLoadDetail) return;
    let active = true;
    setDetailLoading(true);
    setDetailError("");
    onLoadDetail(focusScheduleId)
      .then((loaded) => {
        if (active) setDetail(loaded);
      })
      .catch(() => {
        if (active) {
          setDetail(null);
          setDetailError("Schedule history could not be loaded.");
        }
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    onFocusHandled?.();
    return () => {
      active = false;
    };
  }, [focusScheduleId, onFocusHandled, onLoadDetail]);

  async function openDetail(id) {
    setDetailLoading(true);
    setDetailError("");
    try {
      const loaded = await onLoadDetail(id);
      setDetail(loaded);
    } catch (_error) {
      setDetail(null);
      setDetailError("Schedule history could not be loaded.");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="schedules-view">
      <div className="schedules-main">
        <h1 className="headline">Schedules</h1>
        <div className="schedules-sub mono">{schedules.length} shown</div>
        {!automationReady ? <div className="schedule-policy mono" role="status">{automationUnavailableReason}</div> : null}
        {schedules.length ? (
          <div className="schedule-list">
            {schedules.map((schedule) => (
              <ScheduleRow
                key={schedule.id}
                schedule={schedule}
                onPause={onPause}
                onResume={onResume}
                onDelete={onDelete}
                onRunNow={onRunNow}
                onOpenDetail={onLoadDetail ? openDetail : null}
                automationReady={automationReady}
                automationUnavailableReason={automationUnavailableReason}
              />
            ))}
          </div>
        ) : (
          <div className="planned">NO SCHEDULES</div>
        )}
        {detailLoading ? <div className="planned">LOADING HISTORY</div> : null}
        {detailError ? <div className="jobs-drawer-error mono">{detailError}</div> : null}
        {detail ? <ScheduleDetail detail={detail} onClose={() => setDetail(null)} /> : null}
      </div>

      <ScheduleForm onCreate={onCreate} disabled={!automationReady} />
    </div>
  );
}
