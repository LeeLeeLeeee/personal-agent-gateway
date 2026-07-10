import { Sidebar } from "../../organisms/Sidebar/index.jsx";
import { Statusbar } from "../../organisms/Statusbar/index.jsx";

export function AppShell({
  children,
  screen,
  teamRunBadge,
  status,
  environmentTitle,
  entries,
  busy,
  turnStart,
  turnEnd,
  sseState,
  navOpen,
  onToggleNav,
  onCloseNav,
  onScreenChange
}) {
  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <Sidebar
        screen={screen}
        teamRunBadge={teamRunBadge}
        environmentTitle={environmentTitle}
        onScreenChange={onScreenChange}
      />
      <div className="main-col">
        <Statusbar
          status={status}
          entries={entries}
          busy={busy}
          turnStart={turnStart}
          turnEnd={turnEnd}
          sseState={sseState}
          navOpen={navOpen}
          onToggleNav={onToggleNav}
        />
        <div className="content-row">
          <main className="main">{children}</main>
        </div>
      </div>
      {navOpen ? <div className="nav-backdrop" onClick={onCloseNav} /> : null}
    </div>
  );
}
