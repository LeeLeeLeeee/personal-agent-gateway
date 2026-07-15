import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";

const SEG_COLORS = {
  completed: "#008000", in_progress: "#FFA500", blocked: "#FF0000",
  failed: "#FF0000", pending: "#E8E8E8", canceled: "#808080"
};
const SEG_ORDER = ["completed", "in_progress", "blocked", "failed", "canceled", "pending"];
const ACTIVE = new Set(["running", "planning", "summarizing"]);

function fmtElapsed(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function Profile({ profile, fallbackName = "" }) {
  const name = profile?.name || fallbackName;
  if (!name) return <span className="trc-profile trc-profile-empty mono">—</span>;
  if (profile?.avatar) {
    return (
      <span className="trc-profile" title={name}>
        <img className="trc-member-avatar" src={`/static/avatars/${profile.avatar}.png`} alt="" />
        <span className="trc-profile-name">{name}</span>
      </span>
    );
  }
  return (
    <span className="trc-profile" title={name}>
      <span className="trc-member-avatar trc-member-initials mono">{profile?.initials || "?"}</span>
      <span className="trc-profile-name">{name}</span>
    </span>
  );
}

export function TeamRunCard({ run, onOpen }) {
  const counts = run.task_counts || {};
  const segments = SEG_ORDER
    .filter((key) => counts[key] > 0)
    .map((key) => ({ key, flex: counts[key], color: SEG_COLORS[key] }));
  const active = ACTIVE.has(run.status);

  return (
    <button
      type="button"
      className="trc"
      aria-label={`Open team run ${run.goal}`}
      onClick={() => onOpen(run.id)}
    >
      <div className="trc-main">
        <div className="trc-top">
          <span className="mono trc-id">{run.id}</span>
          <StatusBadge kind={run.status} />
          <span className="mono trc-mode">{run.run_mode}</span>
          {run.team_id ? null : <span className="mono trc-legacy">LEGACY</span>}
        </div>
        <div className="headline trc-goal">{run.goal}</div>
        <div className="trc-roster">
          <span className="mono trc-roster-k">LEADER</span>
          <Profile profile={run.leader} fallbackName={run.leader_name} />
          <span className="mono trc-roster-k">MEMBERS</span>
          <span className="trc-members">
            {(run.members || []).map((member, index) => (
              <Profile key={`${member.name}-${index}`} profile={member} />
            ))}
          </span>
        </div>
      </div>
      <div className="trc-progress">
        <div className="trc-progress-head">
          <span className="mono trc-progress-k">TASKS</span>
          <span className="mono trc-progress-v">{run.task_done} / {run.task_total} DONE</span>
        </div>
        <div className="trc-bar">
          {segments.map((seg) => (
            <span key={seg.key} style={{ flex: seg.flex, background: seg.color }} />
          ))}
        </div>
        <div className="trc-progress-foot">
          <span className="mono trc-elapsed">{active ? "ELAPSED" : "TOOK"} · {fmtElapsed(run.elapsed_seconds)}</span>
          <span className="mono trc-open">OPEN →</span>
        </div>
      </div>
    </button>
  );
}
