import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";

const SEG_COLORS = {
  completed: "#008000", in_progress: "#FFA500", blocked: "#FF0000",
  failed: "#FF0000", pending: "#E8E8E8", canceled: "#808080"
};
const SEG_ORDER = ["completed", "in_progress", "blocked", "failed", "canceled", "pending"];
function fmtTimestamp(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").slice(0, 16);
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
  const shortId = run.id.slice(0, 8);
  const title = run.team_name ? `${run.team_name} · ${shortId}` : shortId;
  const accessibleTitle = run.team_name ? title : (run.current_objective || run.goal || title);
  const displayStatus = run.display_status || run.status;
  const cycle = run.pending_request || run.latest_cycle;
  const cycleNumber = run.pending_request?.slot_ordinal || run.latest_cycle?.sequence;
  const cycleLabel = cycleNumber ? `CYCLE #${cycleNumber}` : "NO CYCLE";
  const series = run.auto_series;
  const policyMeta = series
    ? `AUTO ${series.settled_slots} / ${series.target_slots}${series.next_run_at ? ` · NEXT ${fmtTimestamp(series.next_run_at)}` : ""}`
    : `CYCLES ${run.cycle_count || 0}`;

  return (
    <button
      type="button"
      className="trc"
      aria-label={`Open team run ${accessibleTitle}`}
      onClick={() => onOpen(run.id)}
    >
      <div className="trc-main">
        <div className="trc-top">
          <span className="mono trc-id">{run.id}</span>
          <StatusBadge kind={displayStatus} />
          <span className="mono trc-mode">{String(run.execution_policy || run.run_mode || "legacy").toUpperCase()}</span>
          {run.team_id ? null : <span className="mono trc-legacy">LEGACY</span>}
        </div>
        <div className="headline trc-goal">{title}</div>
        <div className="trc-cycle-row">
          <span className="mono trc-cycle">
            {cycleLabel}{cycle?.status ? ` · ${String(cycle.status).replaceAll("_", " ").toUpperCase()}` : ""}
          </span>
          <span className="trc-objective">{run.current_objective || run.goal || "Ready for trigger"}</span>
        </div>
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
          <span className="mono trc-progress-k">CURRENT CYCLE TASKS</span>
          <span className="mono trc-progress-v">{run.task_done} / {run.task_total} DONE</span>
        </div>
        <div className="trc-bar">
          {segments.map((seg) => (
            <span key={seg.key} style={{ flex: seg.flex, background: seg.color }} />
          ))}
        </div>
        <div className="trc-progress-foot">
          <span className="mono trc-elapsed">{policyMeta}</span>
          <span className="mono trc-open">OPEN →</span>
        </div>
        <div className="mono trc-updated">UPDATED · {fmtTimestamp(run.last_activity_at || run.updated_at)}</div>
      </div>
    </button>
  );
}
