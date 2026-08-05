import { useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { ArtifactModal } from "../ArtifactModal/index.jsx";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";
import { GLYPH, fmtSize } from "../../../lib/artifactFormat.js";
import { fmtDateTime } from "../../../lib/time.js";

const TYPE_FILTERS = [
  ["all", "All"], ["image", "Images"], ["video", "Videos"], ["audio", "Audio"],
  ["document", "Documents"], ["log", "Logs"], ["report", "Reports"], ["archive", "Archives"]
];

function legacyItems(artifacts, segment) {
  return artifacts
    .filter((artifact) => {
      const temporary = artifact.retention_class === "temporary";
      return segment === "recent" ? temporary : !temporary;
    })
    .map((artifact) => {
      const runId = artifact.metadata?.team_run_id;
      const taskId = artifact.metadata?.task_id;
      return {
        artifact,
        source_kind: runId ? "team_task_output" : "manual_upload",
        role: { code: "attachment", label: "Attachment" },
        breadcrumbs: runId
          ? [{ kind: "team_run", id: runId, label: `TEAM RUN ${runId}` }, ...(taskId ? [{ kind: "team_task", id: taskId, label: `TASK ${taskId}` }] : [])]
          : [{ kind: "local", id: "local", label: "Local / user artifacts" }],
        deletion: { allowed: true }
      };
    });
}

function grouped(items) {
  return items.reduce((all, item) => {
    const group = item.breadcrumbs?.[0] || { id: "local", label: "Local / user artifacts" };
    const key = `${group.kind || "source"}:${group.id || group.label}`;
    const existing = all.get(key) || { group, items: [] };
    existing.items.push(item);
    all.set(key, existing);
    return all;
  }, new Map());
}

export function ArtifactsView({ artifacts = [], onChange }) {
  const [segment, setSegment] = useState("saved");
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const [browser, setBrowser] = useState(null);
  const [cleanupPreview, setCleanupPreview] = useState(null);
  const [selected, setSelected] = useState(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const requestRef = useRef(0);
  const confirm = useConfirm();
  const toast = useToast();

  async function refreshBrowser() {
    const currentRequest = requestRef.current + 1;
    requestRef.current = currentRequest;
    try {
      const result = await api.artifactBrowser({ segment, query, fileKind: type === "all" ? "" : type });
      if (currentRequest === requestRef.current && result) setBrowser(result);
    } catch {
      // The legacy list remains usable while a browser request is unavailable.
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => { refreshBrowser(); }, 180);
    return () => window.clearTimeout(timer);
  }, [segment, type, query]);

  const fallback = legacyItems(artifacts, segment);
  const pageItems = browser?.items || fallback;
  const items = segment === "cleanup"
    ? (cleanupPreview?.artifacts || []).map((artifact) => ({ artifact, breadcrumbs: [{ kind: "cleanup", id: "cleanup", label: "Cleanup candidates" }], deletion: { allowed: true } }))
    : pageItems;
  const visibleItems = items.filter((item) => {
    if (segment === "cleanup") return true;
    const title = item.artifact.title || "";
    return !query.trim() || title.toLowerCase().includes(query.trim().toLowerCase()) || Boolean(browser);
  });
  const groups = grouped(visibleItems);
  const counts = browser?.counts || { saved: legacyItems(artifacts, "saved").length, recent: legacyItems(artifacts, "recent").length, cleanup: cleanupPreview?.artifacts?.length || 0 };

  async function openCleanup() {
    setSegment("cleanup");
    setSelectionMode(true);
    setSelectedIds(new Set());
    try {
      setCleanupPreview(await api.artifactCleanupPreview());
    } catch {
      toast("정리 후보를 불러오지 못했습니다", "error");
    }
  }

  function toggleSelection(id) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function deleteSelection() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    const ok = await confirm({ title: "선택한 산출물 삭제", message: `${ids.length}개 항목을 삭제할까요?`, confirmLabel: "삭제", danger: true });
    if (!ok) return;
    const result = segment === "cleanup" ? await api.cleanupArtifacts(ids) : await api.deleteArtifacts(ids);
    if (!result) { toast("삭제에 실패했습니다", "error"); return; }
    const blocked = result.blocked || [];
    if (blocked.length) toast(`${blocked.length}개는 사용 중이라 삭제하지 않았습니다`, "warning");
    if (result.deleted_ids?.length) toast(`${result.deleted_ids.length}개를 삭제했습니다`, "success");
    setSelectedIds(new Set(blocked.map((entry) => entry.artifact_id)));
    if (segment === "cleanup") setCleanupPreview(await api.artifactCleanupPreview());
    else await refreshBrowser();
    onChange?.();
  }

  async function pinCleanup(artifactId) {
    const result = await api.updateArtifactRetention(artifactId, { retention_class: "pinned" });
    if (!result) { toast("보관에 실패했습니다", "error"); return; }
    setSelectedIds((current) => { const next = new Set(current); next.delete(artifactId); return next; });
    setCleanupPreview(await api.artifactCleanupPreview());
    toast("보관했습니다", "success");
    onChange?.();
  }

  return (
    <div className="artifacts-view">
      <div className="artifacts-main">
        <div className="artifacts-heading-row">
          <div><h1 className="headline">Artifacts</h1><div className="artifacts-sub mono">{visibleItems.length} shown · {counts.saved || 0} saved · {counts.recent || 0} recent</div></div>
          {segment !== "cleanup" ? <button type="button" className={`btn btn-sm${selectionMode ? " btn-danger" : ""}`} onClick={() => { setSelectionMode((value) => !value); setSelectedIds(new Set()); }}>{selectionMode ? "선택 취소" : "선택 삭제"}</button> : null}
        </div>
        <label className="artifact-search mono">SEARCH <input className="input-field" aria-label="Search artifacts" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="제목, 채팅, Team Run, Task 검색" /></label>
        <div className="artifacts-filters" aria-label="Artifact retention">
          <button type="button" className={`chip${segment === "saved" ? " chip-active" : ""}`} onClick={() => { setSegment("saved"); setSelectionMode(false); setSelectedIds(new Set()); }}>보관됨 {counts.saved ? `(${counts.saved})` : ""}</button>
          <button type="button" className={`chip${segment === "recent" ? " chip-active" : ""}`} onClick={() => { setSegment("recent"); setSelectionMode(false); setSelectedIds(new Set()); }}>최근 산출물 {counts.recent ? `(${counts.recent})` : ""}</button>
          <button type="button" className={`chip${segment === "cleanup" ? " chip-active" : ""}`} onClick={openCleanup}>정리 후보 {counts.cleanup ? `(${counts.cleanup})` : ""}</button>
        </div>
        <div className="artifacts-filters">
          {TYPE_FILTERS.map(([key, label]) => <button key={key} type="button" className={`chip${type === key ? " chip-active" : ""}`} onClick={() => setType(key)}>{label}</button>)}
        </div>
        {selectionMode && selectedIds.size ? <div className="artifact-bulk-bar"><span>{selectedIds.size}개 선택됨</span><button type="button" className="btn btn-danger btn-sm" onClick={deleteSelection}>선택 {selectedIds.size}개 삭제</button></div> : null}

        {visibleItems.length ? <div className="artifact-groups">
          {[...groups.values()].map(({ group, items: groupItems }) => <section key={`${group.kind}:${group.id}`} className="artifact-group">
            <div className="artifact-group-head"><span className="mono">{group.kind || "source"}</span><h2>{group.label}</h2><span className="mono">{groupItems.length} files</span></div>
            <div className="artifact-list">
              {groupItems.map((item) => {
                const artifact = item.artifact;
                const detail = item.breadcrumbs?.slice(1).map((crumb) => crumb.label).join(" · ");
                return <div className="artifact-row" key={artifact.id}>
                  {selectionMode ? <input type="checkbox" aria-label={`${artifact.title} 선택`} checked={selectedIds.has(artifact.id)} onChange={() => toggleSelection(artifact.id)} /> : null}
                  <button type="button" className="artifact-row-open" aria-label={`Open ${artifact.title}`} onClick={() => setSelected(item)}>
                    <span className="artifact-row-glyph" aria-hidden="true">{GLYPH[artifact.type] || "◫"}</span>
                    <span className="artifact-row-title"><strong>{artifact.title}</strong><small className="mono">{detail || item.role?.label || item.source_kind}</small></span>
                    <span className="artifact-row-meta mono">{fmtSize(artifact.size_bytes)}<br />{fmtDateTime(artifact.created_at)}</span>
                  </button>
                  {segment === "cleanup" ? <button type="button" className="btn btn-sm" onClick={() => pinCleanup(artifact.id)}>보관</button> : null}
                </div>;
              })}
            </div>
          </section>)}
        </div> : <div className="planned">NO ARTIFACTS</div>}
      </div>
      {selected ? <ArtifactModal artifact={selected.artifact} breadcrumbs={selected.breadcrumbs} role={selected.role} presentation="inspector" onClose={() => setSelected(null)} onDeleted={() => { setSelected(null); refreshBrowser(); onChange?.(); }} /> : null}
    </div>
  );
}
