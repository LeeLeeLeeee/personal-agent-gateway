import { useState } from "react";
import { api } from "../../../api/client.js";
import { ArtifactModal } from "../ArtifactModal/index.jsx";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";
import { GLYPH, fmtSize } from "../../../lib/artifactFormat.js";
import { fmtDateTime } from "../../../lib/time.js";

const TYPE_FILTERS = [
  ["all", "All"],
  ["image", "Images"],
  ["video", "Videos"],
  ["audio", "Audio"],
  ["document", "Documents"],
  ["log", "Logs"],
  ["report", "Reports"],
  ["archive", "Archives"]
];

export function ArtifactsView({ artifacts = [], onChange }) {
  const [segment, setSegment] = useState("saved");
  const [cleanupPreview, setCleanupPreview] = useState(null);
  const [cleanupIds, setCleanupIds] = useState(new Set());
  const [type, setType] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const confirm = useConfirm();
  const toast = useToast();

  const grid = (segment === "cleanup" ? (cleanupPreview?.artifacts || []) : artifacts).filter((a) => {
    const isTemporary = a.retention_class === "temporary";
    const inSegment = segment === "cleanup" || (segment === "saved" ? !isTemporary : isTemporary);
    return inSegment && (type === "all" || a.type === type);
  });
  const groups = grid.reduce((current, artifact) => {
    const runId = artifact.metadata?.team_run_id;
    const taskId = artifact.metadata?.task_id;
    const label = runId
      ? `TEAM RUN ${runId}${taskId ? ` · TASK ${taskId}` : ""}`
      : "LOCAL / USER ARTIFACTS";
    current.set(label, [...(current.get(label) || []), artifact]);
    return current;
  }, new Map());
  const selected = artifacts.find((a) => a.id === selectedId) || null;

  return (
    <div className="artifacts-view">
      <div className="artifacts-main">
        <h1 className="headline">Artifacts</h1>
        <div className="artifacts-sub mono">{grid.length} shown · ./data/artifacts</div>

        <div className="artifacts-filters" aria-label="Artifact retention">
          <button type="button" className={`chip${segment === "saved" ? " chip-active" : ""}`} aria-pressed={segment === "saved"} onClick={() => setSegment("saved")}>보관됨</button>
          <button type="button" className={`chip${segment === "recent" ? " chip-active" : ""}`} aria-pressed={segment === "recent"} onClick={() => setSegment("recent")}>최근 산출물</button>
          <button type="button" className={`chip${segment === "cleanup" ? " chip-active" : ""}`} aria-pressed={segment === "cleanup"} onClick={async () => { setSegment("cleanup"); setCleanupIds(new Set()); setCleanupPreview(await api.artifactCleanupPreview()); }}>정리 후보</button>
        </div>

        {segment === "cleanup" ? <p className="artifacts-sub">정리 후보는 선택한 항목만 삭제됩니다.</p> : null}
        {segment === "cleanup" && cleanupIds.size ? <button type="button" className="btn btn-danger btn-sm" onClick={async () => {
          const ids = [...cleanupIds];
          if (!await confirm({ title: "정리 실행", message: `${ids.length}개 항목을 삭제할까요?`, confirmLabel: "정리", danger: true })) return;
          const result = await api.cleanupArtifacts(ids);
          if (!result) { toast("정리에 실패했습니다", "error"); return; }
          toast(`${result.deleted_ids.length}개를 정리했습니다`, "success");
          setCleanupIds(new Set());
          setCleanupPreview(await api.artifactCleanupPreview());
          onChange?.();
        }}>선택 {cleanupIds.size}개 정리</button> : null}

        <div className="artifacts-filters">
          {TYPE_FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${type === key ? " chip-active" : ""}`}
              aria-pressed={type === key}
              onClick={() => setType(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {grid.length ? (
          <div className="artifact-grid">
            {[...groups.entries()].map(([group, groupArtifacts]) => (
              <section key={group} className="artifact-group">
                <h2 className="mono">{group}</h2>
                {groupArtifacts.map((a) => (
              segment === "cleanup" ? (
                <label key={a.id} className="artifact-card">
                  <input type="checkbox" aria-label={`${a.title} 정리 선택`} checked={cleanupIds.has(a.id)} onChange={() => setCleanupIds((current) => {
                    const next = new Set(current); if (next.has(a.id)) next.delete(a.id); else next.add(a.id); return next;
                  })} />
                  <span className="artifact-card-title">{a.title}</span>
                  <span className="mono artifact-card-meta">{fmtSize(a.size_bytes)} · 만료됨</span>
                  <button type="button" className="btn btn-sm" onClick={async (event) => {
                    event.preventDefault();
                    const result = await api.updateArtifactRetention(a.id, { retention_class: "pinned" });
                    if (!result) { toast("보관에 실패했습니다", "error"); return; }
                    setCleanupIds((current) => { const next = new Set(current); next.delete(a.id); return next; });
                    setCleanupPreview(await api.artifactCleanupPreview());
                    toast("보관했습니다", "success");
                    onChange?.();
                  }}>보관</button>
                </label>
              ) : (
              <button
                key={a.id}
                type="button"
                className="artifact-card"
                aria-label={`Open ${a.title}`}
                onClick={() => setSelectedId(a.id)}
              >
                <div className="artifact-card-thumb">
                  {a.type === "image" ? (
                    <img
                      className="artifact-card-img"
                      src={api.artifactContentUrl(a.id)}
                      alt={a.title}
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ) : (
                    <span className="artifact-card-glyph" aria-hidden="true">{GLYPH[a.type] || "◫"}</span>
                  )}
                  <span className="artifact-card-type mono">{a.type}</span>
                </div>
                <div className="artifact-card-body">
                  <div className="artifact-card-title">{a.title}</div>
                  <div className="mono artifact-card-meta">{fmtSize(a.size_bytes)} · {fmtDateTime(a.created_at)}</div>
                  {a.metadata?.team_run_id ? (
                    <div className="mono artifact-card-meta">TEAM RUN {a.metadata.team_run_id}{a.metadata.task_id ? ` · TASK ${a.metadata.task_id}` : ""}</div>
                  ) : null}
                </div>
              </button>
              )
                ))}
              </section>
            ))}
          </div>
        ) : (
          <div className="planned">NO ARTIFACTS</div>
        )}
      </div>

      {selected ? (
        <ArtifactModal
          artifact={selected}
          onClose={() => setSelectedId(null)}
          onDeleted={() => { setSelectedId(null); onChange?.(); }}
        />
      ) : null}
    </div>
  );
}
