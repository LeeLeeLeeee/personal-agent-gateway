import { Button } from "../../atoms/Button/index.jsx";

export const NAV = [
  { key: "chat", label: "Chat" },
  { key: "jobs", label: "Jobs" },
  { key: "schedules", label: "Schedules" },
  { key: "capabilities", label: "Capabilities" },
  { key: "artifacts", label: "Artifacts" },
  { key: "settings", label: "Settings" }
];

export function Sidebar({ screen, onScreenChange, onLogout }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand headline" aria-label="Agent Gateway">Agent<br />Gateway</div>
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
      </nav>
      <div className="sidebar-foot">
        <Button size="btn-sm" onClick={onLogout}>Log out</Button>
      </div>
    </aside>
  );
}
