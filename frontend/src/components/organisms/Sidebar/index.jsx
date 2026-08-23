import { Logo } from "../../atoms/Logo/index.jsx";

export const NAV_GROUPS = [
  {
    label: "WORK",
    items: [
      { key: "dashboard", label: "Home" },
      { key: "chat", label: "Chat" },
      { key: "teams", label: "Team Runs" }
    ]
  },
  {
    label: "KNOWLEDGE",
    items: [
      { key: "library", label: "Library" },
      { key: "outputs", label: "Outputs" }
    ]
  },
  {
    label: "SYSTEM",
    items: [
      { key: "configuration", label: "Configuration" },
      { key: "operations", label: "Operations" },
      { key: "settings", label: "Settings" }
    ]
  }
];

export const NAV = NAV_GROUPS.flatMap((group) => group.items);

function formatEnvironmentLabel(value) {
  const trimmed = String(value || "").trim();
  const parts = trimmed.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return trimmed;
  const machine = parts[parts.length - 1];
  const env = parts.slice(0, -1).join(" ");
  return `${machine}(${env})`;
}

export function Sidebar({ screen, teamRunBadge = 0, hooksBadge = 0, environmentTitle = "", onScreenChange }) {
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
        {NAV_GROUPS.map((group) => (
          <div className="sidebar-nav-group" key={group.label}>
            <div className="sidebar-nav-section">{group.label}</div>
            {group.items.map((item) => {
              const active = screen === item.key;
              const badge = item.key === "teams"
                ? teamRunBadge
                : item.key === "configuration" ? hooksBadge : 0;
              return (
                <button
                  key={item.key}
                  className={`nav-item${active ? " nav-item-active" : ""}`}
                  type="button"
                  aria-current={active ? "page" : undefined}
                  onClick={() => onScreenChange(item.key)}
                >
                  <span>{item.label}</span>
                  {badge > 0 ? <span className="nav-badge" aria-hidden="true">{badge}</span> : null}
                </button>
              );
            })}
          </div>
        ))}
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
