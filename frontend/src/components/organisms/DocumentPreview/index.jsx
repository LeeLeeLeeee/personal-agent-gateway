import { MarkdownContent } from "../MarkdownContent/index.jsx";

function prettyJson(content) {
  try { return JSON.stringify(JSON.parse(content), null, 2); }
  catch { return content; }
}

export function DocumentPreview({ open, doc, onClose }) {
  if (!open || !doc) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card doc-preview" role="dialog" aria-modal="true"
        aria-label={`Document ${doc.path}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mono">{doc.path}</span>
          <button type="button" className="modal-close" aria-label="Close preview" onClick={onClose}>×</button>
        </div>
        <div className="doc-preview-body">
          {!doc.previewable ? (
            <div className="doc-preview-unavailable mono">미리보기 불가 · {doc.reason || "unsupported"}</div>
          ) : doc.kind === "md" ? (
            <MarkdownContent source={doc.content || ""} />
          ) : doc.kind === "json" ? (
            <pre className="doc-preview-pre">{prettyJson(doc.content || "")}</pre>
          ) : (
            <pre className="doc-preview-pre">{doc.content || ""}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
