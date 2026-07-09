import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";
import { buildCron, describeCron } from "../../../lib/cron.js";

const MODES = [
  { value: "daily", label: "DAILY" },
  { value: "weekly", label: "WEEKLY" },
  { value: "interval", label: "INTERVAL" }
];

const WEEKDAYS = [
  [0, "Sunday"], [1, "Monday"], [2, "Tuesday"], [3, "Wednesday"], [4, "Thursday"], [5, "Friday"], [6, "Saturday"]
];

function fmtWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function ScheduleForm({ onCreate }) {
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [mode, setMode] = useState("daily");
  const [time, setTime] = useState("09:00");
  const [weekday, setWeekday] = useState(5);
  const [everyMinutes, setEveryMinutes] = useState(30);

  const spec = { mode, time, weekday: Number(weekday), everyMinutes: Number(everyMinutes) || 1 };
  const cron = buildCron(spec);
  const description = describeCron(spec);

  function submit(event) {
    event.preventDefault();
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

        <button type="submit" className="btn btn-primary btn-lg schedule-submit">Create schedule</button>
      </div>
    </form>
  );
}

function ScheduleRow({ schedule, onPause, onResume, onDelete, onRunNow }) {
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
          <StatusBadge kind={schedule.enabled ? "active" : "default"} />
          <span className="mono schedule-row-when">NEXT · {fmtWhen(schedule.next_run_at) || "—"}</span>
          <span className="mono schedule-row-when">LAST · {fmtWhen(schedule.last_run_at) || "never"}</span>
        </div>
      </div>
      <div className="schedule-row-actions">
        {schedule.enabled ? (
          <button type="button" className="btn btn-sm" onClick={() => onPause(schedule.id)}>Pause</button>
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => onResume(schedule.id)}>Resume</button>
        )}
        <button type="button" className="btn btn-sm" onClick={() => onRunNow(schedule.id)}>Run now</button>
        <button type="button" className="btn btn-sm btn-destructive" onClick={handleDelete}>Delete</button>
      </div>
    </div>
  );
}

export function SchedulesView({ schedules = [], onCreate, onPause, onResume, onDelete, onRunNow }) {
  return (
    <div className="schedules-view">
      <div className="schedules-main">
        <h1 className="headline">Schedules</h1>
        <div className="schedules-sub mono">{schedules.length} shown</div>
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
              />
            ))}
          </div>
        ) : (
          <div className="planned">NO SCHEDULES</div>
        )}
      </div>

      <ScheduleForm onCreate={onCreate} />
    </div>
  );
}
