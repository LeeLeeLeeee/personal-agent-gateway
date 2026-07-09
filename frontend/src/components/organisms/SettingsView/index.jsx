function buildGroups(settings) {
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
          k: "AUTH",
          v: settings.totp_configured ? "AUTHENTICATED" : "NOT CONFIGURED",
          color: settings.totp_configured ? "var(--c-ok)" : undefined
        }
      ]
    },
    {
      name: "Tools",
      rows: [
        { k: "FFMPEG", v: settings.ffmpeg_binary },
        { k: "FFPROBE", v: settings.ffprobe_binary },
        { k: "CAPTURE", v: settings.capture_binary },
        { k: "JOB CONCURRENCY", v: String(settings.job_worker_concurrency ?? "") }
      ]
    },
    {
      name: "Security",
      rows: [
        { k: "TUNNEL", v: "LOCAL ONLY", color: "var(--c-ok)" },
        { k: "COOKIE SECURE", v: settings.cookie_secure ? "ON" : "OFF" },
        { k: "SANDBOX", v: settings.codex_sandbox },
        { k: "APPROVAL POLICY", v: settings.codex_approval_policy }
      ]
    }
  ];
}

export function SettingsView({ settings }) {
  if (!settings) return null;
  const groups = buildGroups(settings).map((group) => ({
    ...group,
    rows: group.rows.filter((row) => row.v)
  }));

  return (
    <section className="screen settings-view">
      <h1 className="headline">Settings</h1>
      <div className="settings-view-sub mono">Read-only · reflects the gateway's runtime configuration</div>
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
    </section>
  );
}
