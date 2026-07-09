export const NAV = [
  { key: "chat", label: "Chat" },
  { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" },
  { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" },
  { key: "settings", label: "Settings" }
];

export const TEAM_NAV = [
  { key: "teams", label: "Team Runs" },
  { key: "personas", label: "Personas" }
];

export function Sidebar({ screen, teamRunBadge = 0, onScreenChange }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-title" aria-label="Agent Gateway">Agent<br />Gateway</div>
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
      </div>
    </aside>
  );
}
