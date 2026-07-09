import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";

const GLYPH = { image: "▦", video: "▶", audio: "♪", document: "▤", log: "≣", report: "¶", archive: "◫" };

function fmtSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function ImageViewer({ src, alt }) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const drag = useRef(null);
  const stageRef = useRef(null);

  function reset() { setScale(1); setPos({ x: 0, y: 0 }); }
  function zoom(delta) { setScale((s) => Math.min(8, Math.max(1, +(s + delta).toFixed(2)))); }

  const onWheel = useCallback((e) => {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 0.2 : -0.2);
  }, []);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return undefined;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel, { passive: false });
  }, [onWheel]);

  function onDown(e) { drag.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }; }
  function onMove(e) {
    if (!drag.current) return;
    setPos({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y });
  }
  function onUp() { drag.current = null; }

  return (
    <div className="viewer">
      <div
        className="viewer-stage"
        ref={stageRef}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
        style={{ cursor: scale > 1 ? "grab" : "default" }}
      >
        <img
          className="viewer-img"
          src={src}
          alt={alt}
          draggable="false"
          style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})` }}
        />
      </div>
      <div className="viewer-controls">
        <button type="button" onClick={() => zoom(-0.2)} aria-label="Zoom out">−</button>
        <span className="mono">{Math.round(scale * 100)}%</span>
        <button type="button" onClick={() => zoom(0.2)} aria-label="Zoom in">+</button>
        <button type="button" onClick={reset} aria-label="Reset">RESET</button>
      </div>
    </div>
  );
}

function Preview({ artifact }) {
  const contentUrl = api.artifactContentUrl(artifact.id);
  const [text, setText] = useState("");

  useEffect(() => {
    if (artifact.type !== "log" && artifact.type !== "report") return undefined;
    let alive = true;
    api.artifactText(artifact.id).then((v) => { if (alive) setText(v); });
    return () => { alive = false; };
  }, [artifact.id, artifact.type]);

  if (artifact.type === "image") return <ImageViewer src={contentUrl} alt={artifact.title} />;
  if (artifact.type === "video") return <video className="modal-media" controls src={contentUrl} />;
  if (artifact.type === "audio") return <audio className="modal-media" controls src={contentUrl} />;
  if (artifact.type === "log" || artifact.type === "report") return <pre className="mono modal-text">{text}</pre>;
  if (artifact.type === "document" && artifact.mime_type === "application/pdf") {
    return <iframe className="modal-doc" src={contentUrl} title={artifact.title} />;
  }
  return (
    <div className="modal-fallback">
      <span aria-hidden="true">{GLYPH[artifact.type] || "◫"}</span>
      <span className="mono">{fmtSize(artifact.size_bytes)}</span>
    </div>
  );
}

export function ArtifactModal({ artifact, onClose, onDeleted }) {
  const toast = useToast();
  const confirm = useConfirm();
  const contentUrl = api.artifactContentUrl(artifact.id);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleCopyPath() {
    await navigator.clipboard.writeText(artifact.relative_path);
    toast("경로가 복사되었습니다", "success");
  }

  async function handleDelete() {
    const ok = await confirm({
      title: "DELETE ARTIFACT",
      message: `"${artifact.title}" 를 삭제할까요? 삭제하면 다시 등록할 수 있습니다.`,
      confirmLabel: "Delete",
      danger: true
    });
    if (!ok) return;
    if (await api.deleteArtifact(artifact.id)) {
      toast("삭제되었습니다", "success");
      onDeleted?.(artifact.id);
      onClose();
    } else {
      toast("삭제에 실패했습니다", "error");
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={artifact.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mono">ARTIFACT · {artifact.type}</span>
          <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-preview"><Preview artifact={artifact} /></div>
        <div className="modal-title">{artifact.title}</div>
        <div className="settings-block modal-provenance">
          <div className="settings-row"><span className="settings-k mono">PATH</span><span className="settings-v mono modal-v">{artifact.relative_path}</span></div>
          <div className="settings-row"><span className="settings-k mono">SIZE</span><span className="settings-v mono modal-v">{fmtSize(artifact.size_bytes)} · {artifact.mime_type}</span></div>
          <div className="settings-row"><span className="settings-k mono">SESSION</span><span className="settings-v mono modal-v">{artifact.source_session_id || "-"}</span></div>
        </div>
        <div className="modal-actions">
          <a className="btn btn-primary btn-sm" href={contentUrl} download>Download</a>
          <button type="button" className="btn btn-sm" onClick={handleCopyPath}>Copy path</button>
          <button type="button" className="btn btn-sm btn-danger" onClick={handleDelete}>Delete</button>
        </div>
      </div>
    </div>
  );
}
