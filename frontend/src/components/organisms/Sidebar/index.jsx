import { Logo } from "../../atoms/Logo/index.jsx";

export const NAV = [
  { key: "chat", label: "Chat" },
  { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" },
  { key: "artifacts", label: "Artifacts" },
  { key: "operations", label: "Operations" },
  { key: "settings", label: "Settings" }
];

export const TEAM_NAV = [
  { key: "teams", label: "Team Runs" },
  { key: "team-admin", label: "Teams" },
  { key: "personas", label: "Personas" },
  { key: "rules", label: "Rules" }
];

function formatEnvironmentLabel(value) {
  const trimmed = String(value || "").trim();
  const parts = trimmed.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return trimmed;
  const machine = parts[parts.length - 1];
  const env = parts.slice(0, -1).join(" ");
  return `${machine}(${env})`;
}

export function Sidebar({ screen, teamRunBadge = 0, environmentTitle = "", onScreenChange }) {
  const environmentLabel = formatEnvironmentLabel(environmentTitle);
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-lockup">
          <Logo className="sidebar-brand-logo" />
          <div className="sidebar-brand-title" aria-label="Agent Gateway">Agent<br />Gateway</div>
        </div>
        <div className="sidebar-brand-sub">LOCAL CONSOLE</div>
      </div>
      <nav className="sidebar-nav">
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`nav-item${screen === item.key ? " nav-item-active" : ""}`}
            type="button"
            aria-current={screen === item.key ? "page" : undefined}
            onClick={() => onScreenChange(item.key)}
          >
            {item.label}
          </button>
        ))}
        <div className="sidebar-nav-section">TEAMS</div>
        {TEAM_NAV.map((item) => {
          const active = screen === item.key;
          const showBadge = item.key === "teams" && teamRunBadge > 0;
          return (
            <button
              key={item.key}
              className={`nav-item${active ? " nav-item-active" : ""}`}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => onScreenChange(item.key)}
            >
              <span>{item.label}</span>
              {showBadge ? <span className="nav-badge" aria-hidden="true">{teamRunBadge}</span> : null}
            </button>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <span className="sidebar-status-dot" />
        <span className="sidebar-status-label">AUTHENTICATED</span>
        {environmentLabel ? (
          <span className="sidebar-env-label" title={environmentTitle}>{environmentLabel}</span>
        ) : null}
      </div>
    </aside>
  );
}
