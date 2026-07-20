import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { PersonaPicker } from "../PersonaPicker/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";
import { fmtDateTime } from "../../../lib/time.js";

function targetSummary(hook, teamRuns) {
  const bits = [];
  if (hook.filter?.from_contains) bits.push(`from∋${hook.filter.from_contains}`);
  if (hook.filter?.subject_contains) bits.push(`subj∋${hook.filter.subject_contains}`);
  bits.push(hook.filter?.folder || "INBOX");
  if (hook.target_kind === "team_run") {
    const target = teamRuns.find((run) => run.id === hook.target_team_run_id);
    bits.push(`team:${target?.goal || hook.target_team_run_id || "missing"}`);
  } else if (hook.target_kind === "persona") {
    bits.push(`persona:${hook.target_persona_snapshot?.name || hook.target_persona_id || "missing"}`);
  } else {
    bits.push(`${hook.target_backend}/${hook.target_model}`);
  }
  return bits.join(" · ");
}

function HookForm({ personas, teamRuns, onCreate, onTestConnection }) {
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromContains, setFromContains] = useState("");
  const [subjectContains, setSubjectContains] = useState("");
  const [folder, setFolder] = useState("INBOX");
  const [promptTemplate, setPromptTemplate] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState(5);
  const [targetPersonaId, setTargetPersonaId] = useState("");
  const [targetKind, setTargetKind] = useState("persona");
  const [targetTeamRunId, setTargetTeamRunId] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const continuousRuns = teamRuns.filter(
    (run) => run.lifecycle_mode === "continuous" && run.run_mode === "plan_and_execute"
  );

  const selectedTargetTeamRunId = continuousRuns.some(
    (run) => run.id === targetTeamRunId
  ) ? targetTeamRunId : (continuousRuns[0]?.id || "");
  const selectedTargetPersonaId = personas.some(
    (persona) => persona.id === targetPersonaId
  ) ? targetPersonaId : (personas[0]?.id || "");
  const targetReady = targetKind === "team_run"
    ? selectedTargetTeamRunId
    : selectedTargetPersonaId;

  const canSubmit =
    name.trim() && host.trim() && username.trim() && password
    && promptTemplate.trim() && targetReady;

  function connectionBody() {
    return { host: host.trim(), port: Number(port) || 993, username: username.trim() };
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await onTestConnection({
        connection: connectionBody(),
        secret: password,
        filter: { folder: folder.trim() || "INBOX" }
      });
      setTestResult(result || { ok: false, error: "No response" });
    } catch (_error) {
      setTestResult({ ok: false, error: "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  function submit(event) {
    event.preventDefault();
    if (!canSubmit) return;
    onCreate({
      name: name.trim(),
      source_type: "email",
      connection: connectionBody(),
      secret: password,
      filter: {
        from_contains: fromContains.trim(),
        subject_contains: subjectContains.trim(),
        folder: folder.trim() || "INBOX"
      },
      target_kind: targetKind,
      target_persona_id: targetKind === "persona" ? selectedTargetPersonaId : null,
      target_team_run_id: targetKind === "team_run" ? selectedTargetTeamRunId : null,
      target_backend: "",
      target_model: "",
      target_options: {},
      prompt_template: promptTemplate.trim(),
      poll_interval_seconds: (Number(intervalMinutes) || 1) * 60
    });
    setName("");
    setHost("");
    setUsername("");
    setPassword("");
    setFromContains("");
    setSubjectContains("");
    setPromptTemplate("");
    setTestResult(null);
  }

  return (
    <form id="new-hook-form" className="schedule-form" onSubmit={submit} aria-label="New hook">
      <div className="schedule-form-head mono">NEW HOOK</div>
      <div className="schedule-form-body">
        <label className="schedule-field">
          <span className="schedule-field-label">Name</span>
          <input className="schedule-input" aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>

        <div className="hook-section mono">CONNECTION</div>
        <label className="schedule-field">
          <span className="schedule-field-label">Host</span>
          <input className="schedule-input" aria-label="Host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="imap.gmail.com" />
        </label>
        <label className="schedule-field">
          <span className="schedule-field-label">Port</span>
          <input type="number" className="schedule-input" aria-label="Port" value={port} onChange={(e) => setPort(e.target.value)} />
        </label>
        <label className="schedule-field">
          <span className="schedule-field-label">Username</span>
          <input className="schedule-input" aria-label="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="schedule-field">
          <span className="schedule-field-label">App password</span>
          <input type="password" className="schedule-input" aria-label="App password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="hook-test-row">
          <button type="button" className="btn btn-sm" disabled={testing || !host.trim() || !username.trim() || !password} onClick={handleTest}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {testResult ? (
            <span className={`hook-test-result mono ${testResult.ok ? "ok" : "err"}`} role="status">
              {testResult.ok ? "✓ Connected" : `✕ ${testResult.error || "Failed"}`}
            </span>
          ) : null}
        </div>

        <div className="hook-section mono">FILTER</div>
        <label className="schedule-field">
          <span className="schedule-field-label">From contains</span>
          <input className="schedule-input" aria-label="From contains" value={fromContains} onChange={(e) => setFromContains(e.target.value)} />
        </label>
        <label className="schedule-field">
          <span className="schedule-field-label">Subject contains</span>
          <input className="schedule-input" aria-label="Subject contains" value={subjectContains} onChange={(e) => setSubjectContains(e.target.value)} />
        </label>
        <label className="schedule-field">
          <span className="schedule-field-label">Folder</span>
          <input className="schedule-input" aria-label="Folder" value={folder} onChange={(e) => setFolder(e.target.value)} />
        </label>

        <div className="hook-section mono">TARGET</div>
        <div className="tp-mode" role="group" aria-label="Hook target">
          <button
            type="button"
            className={`tp-mode-btn${targetKind === "persona" ? " active" : ""}`}
            aria-pressed={targetKind === "persona"}
            onClick={() => setTargetKind("persona")}
          >PERSONA</button>
          <button
            type="button"
            className={`tp-mode-btn${targetKind === "team_run" ? " active" : ""}`}
            aria-pressed={targetKind === "team_run"}
            disabled={!continuousRuns.length}
            onClick={() => setTargetKind("team_run")}
          >TEAM RUN</button>
        </div>
        {targetKind === "persona" ? (
          <PersonaPicker
            personas={personas}
            value={selectedTargetPersonaId}
            onChange={setTargetPersonaId}
          />
        ) : (
          <label className="schedule-field">
            <span className="schedule-field-label">Target team run</span>
            <select
              className="schedule-input"
              aria-label="Target team run"
              value={selectedTargetTeamRunId}
              onChange={(event) => setTargetTeamRunId(event.target.value)}
            >
              {continuousRuns.map((run) => (
                <option key={run.id} value={run.id}>{run.goal}</option>
              ))}
            </select>
          </label>
        )}
        {!continuousRuns.length ? (
          <div className="schedule-policy mono">Create a continuous plan-and-execute Team Run to enable TEAM RUN target.</div>
        ) : null}

        <label className="schedule-field">
          <span className="schedule-field-label">Prompt template</span>
          <textarea
            className="schedule-textarea"
            aria-label="Prompt template"
            value={promptTemplate}
            onChange={(e) => setPromptTemplate(e.target.value)}
            placeholder="Placeholders: {{from}} {{subject}} {{body}} {{date}}"
          />
        </label>

        <label className="schedule-field">
          <span className="schedule-field-label">Poll every (minutes)</span>
          <input type="number" min="1" className="schedule-input" aria-label="Poll minutes" value={intervalMinutes} onChange={(e) => setIntervalMinutes(e.target.value)} />
        </label>

        <button type="submit" className="btn btn-primary btn-lg schedule-submit" disabled={!canSubmit}>Create hook</button>
      </div>
    </form>
  );
}

function HookRow({ hook, teamRuns, onToggle, onRunNow, onDelete, onOpenRuns }) {
  const confirm = useConfirm();
  async function handleDelete() {
    const ok = await confirm({
      title: "DELETE HOOK",
      message: `Delete hook "${hook.name}"? This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true
    });
    if (!ok) return;
    onDelete(hook.id);
  }
  return (
    <div className="schedule-row">
      <div className="schedule-row-main">
        <div className="schedule-row-name">{hook.name}</div>
        <div className="schedule-row-prompt mono">{targetSummary(hook, teamRuns)}</div>
        <div className="schedule-row-prompt mono">{hook.prompt_template}</div>
        <div className="schedule-row-meta">
          <StatusBadge kind={hook.enabled ? "enabled" : "paused"} />
          <span className="mono schedule-row-when">LAST · {fmtDateTime(hook.last_polled_at) || "never"}</span>
          {hook.last_error ? <span className="hook-row-error mono">{hook.last_error}</span> : null}
        </div>
      </div>
      <div className="schedule-row-actions">
        <button type="button" className="btn btn-sm" aria-label={`Runs for ${hook.name}`} onClick={() => onOpenRuns(hook.id)}>Runs</button>
        {hook.enabled ? (
          <button type="button" className="btn btn-sm" onClick={() => onToggle(hook.id, false)}>Pause</button>
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => onToggle(hook.id, true)}>Resume</button>
        )}
        <button type="button" className="btn btn-sm" onClick={() => onRunNow(hook.id)}>Run now</button>
        <button type="button" className="btn btn-sm btn-destructive" onClick={handleDelete}>Delete</button>
      </div>
    </div>
  );
}

function HookRunsDrawer({ hook, runs, onClose, onOpenTeamRun }) {
  return (
    <aside className="schedule-detail" aria-label="Hook runs">
      <div className="jobs-drawer-head">
        <span className="mono">HOOK RUNS · {hook?.name}</span>
        {hook?.target_team_run_id && onOpenTeamRun ? (
          <button type="button" className="btn btn-sm" onClick={() => onOpenTeamRun(hook.target_team_run_id)}>
            Open Team Run
          </button>
        ) : null}
        <button type="button" className="jobs-drawer-close" aria-label="Close runs" onClick={onClose}>✕</button>
      </div>
      <div className="jobs-drawer-body">
        {runs.length ? runs.map((run) => (
          <div className="hook-run" key={run.id}>
            <div className="hook-run-head">
              <span className="hook-run-summary">{run.trigger_summary}</span>
              <StatusBadge kind={run.status} />
            </div>
            <div className="mono schedule-row-when">{fmtDateTime(run.created_at)}</div>
            {run.team_run_cycle_id ? (
              <div className="mono schedule-row-when">CYCLE · {run.team_run_cycle_id}</div>
            ) : null}
            {run.status === "succeeded" && run.result_text ? <div className="hook-run-result">{run.result_text}</div> : null}
            {run.status === "failed" && run.error_message ? <div className="jobs-drawer-error mono">{run.error_message}</div> : null}
          </div>
        )) : <div className="mono schedule-policy">No runs yet.</div>}
      </div>
    </aside>
  );
}

export function HooksView({
  hooks = [],
  hookRuns = [],
  agents = [],
  personas = [],
  teamRuns = [],
  openHookRunsId = null,
  onCreate,
  onToggle,
  onRunNow,
  onDelete,
  onOpenRuns,
  onCloseRuns,
  onTestConnection,
  onOpenTeamRun
}) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const openHook = hooks.find((hook) => hook.id === openHookRunsId) || null;
  return (
    <div className={`schedules-view${showCreateForm ? "" : " hooks-view-list-only"}`}>
      <div className="schedules-main">
        <div className="hooks-view-head">
          <h1 className="headline">Hooks</h1>
          <button
            type="button"
            className={`btn btn-sm${showCreateForm ? " btn-primary" : ""}`}
            aria-controls="new-hook-form"
            aria-expanded={showCreateForm}
            onClick={() => setShowCreateForm((visible) => !visible)}
          >
            CREATE NEW
          </button>
        </div>
        <div className="schedules-sub mono">{hooks.length} shown</div>
        {hooks.length ? (
          <div className="schedule-list">
            {hooks.map((hook) => (
              <HookRow
                key={hook.id}
                hook={hook}
                teamRuns={teamRuns}
                onToggle={onToggle}
                onRunNow={onRunNow}
                onDelete={onDelete}
                onOpenRuns={onOpenRuns}
              />
            ))}
          </div>
        ) : (
          <div className="planned">NO HOOKS</div>
        )}
        {openHook ? (
          <HookRunsDrawer
            hook={openHook}
            runs={hookRuns}
            onClose={onCloseRuns}
            onOpenTeamRun={onOpenTeamRun}
          />
        ) : null}
      </div>

      {showCreateForm ? (
        <HookForm
          personas={personas}
          teamRuns={teamRuns}
          onCreate={onCreate}
          onTestConnection={onTestConnection}
        />
      ) : null}
    </div>
  );
}
