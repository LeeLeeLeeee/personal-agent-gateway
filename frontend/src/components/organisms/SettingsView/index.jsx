import { useState } from "react";
import { fmtDateTime } from "../../../lib/time.js";
import { useConfirm } from "../../providers/UiProvider/index.jsx";

function buildGroups(settings) {
  const tunnelMode = settings.tunnel_mode === "not_reported"
    ? "NOT REPORTED"
    : String(settings.tunnel_mode || "UNKNOWN").replaceAll("_", " ").toUpperCase();
  const automationColor = settings.automation_ready ? "var(--c-ok)" : "var(--c-danger)";
  const availabilityRows = (settings.agent_availability || []).map((agent) => ({
    k: `${agent.id.toUpperCase()} CLI`,
    v: agent.available ? "AVAILABLE" : `UNAVAILABLE${agent.error ? ` · ${agent.error}` : ""}`,
    color: agent.available ? "var(--c-ok)" : "var(--c-danger)"
  }));

  return [
    {
      name: "Workspace",
      rows: [
        { k: "WORKSPACE ROOT", v: settings.workspace_root },
        { k: "ARTIFACT ROOT", v: settings.artifact_root },
        { k: "SESSION DIR", v: settings.session_dir },
        { k: "TEMP DIR", v: settings.temp_dir }
      ]
    },
    {
      name: "Agent",
      rows: [
        { k: "PROVIDER / MODEL", v: `${settings.provider} · ${settings.model}` },
        {
          k: "SESSION",
          v: settings.session_authenticated ? "AUTHENTICATED" : "NOT AUTHENTICATED",
          color: settings.session_authenticated ? "var(--c-ok)" : "var(--c-danger)"
        },
        { k: "TOTP", v: settings.totp_configured ? "CONFIGURED" : "NOT CONFIGURED" }
      ]
    },
    {
      name: "Tools",
      rows: [
        { k: "FFMPEG", v: settings.ffmpeg_binary },
        { k: "FFPROBE", v: settings.ffprobe_binary },
        { k: "CAPTURE", v: settings.capture_binary },
        ...availabilityRows
      ]
    },
    {
      name: "Runtime",
      rows: [
        { k: "JOB CONCURRENCY", v: String(settings.effective_job_concurrency ?? "") },
        { k: "TEAM EXECUTION", v: String(settings.team_execution_mode || "").toUpperCase() },
        { k: "WORKER", v: settings.worker_alive ? "RUNNING" : "STOPPED", color: settings.worker_alive ? "var(--c-ok)" : "var(--c-danger)" },
        { k: "SCHEDULER", v: settings.scheduler_alive ? "RUNNING" : "STOPPED", color: settings.scheduler_alive ? "var(--c-ok)" : "var(--c-danger)" },
        { k: "AUTOMATION", v: settings.automation_ready ? "READY" : "UNAVAILABLE", color: automationColor },
        { k: "AUTOMATION DETAIL", v: settings.automation_unavailable_reason }
      ]
    },
    {
      name: "Security",
      rows: [
        { k: "BIND", v: settings.bind_host },
        { k: "TUNNEL", v: tunnelMode },
        { k: "COOKIE SECURE", v: settings.cookie_secure ? "ON" : "OFF" },
        { k: "ACCESS MODE", v: String(settings.access_mode || "restricted").toUpperCase() },
        { k: "WORKSPACE WRITE", v: settings.workspace_writable ? "AVAILABLE" : "BLOCKED" },
        { k: "AUDIT", v: settings.audit_enabled ? `ON · ${settings.audit_retention_days} DAYS` : "OFF" },
        { k: "SCHEMA", v: settings.schema_version != null ? `V${settings.schema_version}` : "" },
        { k: "SANDBOX", v: settings.codex_sandbox },
        { k: "APPROVAL POLICY", v: settings.codex_approval_policy }
      ]
    }
  ];
}

