import { MarkdownContent } from "../MarkdownContent/index.jsx";

function prettyJson(content) {
  try { return JSON.stringify(JSON.parse(content), null, 2); }
  catch { return content; }
}

function sandboxedHtml(content) {
  const policy = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
    + "font-src data:; form-action 'none'; base-uri 'none'";
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${policy}"></head><body>${content}</body></html>`;
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
          ) : doc.kind === "image" ? (
            <img className="doc-preview-image" src={doc.preview_url} alt={doc.path} />
          ) : doc.kind === "html" ? (
            <iframe
              className="doc-preview-frame"
              title={`${doc.path} preview`}
              sandbox=""
              srcDoc={sandboxedHtml(doc.content || "")}
            />
          ) : (
            <pre className="doc-preview-pre">{doc.content || ""}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
