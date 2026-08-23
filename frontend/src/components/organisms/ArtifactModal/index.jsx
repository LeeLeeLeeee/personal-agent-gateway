import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";
import { GLYPH, fmtSize } from "../../../lib/artifactFormat.js";
import { MarkdownContent } from "../MarkdownContent/index.jsx";

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

  function onDown(e) { if (scale <= 1) return; drag.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }; }
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

function isMarkdown(artifact) {
  return artifact.mime_type === "text/markdown" || /\.md(?:own)?$/i.test(artifact.title || "");
}

function isReadableText(artifact) {
  return isMarkdown(artifact) || artifact.mime_type?.startsWith("text/") || ["log", "report"].includes(artifact.type);
}

function Preview({ artifact }) {
  const contentUrl = api.artifactContentUrl(artifact.id);
  const [text, setText] = useState("");

  useEffect(() => {
    if (!isReadableText(artifact)) return undefined;
    let alive = true;
    setText("");
    api.artifactText(artifact.id).then((v) => { if (alive) setText(v); }).catch(() => { if (alive) setText(""); });
    return () => { alive = false; };
  }, [artifact]);

  if (artifact.type === "image") return <ImageViewer src={contentUrl} alt={artifact.title} />;
  if (artifact.type === "video") return <video className="modal-media" controls src={contentUrl} />;
  if (artifact.type === "audio") return <audio className="modal-media" controls src={contentUrl} />;
  if (isMarkdown(artifact)) return <div className="modal-markdown"><MarkdownContent source={text} pathRegistration={false} /></div>;
  if (isReadableText(artifact)) return <pre className="mono modal-text">{text}</pre>;
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

export function ArtifactModal({
  artifact,
  breadcrumbs = [],
  role = null,
  sourceTarget = null,
  presentation = "modal",
  onOpenSource,
  onClose,
  onDeleted
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [unavailableSourceArtifactId, setUnavailableSourceArtifactId] = useState(null);
  const sourceUnavailable = unavailableSourceArtifactId === artifact.id;
  const contentUrl = api.artifactContentUrl(artifact.id);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleCopyPath() {
    if (!navigator.clipboard?.writeText) { toast("경로 복사를 지원하지 않는 환경입니다", "error"); return; }
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

  async function handleOpenSource() {
    const opened = await onOpenSource(sourceTarget);
    if (opened === false) {
      setUnavailableSourceArtifactId(artifact.id);
      return;
    }
    onClose();
  }

  return (
    <div className={`modal-backdrop${presentation === "inspector" ? " modal-inspector" : ""}`} onClick={onClose}>
      <div className={`modal-card${presentation === "inspector" ? " modal-card-inspector" : ""}`} role="dialog" aria-modal="true" aria-label={artifact.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mono">ARTIFACT · {artifact.type}</span>
          <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-preview"><Preview artifact={artifact} /></div>
        <div className="modal-title">{artifact.title}</div>
        {breadcrumbs.length ? <div className="modal-breadcrumbs mono">{breadcrumbs.map((crumb) => crumb.label).join(" / ")}{role?.label ? ` · ${role.label}` : ""}</div> : null}
        <details className="settings-block modal-provenance">
          <summary className="mono">기술 정보</summary>
          <div className="settings-row"><span className="settings-k mono">PATH</span><span className="settings-v mono modal-v">{artifact.relative_path}</span></div>
          <div className="settings-row"><span className="settings-k mono">SIZE</span><span className="settings-v mono modal-v">{fmtSize(artifact.size_bytes)} · {artifact.mime_type}</span></div>
          <div className="settings-row"><span className="settings-k mono">SESSION</span><span className="settings-v mono modal-v">{artifact.source_session_id || "-"}</span></div>
        </details>
        <div className="modal-actions">
          {sourceUnavailable ? (
            <span className="mono modal-source-unavailable" role="status">원본을 사용할 수 없음</span>
          ) : sourceTarget && onOpenSource ? (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleOpenSource}
            >
              {sourceTarget.label}
            </button>
          ) : null}
          <a className="btn btn-primary btn-sm" href={contentUrl} download>Download</a>
          <button type="button" className="btn btn-sm" onClick={handleCopyPath}>Copy path</button>
          <button type="button" className="btn btn-sm btn-danger" onClick={handleDelete}>Delete</button>
        </div>
      </div>
    </div>
  );
}