export function SettingsView({
  settings,
  authSessions = [],
  notificationState = { supported: false, permission: "unsupported", enabled: false },
  onEnableNotifications,
  onDisableNotifications,
  onAccessModeChange,
  onRevokeSession,
  onRevokeAllSessions
}) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState("");
  if (!settings) return null;
  const groups = buildGroups(settings).map((group) => ({
    ...group,
    rows: group.rows.filter((row) => row.v)
  }));
  const notificationStatus = !notificationState.supported
    ? "UNSUPPORTED"
    : notificationState.permission === "denied"
      ? "BLOCKED"
      : notificationState.enabled ? "ON" : "OFF";

  async function changeAccessMode(mode) {
    if (!onAccessModeChange) return;
    let confirmed = false;
    if (mode === "full_access") {
      confirmed = await confirm({
        title: "ENABLE FULL ACCESS",
        message: "Full Access permits artifact registration outside the workspace. All external path actions are audited.",
        confirmLabel: "Enable full access",
        danger: true
      });
      if (!confirmed) return;
    }
    setBusy("access");
    try {
      await onAccessModeChange(mode, confirmed);
    } finally {
      setBusy("");
    }
  }

  async function revokeAll() {
    const accepted = await confirm({
      title: "REVOKE ALL SESSIONS",
      message: "Sign out every browser session, including this one?",
      confirmLabel: "Revoke all",
      danger: true
    });
    if (accepted) await onRevokeAllSessions?.();
  }

  async function enableNotifications() {
    if (!onEnableNotifications) return;
    setBusy("notifications");
    try {
      await onEnableNotifications();
    } finally {
      setBusy("");
    }
  }

  async function disableNotifications() {
    if (!onDisableNotifications) return;
    setBusy("notifications");
    try {
      await onDisableNotifications();
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="screen settings-view">
      <h1 className="headline">Settings</h1>
      <div className="settings-view-sub mono">Runtime configuration · security changes are audited</div>
      {groups.map((group) => (
        <div key={group.name} className="settings-group">
          <div className="settings-group-head">{group.name}</div>
          <div className="settings-block">
            {group.rows.map((row) => (
              <div key={row.k} className="settings-row">
                <span className="settings-k mono">{row.k}</span>
                <span className="settings-v mono" style={{ color: row.color }}>{row.v}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="settings-group">
        <div className="settings-group-head">Access policy</div>
        <div className="settings-security-actions">
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy === "access" || settings.access_mode === "restricted"}
            onClick={() => changeAccessMode("restricted")}
          >
            Use Restricted
          </button>
          <button
            type="button"
            className="btn btn-sm btn-destructive"
            disabled={busy === "access" || settings.access_mode === "full_access"}
            onClick={() => changeAccessMode("full_access")}
          >
            Enable Full Access
          </button>
        </div>
      </div>
      <div className="settings-group">
        <div className="settings-section-row">
          <div className="settings-group-head">Browser notifications</div>
          <span className="badge" aria-label="Browser notification status">{notificationStatus}</span>
        </div>
        <div className="settings-block">
          <div className="settings-row">
            <span className="settings-k mono">DELIVERY</span>
            <span className="settings-v mono">ONLY WHILE THIS GATEWAY TAB IS OPEN</span>
          </div>
        </div>
        <div className="settings-security-actions">
          {notificationState.enabled ? (
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy === "notifications"}
              onClick={disableNotifications}
            >
              Disable notifications
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy === "notifications" || !notificationState.supported || notificationState.permission === "denied"}
              onClick={enableNotifications}
            >
              Enable notifications
            </button>
          )}
        </div>
      </div>
      <div className="settings-group">
        <div className="settings-section-row">
          <div className="settings-group-head">Browser sessions</div>
          {onRevokeAllSessions ? (
            <button type="button" className="btn btn-sm btn-destructive" onClick={revokeAll}>Revoke all</button>
          ) : null}
        </div>
        <div className="settings-block">
          {authSessions.map((session) => (
            <div className="settings-session-row" key={session.id}>
              <div className="settings-session-main">
                <div className="mono settings-session-id">{session.id}</div>
                <div className="mono settings-session-time">
                  LAST · {fmtDateTime(session.last_seen_at)} · EXPIRES · {fmtDateTime(session.idle_expires_at)}
                </div>
              </div>
              {session.current ? <span className="badge badge-running">CURRENT</span> : null}
              {onRevokeSession ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-label={`Revoke ${session.id}`}
                  onClick={() => onRevokeSession(session.id, session.current)}
                >
                  Revoke
                </button>
              ) : null}
            </div>
          ))}
          {!authSessions.length ? <div className="planned">NO ACTIVE SESSIONS</div> : null}
        </div>
      </div>
    </section>
  );
}
